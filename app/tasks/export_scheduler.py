import csv
import io
import os
import threading
import time
import sqlite3
from datetime import date, datetime

from app.db import get_db
from app.services.security_service import business_today
from app.utils.spreadsheets import SafeCSVWriter
from app.services.email_service import send_email


_scheduler_started = False

EXPORT_INTERVAL_SECONDS = 2 * 24 * 60 * 60
LAST_RUN_KEY = "export_csv_last_run"
LEGACY_REKENING_LABELS = {
    "Rekening Bank Utama": "No_Rek Bank",
    "Rekening Bank Lain": "No_Rek Bank 2",
    "Rekening E-Wallet": "Rek E-Wallet",
    "No Rekening": "No_Rek Bank",
}


def _short_rekening_label(value):
    label = (value or "").strip()
    return LEGACY_REKENING_LABELS.get(label, label or "No_Rek Bank")


def _get_setting(db, key: str):
    row = db.execute(
        "SELECT value FROM app_settings WHERE `key`=? LIMIT 1",
        (key,),
    ).fetchone()
    return row["value"] if row else None


def _set_setting(db, key: str, value: str):
    cur = db.execute(
        "UPDATE app_settings SET value=? WHERE `key`=?",
        (value, key),
    )
    if cur.rowcount == 0:
        db.execute(
            "INSERT INTO app_settings (`key`, value) VALUES (?, ?)",
            (key, value),
        )
    db.commit()


def _current_period_range():
    today = business_today()
    start = date(today.year, today.month, 1)
    if today.month == 12:
        end = date(today.year + 1, 1, 1)
    else:
        end = date(today.year, today.month + 1, 1)
    return start, end


def _build_csv_bytes(rows):
    buf = io.StringIO()
    writer = SafeCSVWriter(buf)
    writer.writerow(
        [
            "ID",
            "Tanggal",
            "Periode",
            "ID Pegawai",
            "Pegawai",
            "Email",
            "Perusahaan",
            "Jabatan",
            "Tipe Rekening",
            "No_Rek Bank",
            "Produk",
            "Nominal",
            "Admin",
            "Status",
            "Keterangan",
            "Dibuat",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r["id"],
                r["tanggal"],
                r["periode"],
                r["id_pegawai"],
                r["pegawai"],
                r["email_user"],
                r["perusahaan"],
                r["jabatan"],
                _short_rekening_label(r["rekening_tujuan_label"]),
                r["no_rekening"],
                r["product"],
                r["nominal"],
                r["admin_fee"],
                r["status"],
                r["keterangan"] or "",
                r["created_at"],
            ]
        )
    return buf.getvalue().encode("utf-8-sig")


def _export_current_period_csv():
    db = get_db()
    start, end = _current_period_range()
    rows = db.execute(
        """
        SELECT t.id, t.tanggal, t.periode,
               u.name AS pegawai,
               u.email AS email_user,
               COALESCE(p.id_pegawai,'') AS id_pegawai,
               COALESCE(p.perusahaan,'') AS perusahaan,
               COALESCE(p.jabatan,'') AS jabatan,
               COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
               COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label,
               t.product, t.nominal, t.admin_fee, t.status, t.keterangan, t.created_at
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
        WHERE t.tanggal >= ? AND t.tanggal < ?
        ORDER BY t.tanggal DESC, t.id DESC
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    data = _build_csv_bytes(rows)

    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    out_dir = os.path.join(root, "data", "csv")
    os.makedirs(out_dir, exist_ok=True)
    period_label = start.strftime("%Y-%m")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"export_periode_{period_label}_{stamp}.csv"
    full_path = os.path.join(out_dir, filename)
    with open(full_path, "wb") as f:
        f.write(data)
    return filename, full_path, data, start, end, len(rows)


def _should_run(db):
    last = _get_setting(db, LAST_RUN_KEY)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    now = datetime.now(last_dt.tzinfo) if last_dt.tzinfo else datetime.now()
    return (now - last_dt).total_seconds() >= EXPORT_INTERVAL_SECONDS


def _run_export_job(app):
    with app.app_context():
        db = get_db()
        lock_name = "gajiku_export_" + app.config.get("MYSQL_DB", "default")[:40]
        if isinstance(db, sqlite3.Connection):
            db.execute("BEGIN IMMEDIATE")
        else:
            locked = db.execute("SELECT GET_LOCK(?, 0) AS acquired", (lock_name,)).fetchone()
            if not locked or locked["acquired"] != 1:
                return
        try:
            _run_locked_export(app, db)
        finally:
            db.rollback()
            if not isinstance(db, sqlite3.Connection):
                db.execute("SELECT RELEASE_LOCK(?)", (lock_name,)).fetchone()

def _run_locked_export(app, db):
    if not _should_run(db):
        return
    filename, full_path, data, start, end, total_rows = _export_current_period_csv()
    period_label = start.strftime("%Y-%m")
    subject = f"[Auto Export] CSV Periode {period_label}"
    body = (
        f"Export otomatis periode berjalan.\n"
        f"Periode: {start.isoformat()} s.d {end.isoformat()} (eksklusif)\n"
        f"Total baris: {total_rows}\n"
        f"File: {full_path}\n"
    )
    sent = send_email(
        subject,
        body,
        attachments=[
            {
                "filename": filename,
                "content": data,
                "mimetype": "text/csv",
            }
        ],
    )
    if sent:
        _set_setting(db, LAST_RUN_KEY, datetime.now().isoformat(timespec="seconds"))
    else:
        app.logger.warning("[EXPORT] email gagal, last_run tidak diperbarui.")


def start_export_scheduler(app, interval_seconds=3600):
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    def _loop():
        while True:
            try:
                _run_export_job(app)
            except Exception as exc:
                app.logger.warning(f"[EXPORT] scheduler gagal: {exc}")
            time.sleep(interval_seconds)

    thread = threading.Thread(target=_loop, daemon=True)
    thread.start()
