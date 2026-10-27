import json
import sqlite3
from datetime import date, datetime, timedelta
from functools import wraps

from flask import request, session

from app.db import get_db
from app.services.client_service import client_by_name, name_key, parse_reg_withdrawal_max


class StalePolicy(ValueError):
    pass


def table_columns(db, table, db_type):
    if db_type == "sqlite":
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})").fetchall()}
    return {row["COLUMN_NAME"] for row in db.execute(
        "SELECT COLUMN_NAME FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=?", (table,)
    ).fetchall()}


def ensure_company_policy_schema(db, db_type):
    fields = {
        "cutoff_mingguan_aktif": "INTEGER NOT NULL DEFAULT 0",
        "cutoff_hari": "INTEGER NOT NULL DEFAULT 2",
        "produk_reg_aktif": "INTEGER NOT NULL DEFAULT 1",
        "produk_urg_aktif": "INTEGER NOT NULL DEFAULT 1",
        "policy_version": "INTEGER NOT NULL DEFAULT 1",
    }
    existing = table_columns(db, "clients", db_type)
    for field, definition in fields.items():
        if field not in existing:
            db.execute(f"ALTER TABLE clients ADD COLUMN {field} {definition}")
    if "reg_withdrawal_max_override" not in table_columns(db, "pegawai", db_type):
        db.execute("ALTER TABLE pegawai ADD COLUMN reg_withdrawal_max_override INTEGER DEFAULT NULL")
    identity = "INTEGER PRIMARY KEY AUTOINCREMENT" if db_type == "sqlite" else "INT AUTO_INCREMENT PRIMARY KEY"
    suffix = "" if db_type == "sqlite" else " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
    db.execute(f"""CREATE TABLE IF NOT EXISTS company_policy_events (
        id {identity}, client_id INTEGER, actor_id INTEGER, kind VARCHAR(40) NOT NULL,
        before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at DATETIME NOT NULL
    ){suffix}""")
    verb = "INSERT OR IGNORE" if db_type == "sqlite" else "INSERT IGNORE"
    for key in ("company_policy_mutex", "system_settings_version"):
        db.execute(f"{verb} INTO app_settings (`key`,value) VALUES (?,?)", (key, "1"))
    db.commit()
    if db_type == "sqlite":
        db.execute("BEGIN IMMEDIATE")
    lock_policy(db)
    marker = "company_policy_v1"
    try:
        if db.execute("SELECT value FROM app_settings WHERE `key`=?", (marker,)).fetchone():
            db.commit()
            return
        admin_columns = table_columns(db, "admins", db_type)
        for client in db.execute("SELECT * FROM clients ORDER BY id").fetchall():
            admins = []
            if {"company", "cutoff_mingguan_aktif", "cutoff_hari", "produk_reg_aktif", "produk_urg_aktif"} <= admin_columns:
                admins = [dict(row) for row in db.execute(
                    "SELECT * FROM admins WHERE LOWER(TRIM(company))=? ORDER BY id DESC", (client["name_key"],)
                ).fetchall()]
            cutoff = next((row for row in admins if row["cutoff_mingguan_aktif"] == 1), None)
            products = admins[0] if admins else None
            day = int(cutoff["cutoff_hari"] if cutoff and cutoff["cutoff_hari"] is not None else 2)
            values = (int(bool(cutoff)), day if day in range(7) else 2,
                      int(products["produk_reg_aktif"] if products and products["produk_reg_aktif"] is not None else 1),
                      int(products["produk_urg_aktif"] if products and products["produk_urg_aktif"] is not None else 1))
            db.execute("""UPDATE clients SET cutoff_mingguan_aktif=?,cutoff_hari=?,
                          produk_reg_aktif=?,produk_urg_aktif=? WHERE id=?""", (*values, client["id"]))
            record_policy_event(db, client["id"], "migration", {"reg_withdrawal_max": client["reg_withdrawal_max"], "legacy": [
                {field: row[field] for field in ("id", *fields.keys()) if field in row} for row in admins
            ]}, {**dict(zip(tuple(fields)[:4], values)), "reg_withdrawal_max": client["reg_withdrawal_max"]})
        db.execute(f"{verb} INTO app_settings (`key`,value) VALUES (?,?)", (marker, "1"))
        db.commit()
    except Exception:
        db.rollback()
        raise


def lock_policy(db):
    if not isinstance(db, sqlite3.Connection):
        row = db.execute("SELECT value FROM app_settings WHERE `key`='company_policy_mutex' FOR UPDATE").fetchone()
        if not row:
            raise RuntimeError("Company policy schema belum siap.")


def serialized_policy_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if request.method != "POST" or not (session.get("is_admin") or session.get("is_superadmin")):
            return function(*args, **kwargs)
        db = get_db()
        db.commit()
        try:
            if isinstance(db, sqlite3.Connection):
                db.execute("BEGIN IMMEDIATE")
            lock_policy(db)
            return function(*args, **kwargs)
        finally:
            db.rollback()
    return wrapped


def record_policy_event(db, client_id, kind, before, after):
    from flask import has_request_context
    epoch = db.execute("SELECT value FROM app_settings WHERE `key`='company_policy_mutex'").fetchone()
    db.execute("UPDATE app_settings SET value=? WHERE `key`='company_policy_mutex'", (str(int(epoch["value"]) + 1),))
    actor = session.get("admin_id") if has_request_context() else None
    db.execute("""INSERT INTO company_policy_events
        (client_id,actor_id,kind,before_json,after_json,created_at) VALUES (?,?,?,?,?,?)""",
        (client_id, actor, kind, json.dumps(before, default=str), json.dumps(after, default=str),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def check_policy_version(form, existing):
    if str(form.get("policy_version", "")) != str(existing["policy_version"]):
        raise StalePolicy("Konfigurasi sudah berubah atau form lama. Muat ulang halaman sebelum menyimpan.")


def effective_reg_limit(db, company, employee=None):
    client = client_by_name(db, company)
    company_limit = client["reg_withdrawal_max"] if client else 1000000
    override = employee["reg_withdrawal_max_override"] if employee and "reg_withdrawal_max_override" in employee.keys() else None
    return {"company": company_limit, "override": override,
            "effective": override if override is not None else company_limit,
            "source": "Khusus karyawan" if override is not None else "Mengikuti perusahaan"}


def user_reg_limit(db, user_id):
    employee = db.execute("""SELECT p.perusahaan_induk,p.reg_withdrawal_max_override FROM pegawai p
        JOIN users u ON LOWER(p.email)=LOWER(u.email) WHERE u.id=?""", (user_id,)).fetchone()
    return effective_reg_limit(db, employee["perusahaan_induk"] if employee else "", employee)


def read_employee_override(form, existing=None, is_superadmin=False):
    mode = form.get("reg_limit_mode")
    raw = form.get("reg_withdrawal_max_override")
    current = existing["reg_withdrawal_max_override"] if existing and "reg_withdrawal_max_override" in existing.keys() else None
    if not is_superadmin and (mode is not None or raw is not None):
        raise ValueError("Hanya Superadmin boleh mengubah batas REG karyawan.")
    if mode is None and raw is None:
        return current
    if mode == "inherit":
        return None
    if mode != "override":
        raise ValueError("Pilih Ikuti perusahaan atau Batas khusus karyawan.")
    return parse_reg_withdrawal_max(str(raw) if raw is not None else "")


def override_employees(db, company):
    return [dict(row) for row in db.execute("""SELECT id,id_pegawai,nama,perusahaan,status_aktif,
        reg_withdrawal_max_override FROM pegawai WHERE LOWER(TRIM(perusahaan_induk))=?
        AND reg_withdrawal_max_override IS NOT NULL ORDER BY nama,id""", (name_key(company),)).fetchall()]


def policy_period(today, weekly, day, cycle):
    if weekly:
        start = today - timedelta(days=(today.weekday() - ((day + 1) % 7)) % 7)
        return start, start + timedelta(days=7)
    start_day = {"A": 1, "B": 16, "C": 21, "D": 26}.get(str(cycle or "A").strip().upper(), 1)
    year, month = today.year, today.month
    if today.day < start_day:
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    start = date(year, month, start_day)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return start, date(next_year, next_month, start_day)


def guard_cutoff_change(db, company, existing, values, today=None):
    old_weekly = bool(existing["cutoff_mingguan_aktif"])
    new_weekly = bool(values["cutoff_mingguan_aktif"])
    old_day, new_day = int(existing["cutoff_hari"]), int(values["cutoff_hari"])
    if old_weekly == new_weekly and (not new_weekly or old_day == new_day):
        return
    from app.services.security_service import business_today
    today = today or business_today()
    scope = "LOWER(TRIM(p.perusahaan_induk))=?"
    pending = db.execute(f"""SELECT t.id FROM transactions t
        JOIN users u ON u.id=t.user_id JOIN pegawai p ON LOWER(p.email)=LOWER(u.email)
        WHERE {scope} AND t.status='on-proses' LIMIT 1""", (name_key(company),)).fetchone()
    if pending:
        raise ValueError("Cutoff belum dapat diubah: masih ada pengajuan on-proses perusahaan ini.")
    cycle_expression = "CASE WHEN UPPER(TRIM(COALESCE(p.siklus_gaji,'A'))) IN ('A','B','C','D') THEN UPPER(TRIM(p.siklus_gaji)) ELSE 'A' END"
    cycles = db.execute(f"SELECT DISTINCT {cycle_expression} AS cycle FROM pegawai p WHERE {scope} ORDER BY cycle",
                        (name_key(company),)).fetchall()
    windows = {}
    for weekly, day in ((old_weekly, old_day), (new_weekly, new_day)):
        for employee in cycles:
            start, end = policy_period(today, weekly, day, employee["cycle"])
            windows[(start, end, None if weekly else employee["cycle"])] = True
    for start, end, cycle in windows:
        params = [name_key(company), start.isoformat(), end.isoformat()]
        cycle_clause = ""
        if cycle:
            cycle_clause = f" AND {cycle_expression}=?"
            params.append(cycle)
        used = db.execute(f"""SELECT t.id FROM transactions t
            JOIN users u ON u.id=t.user_id JOIN pegawai p ON LOWER(p.email)=LOWER(u.email)
            WHERE {scope} AND t.status='sukses' AND t.tanggal>=? AND t.tanggal<? {cycle_clause} LIMIT 1""",
            params).fetchone()
        if used:
            raise ValueError(f"Cutoff belum dapat diubah: ada transaksi selesai pada rentang terdampak {start.isoformat()} sampai {(end-timedelta(days=1)).isoformat()} (WIB).")
