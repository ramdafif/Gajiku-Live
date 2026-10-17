import json, math, re, calendar, sqlite3, time, os
from datetime import datetime, date, timedelta
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Font, PatternFill, Alignment
from flask import Blueprint, current_app, render_template, request, redirect, url_for, session, flash, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import csv, io
from app.utils.spreadsheets import SafeCSVWriter
from PIL import Image, ImageOps, UnidentifiedImageError
import secrets
import hashlib
import zipfile

from app import db
from app.db import get_db
from app.repositories.user_repo import get_user_by_id, get_user_by_email
from app.utils.cache import get_cache, set_cache, clear_cache_prefix
from app.tasks.queue import get_stats as get_queue_stats
from app.services.email_service import enqueue_email
from app.services.client_service import client_by_name, employee_assignment, list_clients, list_projects, name_key
from app.services.security_service import (business_today, credential_version, rate_limit, require_destructive_reset,
    safe_return_url, serialized_admin_write, serialized_employee_write, serialized_transaction, serialized_withdrawal, token_serializer, withdrawal_key, withdrawal_token)
from itsdangerous import BadSignature, SignatureExpired
import hmac

bp = Blueprint("web", __name__)

# ===== Health =====
@bp.route("/health")
def health():
    ret = require_admin()
    if ret:
        return ret
    return {
        "version": current_app.config["APP_VERSION"],
        "admin_fee_flat_options": list(ADMIN_FEE_FLAT_OPTIONS),
        "db_path": current_app.config["DB_PATH"],
        "session_user": session.get("user_id"),
        "session_name": session.get("user_name"),
        "admin_id": session.get("admin_id"),
    }

@bp.get("/metrics")
def metrics():
    ret = require_admin()
    if ret:
        return ret
    return {
        "timestamp": int(time.time()),
        "queue": get_queue_stats(),
    }

# ===== Sanitize angka =====
def parse_int(raw, default=0):
    if raw is None:
        return default
    s = re.sub(r"[^\d\-]", "", str(raw))
    if s in ("", "-"):
        return default
    try:
        return int(s)
    except Exception:
        return default

EMPLOYEE_ID_MAX_LEN = 16
EMPLOYEE_ID_RE = re.compile(rf"^[A-Za-z0-9]{{1,{EMPLOYEE_ID_MAX_LEN}}}$")
EWALLET_RE = re.compile(r"^[A-Za-z0-9 ]*$")
REKENING_LABELS = {
    "bank_utama": "No_Rek Bank",
    "bank_lain": "No_Rek Bank 2",
    "ewallet": "Rek E-Wallet",
}
LEGACY_REKENING_LABELS = {
    "Rekening Bank Utama": REKENING_LABELS["bank_utama"],
    "Rekening Bank Lain": REKENING_LABELS["bank_lain"],
    "Rekening E-Wallet": REKENING_LABELS["ewallet"],
    "No Rekening": REKENING_LABELS["bank_utama"],
}

def normalize_employee_id(raw):
    return (raw or "").strip().upper()

def employee_id_is_valid(value):
    return bool(EMPLOYEE_ID_RE.fullmatch(value or ""))

def ewallet_is_valid(value):
    return bool(EWALLET_RE.fullmatch(value or ""))

def short_rekening_label(value):
    label = (value or "").strip()
    return LEGACY_REKENING_LABELS.get(label, label or REKENING_LABELS["bank_utama"])

def _row_value(row, key, default=""):
    try:
        return row[key] if row and key in row.keys() else default
    except Exception:
        return default

def build_rekening_options(pegawai_row):
    items = [
        ("bank_utama", REKENING_LABELS["bank_utama"], _row_value(pegawai_row, "no_rekening")),
        ("bank_lain", REKENING_LABELS["bank_lain"], _row_value(pegawai_row, "no_rekening_lain")),
        ("ewallet", REKENING_LABELS["ewallet"], _row_value(pegawai_row, "rekening_ewallet")),
    ]
    options = []
    for key, label, value in items:
        value = (value or "").strip()
        if value:
            options.append({"key": key, "label": label, "value": value})
    return options

# ===== Filter Rupiah =====
@bp.app_template_filter("rupiah")
def rupiah_format(value, with_decimal=False):
    try:
        if isinstance(value, str):
            value = re.sub(r"[^\d\-\.]", "", value)
        val = float(value)
        s = f"{val:,.2f}" if with_decimal else f"{int(round(val)):,}"
        s = s.replace(",", "_").replace(".", ",").replace("_", ".")
        return f"Rp. {s}"
    except Exception:
        return "Rp. 0"

# ===== Format Tanggal indonesia =====
@bp.app_template_filter("indo_date")
def indo_date(value):
    """Format tanggal Indonesia: dd-mm-YYYY (menerima date/datetime/string ISO)."""
    try:
        if hasattr(value, "strftime"):
            return value.strftime("%d-%m-%Y")
        return datetime.fromisoformat(str(value)).strftime("%d-%m-%Y")
    except Exception:
        return str(value)

@bp.app_template_filter("format_period_label")
def format_period_label_filter(value):
    return format_period_label(str(value))

@bp.app_template_filter("rekening_label")
def rekening_label_filter(value):
    return short_rekening_label(value)

# ===== Helpers =====
def month_key(d: date) -> str: return d.strftime("%Y-%m")
def ymd(d: date) -> str: return d.strftime("%Y-%m-%d")

SIKLUS_START_DAY = {
    "A": 1,
    "B": 16,
    "C": 21,
    "D": 26,
}
VALID_SIKLUS = tuple(SIKLUS_START_DAY.keys())
# NOTE: sejak fee admin diinput manual, tuple ini HANYA informasi default/saran (dipakai /health),
# TIDAK lagi dipakai untuk validasi. Validasi memakai ADMIN_FEE_FLAT_MIN/MAX di bawah.
ADMIN_FEE_FLAT_OPTIONS = (15000, 17000)
ADMIN_FEE_FLAT_MIN = 0
ADMIN_FEE_FLAT_MAX = 1_000_000   # batas pengaman salah ketik (sama dgn batas nominal REG)
REG_WITHDRAWAL_MAX = 1_000_000

def normalize_siklus(value: str, default: str = "A") -> str:
    siklus = (value or default).strip().upper()
    return siklus if siklus in VALID_SIKLUS else default

def parse_admin_fee_flat(value):
    """Parse fee admin hasil input manual. Return int, atau None bila kosong/tidak valid.
    Menerima format '15000' maupun '15.000'."""
    if value is None or str(value).strip() == "":
        return None
    fee = parse_int(value, None)
    if fee is None or fee < ADMIN_FEE_FLAT_MIN or fee > ADMIN_FEE_FLAT_MAX:
        return None
    return fee

def normalize_admin_fee_flat(value, default: int = 15000) -> int:
    # Fee kini bebas (input manual), bukan lagi hanya pilihan 15.000 / 17.000.
    fee = parse_admin_fee_flat(value)
    return fee if fee is not None else default

def compute_limits(gaji: int, at_date: date, user_id: int):
    """
    Hitung batas talangan untuk user di tanggal tertentu.
    - Siklus A : mulai tanggal 1
    - Siklus B : mulai tanggal 16
    - Siklus C : gajian 20-21, periode mulai tanggal 21
    - Siklus D : gajian 25-26, periode mulai tanggal 26
    - REG: sisa harian dibatasi plafon dan dikurangi seluruh pinjaman aktif pada periode tersebut
    """
    db = get_db()

    # Ambil email user + siklus dari master pegawai (join lewat email)
    row = db.execute("""
        SELECT u.email, p.siklus_gaji, p.perusahaan_induk
        FROM users u
        LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
        WHERE u.id = ?
    """, (user_id,)).fetchone()
    user_email = (row["email"] if row and row["email"] else "").strip()
    siklus = normalize_siklus(row["siklus_gaji"] if row and row["siklus_gaji"] else "A")
    weekly = get_weekly_cutoff_config(row["perusahaan_induk"] if row else "")

    # Plafon & limit harian
    plafon_bulanan = math.floor(0.5 * (gaji or 0))
    # Cutoff mingguan memakai 25% dari plafon bulanan sebagai plafon periode.
    # Pegawai non-cutoff tetap memakai plafon bulanan dan pembagi 30 hari.
    plafon = math.floor(plafon_bulanan * 0.25) if weekly else plafon_bulanan
    limit_period_days = 7 if weekly else 30
    limit_harian = math.floor(plafon / limit_period_days) if gaji else 0

    # Hari ke (menggunakan helper kamu) & kunci periode aktif
    if weekly:
        hari_ke = weekly_day_in_period(at_date, weekly["cutoff_hari"])
        mk = weekly_period_key(at_date, weekly["cutoff_hari"])
    else:
        hari_ke = day_in_cycle(at_date, siklus)
        mk = period_key_by_cycle(at_date, siklus)

    if user_email:
        row = db.execute("""
            SELECT COALESCE(SUM(t.nominal), 0) AS total
            FROM transactions t
            JOIN users u ON u.id = t.user_id
            WHERE LOWER(u.email) = LOWER(?)
              AND t.periode = ?
              AND t.status IN ('sukses','on-proses')
        """, (user_email, mk)).fetchone()
        total_sukses = int(row["total"] if row else 0)
    else:
        row = db.execute("""
            SELECT COALESCE(SUM(nominal), 0) AS total
            FROM transactions
            WHERE user_id = ?
              AND periode = ?
              AND status IN ('sukses','on-proses')
        """, (user_id, mk)).fetchone()
        total_sukses = int(row["total"] if row else 0)

    # Sisa saldo REG (akumulatif harian)
    saldo = max(min(limit_harian * hari_ke, plafon) - total_sukses, 0)
    sisa_plafon = max(plafon - total_sukses, 0)

    return {
        "plafon": plafon,
        "plafon_bulanan": plafon_bulanan,
        "limit_harian": limit_harian,
        "limit_period_days": limit_period_days,
        "hari_ke": hari_ke,
        "total_sukses": total_sukses,
        "saldo": saldo,
        "periode_key": mk,
        "siklus": siklus,
        "sisa_plafon": sisa_plafon,
        "cutoff_mingguan": bool(weekly),
        "cutoff_hari": weekly["cutoff_hari"] if weekly else None,
    }


def get_weekly_cutoff_config(company):
    """Ambil konfigurasi cutoff aktif secara case-insensitive per company."""
    company = (company or "").strip()
    if not company:
        return None
    row = get_db().execute("""
        SELECT cutoff_mingguan_aktif, cutoff_hari
        FROM admins
        WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))
          AND COALESCE(cutoff_mingguan_aktif, 0) = 1
        ORDER BY id DESC LIMIT 1
    """, (company,)).fetchone()
    if not row:
        return None
    day = int(row["cutoff_hari"] if row["cutoff_hari"] is not None else 2)
    return {"cutoff_hari": day if 0 <= day <= 6 else 2}


def get_weekly_cutoff_counts(db, where_clause="1=1", where_params=None):
    """Hitung pegawai aktif per hari cutoff untuk dashboard KPI."""
    params = list(where_params or [])
    rows = db.execute(f"""
        SELECT a.cutoff_hari, COUNT(*) AS total
        FROM pegawai p
        JOIN admins a ON LOWER(TRIM(a.company)) = LOWER(TRIM(p.perusahaan_induk))
        WHERE p.status_aktif=1
          AND COALESCE(a.cutoff_mingguan_aktif, 0)=1
          AND {where_clause}
        GROUP BY a.cutoff_hari
    """, params).fetchall()
    return {int(r["cutoff_hari"]): int(r["total"] or 0) for r in rows}


def weekly_period_start(d: date, cutoff_day: int) -> date:
    """Periode Kamis-Rabu untuk cutoff Rabu; start adalah hari setelah cutoff."""
    days_since_start = (d.weekday() - ((cutoff_day + 1) % 7)) % 7
    return d - timedelta(days=days_since_start)


def weekly_day_in_period(d: date, cutoff_day: int) -> int:
    return (d - weekly_period_start(d, cutoff_day)).days + 1


def weekly_period_key(d: date, cutoff_day: int) -> str:
    return "W:" + weekly_period_start(d, cutoff_day).isoformat()

def day_in_cycle(d: date, siklus: str) -> int:
    """Hitung hari ke- dalam periode berjalan sesuai siklus gaji."""
    siklus = normalize_siklus(siklus)
    start_day = SIKLUS_START_DAY[siklus]
    if start_day == 1:
        return d.day
    if d.day >= start_day:
        return d.day - start_day + 1

    prev_month = d.month - 1
    prev_year = d.year
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    days_in_prev_month = calendar.monthrange(prev_year, prev_month)[1]
    return (days_in_prev_month - start_day + 1) + d.day

def period_key_by_cycle(d: date, siklus: str) -> str:
    """Kunci periode (YYYY-MM) mengikuti bulan 'awal' periode."""
    siklus = normalize_siklus(siklus)
    start_day = SIKLUS_START_DAY[siklus]
    if start_day == 1:
        return d.strftime("%Y-%m")
    if d.day >= start_day:
        return d.strftime("%Y-%m")
    y = d.year; m = d.month - 1
    if m == 0: m = 12; y -= 1
    return f"{y:04d}-{m:02d}"

def add_months(d: date, delta: int) -> date:
    y = d.year + (d.month - 1 + delta) // 12
    m = (d.month - 1 + delta) % 12 + 1
    return date(y, m, 1)

def format_short_date(d: date) -> str:
    months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
    return f"{d.day} {months[d.month - 1]} {d.year}"

def format_period_label(periode: str) -> str:
    """Format label periode 'YYYY-MM' menjadi 'Mon YYYY' (Indonesia)."""
    if str(periode).startswith("W:"):
        try:
            start = date.fromisoformat(str(periode)[2:])
            return f"{format_short_date(start)} – {format_short_date(start + timedelta(days=6))}"
        except Exception:
            return periode
    try:
        y, m = [int(x) for x in periode.split("-", 1)]
        months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
        return f"{months[m - 1]} {y}"
    except Exception:
        return periode

def require_login():
    if "user_id" not in session:
        return redirect(url_for("web.login"))

def require_admin():
    # 💡 MODIFIKASI DI SINI: Izinkan masuk jika dia Admin Biasa ATAU Superadmin
    if session.get("is_admin") or session.get("is_superadmin"):
        return None  # Aman, tidak mengembalikan redirect, route jalan terus!
    flash("Anda harus login sebagai admin.", "error")
    return redirect(url_for("web.login"))
    
def require_superadmin():
    if not session.get("is_superadmin"):
        flash("Hanya Superadmin yang memiliki akses ke halaman ini.", "error")
        return redirect(url_for("web.login"))

# =========================================================================================
# ===== 2-LAYER APPROVAL TARIK GAJI: Admin PT (layer 1)  ->  Superadmin (layer 2 / final) ====
# =========================================================================================
# Alur status transaksi (kolom `status` TIDAK berubah, tetap 'on-proses' sampai final):
#   [pegawai ajukan] on-proses
#        -> Admin PT approve  : admin_approved_at/by terisi, status MASIH 'on-proses'
#        -> Superadmin approve: status 'sukses' (Superadmin yang melakukan transfer)
#   Tolak: Admin PT hanya bisa menolak SEBELUM ia approve; Superadmin bisa menolak kapan saja.
#
# Aturan UMUM (semua PT):
#   - Superadmin baru bisa approve final setelah Admin PT approve.
#   - Pengecualian: PT yang memang belum punya akun admin -> tidak ada layer 1, Superadmin boleh langsung
#     (supaya antrian tidak macet).
#   - Admin PT hanya bisa memproses pegawai dalam cakupan dashboard-nya (induk -> anak PT, PT biasa -> PT sendiri).
# Aturan KHUSUS PT WINDU (dikenali dari kata "windu" pada nama perusahaan pegawai / admin):
#   - Admin PT Windu approve karyawan PT Windu SENDIRI (admin holding / admin global lama TIDAK boleh).
#   - Setelah itu WAJIB approve Superadmin (yang melakukan transfer) -> tidak ada jalur langsung.
LEGACY_GLOBAL_ADMIN_EMAIL = "admin@example.com"   # admin lama yang dashboard-nya melihat semua PT
WINDU_COMPANY_NAME = "Windu Karya"   # exact match (case-insensitive, trim), bukan sekadar mengandung kata "windu"

def _norm(value):
    return (value or "").strip().lower()

def is_windu_company(*names):
    """True bila salah satu nama perusahaan SAMA PERSIS dengan 'Windu Karya' (bukan sekadar mengandung kata
    'windu' - PT lain yang kebetulan memuat kata itu tidak ikut tertangkap)."""
    target = _norm(WINDU_COMPANY_NAME)
    return any(_norm(n) == target for n in names)

def _is_parent_company(db, company):
    company = (company or "").strip()
    if not company:
        return False
    if client_by_name(db, company) or is_windu_company(company):
        return True
    return bool(db.execute(
        "SELECT 1 FROM pegawai WHERE LOWER(TRIM(perusahaan_induk))=LOWER(TRIM(?)) LIMIT 1",
        (company,),
    ).fetchone())

def load_admin_companies(db):
    """Kumpulan nama perusahaan (lowercase) yang punya admin dengan HAK APPROVAL menyala (hak_approval=1).
    Ini yang menentukan apakah sebuah PT masih perlu layer 1 (admin approve dulu) atau langsung
    bypass ke Superadmin - BUKAN sekadar "punya akun admin" lagi seperti sebelumnya. PT yang semua
    adminnya hak_approval OFF (atau memang tidak punya admin sama sekali) dianggap sama seperti
    "PT tanpa admin": Superadmin boleh approve+transfer langsung (kecuali PT Windu Karya)."""
    rows = db.execute("""
        SELECT DISTINCT LOWER(TRIM(company)) AS company
        FROM admins
        WHERE company IS NOT NULL AND TRIM(company) <> ''
          AND LOWER(COALESCE(role, 'admin')) <> 'superadmin'
          AND hak_approval = 1 AND status_aktif = 1
    """).fetchall()
    return {r["company"] for r in rows if r["company"]}

def company_has_admin(perusahaan, perusahaan_induk, admin_companies):
    keys = {_norm(perusahaan), _norm(perusahaan_induk)} - {""}
    return bool(keys & set(admin_companies))

def get_tx_approval_row(db, txid):
    """Ambil transaksi + perusahaan pegawainya + status approval layer 1."""
    return db.execute("""
        SELECT t.id, t.status, t.admin_approved_at, t.admin_approved_by,
               COALESCE(p.perusahaan, '') AS perusahaan,
               COALESCE(p.perusahaan_induk, '') AS perusahaan_induk
        FROM transactions t
        LEFT JOIN users u ON u.id = t.user_id
        LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
        WHERE t.id = ?
    """, (txid,)).fetchone()

def admin_can_handle(db, perusahaan, perusahaan_induk, self_is_parent=None):
    """LAYER 1: apakah Admin PT yang sedang login berwenang atas transaksi pegawai ini?
    Gerbang pertama: admin ini harus punya hak_approval menyala - kalau tidak, TIDAK PERNAH boleh
    approve/tolak apa pun, terlepas dari PT mana pun (berlaku juga untuk admin global lama)."""
    if not session.get("hak_approval"):
        return False

    company = _norm(session.get("company"))
    peg_c, peg_i = _norm(perusahaan), _norm(perusahaan_induk)

    # Khusus PT Windu: hanya Admin PT Windu sendiri (bukan admin holding / admin global lama)
    if is_windu_company(peg_c, peg_i):
        return is_windu_company(company)

    if not company:
        return False
    if self_is_parent is None:
        self_is_parent = _is_parent_company(db, company)
    # sama persis dengan filter cakupan di admin_dashboard / admin_pegawai
    return (peg_i == company) if self_is_parent else (peg_c == company)

def superadmin_final_gate(tx, admin_companies):
    """LAYER 2: apakah Superadmin boleh approve final transaksi ini sekarang? -> (boleh, alasan)."""
    if tx["admin_approved_at"]:
        return True, ""
    pc, pi = tx["perusahaan"], tx["perusahaan_induk"]
    if is_windu_company(pc, pi):
        return False, "Transaksi PT Windu wajib di-approve Admin PT Windu terlebih dahulu."
    if company_has_admin(pc, pi, admin_companies):
        return False, "Menunggu approve Admin PT terlebih dahulu (2 layer approval)."
    return True, ""            # PT tanpa akun admin: tidak ada layer 1




def decorate_pending_tx(db, rows):
    """Ubah baris antrian on-proses menjadi dict + info tahap approval untuk template.
    Status approval disegarkan dari DB agar tidak basi walau daftar berasal dari cache 10 detik.
    Field tambahan: stage, is_windu, admin_can_act, can_final, final_block_reason."""
    items = [dict(r) for r in rows]
    if not items:
        return []
    ids = [i["id"] for i in items]
    marks = ",".join("?" * len(ids))
    fresh = {
        r["id"]: r for r in db.execute(
            f"SELECT id, status, admin_approved_at, admin_approved_by FROM transactions WHERE id IN ({marks})",
            ids,
        ).fetchall()
    }
    admin_companies = load_admin_companies(db)
    is_super = bool(session.get("is_superadmin"))
    self_company = (session.get("company") or "").strip()
    self_is_parent = _is_parent_company(db, self_company) if (self_company and not is_super) else False

    out = []
    for it in items:
        f = fresh.get(it["id"])
        if not f or _norm(f["status"]) != "on-proses":
            continue                      # sudah diproses orang lain -> jangan tampil lagi
        it["admin_approved_at"] = f["admin_approved_at"]
        it["admin_approved_by"] = f["admin_approved_by"]
        pc = it.get("perusahaan") if it.get("perusahaan") not in (None, "-") else ""
        pi = it.get("perusahaan_induk") or ""
        approved = bool(it["admin_approved_at"])
        it["stage"] = "menunggu_superadmin" if approved else "menunggu_admin"
        it["is_windu"] = is_windu_company(pc, pi)
        it["admin_can_act"] = (not approved) and (not is_super) and admin_can_handle(db, pc, pi, self_is_parent)
        ok, reason = superadmin_final_gate(
            {"admin_approved_at": it["admin_approved_at"], "perusahaan": pc, "perusahaan_induk": pi},
            admin_companies,
        )
        it["can_final"] = ok
        it["final_block_reason"] = reason
        it["has_admin_layer"] = approved or it["is_windu"] or company_has_admin(pc, pi, admin_companies)
        out.append(it)
    return out

def current_sim_date():
    if not current_app.config.get("ALLOW_DATE_OVERRIDE"):
        return business_today()
    try:
        raw = request.args.get("tanggal") or session.get("sim_date") or business_today().isoformat()
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return business_today()

def get_formal_status():
    """
    Baca status aktif akun formal dari DB berdasar email formal di sesi.
    Return 1 jika aktif, 0 jika non-aktif, None kalau tidak ketemu.
    """
    email = session.get("formal_email")
    if not email:
        return None
    db = get_db()
    row = db.execute("""
        SELECT CASE WHEN ua.status_aktif=1 AND p.status_aktif=1 THEN 1 ELSE 0 END AS aktif
        FROM user_accounts ua
        JOIN pegawai p ON p.id = ua.pegawai_id
        WHERE ua.email = ?
        LIMIT 1
    """, (email,)).fetchone()
    return int(row["aktif"]) if row else None

def password_ok(pw: str) -> bool:
    return isinstance(pw, str) and 6 <= len(pw) <= 256

def valid_email(value):
    return isinstance(value, str) and len(value) <= 254 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))

def admin_coverage_error(db, admin_id, company=None, active=0, approval=0):
    existing = db.execute("SELECT * FROM admins WHERE id=?", (admin_id,)).fetchone()
    if not existing or not int(existing["status_aktif"] or 0):
        return None
    role = str(existing["role"] or "").strip().lower()
    if role == "superadmin" and not active:
        others = db.execute("SELECT COUNT(*) AS total FROM admins WHERE id<>? AND LOWER(role)='superadmin' AND status_aktif=1", (admin_id,)).fetchone()["total"]
        env_super = current_app.config.get("SUPERADMIN_USERNAME") and current_app.config.get("SUPERADMIN_PASSWORD")
        if not others and not env_super:
            return "Superadmin aktif terakhir tidak dapat dihapus/dinonaktifkan. Siapkan akun pengganti terlebih dahulu."
    if is_windu_company(existing["company"]) and existing["hak_approval"] and not (active and approval and is_windu_company(company)):
        others = db.execute("""SELECT COUNT(*) AS total FROM admins WHERE id<>? AND status_aktif=1 AND hak_approval=1
            AND LOWER(COALESCE(role,'admin'))<>'superadmin' AND LOWER(TRIM(company))=?""", (admin_id, name_key(WINDU_COMPANY_NAME))).fetchone()["total"]
        pending = db.execute("""SELECT 1 FROM transactions t JOIN users u ON u.id=t.user_id
            JOIN pegawai p ON LOWER(p.email)=LOWER(u.email) WHERE t.status='on-proses' AND t.admin_approved_at IS NULL
            AND (LOWER(TRIM(p.perusahaan_induk))=? OR LOWER(TRIM(p.perusahaan))=?) LIMIT 1""",
            (name_key(WINDU_COMPANY_NAME), name_key(WINDU_COMPANY_NAME))).fetchone()
        if not others and pending:
            return "Masih ada pengajuan Windu yang menunggu approval. Siapkan admin Windu aktif dengan hak approval sebelum melepas admin terakhir."
    return None


def admin_employee_scope(db, alias="p"):
    if session.get("is_superadmin"):
        return "1=1", []
    company = (session.get("company") or "").strip()
    if not company:
        return "1=0", []
    column = "perusahaan_induk" if _is_parent_company(db, company) else "perusahaan"
    return f"LOWER(TRIM({alias}.{column})) = ?", [company.lower()]

def format_rupiah(n):
    try:
        return f"{int(n):,}".replace(",", ".")
    except:
        return str(n)

def apply_ppn(amount: int) -> int:
    """Tambahkan PPN 11% ke admin fee (dibulatkan ke atas)."""
    base = int(amount or 0)
    if not get_ppn_enabled():
        return base
    return (base * 111 + 99) // 100

def get_enabled_products():
    db = get_db()
    row = db.execute(
        "SELECT value FROM app_settings WHERE `key` ='enabled_products' LIMIT 1"
    ).fetchone()
    if not row:
        return ["reg", "urg"]
    parts = [p.strip().lower() for p in (row["value"] or "").split(",") if p.strip()]
    enabled = []
    for p in parts:
        if p in ("reg", "urg") and p not in enabled:
            enabled.append(p)
    return enabled if enabled else ["reg", "urg"]


def get_products_for_company(company):
    """Global product setting intersected with the active company admin policy."""
    enabled = set(get_enabled_products())
    company = (company or "").strip()
    if not company:
        return sorted(enabled)
    row = get_db().execute("""
        SELECT COALESCE(produk_reg_aktif, 1) AS reg_on,
               COALESCE(produk_urg_aktif, 1) AS urg_on
        FROM admins
        WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))
        ORDER BY id DESC LIMIT 1
    """, (company,)).fetchone()
    if row:
        if not int(row["reg_on"] or 0):
            enabled.discard("reg")
        if not int(row["urg_on"] or 0):
            enabled.discard("urg")
    return sorted(enabled)

def save_app_setting(key, value):
    db = get_db()
    if isinstance(db, sqlite3.Connection):
        db.execute("INSERT INTO app_settings (`key`, value) VALUES (?,?) ON CONFLICT(`key`) DO UPDATE SET value=excluded.value", (key, value))
    else:
        db.execute("INSERT INTO app_settings (`key`, value) VALUES (?,?) ON DUPLICATE KEY UPDATE value=VALUES(value)", (key, value))
    db.commit()

def set_enabled_products(products):
    value = ",".join(products)
    save_app_setting("enabled_products", value)

def get_ppn_enabled() -> bool:
    db = get_db()
    row = db.execute(
        "SELECT value FROM app_settings WHERE `key`='ppn_enabled' LIMIT 1"
    ).fetchone()
    if not row:
        return True
    val = str(row["value"] or "").strip().lower()
    return val not in ("0", "false", "no", "off")

def set_ppn_enabled(enabled: bool) -> None:
    value = "1" if enabled else "0"
    save_app_setting("ppn_enabled", value)

def get_admin_fee_flat_for_user(user_id: int) -> int:
    default_fee = normalize_admin_fee_flat(current_app.config["ADMIN_FEE"], 15000)
    try:
        row = get_db().execute("""
            SELECT p.admin_fee_flat
            FROM users u
            LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
            WHERE u.id = ?
        """, (user_id,)).fetchone()
        if row and row["admin_fee_flat"] is not None:
            return normalize_admin_fee_flat(row["admin_fee_flat"], default_fee)
    except Exception:
        pass
    return default_fee

def get_runtime_force_limit() -> bool:
    db = get_db()
    row = db.execute(
        "SELECT value FROM app_settings WHERE `key`='runtime_force_limit' LIMIT 1"
    ).fetchone()
    if not row:
        return False
    return str(row["value"] or "").strip().lower() in {"1", "true", "yes", "on"}

def set_runtime_force_limit(enabled: bool) -> None:
    value = "1" if enabled else "0"
    save_app_setting("runtime_force_limit", value)

def allowed_avatar(filename: str) -> bool:
    ext = os.path.splitext(filename or "")[1].lower()
    return ext in {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# app.py — TARUH DI BAWAH def protect_admin_routes() atau di bagian helpers

# ===== Routes umum =====
@bp.route("/")
def index():
    if session.get("is_superadmin"):
        return redirect(url_for("web.superadmin_dashboard"))
    if session.get("is_admin"):
        return redirect(url_for("web.admin_dashboard"))
    return redirect(url_for("web.dashboard") if "user_id" in session else url_for("web.login"))

# === LOGIN FORMAL (EMAIL + PASSWORD) + SIMULASI TANGGAL ===
@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        limited = rate_limit("login", email)
        if limited:
            return limited
        db = get_db()

        # --- 1. Login Superadmin (Sebelumnya Owner) ---
        superadmin_username = str(current_app.config.get("SUPERADMIN_USERNAME") or "").strip().lower()
        superadmin_password = str(current_app.config.get("SUPERADMIN_PASSWORD") or "")
        if superadmin_username and superadmin_password and email == superadmin_username and password == superadmin_password:
            session.clear()
            session["is_admin"] = True      # Admin biasa dimatikan
            session["is_superadmin"] = True  # Menggantikan peran Owner lama
            session["auth_source"] = "env_super"
            session["auth_version"] = credential_version(superadmin_password)
            session["authenticated_at"] = int(time.time())
            session.permanent = True
            session["admin_id"] = 0
            session["admin_name"] = "Superadmin"
            session["admin_email"] = superadmin_username
            session["company"] = None  # Superadmin bisa melihat semua PT
            return redirect(url_for("web.superadmin_dashboard")) # Diarahkan ke dashboard superadmin

        # --- 2. Login Admin Biasa dari Database ---
        # Admins table (role_id: 2 for Admin, 1 for Superadmin)
        adm = db.execute(
            "SELECT id, name, email, password_hash, role, company, hak_approval, status_aktif FROM admins WHERE LOWER(email)=? OR LOWER(name)=?",
            (email, email),
        ).fetchone()

        if adm and check_password_hash(adm["password_hash"], password):
            if int(adm["status_aktif"] or 0) != 1:
                flash("Akun tidak aktif. Hubungi Superadmin.", "error")
                return render_template("login.html", today=business_today())
            session.clear()
            session["auth_source"] = "db"
            session["auth_version"] = credential_version(adm["password_hash"])
            session["authenticated_at"] = int(time.time())
            session.permanent = True
            session["admin_id"] = adm["id"]
            session["admin_name"] = adm["name"]
            session["admin_email"] = adm["email"]
            session["company"] = adm["company"]  # Menyimpan data perusahaan ke session
            # Hak approve/tolak tarik gaji (LAYER 1) - per-orang, diatur Superadmin di Kelola Admin.
            # Default 0/OFF kalau kolomnya NULL (baris admin lama sebelum kolom ini ada).
            session["hak_approval"] = bool(adm["hak_approval"]) if "hak_approval" in adm.keys() else False

            role_value = adm["role"] if "role" in adm.keys() else ""
            is_superadmin = str(role_value or "").strip().lower() == "superadmin"
            session["is_superadmin"] = is_superadmin
            session["is_admin"] = True
            if is_superadmin:
                session["company"] = None
                flash("Login superadmin berhasil.", "success")
                return redirect(url_for("web.superadmin_dashboard"))
            else:
                flash("Login admin berhasil.", "success")
                return redirect(url_for("web.admin_dashboard"))

        # --- 3. Login Admin Biasa dari File Konfigurasi (ENV) ---
        valid_ids = {
            str(current_app.config["ADMIN_USERNAME"]).strip().lower(),
            str(current_app.config["ADMIN_EMAIL"]).strip().lower(),
        }
        if not adm and current_app.config["ADMIN_PASSWORD"] and email in valid_ids and password == current_app.config["ADMIN_PASSWORD"]:
            session.clear()
            session["is_admin"] = True
            session["is_superadmin"] = False
            session["auth_source"] = "env_admin"
            session["auth_version"] = credential_version(current_app.config["ADMIN_PASSWORD"])
            session["authenticated_at"] = int(time.time())
            session.permanent = True
            session["admin_id"] = None
            session["admin_name"] = current_app.config["ADMIN_USERNAME"]
            session["admin_email"] = current_app.config["ADMIN_EMAIL"]
            session["company"] = current_app.config.get("ADMIN_COMPANY", "")
            # Admin dari ENV tidak punya baris di tabel `admins` secara default - cek kalau ada baris
            # dengan email yang sama supaya hak approval-nya tetap bisa diatur Superadmin lewat Kelola
            # Admin; kalau tidak ada, default OFF (konsisten dengan admin lain).
            env_adm = db.execute(
                "SELECT hak_approval FROM admins WHERE LOWER(email)=?", (email,)
            ).fetchone()
            session["hak_approval"] = bool(env_adm["hak_approval"]) if env_adm else False
            flash("Login admin berhasil (ENV).", "success")
            return redirect(url_for("web.admin_dashboard"))

        # ... (Sisa kode ke bawah untuk login pegawai/user dibiarkan tetap sama)

        today = business_today()
        try:
            day = int(request.form.get("tanggal") or today.day) if current_app.config.get("ALLOW_DATE_OVERRIDE") else today.day
        except ValueError:
            day = today.day
        last_day = calendar.monthrange(today.year, today.month)[1]
        if day < 1 or day > last_day:
            flash(f"Tanggal tidak valid untuk bulan ini (1–{last_day}).", "error")
            return render_template("login.html", today=today)

        sim_date = date(today.year, today.month, day).isoformat()

        acc = db.execute("SELECT * FROM user_accounts WHERE LOWER(email)=?", (email,)).fetchone()
        if not acc or not check_password_hash(acc["password_hash"], password):
            flash("Email atau password salah.", "error")
            return render_template("login.html", today=today)

        p = db.execute("SELECT * FROM pegawai WHERE id=?", (acc["pegawai_id"],)).fetchone()
        if not p:
            flash("Akun tidak terhubung dengan master pegawai. Hubungi admin.", "error")
            return render_template("login.html", today=today)

        formal_active = int(p["status_aktif"] or 0)
        if formal_active != int(acc["status_aktif"] or 0):
            db.execute("UPDATE user_accounts SET status_aktif=? WHERE id=?", (formal_active, acc["id"]))
            db.commit()

        db.commit()
        if isinstance(db, sqlite3.Connection):
            db.execute("BEGIN IMMEDIATE")
        else:
            db.execute("SELECT id FROM pegawai WHERE id=? FOR UPDATE", (p["id"],)).fetchall()
        p = db.execute("SELECT * FROM pegawai WHERE id=?", (acc["pegawai_id"],)).fetchone()
        account_exists = db.execute("SELECT id FROM user_accounts WHERE id=?", (acc["id"],)).fetchone()
        if not p or not account_exists:
            db.rollback()
            flash("Data akun berubah. Silakan login kembali.", "error")
            return redirect(url_for("web.login"))
        formal_active = int(p["status_aktif"] or 0)
        db.execute("UPDATE user_accounts SET status_aktif=? WHERE id=?", (formal_active, acc["id"]))
        name_for_users = p["nama"] or acc["name"]
        existing_user = db.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(?)", (p["email"],)).fetchone()
        
        if existing_user:
            # Jika user sudah ada di tabel users, lakukan UPDATE data terbarunya
            db.execute("""
                UPDATE users 
                SET name = ?, gaji = ?
                WHERE id = ?
            """, (name_for_users, int(p["gaji"] or 0), existing_user["id"]))
        else:
            # Jika belum terdaftar sama sekali, lakukan INSERT baru
            db.execute("""
                INSERT INTO users(name, email, gaji, created_at)
                VALUES (?,?,?,?)
            """, (name_for_users, p["email"], int(p["gaji"] or 0), datetime.now().isoformat(timespec="seconds")))
        
        db.commit()

        u = get_user_by_email(p["email"])

        session.clear()
        session["auth_source"] = "employee"
        session["auth_version"] = credential_version(acc["password_hash"])
        session["authenticated_at"] = int(time.time())
        session.permanent = True
        session["user_id"] = u["id"]
        session["user_name"] = u["name"]
        session["sim_date"] = sim_date
        session["formal_active"] = formal_active
        session["formal_email"] = p["email"]
        session["formal_email"] = p["email"]      # penting untuk re-check server-side
        session["formal_active"] = formal_active  # 1 aktif, 0 non-aktif

        flash("Login berhasil.", "success")
        return redirect(url_for("web.dashboard", tanggal=sim_date))

    return render_template("login.html", today=business_today())

@bp.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        limited = rate_limit("forgot", email, limit=3)
        if limited:
            return limited
        db = get_db()
        account = db.execute("SELECT id, name, email, password_hash FROM user_accounts WHERE LOWER(email)=?", (email,)).fetchone()
        if account:
            token = token_serializer("password-reset").dumps({
                "id": account["id"], "version": credential_version(account["password_hash"]),
            })
            reset_path = url_for("web.reset_password", token=token)
            origin = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
            if origin:
                from app.services.email_service import send_email
                body = (f"Halo {account['name']},\n\nUntuk mengganti password, buka tautan berikut (berlaku 30 menit):\n"
                        f"{origin}{reset_path}\n\nJika bukan Anda yang meminta, abaikan email ini. Password belum diubah.")
                if not send_email("[GajiKu] Permintaan Ganti Password", body, to_list=[account["email"]]):
                    current_app.logger.error("Pengiriman tautan reset password gagal")
            else:
                current_app.logger.error("PUBLIC_BASE_URL belum dikonfigurasi untuk reset password")
        flash("Jika email terdaftar, instruksi reset akan dikirim. Periksa email atau hubungi admin jika belum diterima.", "info")
        return redirect(url_for("web.login"))
    return render_template("forgot.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    db = get_db()
    try:
        payload = token_serializer("password-reset").loads(token, max_age=1800)
        account = db.execute("SELECT id, password_hash FROM user_accounts WHERE id=?", (payload["id"],)).fetchone()
        if not account or not hmac.compare_digest(payload["version"], credential_version(account["password_hash"])):
            raise BadSignature("Token sudah digunakan")
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        flash("Tautan reset tidak valid, kedaluwarsa, atau sudah digunakan.", "error")
        return redirect(url_for("web.forgot_password"))
    if request.method == "POST":
        limited = rate_limit("reset-password", str(account["id"]), limit=10)
        if limited:
            return limited
        password = request.form.get("new_password") or ""
        if not password_ok(password) or password != request.form.get("new_password2"):
            flash("Password minimal 6, maksimal 256 karakter dan konfirmasi harus sama.", "error")
            return render_template("reset_password.html")
        cursor = db.execute("UPDATE user_accounts SET password_hash=? WHERE id=? AND password_hash=?",
                            (generate_password_hash(password), account["id"], account["password_hash"]))
        db.commit()
        if cursor.rowcount != 1:
            flash("Tautan sudah digunakan. Minta tautan reset baru.", "error")
            return redirect(url_for("web.forgot_password"))
        session.clear()
        flash("Password berhasil diganti. Silakan login kembali.", "success")
        return redirect(url_for("web.login"))
    return render_template("reset_password.html")

# =========================================================
# ================ MODUL REGISTER + SIGNIN ================
# =========================================================
@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name  = (request.form.get("name")  or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        pw    = request.form.get("password") or ""
        pw2   = request.form.get("password2") or ""

        limited = rate_limit("register", email, limit=5)
        if limited:
            return limited
        if not name or len(name) > 255 or not valid_email(email) or not pw or not pw2:
            flash("Semua field wajib diisi.", "error"); return render_template("register.html")
        if not password_ok(pw):
            flash("Password minimal 6 karakter.", "error"); return render_template("register.html")
        if pw != pw2:
            flash("Konfirmasi password tidak cocok.", "error"); return render_template("register.html")

        db = get_db()
        p = db.execute("SELECT * FROM pegawai WHERE LOWER(email)=?", (email,)).fetchone()
        if not p:
            flash("Email Anda belum terdaftar di master pegawai. Hubungi Admin/HR.", "error")
            return render_template("register.html")

        exists = db.execute("SELECT 1 FROM user_accounts WHERE LOWER(email)=?", (email,)).fetchone()
        if exists:
            flash("Email sudah memiliki akun. Silakan masuk.", "error")
            return redirect(url_for("web.login"))

        origin = (current_app.config.get("PUBLIC_BASE_URL") or "").rstrip("/")
        if not origin:
            flash("Registrasi belum tersedia. Hubungi admin untuk konfigurasi verifikasi email.", "error")
            return render_template("register.html")
        token = secrets.token_urlsafe(32)
        values = (email, hashlib.sha256(token.encode()).hexdigest(), p["id"], name,
                  generate_password_hash(pw), int(time.time()) + 1800)
        if isinstance(db, sqlite3.Connection):
            db.execute("""INSERT INTO pending_registrations (email,token_hash,pegawai_id,name,password_hash,expires_at)
                VALUES (?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET token_hash=excluded.token_hash,
                pegawai_id=excluded.pegawai_id,name=excluded.name,password_hash=excluded.password_hash,expires_at=excluded.expires_at""", values)
        else:
            db.execute("""INSERT INTO pending_registrations (email,token_hash,pegawai_id,name,password_hash,expires_at)
                VALUES (?,?,?,?,?,?) ON DUPLICATE KEY UPDATE token_hash=VALUES(token_hash),pegawai_id=VALUES(pegawai_id),
                name=VALUES(name),password_hash=VALUES(password_hash),expires_at=VALUES(expires_at)""", values)
        db.execute("DELETE FROM pending_registrations WHERE expires_at < ?", (int(time.time()),))
        db.commit()
        from app.services.email_service import send_email
        verification_url = origin + url_for("web.verify_registration", token=token)
        body = f"Halo {name},\n\nVerifikasi email untuk mengaktifkan registrasi GajiKu (berlaku 30 menit):\n{verification_url}\n\nJika bukan Anda yang mendaftar, abaikan email ini."
        if send_email("[GajiKu] Verifikasi Registrasi", body, to_list=[email]):
            flash("Periksa email untuk menyelesaikan registrasi. Akun belum dibuat sebelum email diverifikasi.", "success")
        else:
            flash("Email verifikasi belum berhasil dikirim. Coba kembali atau hubungi admin.", "error")
        return redirect(url_for("web.login"))

    return render_template("register.html")

@bp.route("/verify-registration/<token>", methods=["GET", "POST"])
def verify_registration(token):
    if len(token) > 128:
        return ("Tautan tidak valid.", 400)
    db = get_db()
    pending = db.execute("SELECT * FROM pending_registrations WHERE token_hash=? AND expires_at>?",
                         (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
    if not pending:
        flash("Tautan verifikasi kedaluwarsa atau sudah digunakan. Silakan daftar kembali.", "error")
        return redirect(url_for("web.register"))
    if request.method == "POST":
        limited = rate_limit("verify-registration", pending["email"], limit=10)
        if limited:
            return limited
        db.commit()
        if isinstance(db, sqlite3.Connection):
            db.execute("BEGIN IMMEDIATE")
        lock = "" if isinstance(db, sqlite3.Connection) else " FOR UPDATE"
        pending = db.execute("SELECT * FROM pending_registrations WHERE token_hash=? AND expires_at>?" + lock,
                             (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        if not pending:
            db.rollback()
            flash("Tautan verifikasi kedaluwarsa atau sudah diganti. Silakan daftar kembali.", "error")
            return redirect(url_for("web.register"))
        employee = db.execute("SELECT * FROM pegawai WHERE id=? AND LOWER(email)=?" + lock,
                              (pending["pegawai_id"], pending["email"])).fetchone()
        if not employee:
            db.rollback()
            flash("Data pegawai berubah. Hubungi admin sebelum registrasi kembali.", "error")
            return redirect(url_for("web.register"))
        try:
            db.execute("""INSERT INTO user_accounts
                (pegawai_id,name,email,password_hash,status_aktif,created_at,register_ip,company)
                VALUES (?,?,?,?,?,?,?,?)""", (employee["id"],pending["name"],pending["email"],pending["password_hash"],
                int(employee["status_aktif"] or 0),datetime.now().isoformat(timespec="seconds"),request.remote_addr,employee["perusahaan"]))
            db.execute("DELETE FROM pending_registrations WHERE email=? AND token_hash=?", (pending["email"],pending["token_hash"]))
            db.commit()
        except Exception as error:
            db.rollback()
            if not isinstance(error, sqlite3.IntegrityError) and getattr(error, "errno", None) != 1062:
                raise
            flash("Akun sudah terdaftar. Silakan login atau gunakan lupa password.", "info")
            return redirect(url_for("web.login"))
        flash("Email terverifikasi. Silakan login; status aktif mengikuti persetujuan admin.", "success")
        return redirect(url_for("web.login"))
    return render_template("verify_registration.html", email=pending["email"])

@bp.post("/logout")
def logout():
    session.clear()
    flash("Anda telah logout.", "info")
    return redirect(url_for("web.login"))

@bp.post("/avatar/upload")
def avatar_upload():
    if not session.get("user_id") and not session.get("is_admin"):
        flash("Silakan login terlebih dahulu.", "error")
        return redirect(url_for("web.login"))

    limited = rate_limit("avatar", str(session.get("admin_id") or session.get("user_id") or session.get("admin_email")), limit=10, window=60)
    if limited:
        return limited
    file = request.files.get("avatar")
    if not file or not file.filename:
        flash("Pilih file gambar terlebih dahulu.", "error")
        return redirect(safe_return_url("web.dashboard"))

    filename = secure_filename(file.filename)
    if not filename or not allowed_avatar(filename):
        flash("Format gambar tidak didukung.", "error")
        return redirect(safe_return_url("web.dashboard"))

    ext = os.path.splitext(filename)[1].lower()
    content = file.stream.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        flash("Avatar maksimal 2 MB.", "error")
        return redirect(safe_return_url("web.dashboard"))
    try:
        image = Image.open(io.BytesIO(content))
        expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF", ".webp": "WEBP"}[ext]
        if image.format != expected or image.width * image.height > 16_000_000:
            raise ValueError("Gambar tidak valid")
        image.verify()
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(content)))
        image.thumbnail((1024, 1024))
        if expected == "JPEG":
            image = image.convert("RGB")
        encoded = io.BytesIO()
        image.save(encoded, format=expected)
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        flash("File bukan gambar yang valid atau dimensinya terlalu besar.", "error")
        return redirect(safe_return_url("web.dashboard"))
    stamp = secrets.token_hex(16)
    superadmin_id = session.get("admin_id") if session.get("is_admin") else session.get("user_id")
    superadmin_prefix = "admin" if session.get("is_admin") else "user"
    safe_name = f"{superadmin_prefix}_{superadmin_id or 'env'}_{stamp}{ext}"

    upload_dir = os.path.join(current_app.static_folder, "uploads", "avatars")
    os.makedirs(upload_dir, exist_ok=True)
    with open(os.path.join(upload_dir, safe_name), "wb") as avatar_file:
        avatar_file.write(encoded.getvalue())
    rel_path = f"uploads/avatars/{safe_name}"

    db = get_db()
    if session.get("is_admin"):
        if session.get("admin_id"):
            db.execute("UPDATE admins SET avatar_path=? WHERE id=?", (rel_path, session["admin_id"]))
        elif session.get("admin_email"):
            db.execute("UPDATE admins SET avatar_path=? WHERE LOWER(email)=?", (rel_path, session["admin_email"].lower()))
        db.commit()
        flash("Avatar admin diperbarui.", "success")
        return redirect(url_for("web.superadmin_dashboard" if session.get("is_superadmin") else "web.admin_dashboard"))

    email = (session.get("formal_email") or "").strip().lower()
    if not email:
        u = db.execute("SELECT email FROM users WHERE id=?", (session["user_id"],)).fetchone()
        email = (u["email"] or "").strip().lower() if u else ""
    if email:
        db.execute("UPDATE user_accounts SET avatar_path=? WHERE LOWER(email)=?", (rel_path, email))
        db.commit()
    flash("Avatar diperbarui.", "success")
    return redirect(safe_return_url("web.dashboard"))

@bp.post("/avatar/delete")
def avatar_delete():
    if not session.get("user_id") and not session.get("is_admin"):
        flash("Silakan login terlebih dahulu.", "error")
        return redirect(url_for("web.login"))

    db = get_db()
    if session.get("is_admin"):
        if session.get("admin_id"):
            db.execute("UPDATE admins SET avatar_path='' WHERE id=?", (session["admin_id"],))
        elif session.get("admin_email"):
            db.execute("UPDATE admins SET avatar_path='' WHERE LOWER(email)=?", (session["admin_email"].lower(),))
        db.commit()
        flash("Avatar admin dihapus.", "success")
        return redirect(safe_return_url("web.admin_dashboard"))

    email = (session.get("formal_email") or "").strip().lower()
    if not email:
        u = db.execute("SELECT email FROM users WHERE id=?", (session["user_id"],)).fetchone()
        email = (u["email"] or "").strip().lower() if u else ""
    if email:
        db.execute("UPDATE user_accounts SET avatar_path='' WHERE LOWER(email)=?", (email,))
        db.commit()
    flash("Avatar dihapus.", "success")
    return redirect(safe_return_url("web.dashboard"))

# ===== DASHBOARD USER =====
@bp.route("/dashboard")
def dashboard():
    uid = session.get("user_id")
    if not uid:
        flash("Sesi habis. Silakan login lagi.", "error")
        return redirect(url_for("web.login"))

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if user is None:
        session.clear()
        flash("Data akun tidak ditemukan. Silakan login kembali.", "error")
        return redirect(url_for("web.login"))

    at_date = current_sim_date()
    gaji_int = int(user["gaji"] or 0)
    limits = compute_limits(gaji_int, at_date, user["id"])
    mk = limits["periode_key"]
        # KPI baru: Sisa Plafon URG (plafon - total_sukses)
    sisa_plafon_urg = max(int(limits.get("plafon", 0)) - int(limits.get("total_sukses", 0)), 0)


    rows = db.execute(
        """SELECT t.id, t.tanggal, t.nominal, t.admin_fee, t.status, t.product, t.created_at,
                  t.keterangan, t.cancel_until, t.admin_approved_at,
                  COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
                  COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label,
                  COALESCE(p.no_telp,'') AS no_telp
           FROM transactions t
           JOIN users u ON u.id = t.user_id
           LEFT JOIN pegawai p ON LOWER(p.email)=LOWER(u.email)
           WHERE t.user_id=?
           ORDER BY t.created_at DESC, t.id DESC
           LIMIT 50""",
        (user["id"],),
    ).fetchall()

    pegawai_info = None
    try:
        email = (user["email"] or "").strip().lower()
        if email:
            pegawai_info = db.execute(
                """SELECT id_pegawai, no_rekening, no_rekening_lain, rekening_ewallet, no_telp
                   FROM pegawai WHERE LOWER(email)=LOWER(?)""",
                (email,),
            ).fetchone()
    except Exception:
        pegawai_info = None

    total_nom   = sum(int(r["nominal"] or 0) for r in rows if r["status"] == "sukses")
    total_admin = sum(int(r["admin_fee"] or 0) for r in rows if r["status"] == "sukses")

    # hitung sisa detik countdown pembatalan untuk tiap transaksi on-proses
    now = datetime.now()
    remaining_map = {}
    for r in rows:
        remaining = 0
        if r["status"] == "on-proses" and not r["admin_approved_at"] and r["cancel_until"]:
            try:
                cu = datetime.fromisoformat(str(r["cancel_until"]))
                delta = (cu - now).total_seconds()
                remaining = int(delta) if delta > 0 else 0
            except Exception:
                remaining = 0
        remaining_map[r["id"]] = remaining

    try:
        account_name = user["name"] or user["email"] or "Akun"
    except Exception:
        # kalau suatu saat tipe-nya dict, tetap aman
        account_name = (user.get("name") or user.get("email") or "Akun")

    account_company = ""
    try:
        email = (user["email"] or "").strip().lower()
        if email:
            company_row = db.execute(
                "SELECT COALESCE(perusahaan_induk, '') AS perusahaan_induk FROM pegawai WHERE LOWER(email)=?",
                (email,),
            ).fetchone()
            account_company = company_row["perusahaan_induk"] if company_row else ""
    except Exception:
        account_company = ""

    enabled_products = get_products_for_company(account_company)
    avatar_url = None
    try:
        email = (session.get("formal_email") or user["email"] or "").strip().lower()
        if email:
            row = db.execute(
                "SELECT avatar_path FROM user_accounts WHERE LOWER(email)=?",
                (email,),
            ).fetchone()
            if row and row["avatar_path"]:
                avatar_url = url_for("static", filename=row["avatar_path"])
    except Exception:
        avatar_url = None

    return render_template(
        "dashboard.html",
        user=user,
        at_date=at_date,
        mk=mk,
        limits=limits,
        rows=rows,
        total_nom=total_nom,
        total_admin=total_admin,
        account_name=account_name,
        account_company=account_company,
        remaining_map=remaining_map,
        sisa_plafon_urg=sisa_plafon_urg,
        enabled_products=enabled_products,
        avatar_url=avatar_url,
        pegawai_info=pegawai_info,
    )

@bp.route("/settings", methods=["GET", "POST"])
def settings_account():
    # wajib login user formal
    if "user_id" not in session:
        flash("Silakan login terlebih dahulu.", "error")
        return redirect(url_for("web.login"))

    db = get_db()

    # temukan akun formal (user_accounts) via sesi login formal
    # prioritas: formal_email → kalau tidak ada, fallback ke email di 'users'
    formal_email = (session.get("formal_email") or "").strip().lower()
    if not formal_email:
        # fallback dengan relasi users -> user_accounts via email yang sama
        u = db.execute("SELECT email FROM users WHERE id=?", (session["user_id"],)).fetchone()
        formal_email = (u["email"] or "").strip().lower() if u else ""

    acc = None
    if formal_email:
        acc = db.execute("SELECT id, email, password_hash FROM user_accounts WHERE LOWER(email)=?", (formal_email,)).fetchone()

    if not acc:
        flash("Akun formal Anda belum tersedia. Hubungi admin untuk registrasi.", "error")
        return redirect(url_for("web.dashboard"))

    if request.method == "POST":
        old_pw = request.form.get("old_password") or ""
        new_pw = request.form.get("new_password") or ""
        new_pw2 = request.form.get("new_password2") or ""

        if not check_password_hash(acc["password_hash"], old_pw):
            flash("Password lama tidak cocok.", "error")
            return render_template("settings.html")

        if not password_ok(new_pw):
            flash("Password baru minimal 6 karakter.", "error")
            return render_template("settings.html")

        if new_pw != new_pw2:
            flash("Konfirmasi password baru tidak cocok.", "error")
            return render_template("settings.html")

        new_hash = generate_password_hash(new_pw)
        cursor = db.execute("UPDATE user_accounts SET password_hash=? WHERE id=? AND password_hash=?",
                            (new_hash, acc["id"], acc["password_hash"]))
        db.commit()
        if cursor.rowcount != 1:
            session.clear()
            flash("Password akun berubah saat diproses. Silakan login kembali.", "error")
            return redirect(url_for("web.login"))
        session["auth_version"] = credential_version(new_hash)
        flash("Password berhasil diperbarui.", "success")
        return redirect(url_for("web.dashboard"))

    return render_template("settings.html")


# =========================================================
# ===================== PENCAIRAN =========================
# =========================================================
@bp.route("/tarik-gaji", methods=["GET", "POST"])
@serialized_withdrawal
def tarik_gaji():
    if "user_id" not in session:
        flash("Silakan login terlebih dahulu.", "error")
        return redirect(url_for("web.login"))

    admin_fee_base = get_admin_fee_flat_for_user(session["user_id"])
    admin_fee_per_day = admin_fee_base
    ppn_enabled = get_ppn_enabled()
    db      = get_db()
    user    = get_user_by_id(session["user_id"])
    if not user:
        session.clear()
        return redirect(url_for("web.login"))
    at_date = current_sim_date() # Tanggal simulasi bawaan sistem
    limits  = compute_limits(int(user["gaji"] or 0), at_date, user["id"])
    pegawai_company = ""
    try:
        company_row = db.execute("SELECT perusahaan_induk FROM pegawai WHERE LOWER(email)=LOWER(?)", (user["email"],)).fetchone()
        pegawai_company = company_row["perusahaan_induk"] if company_row else ""
    except Exception:
        pass
    enabled_products = get_products_for_company(pegawai_company)
    selected_product = enabled_products[0] if enabled_products else "reg"
    pegawai_rekening = None
    try:
        email = (user["email"] or "").strip().lower()
        if email:
            pegawai_rekening = db.execute(
                """SELECT id_pegawai, perusahaan, no_rekening, no_rekening_lain, rekening_ewallet
                   FROM pegawai WHERE LOWER(email)=LOWER(?)""",
                (email,),
            ).fetchone()
    except Exception:
        pegawai_rekening = None
    rekening_options = build_rekening_options(pegawai_rekening)
    default_rekening_key = rekening_options[0]["key"] if rekening_options else ""

    def render_tarik(form_date, form_limits, product=None, rekening_key=None):
        return render_template(
            "tarik_gaji.html",
            user=user,
            at_date=form_date,
            limits=form_limits,
            ADMIN_FEE=admin_fee_base,
            ADMIN_FEE_PER_DAY=admin_fee_per_day,
            REG_WITHDRAWAL_MAX=REG_WITHDRAWAL_MAX,
            ppn_enabled=ppn_enabled,
            enabled_products=enabled_products,
            selected_product=product or selected_product,
            rekening_options=rekening_options,
            selected_rekening_key=rekening_key or default_rekening_key,
            request_token=withdrawal_token(),
            pegawai_info=pegawai_rekening,   # untuk invoice (ID pegawai & perusahaan)
        )

    # ---- HARD GATE: akun formal harus aktif sekarang (cek langsung ke DB) ----
    fresh_active = get_formal_status()
    if fresh_active != 1:
        session["formal_active"] = 0
        flash("Akun Anda belum diaktifkan admin. Pengajuan tarik gaji terkunci.", "error")
        return redirect(url_for("web.dashboard", tanggal=at_date.isoformat()))
    elif fresh_active == 1:
        session["formal_active"] = 1

    # ===================== POST =====================
    if request.method == "POST":
        # 🟢 FIX TANGGAL: Ambil acuan bulan/tahun dari AT_DATE simulasi, bukan kalender real server
        try:
            day = int(request.form.get("tanggal") or at_date.day) if current_app.config.get("ALLOW_DATE_OVERRIDE") else at_date.day
        except ValueError:
            day = at_date.day
            
        import calendar
        last_day = calendar.monthrange(at_date.year, at_date.month)[1]
        
        if day < 1 or day > last_day:
            flash(f"Tanggal tidak valid (1-{last_day}).", "error")
            return render_tarik(at_date, limits)

        at_day = date(at_date.year, at_date.month, day)
        
        # Ambil produk & pastikan lowercase
        produk = (request.form.get("produk") or "reg").strip().lower()
        if produk not in enabled_products:
            flash("Produk tidak aktif. Pilih produk lain.", "error")
            return render_tarik(
                at_day,
                compute_limits(int(user["gaji"] or 0), at_day, user["id"]),
                selected_product,
                request.form.get("rekening_tujuan_key"),
            )
        selected_product = produk
        
        # 🟢 FIX NOMINAL: Buang paksa semua titik ribuan rupiah sebelum diparse ke Integer
        nominal_raw = request.form.get("nominal") or "0"
        nominal_clean = re.sub(r"[.,\s]", "", nominal_raw)
        nominal = int(nominal_clean) if re.fullmatch(r"[0-9]{1,10}", nominal_clean) else 0
        
        ket     = (request.form.get("keterangan") or "").strip()
        urg_lock_until = None  
        selected_rekening_key = (request.form.get("rekening_tujuan_key") or "").strip()
        rekening_by_key = {opt["key"]: opt for opt in rekening_options}
        rekening_choice = rekening_by_key.get(selected_rekening_key)

        if not rekening_choice:
            flash("Pilih rekening tujuan yang tersedia.", "error")
            return render_tarik(
                at_day,
                compute_limits(int(user["gaji"] or 0), at_day, user["id"]),
                selected_product,
                selected_rekening_key,
            )
        rekening_tujuan = rekening_choice["value"]
        rekening_tujuan_label = rekening_choice["label"]

        if nominal <= 0:
            flash("Nominal tarik gaji wajib > 0.", "error")
            return render_tarik(
                at_day,
                compute_limits(int(user["gaji"] or 0), at_day, user["id"]),
                selected_product,
                request.form.get("rekening_tujuan_key"),
            )

        # --- Limit untuk hari yang diminta ---
        lim_day = compute_limits(int(user["gaji"] or 0), at_day, user["id"])

        # --- Validasi & fee ---
        if produk == "reg":
            if nominal > REG_WITHDRAWAL_MAX:
                flash(f"Nominal REG maksimal {rupiah_format(REG_WITHDRAWAL_MAX)} per pengajuan.", "error")
                return render_tarik(at_day, lim_day, selected_product, selected_rekening_key)
            if nominal > lim_day["saldo"]:
                flash(f"Permintaan melebihi limit plafon harian. Sisa hari ini: {rupiah_format(lim_day['saldo'])}.", "error")
                return render_tarik(at_day, lim_day, selected_product, selected_rekening_key)
            admin_fee = apply_ppn(admin_fee_base)
        
        else:
            # ————— URG —————
            plafon        = int(lim_day["plafon"] or 0)
            total_sukses  = int(lim_day["total_sukses"] or 0)
            limit_harian  = int(lim_day["limit_harian"] or 0)
            hari_ke       = int(lim_day["hari_ke"] or 1)  
            sisa_plafon   = max(plafon - total_sukses, 0)

            limit_urg_max = sisa_plafon
            if nominal > limit_urg_max:
                flash(f"Permintaan melebihi limit URG. Maksimal URG saat ini: {rupiah_format(limit_urg_max)}.", "error")
                return render_tarik(at_day, lim_day, selected_product, selected_rekening_key)

            saldo_reg_tersedia = max(limit_harian * hari_ke - total_sukses, 0)

            reg_portion = min(nominal, saldo_reg_tersedia)
            urg_portion = max(nominal - reg_portion, 0)

            fee_reg = admin_fee_base if reg_portion > 0 else 0
            fee_urg = math.ceil(urg_portion / limit_harian) * admin_fee_base if limit_harian > 0 else 0

            admin_fee = apply_ppn(fee_reg + fee_urg)

        # --- Window pembatalan 25 detik ---
        from datetime import datetime, timedelta # Pastikan diimport
        cancel_until = (datetime.now() + timedelta(seconds=25)).isoformat(timespec="seconds")

        # --- Ensure user exists in users table ---
        user_check = db.execute("SELECT id FROM users WHERE id=?", (user["id"],)).fetchone()
        if not user_check:
            flash("Data pengguna tidak ditemukan di sistem. Silakan logout dan login kembali.", "error")
            return redirect(url_for("web.dashboard", tanggal=at_date.isoformat()))

        # --- Simpan transaksi on-proses ---
        db.execute("""
            INSERT INTO transactions
            (user_id, tanggal, periode, nominal, admin_fee, status, keterangan, rekening_tujuan, rekening_tujuan_label, created_at, product, cancel_until, urg_lock_until, request_key)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            user["id"],
            at_day.isoformat(),
            lim_day["periode_key"],       
            nominal,
            admin_fee,
            "on-proses",
            ket,
            rekening_tujuan,
            rekening_tujuan_label,
            datetime.now().isoformat(timespec="seconds"),
            produk,
            cancel_until,
            urg_lock_until,
            withdrawal_key(request.form.get("request_token")),
        ))
        db.commit()

        # --- Kirim notifikasi email (best-effort) ---
        try:
            peg = db.execute("""
                SELECT p.id_pegawai, p.nama, p.email, p.perusahaan, p.jabatan
                FROM users u
                JOIN pegawai p ON LOWER(p.email)=LOWER(u.email)
                WHERE u.id=?
            """, (user["id"],)).fetchone()

            sub = f"[Dana-Talangan] Pengajuan baru ({produk.upper()})"
            body = (
                f"Tanggal : {at_day.isoformat()}\n"
                f"ID Pegawai : {(peg['id_pegawai'] if peg and peg['id_pegawai'] else '-')}\n"
                f"Pegawai : {(peg['nama'] if peg else user['name'])} <{(peg['email'] if peg else user['email'])}>\n"
                f"Perusahaan/Jabatan : {(peg['perusahaan'] if peg and peg['perusahaan'] else '-')}"
                f" / {(peg['jabatan'] if peg and peg['jabatan'] else '-')}\n"
                f"Rekening Tujuan : {short_rekening_label(rekening_tujuan_label)} - {rekening_tujuan}\n"
                f"Nominal : {rupiah_format(nominal)}\n"
                f"Admin   : {rupiah_format(admin_fee)}\n"
                f"Produk  : {produk.upper()}\n"
                f"Status  : on-proses\n"
                f"Catatan : {ket or '-'}\n"
            )
            enqueue_email(sub, body)
        except Exception as e:
            print("[WARN] Notifikasi email di-skip:", e)

        session["sim_date"] = at_day.isoformat()
        flash("Pengajuan direkam dan masuk antrian admin (status: on-proses).", "success")
        return redirect(url_for("web.dashboard", tanggal=at_day.isoformat()))

    # ===================== GET =====================
    return render_tarik(at_date, limits)

# Redirect URL lama agar tidak memutus link yang sudah ada
@bp.route("/pencairan", methods=["GET", "POST"])
def pencairan_redirect():
    return redirect(url_for("web.tarik_gaji", **request.args), code=307 if request.method == "POST" else 302)



# ===== Pembatalan user (selama belum di-approve admin) =====
@bp.post("/tx/<int:txid>/cancel")
@serialized_transaction
def tx_cancel(txid):
    if "user_id" not in session:
        flash("Silakan login.", "error")
        return redirect(url_for("web.login"))
    db = get_db()
    row = db.execute("SELECT id, user_id, status, cancel_until, admin_approved_at FROM transactions WHERE id=? AND user_id=?",
                     (txid, session["user_id"])).fetchone()
    if not row:
        flash("Transaksi tidak ditemukan.", "error")
        return redirect(url_for("web.dashboard"))
    try:
        deadline = datetime.fromisoformat(str(row["cancel_until"]))
        now = datetime.now(deadline.tzinfo) if deadline.tzinfo else datetime.now()
    except (TypeError, ValueError):
        deadline, now = None, datetime.now()
    if _norm(row["status"]) != "on-proses" or row["admin_approved_at"] or not deadline or deadline <= now:
        flash("Batas pembatalan berakhir atau transaksi sudah diproses admin.", "info")
        return redirect(url_for("web.dashboard"))
    cursor = db.execute("""UPDATE transactions SET status='dibatalkan'
        WHERE id=? AND user_id=? AND status='on-proses' AND admin_approved_at IS NULL AND cancel_until > ?""",
        (txid, session["user_id"], now.isoformat(timespec="seconds")))
    db.commit()
    clear_cache_prefix("admin_kpi:")
    flash("Transaksi dibatalkan." if cursor.rowcount == 1 else "Transaksi sudah diproses admin.", "info")
    return redirect(url_for("web.dashboard"))

# ===== API status transaksi (dipakai polling countdown di dashboard) =====
@bp.get("/api/tx_status")
def api_tx_status():
    if "user_id" not in session:
        return {"ok": False, "err": "unauth"}, 401

    ids_raw = (request.args.get("ids") or "").strip()
    try:
        ids = [int(x) for x in ids_raw.split(",") if x.strip().isdigit()]
    except Exception:
        ids = []

    if len(ids) > 100:
        return {"ok": False, "err": "too_many_ids"}, 400
    if not ids:
        return {"ok": True, "items": []}

    db = get_db()
    rows = db.execute(
        """SELECT id, user_id, tanggal, status, cancel_until, product, nominal, admin_fee, admin_approved_at
           FROM transactions
           WHERE user_id=? AND id IN (%s)""" % ",".join("?"*len(ids)),
        [session["user_id"]] + ids
    ).fetchall()

    now = datetime.now()
    items = []
    for r in rows:
        # hitung sisa detik cancel window
        remaining = 0
        if r["status"] == "on-proses" and not r["admin_approved_at"] and r["cancel_until"]:
            try:
                cu = datetime.fromisoformat(str(r["cancel_until"]))
                delta = (cu - now).total_seconds()
                remaining = int(delta) if delta > 0 else 0
            except Exception:
                remaining = 0

        items.append({
            "id": r["id"],
            "status": r["status"],
            "remaining": remaining,
            "can_cancel": remaining > 0 and not r["admin_approved_at"] and r["status"] == "on-proses"
        })

    return {"ok": True, "items": items}

@bp.route("/riwayat", endpoint="riwayat")
def riwayat_view():
    if "user_id" not in session:
        flash("Silakan login terlebih dahulu.", "error")
        return redirect(url_for("web.login"))

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if user is None:
        session.clear()
        flash("Sesi tidak valid. Silakan login lagi.", "error")
        return redirect(url_for("web.login"))

    at_date = current_sim_date()
    limits_u = compute_limits(int(user["gaji"] or 0), at_date, user["id"])
    weekly_cutoff = limits_u.get("cutoff_hari") if limits_u.get("cutoff_mingguan") else None
    requested_periode = request.args.get("periode")
    if weekly_cutoff is not None:
        current_start = weekly_period_start(at_date, weekly_cutoff)
        weekly_periods = ["W:" + (current_start - timedelta(days=7 * i)).isoformat() for i in range(6)]
        last_periods = weekly_periods
        periode_options = [{"value": "last-6", "label": "6 Periode Mingguan Terakhir"}]
        periode_options += [{"value": p, "label": format_period_label(p)} for p in weekly_periods]
        if not requested_periode or requested_periode == "last-6":
            selected_periode = "last-6"
            periods_for_query = weekly_periods
        elif requested_periode in weekly_periods:
            selected_periode = requested_periode
            periods_for_query = [requested_periode]
        else:
            selected_periode = "last-6"
            periods_for_query = weekly_periods
        base_date = current_start
        mk = weekly_periods[0]
    else:
        if not requested_periode or requested_periode == "last-6":
            mk = at_date.strftime("%Y-%m")
            selected_periode = "last-6"
        else:
            mk = requested_periode
            selected_periode = mk
        try:
            base_year, base_month = [int(x) for x in mk.split("-", 1)]
            base_date = date(base_year, base_month, 1)
        except Exception:
            base_date = at_date.replace(day=1)
            mk = base_date.strftime("%Y-%m")
            selected_periode = "last-6"
        last_periods = [month_key(add_months(base_date, -i)) for i in range(6)]
        periode_options = [{"value": "last-6", "label": "6 Periode Terakhir"}]
        periode_options += [{"value": p, "label": format_period_label(p)} for p in last_periods]
        periods_for_query = last_periods if selected_periode == "last-6" else [mk]

    placeholders = ",".join(["?"] * len(periods_for_query))

    rows = db.execute(
        f"""SELECT t.tanggal, t.periode, t.nominal, t.admin_fee, t.status, t.keterangan, t.product,
                    COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
                    COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label
             FROM transactions t
             JOIN users u ON u.id = t.user_id
             LEFT JOIN pegawai p ON LOWER(p.email)=LOWER(u.email)
             WHERE t.user_id=? AND t.periode IN ({placeholders})
             ORDER BY t.periode DESC, t.tanggal DESC, t.id DESC""",
        (user["id"], *periods_for_query),
    ).fetchall()

    total_nom   = sum(int(r["nominal"] or 0) for r in rows if r["status"] == "sukses")
    total_admin = sum(int(r["admin_fee"] or 0) for r in rows if r["status"] == "sukses")

    # Penjelasan periode contoh berdasarkan siklus & periode yang dipilih
    a_start = base_date
    a_end = (base_date + timedelta(days=6)) if weekly_cutoff is not None else date(base_date.year, base_date.month, calendar.monthrange(base_date.year, base_date.month)[1])
    b_start = date(base_date.year, base_date.month, 16)
    b_end = date(add_months(base_date, 1).year, add_months(base_date, 1).month, 15)

    return render_template(
        "riwayat.html",
        user=user,
        periode=selected_periode,
        periode_list=last_periods,
        periode_options=periode_options,
        rows=rows,
        total_nom=total_nom,
        total_admin=total_admin,
        periode_a_info=f"{format_short_date(a_start)} – {format_short_date(a_end)}",
        periode_b_info=f"{format_short_date(b_start)} – {format_short_date(b_end)}",
    )

# =========================================================================================================================
# ================== MODUL ADMIN ==========================================================================================
# =========================================================================================================================

@bp.route("/admin/login", methods=["GET", "POST"], endpoint="admin_login")
def admin_login():
    return redirect(url_for("web.login"))


@bp.post("/admin/logout")
def admin_logout():
    session.clear()
    flash("Anda telah logout admin.", "info")
    return redirect(url_for("web.login"))

# ====== TAMPILAN BARU KHUSUS ADMIN BIASA ======
@bp.route("/admin/dashboard")
def admin_dashboard():
    ret = require_admin() # Tetap memakai admin biasa
    if ret:
        return ret

    db = get_db()
    
    # 🏢 Ambil data identitas admin dari session login
    admin_company = session.get("company")
    admin_email = session.get("admin_email", "").lower() or session.get("email", "").lower()
    account_name = ""
    avatar_url = None
    try:
        admin_email_norm = (admin_email or "").strip().lower()
        if admin_email_norm:
            row = db.execute(
                "SELECT COALESCE(name, '') AS nama, avatar_path FROM admins WHERE LOWER(email)=?",
                (admin_email_norm,),
            ).fetchone()
            account_name = row["nama"] if row and row["nama"] else ""
            if row and row["avatar_path"]:
                avatar_url = url_for("static", filename=row["avatar_path"])
    except Exception:
        account_name = session.get("admin_name", "")
    
    # 💡 DETEKSI ROLE (Superadmin vs Induk vs Anak)
    is_super = bool(session.get("is_superadmin"))
    is_parent = bool(admin_company) and not is_super and _is_parent_company(db, admin_company)

    enabled_products = get_enabled_products()
    ppn_enabled = get_ppn_enabled()
    
    admin_fee_month = (request.args.get("admin_fee_month") or "").strip()
    if admin_fee_month:
        try:
            y, m = [int(x) for x in admin_fee_month.split("-", 1)]
            date(y, m, 1)
        except Exception:
            admin_fee_month = ""
    # =========================================================================
    # 🟢 KENDALI WAKTU MULTI-URUSAN (MURNI BERBASIS SIMULASI)
    # =========================================================================
    sim_today = current_sim_date()
    periode_key = f"{sim_today.year}-{sim_today.month:02d}"
    mk = periode_key 

    first_day = date(sim_today.year, sim_today.month, 1)
    s_first = first_day.isoformat()

    if sim_today.month == 12:
        first_day_next = date(sim_today.year + 1, 1, 1)
    else:
        first_day_next = date(sim_today.year, sim_today.month + 1, 1)
    s_next = first_day_next.isoformat()

    def add_months(d: date, delta: int) -> date:
        y = d.year + (d.month - 1 + delta) // 12
        m = (d.month - 1 + delta) % 12 + 1
        return date(y, m, 1)

    trend_start = add_months(first_day, -5)
    trend_start_key = trend_start.strftime("%Y-%m")
    
    # Penentuan Cache Key dinamis berdasarkan status kepemilikan data
    if is_super:
        cache_company_key = "GLOBAL_SUPER"
    elif is_parent:
        cache_company_key = f"PARENT_{admin_company}"
    else:
        cache_company_key = f"LOCAL_{admin_company}"
        
    where_clause, where_params = admin_employee_scope(db)

    cache_key = f"admin_kpi:v6:{cache_company_key}:{s_first}:{s_next}:{periode_key}:{trend_start_key}"
    cached_kpi = get_cache(cache_key)
    
    if cached_kpi:
        (
            transactions, recent, total_pegawai, total_register, reg_aktif, eligible,
            trx_count, trx_sum, unique_borrowers, not_borrowed, not_registered,
            admin_fee_reg_total, admin_fee_urg_total, admin_fee_total,
            pending_count, pending_reg, pending_urg,
            cycle_a, cycle_b, cycle_c, cycle_d, inactive_count,
            chart_labels, chart_values, trend_labels, trend_values, cohort_rows,
        ) = cached_kpi
    else:
        # =========================================================================
        # 📊 GENERASI DATA JIKA CACHE KOSONG (SINKRON DENGAN STRUKTUR HOLDING / GMI)
        # =========================================================================
        

        # 1) AMBIL DATA DAFTAR TRANSAKSI (SINKRON KE TABEL 'users')
        if is_super:
            transactions = db.execute("""
                SELECT t.id, t.tanggal, t.created_at, t.nominal, t.status, t.product,
                       u.name AS nama, p.id_pegawai
                FROM transactions t
                JOIN users u ON u.id = t.user_id
                LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
                WHERE date(t.tanggal) >= date(?) AND date(t.tanggal) < date(?)
                ORDER BY date(t.tanggal) DESC, t.id DESC LIMIT 50
            """, (s_first, s_next)).fetchall()
        else:
            transactions = db.execute(f"""
                SELECT t.id, t.tanggal, t.created_at, t.nominal, t.status, t.product,
                       u.name AS nama, p.id_pegawai
                FROM transactions t
                JOIN users u ON u.id = t.user_id
                JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
                WHERE {where_clause} AND date(t.tanggal) >= date(?) AND date(t.tanggal) < date(?)
                ORDER BY date(t.tanggal) DESC, t.id DESC LIMIT 50
            """, where_params + [s_first, s_next]).fetchall()

        # 2) HITUNG KPI DASAR (Pegawai & Register - SINKRON KE TABEL 'users')
        # 2) HITUNG KPI DASAR (Pegawai & Register - SINKRON KE TABEL 'users')
        if is_super:
            row = db.execute("SELECT COUNT(*) FROM pegawai").fetchone()
            total_pegawai  = row["COUNT(*)"] if row else 0
            row = db.execute("SELECT COUNT(*) FROM users").fetchone()
            total_register = row["COUNT(*)"] if row else 0
            # Di bawah ini juga disesuaikan jika superadmin ikut error, pastikan cek kolom yg benar
            row = db.execute("SELECT COUNT(*) FROM users u JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE p.status_aktif=1").fetchone()
            reg_aktif      = row["COUNT(*)"] if row else 0
            row = db.execute("SELECT COUNT(*) FROM pegawai WHERE status_aktif=1").fetchone()
            eligible       = row["COUNT(*)"] if row else 0
            row = db.execute("SELECT COUNT(*) FROM pegawai WHERE status_aktif=0").fetchone()
            inactive_count = row["COUNT(*)"] if row else 0
        else:
            row = db.execute(f"SELECT COUNT(*) FROM pegawai p WHERE {where_clause}", where_params).fetchone()
            total_pegawai  = row["COUNT(*)"] if row else 0
            
            # --- BAGIAN YANG DIUPDATE (u.status_aktif diganti p.status_aktif) ---
            row = db.execute(f"SELECT COUNT(*) FROM users u JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE {where_clause}", where_params).fetchone()
            total_register = row["COUNT(*)"] if row else 0
            row = db.execute(f"SELECT COUNT(*) FROM users u JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE p.status_aktif=1 AND {where_clause}", where_params).fetchone()
            reg_aktif      = row["COUNT(*)"] if row else 0
            # ---------------------------------------------------------------------
            
            row = db.execute(f"SELECT COUNT(*) FROM pegawai p WHERE p.status_aktif=1 AND {where_clause}", where_params).fetchone()
            eligible       = row["COUNT(*)"] if row else 0
            row = db.execute(f"SELECT COUNT(*) FROM pegawai p WHERE p.status_aktif=0 AND {where_clause}", where_params).fetchone()
            inactive_count = row["COUNT(*)"] if row else 0
        # 3) HITUNG NOMINAL & JUMLAH TRANSAKSI
        if is_super:
            row = db.execute("SELECT COUNT(*) FROM transactions WHERE tanggal >= ? AND tanggal < ?", (s_first, s_next)).fetchone()
            trx_count = row["COUNT(*)"] if row else 0
            row = db.execute("SELECT COALESCE(SUM(nominal),0) FROM transactions WHERE status='sukses' AND tanggal >= ? AND tanggal < ?", (s_first, s_next)).fetchone()
            trx_sum = row["COALESCE(SUM(nominal),0)"] if row else 0
            row = db.execute("SELECT COUNT(DISTINCT user_id) FROM transactions WHERE tanggal >= ? AND tanggal < ? AND status IN ('sukses','on-proses')", (s_first, s_next)).fetchone()
            unique_borrowers = row["COUNT(DISTINCT user_id)"] if row else 0
        else:
            row = db.execute(f"SELECT COUNT(*) FROM transactions t JOIN users u ON u.id = t.user_id JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE {where_clause} AND t.tanggal >= ? AND t.tanggal < ?", where_params + [s_first, s_next]).fetchone()
            trx_count = row["COUNT(*)"] if row else 0
            row = db.execute(f"SELECT COALESCE(SUM(t.nominal),0) FROM transactions t JOIN users u ON u.id = t.user_id JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE t.status='sukses' AND {where_clause} AND t.tanggal >= ? AND t.tanggal < ?", where_params + [s_first, s_next]).fetchone()
            trx_sum = row["COALESCE(SUM(t.nominal),0)"] if row else 0
            row = db.execute(f"SELECT COUNT(DISTINCT t.user_id) FROM transactions t JOIN users u ON u.id = t.user_id JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE {where_clause} AND t.tanggal >= ? AND t.tanggal < ? AND t.status IN ('sukses','on-proses')", where_params + [s_first, s_next]).fetchone()
            unique_borrowers = row["COUNT(DISTINCT t.user_id)"] if row else 0

        not_borrowed = max(total_register - unique_borrowers, 0)
        not_registered = max(total_pegawai - total_register, 0)

        # 4) HITUNG ADMIN FEE (PENDAPATAN)
        if is_super:
            row_fee = db.execute("""
                SELECT
                  COALESCE(SUM(CASE WHEN product='reg' THEN admin_fee END), 0) AS fee_reg,
                  COALESCE(SUM(CASE WHEN product='urg' THEN admin_fee END), 0) AS fee_urg
                FROM transactions
                WHERE tanggal >= ?
                  AND tanggal < ?
                  AND status = 'sukses'
                  AND product IN ('reg', 'urg')
            """, (s_first, s_next)).fetchone()
        else:
            row_fee = db.execute(f"""
                SELECT
                  COALESCE(SUM(CASE WHEN t.product='reg' THEN t.admin_fee END), 0) AS fee_reg,
                  COALESCE(SUM(CASE WHEN t.product='urg' THEN t.admin_fee END), 0) AS fee_urg
                FROM transactions t
                JOIN users u ON u.id = t.user_id
                JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
                WHERE t.tanggal >= ?
                  AND t.tanggal < ?
                  AND t.status = 'sukses'
                  AND t.product IN ('reg', 'urg')
                  AND {where_clause}
            """, [s_first, s_next] + where_params).fetchone()

        admin_fee_reg_total = int(row_fee["fee_reg"] or 0)
        admin_fee_urg_total = int(row_fee["fee_urg"] or 0)
        admin_fee_total = admin_fee_reg_total + admin_fee_urg_total

        # 5) DATA PERMINTAAN ON-PROSES (PENDING REG & URG) - FIXED VIA TABLE 'users' & LOWER TRIM
        if is_super:
            row = db.execute("""
                SELECT COUNT(*) FROM transactions 
                WHERE LOWER(TRIM(status)) = 'on-proses'
            """).fetchone()
            pending_count = row["COUNT(*)"] if row else 0

            pending_reg = db.execute("""
                SELECT t.id, t.created_at, t.tanggal, t.nominal, t.status, t.product, t.admin_fee, 
                       p.id_pegawai, COALESCE(u.name, 'Pegawai') AS nama, COALESCE(p.perusahaan, '-') AS perusahaan, COALESCE(p.perusahaan_induk, '') AS perusahaan_induk, t.admin_approved_at, t.admin_approved_by, 
                       COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening, 
                       COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label, 
                       p.no_telp, p.jabatan 
                FROM transactions t 
                LEFT JOIN users u ON u.id = t.user_id 
                LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) 
                WHERE LOWER(TRIM(t.status)) = 'on-proses' AND LOWER(TRIM(t.product)) = 'reg' 
                ORDER BY t.created_at ASC, t.id ASC
            """).fetchall()

            pending_urg = db.execute("""
                SELECT t.id, t.created_at, t.tanggal, t.nominal, t.status, t.product, t.admin_fee, 
                       p.id_pegawai, COALESCE(u.name, 'Pegawai') AS nama, COALESCE(p.perusahaan, '-') AS perusahaan, COALESCE(p.perusahaan_induk, '') AS perusahaan_induk, t.admin_approved_at, t.admin_approved_by, 
                       COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening, 
                       COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label, 
                       p.no_telp, p.jabatan 
                FROM transactions t 
                LEFT JOIN users u ON u.id = t.user_id 
                LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) 
                WHERE LOWER(TRIM(t.status)) = 'on-proses' AND LOWER(TRIM(t.product)) = 'urg' 
                ORDER BY t.created_at ASC, t.id ASC
            """).fetchall()

            recent = db.execute("""
                SELECT t.tanggal, t.created_at, t.nominal, t.admin_fee, t.status, t.product, 
                       u.name AS nama, COALESCE(p.id_pegawai, '') AS id_pegawai,
                       COALESCE(p.perusahaan, '-') AS company
                FROM transactions t 
                JOIN users u ON u.id = t.user_id 
                LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) 
                ORDER BY t.created_at DESC, t.id DESC LIMIT 100
            """,).fetchall()
        else:
            row = db.execute(f"""
                SELECT COUNT(*) 
                FROM transactions t 
                JOIN users u ON u.id = t.user_id 
                JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) 
                WHERE LOWER(TRIM(t.status)) = 'on-proses' AND {where_clause}
            """, where_params).fetchone()
            pending_count = row["COUNT(*)"] if row else 0

            pending_reg = db.execute(f"""
                SELECT t.id, t.created_at, t.tanggal, t.nominal, t.status, t.product, t.admin_fee, 
                       p.id_pegawai, u.name AS nama, p.perusahaan AS perusahaan, COALESCE(p.perusahaan_induk, '') AS perusahaan_induk, t.admin_approved_at, t.admin_approved_by, 
                       COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening, 
                       COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label, 
                       p.no_telp, p.jabatan 
                FROM transactions t 
                JOIN users u ON u.id = t.user_id 
                JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) 
                WHERE LOWER(TRIM(t.status)) = 'on-proses' AND LOWER(TRIM(t.product)) = 'reg' AND {where_clause} 
                ORDER BY t.created_at ASC, t.id ASC
            """, where_params).fetchall()

            pending_urg = db.execute(f"""
                SELECT t.id, t.created_at, t.tanggal, t.nominal, t.status, t.product, t.admin_fee, 
                       p.id_pegawai, u.name AS nama, p.perusahaan AS perusahaan, COALESCE(p.perusahaan_induk, '') AS perusahaan_induk, t.admin_approved_at, t.admin_approved_by, 
                       COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening, 
                       COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label, 
                       p.no_telp, p.jabatan 
                FROM transactions t 
                JOIN users u ON u.id = t.user_id 
                JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) 
                WHERE LOWER(TRIM(t.status)) = 'on-proses' AND LOWER(TRIM(t.product)) = 'urg' AND {where_clause} 
                ORDER BY t.created_at ASC, t.id ASC
            """, where_params).fetchall()

            recent = db.execute(f"""
                SELECT t.tanggal, t.created_at, t.nominal, t.admin_fee, t.status, t.product, 
                       u.name AS nama, p.perusahaan AS company, COALESCE(p.id_pegawai, '') AS id_pegawai 
                FROM transactions t 
                JOIN users u ON u.id = t.user_id 
                JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) 
                WHERE {where_clause} 
                ORDER BY t.created_at DESC, t.id DESC LIMIT 100
            """, where_params).fetchall()

        # 6) DATA KPI SIKLUS GAJI PEGAWAI
        peg_where_active = f"AND {where_clause}"
        row = db.execute(f"SELECT COUNT(*) FROM pegawai p WHERE p.status_aktif=1 AND COALESCE(p.siklus_gaji,'A')='A' {peg_where_active}", where_params).fetchone()
        cycle_a = row["COUNT(*)"] if row else 0
        row = db.execute(f"SELECT COUNT(*) FROM pegawai p WHERE p.status_aktif=1 AND COALESCE(p.siklus_gaji,'A')='B' {peg_where_active}", where_params).fetchone()
        cycle_b = row["COUNT(*)"] if row else 0
        row = db.execute(f"SELECT COUNT(*) FROM pegawai p WHERE p.status_aktif=1 AND COALESCE(p.siklus_gaji,'A')='C' {peg_where_active}", where_params).fetchone()
        cycle_c = row["COUNT(*)"] if row else 0
        row = db.execute(f"SELECT COUNT(*) FROM pegawai p WHERE p.status_aktif=1 AND COALESCE(p.siklus_gaji,'A')='D' {peg_where_active}", where_params).fetchone()
        cycle_d = row["COUNT(*)"] if row else 0

        # 7) GRAFIK HARIAN (CHART DATA)
        if is_super:
            chart_data = db.execute("SELECT substr(tanggal, 9, 2) AS hari, SUM(nominal) AS total FROM transactions WHERE status='sukses' AND tanggal >= ? AND tanggal < ? GROUP BY hari ORDER BY hari", (s_first, s_next)).fetchall()
        else:
            chart_data = db.execute(f"SELECT substr(t.tanggal, 9, 2) AS hari, SUM(t.nominal) AS total FROM transactions t JOIN users u ON u.id = t.user_id JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE t.status='sukses' AND t.tanggal >= ? AND t.tanggal < ? AND {where_clause} GROUP BY hari ORDER BY hari", [s_first, s_next] + where_params).fetchall()
        chart_labels = [r["hari"] for r in chart_data]
        chart_values = [r["total"] for r in chart_data]

        # 8) GRAFIK TREN 6 BULAN (TREND DATA)
        trend_months = [add_months(trend_start, i).strftime("%Y-%m") for i in range(6)]
        trend_map = {m: 0 for m in trend_months}
        if is_super:
            trend_rows = db.execute("SELECT periode, COALESCE(SUM(nominal),0) AS total FROM transactions WHERE status='sukses' AND periode >= ? GROUP BY periode", (trend_start_key,)).fetchall()
        else:
            trend_rows = db.execute(f"SELECT t.periode, COALESCE(SUM(t.nominal),0) AS total FROM transactions t JOIN users u ON u.id = t.user_id JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE t.status='sukses' AND t.periode >= ? AND {where_clause} GROUP BY t.periode", [trend_start_key] + where_params).fetchall()
        for r in trend_rows:
            if r["periode"] in trend_map:
                trend_map[r["periode"]] = int(r["total"] or 0)
        trend_labels = trend_months
        trend_values = [trend_map[m] for m in trend_months]

        # 9) COHORT ANALYSIS ROWS
        cohort_rows = []
        if is_super:
            cohort_activity = db.execute("SELECT user_id, periode FROM transactions WHERE status IN ('sukses','on-proses') AND periode >= ?", (trend_start_key,)).fetchall()
        else:
            cohort_activity = db.execute(f"SELECT t.user_id, t.periode FROM transactions t JOIN users u ON u.id = t.user_id JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) WHERE t.status IN ('sukses','on-proses') AND t.periode >= ? AND {where_clause}", [trend_start_key] + where_params).fetchall()
        
        first_period = {}
        activity_set = set()
        for r in cohort_activity:
            uid = r["user_id"]
            per = r["periode"]
            activity_set.add((uid, per))
            if uid not in first_period or per < first_period[uid]:
                first_period[uid] = per

        for idx, cohort in enumerate(trend_months[:-1]):
            users = [u for u, p in first_period.items() if p == cohort]
            total = len(users)
            next_month = trend_months[idx + 1]
            repeat = sum(1 for u in users if (u, next_month) in activity_set)
            rate = int(round((repeat / total) * 100)) if total else 0
            cohort_rows.append({
                "cohort": cohort,
                "total": total,
                "repeat": repeat,
                "rate": rate
            })

        # SIMPAN HASIL KE CACHE
        set_cache(
            cache_key,
            (
                transactions, recent, total_pegawai, total_register, reg_aktif, eligible,
                trx_count, trx_sum, unique_borrowers, not_borrowed, not_registered,
                admin_fee_reg_total, admin_fee_urg_total, admin_fee_total,
                pending_count, pending_reg, pending_urg,
                cycle_a, cycle_b, cycle_c, cycle_d, inactive_count,
                chart_labels, chart_values, trend_labels, trend_values, cohort_rows,
            ),
            ttl_seconds=10,
        )

    # 🎉 Kirim data final yang akurat ke file HTML admin_dashboard.html
    # ===== 2-LAYER APPROVAL: tandai tahap approval tiap antrian + segarkan dari DB (lepas dari cache 10 dtk) =====
    pending_reg = decorate_pending_tx(db, pending_reg)
    pending_urg = decorate_pending_tx(db, pending_urg)
    pending_count = len(pending_reg) + len(pending_urg)
    pending_admin_count = sum(1 for t in (pending_reg + pending_urg) if t["stage"] == "menunggu_admin")

    weekly_cutoff_counts = get_weekly_cutoff_counts(db, where_clause, where_params)
    return render_template(
        "admin_dashboard.html",
        recent=recent,
        enabled_products=enabled_products,
        pending_admin_count=pending_admin_count,
        transactions=transactions,
        mk=mk,
        account_name=account_name,
        avatar_url=avatar_url,
        total_pegawai=total_pegawai,
        total_register=total_register,
        not_registered=not_registered,
        reg_aktif=reg_aktif,
        eligible=eligible,
        trx_count=trx_count,
        trx_sum=trx_sum,
        unique_borrowers=unique_borrowers,
        not_borrowed=not_borrowed,
        admin_fee_reg_total=admin_fee_reg_total,
        admin_fee_urg_total=admin_fee_urg_total,
        admin_fee_total=admin_fee_total,
        pending_count=pending_count,
        pending_reg=pending_reg,
        pending_urg=pending_urg,
        cycle_a=cycle_a,
        cycle_b=cycle_b,
        cycle_c=cycle_c,
        cycle_d=cycle_d,
        weekly_cutoff_counts=weekly_cutoff_counts,
        inactive_count=inactive_count,
        chart_labels=chart_labels,
        chart_values=chart_values,
        trend_labels=trend_labels,
        trend_values=trend_values,
        cohort_rows=cohort_rows,
    )

# =========================================================
# ==================== ADMIN SETTING ======================
# =========================================================
@bp.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    # 🔥 Pastikan yang akses minimal memiliki role admin
    ret = require_admin()
    if ret:
        return ret

    db = get_db()
    adm = None
    
    # Ambil data admin yang sedang login dari session
    if session.get("admin_id"):
        adm = db.execute(
            "SELECT id, name, email, password_hash, role FROM admins WHERE id=?", 
            (session["admin_id"],)
        ).fetchone()
        
    if not adm and session.get("admin_email"):
        adm = db.execute(
            "SELECT id, name, email, password_hash, role FROM admins WHERE LOWER(email)=?", 
            (session["admin_email"].lower(),)
        ).fetchone()

    # Jika session bermasalah dan tidak ada data admin
    env_account = session.get("auth_source") == "env_admin" and not adm
    if env_account:
        adm = {"email": session.get("admin_email"), "name": session.get("admin_name"), "password_hash": ""}
    if not adm:
        flash("Data sesi admin tidak valid. Silakan login kembali.", "error")
        return redirect(url_for("web.login"))

    # Context sederhana khusus untuk admin biasa (tanpa PPN & global force limit)
    def settings_context():
        return {
            "admin": adm,
            "is_superadmin": False,
            "env_account": env_account
        }

    if request.method == "POST":
        if env_account:
            flash("Password akun ENV dikelola melalui konfigurasi VPS.", "info")
            return redirect(url_for("web.admin_settings"))
        form_type = request.form.get("form_type") or "password"
        
        # Keamanan tambahan: Tolak jika admin biasa mencoba menembak form_type milik superadmin
        if form_type in ["ppn", "runtime_force_limit"]:
            flash("Anda tidak memiliki hak akses untuk mengubah pengaturan ini.", "error")
            return redirect(url_for("web.admin_settings"))

        # Proses Ubah Password
        old_pw = request.form.get("old_password") or ""
        new_pw = request.form.get("new_password") or ""
        new_pw2 = request.form.get("new_password2") or ""

        # Verifikasi password lama via Hash DB
        if not check_password_hash(adm["password_hash"], old_pw):
            flash("Password lama tidak cocok.", "error")
            return render_template("admin_settings.html", **settings_context())

        # Validasi minimal 6 karakter
        if not password_ok(new_pw):
            flash("Password baru minimal 6 karakter.", "error")
            return render_template("admin_settings.html", **settings_context())

        # Validasi kesamaan konfirmasi password
        if new_pw != new_pw2:
            flash("Konfirmasi password baru tidak cocok.", "error")
            return render_template("admin_settings.html", **settings_context())

        # Eksekusi update password baru ke database
        new_hash = generate_password_hash(new_pw)
        cursor = db.execute("UPDATE admins SET password_hash=? WHERE id=? AND password_hash=?",
                            (new_hash, adm["id"], adm["password_hash"]))
        db.commit()
        if cursor.rowcount != 1:
            session.clear()
            flash("Password akun berubah saat diproses. Silakan login kembali.", "error")
            return redirect(url_for("web.login"))
        session["auth_version"] = credential_version(new_hash)

        flash("Password Anda berhasil diperbarui.", "success")
        return redirect(url_for("web.admin_dashboard"))

    # Render halaman setting khusus admin biasa
    return render_template("admin_settings.html", **settings_context())

@bp.get("/admin/riwayat")
def admin_riwayat():
    ret = require_admin()
    if ret:
        return ret

    db = get_db()

    # 🏢 AMBIL DATA DARI SESSION (Sekarang udah disamain persis kuncinya, bro!)
    admin_company = session.get("company") # 🟢 Pakai "company", bukan "admin_company"
    admin_email = session.get("admin_email", "").lower() or session.get("email", "").lower()
    
    # 💡 DETEKSI ROLE (Copas rumus sakti dari dashboard lu)
    is_super = bool(session.get("is_superadmin"))
    
    # PT Windu Karya selalu dianggap induk/holding (lihat _is_parent_company)
    is_parent = bool(admin_company) and not is_super and _is_parent_company(db, admin_company)

    # Aturan main skope data filter perusahaan (Rumus dashboard)
    where_clause, where_params = admin_employee_scope(db)

    # ... ke bawahnya sama (query SQL pakai JOIN murni + params) ...

    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    product = (request.args.get("product") or "").strip()
    f_company = (request.args.get("company") or "").strip()
    f_project = (request.args.get("project") or "").strip()
    start_raw = (request.args.get("start") or "").strip()
    end_raw = (request.args.get("end") or "").strip()

    today = business_today()
    default_start = add_months(date(today.year, today.month, 1), -5)
    default_end = today

    def parse_date(raw, fallback):
        if not raw:
            return fallback
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except Exception:
            return None

    start_dt = parse_date(start_raw, default_start)
    end_dt = parse_date(end_raw, default_end)
    if start_dt is None or end_dt is None:
        flash("Tanggal filter tidak valid. Gunakan format YYYY-MM-DD.", "error")
        start_dt, end_dt = default_start, default_end

    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

   # 1. Query Utama (Standar & Bersih)
    sql = f"""
        SELECT t.id, t.tanggal, t.periode, t.nominal, t.admin_fee, t.status, t.product,
               t.keterangan, t.created_at,
               u.name AS nama, u.email AS email_user,
               COALESCE(p.perusahaan_induk, '') AS company,
               COALESCE(p.perusahaan, '') AS project,
               COALESCE(p.id_pegawai,'') AS id_pegawai,
               COALESCE(p.jabatan,'') AS jabatan,
               COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
               COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        JOIN pegawai p ON LOWER(TRIM(p.email)) = LOWER(TRIM(u.email))
        WHERE t.tanggal >= ? AND t.tanggal <= ? AND {where_clause}
    """
    params = [start_dt.isoformat(), end_dt.isoformat()] + where_params

    # 2. Logic Fitur Pencarian (Gunakan += biar nempel di belakang WHERE utama)
    if q:
        sql += """ AND (
            LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ?
            OR LOWER(COALESCE(p.id_pegawai,'')) LIKE ?
            OR LOWER(COALESCE(p.perusahaan,'')) LIKE ?
            OR LOWER(COALESCE(p.jabatan,'')) LIKE ?
            OR LOWER(COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '')) LIKE ?
        )"""
        q_like = f"%{q.lower()}%"
        params.extend([q_like, q_like, q_like, q_like, q_like, q_like])

    if is_super and f_company:
        sql += " AND LOWER(TRIM(COALESCE(p.perusahaan_induk,''))) = ?"
        params.append(f_company.lower())
    if f_project:
        sql += " AND LOWER(TRIM(COALESCE(p.perusahaan,''))) = ?"
        params.append(f_project.lower())

    if status:
        sql += " AND t.status = ?"
        params.append(status)

    if product:
        sql += " AND t.product = ?"
        params.append(product)

    sql += " ORDER BY t.tanggal DESC, t.id DESC"

    rows = db.execute(sql, params).fetchall()

    total_nom = sum(int(r["nominal"] or 0) for r in rows if r["status"] == "sukses")
    total_admin = sum(int(r["admin_fee"] or 0) for r in rows if r["status"] == "sukses")

    project_sql = "SELECT DISTINCT TRIM(perusahaan) AS project FROM pegawai WHERE perusahaan IS NOT NULL AND TRIM(perusahaan) <> ''"
    project_params = []
    if not is_super and not admin_company:
        project_sql += " AND 1=0"
    elif not is_super and admin_company:
        project_sql += " AND LOWER(TRIM(perusahaan_induk)) = LOWER(TRIM(?))" if is_parent else " AND LOWER(TRIM(perusahaan)) = LOWER(TRIM(?))"
        project_params.append(admin_company)
    elif is_super and f_company:
        project_sql += " AND LOWER(TRIM(perusahaan_induk)) = LOWER(TRIM(?))"
        project_params.append(f_company)
    projects = sorted({r["project"] for r in db.execute(project_sql, project_params).fetchall() if r["project"]})

    return render_template(
        "admin_riwayat.html",
        rows=rows,
        q=q,
        status=status,
        product=product,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        total_nom=total_nom,
        total_admin=total_admin,
        companies=([admin_company] if not is_super and admin_company else []),
        projects=projects,
        f_company=f_company,
        f_project=f_project,
    )

# =========================================================
# ==================== ADMIN export csv ======================
@bp.get("/admin/export")
def admin_export():
    ret = require_admin()
    if ret: return ret

    # Filter opsional
    periode = (request.args.get("periode") or "").strip()   # "YYYY-MM" atau "" = semua
    product = (request.args.get("product") or "").strip()   # "reg"/"urg"/""
    status  = (request.args.get("status") or "").strip()    # "sukses"/"on-proses"/"ditolak"/"dibatalkan"/""

    # Join yang benar: t.user_id -> users.id, lalu cocokkan pegawai via email (LEFT JOIN)
    sql = """
        SELECT t.id, t.tanggal, t.periode,
               u.name   AS pegawai,
               u.email  AS email_user,
               COALESCE(p.id_pegawai,'') AS id_pegawai,
               COALESCE(p.perusahaan,'') AS perusahaan,
               COALESCE(p.jabatan,'')    AS jabatan,
               COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
               COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label,
               t.product, t.nominal, t.admin_fee, t.status, t.keterangan, t.created_at
      FROM transactions t
      JOIN users u         ON u.id = t.user_id
      LEFT JOIN pegawai p  ON LOWER(p.email) = LOWER(u.email)
      WHERE 1=1
    """
    db = get_db()
    scope, params = admin_employee_scope(db)
    sql += f" AND {scope}"
    if periode:
        sql += " AND t.periode = ?"
        params.append(periode)
    if product:
        sql += " AND t.product = ?"
        params.append(product)
    if status:
        sql += " AND t.status = ?"
        params.append(status)
    sql += " ORDER BY t.periode DESC, t.tanggal DESC, t.id DESC"

    rows = db.execute(sql, params).fetchall()

    # Buat CSV in-memory (UTF-8-SIG nyaman di Excel)
    buf = io.StringIO()
    w = SafeCSVWriter(buf)
    w.writerow(["ID","Tanggal","Periode","ID Pegawai","Pegawai","Email","Perusahaan","Jabatan","Tipe Rekening","No_Rek Bank",
                "Produk","Nominal","Admin","Status","Keterangan","Dibuat"])
    for r in rows:
        w.writerow([
            r["id"], r["tanggal"], format_period_label(r["periode"]), r["id_pegawai"], r["pegawai"], r["email_user"],
            r["perusahaan"], r["jabatan"], short_rekening_label(r["rekening_tujuan_label"]), r["no_rekening"], r["product"], r["nominal"], r["admin_fee"],
            r["status"], (r["keterangan"] or ""), r["created_at"]
        ])

    data = buf.getvalue().encode("utf-8-sig")
    from flask import Response
    fname = "export_dana_talangan"
    if periode: fname += f"_{periode}"
    if product: fname += f"_{product}"
    if status:  fname += f"_{status}"
    fname += ".csv"

    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )

# --- Admin: Pegawai CRUD ---
@bp.route("/admin/pegawai", methods=["GET"], endpoint="admin_pegawai")
def admin_pegawai():
    ret = require_admin()
    if ret: return ret

    if not session.get("is_superadmin"):
        ret = require_admin()
        if ret: return ret

    db = get_db()

    # [Tetap Aman] Blok pengecekan/migrasi kolom otomatis bawaan lu
    try:
        db.execute("SELECT id_pegawai FROM pegawai LIMIT 1").fetchone()
    except Exception:
        db.execute("ALTER TABLE pegawai ADD COLUMN id_pegawai TEXT DEFAULT ''")
        db.commit()
    try:
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pegawai_id_pegawai "
            "ON pegawai(id_pegawai) WHERE id_pegawai <> ''"
        )
        db.commit()
    except Exception:
        pass
    try:
        db.execute("SELECT perusahaan FROM pegawai LIMIT 1").fetchone()
    except Exception:
        db.execute("ALTER TABLE pegawai ADD COLUMN perusahaan TEXT DEFAULT ''")
        db.commit()
    try:
        db.execute("SELECT no_rekening FROM pegawai LIMIT 1").fetchone()
    except Exception:
        db.execute("ALTER TABLE pegawai ADD COLUMN no_rekening TEXT DEFAULT ''")
        db.commit()
    try:
        db.execute("SELECT no_rekening_lain FROM pegawai LIMIT 1").fetchone()
    except Exception:
        db.execute("ALTER TABLE pegawai ADD COLUMN no_rekening_lain TEXT DEFAULT ''")
        db.commit()
    try:
        db.execute("SELECT rekening_ewallet FROM pegawai LIMIT 1").fetchone()
    except Exception:
        db.execute("ALTER TABLE pegawai ADD COLUMN rekening_ewallet TEXT DEFAULT ''")
        db.commit()
    try:
        db.execute("SELECT no_telp FROM pegawai LIMIT 1").fetchone()
    except Exception:
        db.execute("ALTER TABLE pegawai ADD COLUMN no_telp TEXT DEFAULT ''")
        db.commit()
    try:
        db.execute("SELECT admin_fee_flat FROM pegawai LIMIT 1").fetchone()
    except Exception:
        db.execute("ALTER TABLE pegawai ADD COLUMN admin_fee_flat INTEGER DEFAULT 15000")
        db.commit()

    # 1. Ambil parameter filter dari frontend
    q = (request.args.get("q") or "").strip()
    f_company = (request.args.get("company") or "").strip() # Filter anak perusahaan (Cakra, dll)
    
    # 🏢 Ambil data identitas admin dari session login
    admin_company = session.get("company") # Nilainya dinamis dari DB admins
    admin_email = session.get("admin_email")
    
    # 💡 DETEKSI ROLE SUPERADMIN (Sama seperti sebelumnya)
    is_super = bool(session.get("is_superadmin"))
    # =========================================================================
    # 🔍 PROSES MERAKIT QUERY SQL PEGAWAI (OTOMATIS & DINAMIS)
    # =========================================================================
    base_sql = """
        SELECT id, COALESCE(id_pegawai,'') AS id_pegawai, nama, email, jabatan, gaji, status_aktif,
               COALESCE(perusahaan,'') AS perusahaan,
               COALESCE(perusahaan_induk,'') AS perusahaan_induk,
               COALESCE(no_rekening,'') AS no_rekening,
               COALESCE(no_rekening_lain,'') AS no_rekening_lain,
               COALESCE(rekening_ewallet,'') AS rekening_ewallet,
               COALESCE(no_telp,'') AS no_telp,
               COALESCE(admin_fee_flat, 15000) AS admin_fee_flat,
               siklus_gaji,
               COALESCE((SELECT a.cutoff_mingguan_aktif FROM admins a
                         WHERE LOWER(TRIM(a.company)) = LOWER(TRIM(pegawai.perusahaan_induk))
                           AND COALESCE(a.cutoff_mingguan_aktif, 0)=1
                         ORDER BY a.id DESC LIMIT 1), 0) AS cutoff_mingguan_aktif,
               (SELECT a.cutoff_hari FROM admins a
                WHERE LOWER(TRIM(a.company)) = LOWER(TRIM(pegawai.perusahaan_induk))
                  AND COALESCE(a.cutoff_mingguan_aktif, 0)=1
                ORDER BY a.id DESC LIMIT 1) AS cutoff_hari,
               created_at
        FROM pegawai
        WHERE 1=1
    """
    params = []
    
    # 🛑 FILTER HAK AKSES OTOMATIS TANPA ELIF HARDCODE
    if is_super:
        # Superadmin bebas melihat semua data tanpa batas
        pass
    elif admin_company:
        # Cek secara live: apakah company si admin ini induk/holding? (PT Windu Karya selalu induk)
        is_parent = _is_parent_company(db, admin_company)

        if is_parent:
            # 🏢 JIKA DIA INDUK (Semi-Admin otomatis): Kunci data berdasarkan induknya
            base_sql += " AND LOWER(TRIM(perusahaan_induk)) = LOWER(TRIM(?))"
            params.append(admin_company)
        else:
            # 🏢 JIKA DIA PT BIASA / ANAK (Admin lokal biasa): Kunci data langsung ke nama PT-nya
            base_sql += " AND LOWER(TRIM(perusahaan)) = LOWER(TRIM(?))"
            params.append(admin_company)
    else:
        # Jika session company kosong, proteksi agar tidak menampilkan data
        base_sql += " AND 1=0"

    # 🔍 FILTER SEARCH BOX
    if q:
        like = f"%{q}%"
        base_sql += """ AND (
            COALESCE(id_pegawai,'') LIKE ? OR nama LIKE ? OR email LIKE ?
            OR jabatan LIKE ? OR COALESCE(perusahaan,'') LIKE ?
            OR COALESCE(no_rekening,'') LIKE ? OR COALESCE(no_rekening_lain,'') LIKE ?
            OR COALESCE(rekening_ewallet,'') LIKE ?
        )"""
        params += [like, like, like, like, like, like, like, like]
        
    # 🎯 FILTER DROPDOWN
    if f_company:
        base_sql += " AND LOWER(TRIM(COALESCE(perusahaan,''))) = ?"
        params.append(f_company.strip().lower())
        
    base_sql += " ORDER BY id DESC"
    rows = db.execute(base_sql, params).fetchall()

    # =========================================================================
    # 🗂️ LOGIK MENAMPILKAN ISI DROPDOWN FILTER SECARA OTOMATIS
    # =========================================================================
    # =========================================================================
    # 🗂️ LOGIK MENAMPILKAN ISI DROPDOWN FILTER SECARA OTOMATIS (FIXED)
    # =========================================================================
    # =========================================================================
    # 🗂️ LOGIK MENAMPILKAN ISI DROPDOWN FILTER SECARA OTOMATIS (SQUASH DUPLICATE)
    # =========================================================================
    company_sql = "SELECT DISTINCT perusahaan FROM pegawai WHERE TRIM(COALESCE(perusahaan,''))<>''"
    company_params = []
    if not is_super:
        company_sql += " AND LOWER(TRIM(perusahaan_induk))=LOWER(TRIM(?))"
        company_params.append(admin_company or "")
    companies = sorted({row["perusahaan"] for row in db.execute(company_sql, company_params).fetchall()}, key=str.lower)
    catalog = list_clients(db) if is_super else [client_by_name(db, admin_company)]
    client_catalog = [{"name": client["name"], "allow_projects": bool(client["allow_projects"]),
                       "projects": [project["name"] for project in list_projects(db, client["id"])]}
                      for client in catalog if client]
    return render_template("admin_pegawai.html", rows=rows, q=q, companies=companies,
                           f_company=f_company, total=len(rows), is_superadmin=is_super,
                           client_catalog=client_catalog, selected_client=request.args.get("client") or "")

@bp.route("/admin/pegawai/add", methods=["POST"], endpoint="admin_pegawai_add")
def admin_pegawai_add():
    if not session.get("is_superadmin"):
        ret = require_admin()
        if ret: return ret

    id_pegawai  = normalize_employee_id(request.form.get("id_pegawai"))
    nama        = (request.form.get("nama") or "").strip()
    email       = (request.form.get("email") or "").strip().lower()
    jabatan     = (request.form.get("jabatan") or "").strip()
    
    # 🏢 Ambil company dari admin yang sedang login
    admin_company = session.get("company")
    db = get_db()
    try:
        perusahaan_induk, perusahaan = employee_assignment(
            db, request.form.get("perusahaan_induk", admin_company or ""),
            request.form.get("perusahaan", ""),
            bool(session.get("is_superadmin")), admin_company,
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("web.admin_pegawai"))

    no_rekening = (request.form.get("no_rekening") or "").strip()
    no_rekening_lain = (request.form.get("no_rekening_lain") or "").strip()
    rekening_ewallet = (request.form.get("rekening_ewallet") or "").strip()
    no_telp     = (request.form.get("no_telp") or "").strip()
    gaji        = parse_int(request.form.get("gaji"), 0)
    status      = 1 if request.form.get("status") == "1" else 0
    siklus      = normalize_siklus(request.form.get("siklus_gaji"), default="B")
    
    if session.get("is_superadmin"):
        # Fee admin WAJIB diinput manual oleh Superadmin (bukan lagi pilihan 15.000 / 17.000)
        admin_fee_flat = parse_admin_fee_flat(request.form.get("admin_fee_flat"))
    else:
        admin_fee_flat = 15000

    if not nama or not valid_email(email) or not 0 <= gaji <= 2_147_483_647:
        flash("Nama/email wajib valid dan gaji harus berada antara 0 sampai 2.147.483.647.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if not id_pegawai or not nama or not email:
        flash("ID pegawai, nama, dan email wajib diisi.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if not employee_id_is_valid(id_pegawai):
        flash(f"ID pegawai maksimal {EMPLOYEE_ID_MAX_LEN} karakter alfanumerik.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if not ewallet_is_valid(rekening_ewallet):
        flash("Rekening e-wallet hanya boleh berisi huruf, angka, dan spasi.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if admin_fee_flat is None:
        flash(f"Admin fee wajib diisi manual (angka 0 - {rupiah_format(ADMIN_FEE_FLAT_MAX)}).", "error")
        return redirect(url_for("web.admin_pegawai"))

    db = get_db()

    exists = db.execute(
        "SELECT 1 FROM pegawai WHERE id_pegawai=? OR LOWER(email)=?",
        (id_pegawai, email.lower()),
    ).fetchone()

    if exists:
        flash("ID pegawai atau email sudah terdaftar.", "error")
        return redirect(url_for("web.admin_pegawai"))

    # 🚀 JALANKAN INSERT DATA BESERTA KOLOM perusahaan_induk HASIL OTOMATISASI BACKEND
    db.execute("""
        INSERT INTO pegawai (
            id_pegawai, nama, email, jabatan, gaji, status_aktif, 
            perusahaan, perusahaan_induk, no_rekening, no_rekening_lain, 
            rekening_ewallet, no_telp, siklus_gaji, admin_fee_flat, created_at
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        id_pegawai, nama, email, jabatan, gaji, status, 
        perusahaan, perusahaan_induk, no_rekening, no_rekening_lain, 
        rekening_ewallet, no_telp, siklus, admin_fee_flat, 
        datetime.now().isoformat(timespec="seconds")
    ))
    db.commit()

    flash("Pegawai ditambahkan.", "success")
    return redirect(url_for("web.admin_pegawai"))


# =========================================================================================
# ===== IMPORT PEGAWAI MASSAL LEWAT EXCEL (.xlsx) =====
# =========================================================================================

IMPORT_MAX_ROWS = 500          # batas baris per file, jaga-jaga dari file raksasa/typo massal
IMPORT_MAX_FILE_BYTES = 5 * 1024 * 1024   # 5 MB
IMPORT_STATUS_AKTIF_WORDS = {"1", "aktif", "active", "ya", "yes"}
IMPORT_STATUS_NONAKTIF_WORDS = {"0", "nonaktif", "tidak aktif", "inactive", "tidak", "no"}

def _import_cell(row, i):
    """Ambil cell ke-i (0-based) dari tuple baris openpyxl, aman kalau barisnya lebih pendek dari header."""
    return row[i] if i < len(row) else None

def _import_str(v):
    return str(v).strip() if v is not None else ""

@bp.get("/admin/pegawai/import/template")
def admin_pegawai_import_template():
    ret = require_admin()
    if ret:
        return ret
    from flask import send_file
    db = get_db()
    is_super = bool(session.get("is_superadmin"))
    catalog = list_clients(db) if is_super else [client_by_name(db, session.get("company"))]
    catalog = [client for client in catalog if client]
    wb = Workbook()
    ws = wb.active
    ws.title = "Import Pegawai"
    headers = ["ID Pegawai*", "Nama*", "Email*", "Jabatan", "Project",
               "No Rekening Bank Utama", "No Rekening Bank Lain", "Rekening E-Wallet", "No Telp",
               "Gaji Pokok", "Status Aktif", "Siklus Gaji", "Admin Fee", "Perusahaan*"]
    for index, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=index, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
        ws.column_dimensions[cell.column_letter].width = max(18, len(header) + 2)
    company = catalog[0] if catalog else None
    projects = list_projects(db, company["id"]) if company and company["allow_projects"] else []
    example = ["EMP001", "Contoh Nama", "contoh@email.com", "Staff", projects[0]["name"] if projects else "",
               "1234567890", "", "", "081234567890", 5000000, "Aktif", "B", "",
               company["name"] if company else ""]
    ws.append(example)
    for cells, formula in (("K2:K1000", '"Aktif,Nonaktif"'), ("L2:L1000", '"A,B,C,D"')):
        validation = DataValidation(type="list", formula1=formula, allow_blank=True)
        ws.add_data_validation(validation)
        validation.add(cells)
    helper = wb.create_sheet("_master_client")
    helper.append(["Perusahaan", "Project", "Milik PT"])
    all_projects = []
    for index, client in enumerate(catalog, start=2):
        helper.cell(row=index, column=1, value=client["name"])
        if client["allow_projects"]:
            for project in list_projects(db, client["id"]):
                all_projects.append((project["name"], client["name"]))
    for index, (project, parent) in enumerate(all_projects, start=2):
        helper.cell(row=index, column=2, value=project)
        helper.cell(row=index, column=3, value=parent)
    if catalog:
        validation = DataValidation(type="list", formula1=f'INDIRECT("_master_client!A2:A{len(catalog)+1}")', allow_blank=False)
        ws.add_data_validation(validation)
        validation.add("N2:N1000")
    if all_projects:
        validation = DataValidation(type="list", formula1=f'INDIRECT("_master_client!B2:B{len(all_projects)+1}")', allow_blank=True)
        ws.add_data_validation(validation)
        validation.add("E2:E1000")
    helper.sheet_state = "hidden"
    instructions = wb.create_sheet("Petunjuk")
    instructions.column_dimensions["A"].width = 110
    for instruction in [
        "IMPORT PEGAWAI: satu client = satu PT. Jangan ubah urutan 14 kolom.",
        "Hapus atau timpa baris contoh sebelum upload. ID, nama, email, dan perusahaan wajib diisi.",
        "Kolom E adalah Project; kolom N adalah Perusahaan. Superadmin wajib mengisi kolom N.",
        "Admin PT terkunci pada perusahaan akunnya. Kolom N boleh kosong atau sama dengan PT akun.",
        "Allow Projects: kolom E wajib memilih project terdaftar milik PT pada kolom N.",
        "Disallow Projects: kolom E wajib kosong. Daftarkan project lewat Kelola Projects terlebih dahulu.",
        "Dropdown project Superadmin memuat semua project. Server memvalidasi pemilik project per PT.",
        "ID pegawai: alfanumerik maksimal 16 karakter. Siklus A/B/C/D. Status Aktif/Nonaktif.",
        "Admin Fee hanya untuk Superadmin: angka 0 sampai 1.000.000. Admin PT memakai fee default.",
        f"Maksimal {IMPORT_MAX_ROWS} baris. Baris valid disimpan; baris gagal dilaporkan.",
        "Template lama tanpa kolom N tidak dapat dipakai Superadmin; unduh template terbaru.",
    ]:
        instructions.append([instruction])
    for sheet in wb.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name="Template_Import_Pegawai.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.post("/admin/pegawai/import")
def admin_pegawai_import():
    ret = require_admin()
    if ret: return ret

    limited = rate_limit("employee-import", str(session.get("admin_id") or session.get("admin_email")), limit=5, window=60)
    if limited:
        return limited
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Pilih file Excel (.xlsx) terlebih dahulu.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if not file.filename.lower().endswith(".xlsx"):
        flash("Format file harus .xlsx (Excel). Format lain (.xls/.csv) belum didukung.", "error")
        return redirect(url_for("web.admin_pegawai"))

    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > IMPORT_MAX_FILE_BYTES:
        flash(f"Ukuran file maksimal {IMPORT_MAX_FILE_BYTES // (1024*1024)} MB.", "error")
        return redirect(url_for("web.admin_pegawai"))

    try:
        with zipfile.ZipFile(file.stream) as archive:
            if len(archive.infolist()) > 2000 or sum(item.file_size for item in archive.infolist()) > 50 * 1024 * 1024:
                raise ValueError("File Excel terlalu besar setelah diekstrak")
        file.seek(0)
        wb = load_workbook(file, data_only=True, read_only=True)
        try:
            ws = wb.active
            if ws.max_column and ws.max_column > 30:
                raise ValueError("Jumlah kolom Excel tidak valid")
            if ws.max_row and ws.max_row > (IMPORT_MAX_ROWS + 1) * 5:
                raise ValueError("Terlalu banyak baris fisik pada file Excel")
            raw_rows = []
            for physical_row, row in enumerate(ws.iter_rows(min_row=2, max_col=14, values_only=True), start=2):
                if physical_row > (IMPORT_MAX_ROWS + 1) * 5:
                    raise ValueError("Terlalu banyak baris fisik pada file Excel")
                if any(value not in (None, "") for value in row):
                    raw_rows.append(row)
                if len(raw_rows) > IMPORT_MAX_ROWS:
                    raise ValueError("Jumlah baris melebihi batas import")
        finally:
            wb.close()
    except Exception:
        flash("File tidak bisa dibaca. Pastikan formatnya .xlsx yang valid dan tidak corrupt.", "error")
        return redirect(url_for("web.admin_pegawai"))

    # buang baris yang benar-benar kosong semua (sering muncul di akhir file Excel)
    rows = [r for r in raw_rows if any(c not in (None, "") for c in r)]
    if not rows:
        flash("File tidak berisi data pegawai (cek apakah baris data ada di bawah baris judul).", "error")
        return redirect(url_for("web.admin_pegawai"))
    if len(rows) > IMPORT_MAX_ROWS:
        flash(f"Maksimal {IMPORT_MAX_ROWS} baris per import. File Anda berisi {len(rows)} baris.", "error")
        return redirect(url_for("web.admin_pegawai"))

    db = get_db()
    admin_company = session.get("company")
    is_super = bool(session.get("is_superadmin"))


    existing = db.execute("SELECT id_pegawai, email FROM pegawai").fetchall()
    existing_ids = {(r["id_pegawai"] or "").strip().upper() for r in existing if r["id_pegawai"]}
    existing_emails = {(r["email"] or "").strip().lower() for r in existing if r["email"]}

    seen_ids, seen_emails = set(), set()
    valid_rows, errors = [], []
    now_str = datetime.now().isoformat(timespec="seconds")

    for i, row in enumerate(rows, start=2):   # baris 2 = baris data pertama (baris 1 = header)
        id_pegawai = normalize_employee_id(_import_str(_import_cell(row, 0)))
        nama = _import_str(_import_cell(row, 1))
        email = _import_str(_import_cell(row, 2)).lower()
        jabatan = _import_str(_import_cell(row, 3))
        perusahaan_cell = _import_str(_import_cell(row, 4))
        no_rekening = _import_str(_import_cell(row, 5))
        no_rekening_lain = _import_str(_import_cell(row, 6))
        rekening_ewallet = _import_str(_import_cell(row, 7))
        no_telp = _import_str(_import_cell(row, 8))
        gaji = parse_int(_import_cell(row, 9), 0)
        status_raw = _import_str(_import_cell(row, 10)).lower()
        siklus = normalize_siklus(_import_str(_import_cell(row, 11)) or "B", default="B")
        fee_raw = _import_cell(row, 12)

        row_errors = []

        if not id_pegawai or not nama or not email:
            row_errors.append("ID Pegawai/Nama/Email wajib diisi")
        elif not employee_id_is_valid(id_pegawai):
            row_errors.append(f"ID Pegawai maksimal {EMPLOYEE_ID_MAX_LEN} karakter alfanumerik")
        if email and not valid_email(email):
            row_errors.append("Format email tidak valid")
        if not 0 <= gaji <= 2_147_483_647:
            row_errors.append("Gaji harus berada antara 0 sampai 2.147.483.647")
        if rekening_ewallet and not ewallet_is_valid(rekening_ewallet):
            row_errors.append("Rekening e-wallet hanya boleh huruf, angka, dan spasi")

        status = 0 if status_raw in IMPORT_STATUS_NONAKTIF_WORDS else 1  # default Aktif kalau kosong/tak dikenali

        company_cell = _import_str(_import_cell(row, 13)) if is_super else (admin_company or "")
        if not is_super and len(row) > 13 and _import_str(row[13]) and name_key(_import_str(row[13])) != name_key(admin_company):
            row_errors.append("Perusahaan pada file tidak sesuai perusahaan akun admin")
        try:
            perusahaan_induk, perusahaan = employee_assignment(
                db, company_cell, perusahaan_cell, is_super, admin_company,
            )
        except ValueError as error:
            row_errors.append(str(error))
            perusahaan_induk, perusahaan = company_cell, perusahaan_cell

        if is_super:
            fee_value = parse_admin_fee_flat(fee_raw)
            if fee_value is None:
                if fee_raw not in (None, ""):
                    row_errors.append(f"Admin fee tidak valid (0 - {rupiah_format(ADMIN_FEE_FLAT_MAX)})")
                fee_value = 15000
        else:
            fee_value = 15000   # Admin biasa: kolom Admin Fee di file selalu diabaikan

        id_key = id_pegawai.strip().upper()
        email_key = email.strip().lower()
        if id_key and (id_key in existing_ids or id_key in seen_ids):
            row_errors.append("ID Pegawai duplikat (sudah ada di database atau di baris lain file ini)")
        if email_key and (email_key in existing_emails or email_key in seen_emails):
            row_errors.append("Email duplikat (sudah ada di database atau di baris lain file ini)")

        if row_errors:
            errors.append((i, "; ".join(row_errors)))
            continue

        seen_ids.add(id_key)
        seen_emails.add(email_key)
        valid_rows.append((
            id_pegawai, nama, email, jabatan, gaji, status, perusahaan, perusahaan_induk,
            no_rekening, no_rekening_lain, rekening_ewallet, no_telp, siklus, fee_value, now_str,
        ))

    if valid_rows:
        db.executemany("""
            INSERT INTO pegawai (
                id_pegawai, nama, email, jabatan, gaji, status_aktif,
                perusahaan, perusahaan_induk, no_rekening, no_rekening_lain,
                rekening_ewallet, no_telp, siklus_gaji, admin_fee_flat, created_at
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, valid_rows)
        db.commit()

    total = len(valid_rows) + len(errors)
    if valid_rows and not errors:
        flash(f'Import selesai: {len(valid_rows)} pegawai berhasil ditambahkan.', "success")
    elif valid_rows and errors:
        flash(f'Import selesai sebagian: {len(valid_rows)} dari {total} baris berhasil, '
              f'{len(errors)} baris gagal (detail di bawah).', "warning")
    else:
        flash(f'Import gagal total: semua {len(errors)} baris bermasalah, tidak ada yang disimpan.', "error")

    for row_no, msg in errors[:20]:
        flash(f"Baris {row_no}: {msg}", "error")
    if len(errors) > 20:
        flash(f"...dan {len(errors) - 20} baris lain juga gagal (perbaiki lalu upload ulang khusus baris itu).", "error")

    return redirect(url_for("web.admin_pegawai"))


@bp.route("/admin/pegawai/<int:pid>/update", methods=["POST"], endpoint="admin_pegawai_update")
def admin_pegawai_update(pid):
    if not session.get("is_superadmin"):
        ret = require_admin()
        if ret: return ret
    db = get_db()
    
    id_pegawai = normalize_employee_id(request.form.get("id_pegawai"))
    nama       = (request.form.get("nama") or "").strip()
    email      = (request.form.get("email") or "").strip().lower()
    jabatan    = (request.form.get("jabatan") or "").strip()
    
    # 🏢 Ambil company dari admin yang sedang login
    admin_company = session.get("company")
    db = get_db()
    existing = db.execute("SELECT * FROM pegawai WHERE id=?", (pid,)).fetchone()
    if not existing:
        return "Pegawai tidak ditemukan", 404
    try:
        perusahaan_induk, perusahaan = employee_assignment(
            db, request.form.get("perusahaan_induk", existing["perusahaan_induk"]),
            request.form.get("perusahaan", existing["perusahaan"]),
            bool(session.get("is_superadmin")), admin_company, existing=existing,
        )
    except ValueError as error:
        flash(str(error), "error")
        return redirect(url_for("web.admin_pegawai"))

    no_rekening = (request.form.get("no_rekening") or "").strip()
    no_rekening_lain = (request.form.get("no_rekening_lain") or "").strip()
    rekening_ewallet = (request.form.get("rekening_ewallet") or "").strip()
    no_telp    = (request.form.get("no_telp") or "").strip()
    gaji       = parse_int(request.form.get("gaji"), 0)
    status     = 1 if request.form.get("status") == "1" else 0
    siklus     = normalize_siklus(request.form.get("siklus_gaji"), default="A")

    current_data = db.execute("SELECT admin_fee_flat FROM pegawai WHERE id=?", (pid,)).fetchone()
    current_fee = current_data["admin_fee_flat"] if current_data and current_data["admin_fee_flat"] is not None else 15000
    if session.get("is_superadmin"):
        # Fee admin diinput manual. Kolom kosong -> pertahankan fee sekarang; terisi tapi di luar batas -> error.
        raw_fee = request.form.get("admin_fee_flat")
        admin_fee_flat = parse_admin_fee_flat(raw_fee)
        if admin_fee_flat is None:
            if (raw_fee or "").strip():
                flash(f"Admin fee tidak valid (angka 0 - {rupiah_format(ADMIN_FEE_FLAT_MAX)}).", "error")
                return redirect(url_for("web.admin_pegawai"))
            admin_fee_flat = current_fee
    else:
        admin_fee_flat = current_fee

    if not nama or not valid_email(email) or not 0 <= gaji <= 2_147_483_647:
        flash("Nama/email wajib valid dan gaji harus berada antara 0 sampai 2.147.483.647.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if not id_pegawai:
        flash("ID pegawai wajib diisi.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if not employee_id_is_valid(id_pegawai):
        flash(f"ID pegawai maksimal {EMPLOYEE_ID_MAX_LEN} karakter alfanumerik.", "error")
        return redirect(url_for("web.admin_pegawai"))
    if not ewallet_is_valid(rekening_ewallet):
        flash("Rekening e-wallet hanya boleh berisi huruf, angka, dan spasi.", "error")
        return redirect(url_for("web.admin_pegawai"))

    row = db.execute("SELECT email FROM pegawai WHERE id=?", (pid,)).fetchone()
    old_email = (row["email"] or "").strip().lower() if row else ""

# 1. Kita hapus LOWER(nama) dari query, jadi cuma ngecek EMAIL aja di awal
    exists_sql = "SELECT id FROM pegawai WHERE (LOWER(email)=?"
    exists_params = [email.lower()]  # Pastikan email juga di-lower biar adil

    # 2. Pengecekan ID Pegawai tetep jalan kalau di-input
    if id_pegawai:
        exists_sql += " OR COALESCE(id_pegawai,'')=?"
        exists_params.append(id_pegawai)

    # 3. Tutup kurung query-nya dan pastikan TIDAK mengecek ID diri sendiri (saat edit)
    exists_sql += ") AND id<>?"
    exists_params.append(pid)

    exists = db.execute(exists_sql, exists_params).fetchone()
    if exists:
        # 4. Pesan flash-nya disesuaikan (kata 'nama' dibuang biar ga bingung)
        flash("ID pegawai atau email sudah terdaftar.", "error")
        return redirect(url_for("web.admin_pegawai"))

    # 🚀 JALANKAN UPDATE MASTER PEGAWAI BESERTA KOLOM perusahaan_induk
    db.execute("""UPDATE pegawai
                  SET id_pegawai=?, nama=?, email=?, jabatan=?, gaji=?, status_aktif=?, perusahaan=?, perusahaan_induk=?, no_rekening=?, no_rekening_lain=?, rekening_ewallet=?, no_telp=?, siklus_gaji=?, admin_fee_flat=?
                  WHERE id=?""",
               (id_pegawai, nama, email, jabatan, gaji, status, perusahaan, perusahaan_induk, no_rekening, no_rekening_lain, rekening_ewallet, no_telp, siklus, admin_fee_flat, pid))

    db.execute(
        "UPDATE user_accounts SET status_aktif=?, email=? WHERE pegawai_id=?",
        (status, email, pid),
    )
    if old_email and old_email != email:
        db.execute("UPDATE users SET email=? WHERE LOWER(email)=?", (email, old_email))
    db.execute("UPDATE users SET name=?, gaji=? WHERE LOWER(email)=?", (nama, gaji, email))

    db.commit()
    flash("Data pegawai diperbarui dan disinkron ke akun formal.", "success")
    return redirect(url_for("web.admin_pegawai"))


@bp.route("/admin/pegawai/<int:pid>/delete", methods=["POST"])
@serialized_employee_write
def admin_pegawai_delete(pid: int):
    if not session.get("is_superadmin"):
        ret = require_admin()
        if ret: return ret
    db = get_db()
    
    # 💡 SATPAM VALIDASI KEPEMILIKAN DATA
    is_super = session.get("is_superadmin")
    admin_company = (session.get("company") or "").strip()
    
    # Cek dulu data pegawainya ada atau enggak
    row = db.execute("SELECT * FROM pegawai WHERE id=?", (pid,)).fetchone()
    if not row:
        flash("Pegawai tidak ditemukan.", "error")
        return redirect(url_for("web.admin_pegawai"))
        
    peg_email = name_key(row["email"])
    if not is_super and (not name_key(admin_company) or name_key(row["perusahaan_induk"]) != name_key(admin_company)):
        flash("Akses ditolak! Pegawai ini bukan milik perusahaan Anda.", "error")
        return redirect(url_for("web.admin_pegawai"))
    has_history = db.execute("""SELECT 1 FROM transactions t JOIN users u ON u.id=t.user_id
        WHERE LOWER(u.email)=? LIMIT 1""", (peg_email,)).fetchone()
    if has_history:
        flash("Pegawai memiliki riwayat transaksi. Nonaktifkan pegawai agar riwayat finansial tetap tersimpan.", "error")
        return redirect(url_for("web.admin_pegawai"))

    # =========================================================================
    # ---- Blok Proses Sapu Bersih Data ----
    # =========================================================================
    try:
        # ---- 1) Arsipkan snapshot pegawai ----
        # ---- 1) Arsipkan snapshot pegawai (FIXED SNAPSHOT KOSONG) ----
        
        # Bongkar data objek row MySQL menjadi dict murni Python
        try:
            if isinstance(row, dict):
                # Kalau drivernya udah dict murni
                snap_dict = dict(row)
            elif hasattr(row, 'keys'):
                # Kalau drivernya berupa object mapping (punya .keys())
                snap_dict = {k: row[k] for k in row.keys()}
            elif hasattr(db, 'description') and db.description:
                # Kalau drivernya mengembalikan tuple, kita mapping pake nama kolom dari cursor
                columns = [col[0] for col in db.description]
                snap_dict = dict(zip(columns, row))
            else:
                # Fallback terakhir kalau bener-bener gak kedetek
                snap_dict = {"id": pid, "info": "Data terhapus, gagal parse format driver"}
                
            snap = json.dumps(snap_dict, default=str) # default=str biar aman dari error datetime
        except Exception:
            snap = json.dumps({"id": pid, "error": "Gagal serialize snapshot"})
        
        db.execute(
            "INSERT INTO pegawai_archive (pegawai_id, snapshot, deleted_at) VALUES (?,?,?)",
            (pid, snap, datetime.now().isoformat(timespec="seconds"))
        )

        # ---- 2) Hapus semua relasi anak ----
        raw_tables = db.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE()"
        ).fetchall()
        
        # Ambil nama tabel secara aman dari dict atau tuple
        tables = []
        for r in raw_tables:
            val = r.get("table_name") or r.get("TABLE_NAME") if isinstance(r, dict) else r[0]
            if val: tables.append(val)

        def has_column(tname: str, col: str) -> bool:
            try:
                raw_cols = db.execute(f"SHOW COLUMNS FROM {tname}").fetchall()
                cols = []
                for c in raw_cols:
                    val = c.get("Field") or c.get("field") if isinstance(c, dict) else c[0]
                    if val: cols.append(val.lower())
                return col.lower() in cols
            except Exception:
                return False

        for t in tables:
            if t in ("pegawai", "pegawai_archive"):
                continue
            if has_column(t, "pegawai_id"):
                db.execute(f"DELETE FROM {t} WHERE pegawai_id=?", (pid,))

        # Tangani relasi via akun user berdasarkan email
        raw_users = db.execute(
            "SELECT id FROM users WHERE LOWER(email)=?",
            (peg_email,)
        ).fetchall()
        
        user_ids = []
        for r in raw_users:
            val = r.get("id") or r.get("ID") if isinstance(r, dict) else r[0]
            if val: user_ids.append(val)
        
        if user_ids:
            ids_sql = ",".join([str(i) for i in user_ids])
            for t in tables:
                if t in ("user_accounts", "pegawai", "pegawai_archive"):
                    continue
                if has_column(t, "user_id"):
                    db.execute(f"DELETE FROM {t} WHERE user_id IN ({ids_sql})")
            db.execute("DELETE FROM user_accounts WHERE pegawai_id=?", (pid,))

        # ---- 3) Hapus master pegawai ----
        db.execute("DELETE FROM pegawai WHERE id=?", (pid,))

        db.commit()
        flash("Pegawai dan seluruh data terkait telah DIHAPUS permanen.", "success")

    except Exception as e:
        db.rollback()
        import traceback
        error_msg = traceback.format_exc().strip().split('\n')[-1]
        current_app.logger.exception("Gagal menghapus pegawai id=%s", pid)
        flash("Pegawai belum dapat dihapus. Periksa dependensi dan log server.", "error")

    return redirect(url_for("web.admin_pegawai"))

# ===== Approve / Reject ADMIN PT (LAYER 1 dari 2-layer approval) =====
# Approve Admin TIDAK membuat transaksi 'sukses'. Status tetap 'on-proses' dan baru final
# setelah Superadmin approve (lihat superadmin_tx_approve).
@bp.post("/admin/tx/<int:txid>/approve")
@serialized_transaction
def admin_tx_approve(txid):
    ret = require_admin()
    if ret: return ret
    if session.get("is_superadmin"):
        # Superadmin tidak boleh "melompati" layer 1 lewat endpoint admin
        flash("Superadmin melakukan approve final lewat dashboard Superadmin.", "info")
        return redirect(url_for("web.superadmin_dashboard"))
    if not session.get("hak_approval"):
        flash("Anda tidak memiliki hak approval tarik gaji. Hubungi Superadmin untuk mengaktifkannya.", "error")
        return redirect(url_for("web.admin_dashboard"))

    db = get_db()
    tx = get_tx_approval_row(db, txid)
    if not tx:
        flash("Transaksi tidak ditemukan.", "error")
        return redirect(url_for("web.admin_dashboard"))
    if not admin_can_handle(db, tx["perusahaan"], tx["perusahaan_induk"]):
        flash("Transaksi ini bukan milik perusahaan yang Anda kelola.", "error")
        return redirect(url_for("web.admin_dashboard"))
    if _norm(tx["status"]) != "on-proses":
        flash("Transaksi sudah diproses.", "info")
        return redirect(url_for("web.admin_dashboard"))
    if tx["admin_approved_at"]:
        flash("Transaksi sudah Anda approve. Menunggu approve Superadmin.", "info")
        return redirect(url_for("web.admin_dashboard"))

    who = session.get("admin_name") or session.get("admin_email") or "admin"
    cursor = db.execute(
        "UPDATE transactions SET admin_approved_at=?, admin_approved_by=? "
        "WHERE id=? AND status='on-proses' AND admin_approved_at IS NULL",
        (datetime.now().isoformat(timespec="seconds"), str(who)[:255], txid),
    )
    db.commit()
    clear_cache_prefix("admin_kpi:")
    flash("Approve Admin berhasil. Menunggu approve Superadmin (transfer)." if cursor.rowcount == 1 else "Transaksi sudah diproses.", "info")
    return redirect(url_for("web.admin_dashboard"))

@bp.post("/admin/tx/<int:txid>/reject")
@serialized_transaction
def admin_tx_reject(txid):
    ret = require_admin()
    if ret: return ret
    if session.get("is_superadmin"):
        return superadmin_tx_reject(txid)
    if not session.get("hak_approval"):
        flash("Anda tidak memiliki hak approval tarik gaji. Hubungi Superadmin untuk mengaktifkannya.", "error")
        return redirect(url_for("web.admin_dashboard"))

    db = get_db()
    tx = get_tx_approval_row(db, txid)
    if not tx:
        flash("Transaksi tidak ditemukan.", "error")
        return redirect(url_for("web.admin_dashboard"))
    if not admin_can_handle(db, tx["perusahaan"], tx["perusahaan_induk"]):
        flash("Transaksi ini bukan milik perusahaan yang Anda kelola.", "error")
        return redirect(url_for("web.admin_dashboard"))
    if _norm(tx["status"]) != "on-proses":
        flash("Transaksi sudah diproses.", "info")
        return redirect(url_for("web.admin_dashboard"))
    if tx["admin_approved_at"]:
        flash("Transaksi sudah Anda approve; penolakan selanjutnya dilakukan oleh Superadmin.", "info")
        return redirect(url_for("web.admin_dashboard"))

    cursor = db.execute(
        "UPDATE transactions SET status='ditolak' WHERE id=? AND status='on-proses' AND admin_approved_at IS NULL",
        (txid,),
    )
    db.commit()
    clear_cache_prefix("admin_kpi:")
    flash("Transaksi ditolak." if cursor.rowcount == 1 else "Transaksi sudah diproses.", "info")
    return redirect(url_for("web.admin_dashboard"))

# =========================================================================================================================
# ================== MODUL SUPERADMIN =====================================================================================
# =========================================================================================================================

@bp.route("/superadmin/dashboard")
def superadmin_dashboard():
    ret = require_superadmin()
    if ret:
        return ret

    db = get_db()
    enabled_products = get_enabled_products()
    ppn_enabled = get_ppn_enabled()
    company_selected = (request.args.get("company") or "").strip()
    admin_fee_month = (request.args.get("admin_fee_month") or "").strip()
    if admin_fee_month:
        try:
            y, m = [int(x) for x in admin_fee_month.split("-", 1)]
            date(y, m, 1)
        except Exception:
            admin_fee_month = ""

    company_rows = db.execute("""
        SELECT DISTINCT LOWER(TRIM(perusahaan)) AS company
        FROM pegawai
        WHERE perusahaan IS NOT NULL AND TRIM(perusahaan) <> ''
        ORDER BY company
    """).fetchall()
    companies = [r["company"].title() for r in company_rows if r["company"]]
    today = business_today()

    admin_email = session.get("admin_email", "").lower() or session.get("email", "").lower()
    account_name = ""
    try:
        admin_email_norm = (admin_email or "").strip().lower()
        if admin_email_norm:
            row = db.execute(
                "SELECT COALESCE(name, '') AS nama FROM admins WHERE LOWER(email)=?",
                (admin_email_norm,),
            ).fetchone()
            account_name = row["nama"] if row and row["nama"] else ""
    except Exception:
        account_name = session.get("admin_name", "")

    # Bulan berjalan (kalender) utk tampilan dashboard
    mk = today.strftime("%Y-%m")
    first_day = date(today.year, today.month, 1)
    # hitung first day next month utk batas eksklusif
    if today.month == 12:
        first_day_next = date(today.year + 1, 1, 1)
    else:
        first_day_next = date(today.year, today.month + 1, 1)
    s_first = first_day.isoformat()
    s_next  = first_day_next.isoformat()

    # 1. Ambil tanggal dari sistem simulasi aplikasi lu (Gunakan sim_today, JANGAN datetime.now())
    sim_today = current_sim_date()
    
    # 2. Bikin kunci periode (Hasilnya pasti string: "2026-06")
    periode_key = f"{sim_today.year}-{sim_today.month:02d}"
    mk = periode_key # samakan dengan variabel mk milik employee

    # 3. Set s_first menjadi tanggal 1 di bulan simulasi berjalan
    first_day = date(sim_today.year, sim_today.month, 1)
    s_first = first_day.isoformat() # Hasil: "2026-06-01"

    # 4. Set s_next menjadi tanggal 1 di bulan berikutnya berdasarkan bulan simulasi
    if sim_today.month == 12:
        first_day_next = date(sim_today.year + 1, 1, 1)
    else:
        first_day_next = date(sim_today.year, sim_today.month + 1, 1)
    s_next = first_day_next.isoformat() # Hasil: "2026-07-01"

    # 5. Set besok_sim untuk handle transaksi hari ini murni (Jika lu mau pakai range dinamis)
    # besok_sim = sim_today + timedelta(days=1)

    def add_months(d: date, delta: int) -> date:
        y = d.year + (d.month - 1 + delta) // 12
        m = (d.month - 1 + delta) % 12 + 1
        return date(y, m, 1)

    trend_start = add_months(date(today.year, today.month, 1), -5)
    trend_start_key = trend_start.strftime("%Y-%m")
    cache_key = f"admin_kpi:v2:{s_first}:{s_next}:{periode_key}:{trend_start_key}"
    cached_kpi = get_cache(cache_key)
    if cached_kpi:
        (
            total_pegawai,
            total_register,
            reg_aktif,
            eligible,
            trx_count,
            trx_sum,
            unique_borrowers,
            not_borrowed,
            admin_fee_reg_total,
            admin_fee_urg_total,
            admin_fee_total,
            pending_count,
            cycle_a,
            cycle_b,
            cycle_c,
            cycle_d,
            inactive_count,
            chart_labels,
            chart_values,
            trend_labels,
            trend_values,
            cohort_rows,
        ) = cached_kpi
    else:
        # --- KPI dasar
        row = db.execute("SELECT COUNT(*) FROM pegawai").fetchone()
        total_pegawai  = row["COUNT(*)"] if row else 0
        row = db.execute("SELECT COUNT(*) FROM user_accounts").fetchone()
        total_register = row["COUNT(*)"] if row else 0
        row = db.execute("SELECT COUNT(*) FROM user_accounts WHERE status_aktif=1").fetchone()
        reg_aktif      = row["COUNT(*)"] if row else 0
        row = db.execute("SELECT COUNT(*) FROM pegawai WHERE status_aktif=1").fetchone()
        eligible       = row["COUNT(*)"] if row else 0

        # --- KPI transaksi untuk bulan kalender berjalan (berdasar TANGGAL, bukan periode)
        row = db.execute("""
            SELECT COUNT(*) FROM transactions
            WHERE tanggal >= ? AND tanggal < ?
        """, (s_first, s_next)).fetchone()
        trx_count = row["COUNT(*)"] if row else 0

        row = db.execute("""
            SELECT COALESCE(SUM(nominal),0) FROM transactions
            WHERE status='sukses' AND tanggal >= ? AND tanggal < ?
        """, (s_first, s_next)).fetchone()
        trx_sum = row["COALESCE(SUM(nominal),0)"] if row else 0

        # Borrowers unik bulan ini (sukses atau on-proses)
        row = db.execute("""
            SELECT COUNT(DISTINCT user_id) FROM transactions
            WHERE tanggal >= ? AND tanggal < ? AND status IN ('sukses','on-proses')
        """, (s_first, s_next)).fetchone()
        unique_borrowers = row["COUNT(DISTINCT user_id)"] if row else 0

        # Pegawai yg BELUM mencairkan (pakai jumlah akun register aktif sebagai basis)
        not_borrowed = max(total_register - unique_borrowers, 0)

        # --- total admin fee periode ini, mengikuti riwayat:
        #     hanya transaksi sukses di rentang tanggal bulan simulasi ---
        row_fee = db.execute("""
            SELECT
              COALESCE(SUM(CASE WHEN product='reg' THEN admin_fee END), 0) AS fee_reg,
              COALESCE(SUM(CASE WHEN product='urg' THEN admin_fee END), 0) AS fee_urg
            FROM transactions
            WHERE tanggal >= ?
              AND tanggal < ?
              AND status = 'sukses'
              AND product IN ('reg', 'urg')
        """, (s_first, s_next)).fetchone()

        admin_fee_reg_total  = int(row_fee["fee_reg"] or 0)
        admin_fee_urg_total  = int(row_fee["fee_urg"] or 0)
        admin_fee_total      = admin_fee_reg_total + admin_fee_urg_total
    
        # Jumlah on-proses yang sedang menunggu (tampilkan SEMUA yang masih hidup - tak dibatasi periode,
        # supaya admin selalu melihat antrian real-time lintas siklus)
        row = db.execute("""
            SELECT COUNT(*) FROM transactions WHERE status='on-proses'
        """).fetchone()
        pending_count = row["COUNT(*)"] if row else 0

        # KPI siklus + tidak aktif
        row = db.execute(
            "SELECT COUNT(*) FROM pegawai WHERE status_aktif=1 AND COALESCE(siklus_gaji,'A')='A'"
        ).fetchone()
        cycle_a = row["COUNT(*)"] if row else 0
        row = db.execute(
            "SELECT COUNT(*) FROM pegawai WHERE status_aktif=1 AND COALESCE(siklus_gaji,'A')='B'"
        ).fetchone()
        cycle_b = row["COUNT(*)"] if row else 0
        row = db.execute(
            "SELECT COUNT(*) FROM pegawai WHERE status_aktif=1 AND COALESCE(siklus_gaji,'A')='C'"
        ).fetchone()
        cycle_c = row["COUNT(*)"] if row else 0
        row = db.execute(
            "SELECT COUNT(*) FROM pegawai WHERE status_aktif=1 AND COALESCE(siklus_gaji,'A')='D'"
        ).fetchone()
        cycle_d = row["COUNT(*)"] if row else 0
        row = db.execute(
            "SELECT COUNT(*) FROM pegawai WHERE status_aktif=0"
        ).fetchone()
        inactive_count = row["COUNT(*)"] if row else 0

        # --- Data grafik (total sukses per hari di bulan kalender)
        chart_data = db.execute("""
            SELECT substr(tanggal, 9, 2) AS hari, SUM(nominal) AS total
            FROM transactions
            WHERE status='sukses' AND tanggal >= ? AND tanggal < ?
            GROUP BY hari ORDER BY hari
        """, (s_first, s_next)).fetchall()
        chart_labels = [r["hari"] for r in chart_data]
        chart_values = [r["total"] for r in chart_data]

        # --- Trend 6 bulan terakhir (berdasar periode)
        trend_months = [add_months(trend_start, i).strftime("%Y-%m") for i in range(6)]
        trend_map = {m: 0 for m in trend_months}
        trend_rows = db.execute("""
            SELECT periode, COALESCE(SUM(nominal),0) AS total
            FROM transactions
            WHERE status='sukses' AND periode >= ?
            GROUP BY periode
        """, (trend_start_key,)).fetchall()
        for r in trend_rows:
            if r["periode"] in trend_map:
                trend_map[r["periode"]] = int(r["total"] or 0)
        trend_labels = trend_months
        trend_values = [trend_map[m] for m in trend_months]

        # --- Cohort sederhana: bulan pertama transaksi vs repeat bulan+1
        cohort_rows = []
        cohort_activity = db.execute("""
            SELECT user_id, periode
            FROM transactions
            WHERE status IN ('sukses','on-proses') AND periode >= ?
        """, (trend_start_key,)).fetchall()
        first_period = {}
        activity_set = set()
        for r in cohort_activity:
            uid = r["user_id"]
            per = r["periode"]
            activity_set.add((uid, per))
            if uid not in first_period or per < first_period[uid]:
                first_period[uid] = per

        for idx, cohort in enumerate(trend_months[:-1]):
            users = [u for u, p in first_period.items() if p == cohort]
            total = len(users)
            next_month = trend_months[idx + 1]
            repeat = sum(1 for u in users if (u, next_month) in activity_set)
            rate = int(round((repeat / total) * 100)) if total else 0
            cohort_rows.append({
                "cohort": cohort,
                "total": total,
                "repeat": repeat,
                "rate": rate
            })

        set_cache(
            cache_key,
            (
                total_pegawai,
                total_register,
                reg_aktif,
                eligible,
                trx_count,
                trx_sum,
                unique_borrowers,
                not_borrowed,
                admin_fee_reg_total,
                admin_fee_urg_total,
                admin_fee_total,
                pending_count,
                cycle_a,
                cycle_b,
                cycle_c,
                cycle_d,
                inactive_count,
                chart_labels,
                chart_values,
                trend_labels,
                trend_values,
                cohort_rows,
            ),
            ttl_seconds=10,
        )

    fee_recap_where = "WHERE status IN ('sukses', 'on-proses')"
    fee_recap_params = []
    if admin_fee_month:
        fee_recap_where += " AND periode = ?"
        fee_recap_params.append(admin_fee_month)

    admin_fee_recap_rows = db.execute(f"""
        SELECT
          COALESCE(product, 'reg') AS product,
          COUNT(*) AS trx_count,
          COALESCE(SUM(admin_fee), 0) AS admin_fee_total
        FROM transactions
        {fee_recap_where}
        GROUP BY COALESCE(product, 'reg')
        ORDER BY CASE COALESCE(product, 'reg')
          WHEN 'reg' THEN 1
          WHEN 'urg' THEN 2
          ELSE 3
        END
    """, fee_recap_params).fetchall()
    admin_fee_recap_total = sum(int(r["admin_fee_total"] or 0) for r in admin_fee_recap_rows)
    admin_fee_recap_count = sum(int(r["trx_count"] or 0) for r in admin_fee_recap_rows)
    admin_fee_recap_label = format_period_label(admin_fee_month) if admin_fee_month else "All"

    # --- Transaksi terbaru bulan berjalan (pakai tanggal bulanan)
# 🟢 SAMAKAN JOIN NYA KE TABEL 'users' DAN SINKRONKAN FILTER PERUSAHAAN
    # 🟢 DI SUPERADMIN CUKUP FILTER BERDASARKAN PERIODE BULAN SAJA
    recent = db.execute("""
        SELECT t.tanggal, t.created_at, t.nominal, t.admin_fee, t.status, t.product,
               u.name AS nama,
               COALESCE(p.id_pegawai, '') AS id_pegawai,
               COALESCE(p.perusahaan, '-') AS company
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
        ORDER BY t.created_at DESC, t.id DESC
        LIMIT 100
    """).fetchall()

    # --- Antrian on-proses REG & URG (jangan pakai periode—ambil yang benar2 on-proses)
    # pending_reg = db.execute("""
    #     SELECT t.id, t.tanggal, t.created_at, t.nominal, t.admin_fee, t.product,
    #            COALESCE(p.id_pegawai, '') AS id_pegawai,
    #            COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
    #            COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label,
    #            COALESCE(p.no_telp, '') AS no_telp,
    #            u.name AS nama,
    #            COALESCE(p.jabatan, '') AS jabatan,
    #            COALESCE(p.perusahaan, '') AS perusahaan
    #     FROM transactions t
    #     JOIN user_accounts u ON u.id=t.user_id
    #     LEFT JOIN pegawai p ON LOWER(p.email)=LOWER(u.email)
    #     WHERE t.status='on-proses' AND t.product='reg'
    #     ORDER BY t.created_at ASC, t.id ASC
    # """).fetchall()

    pending_reg = db.execute("""
    SELECT t.id, t.created_at, t.tanggal, t.nominal, t.status, t.product, t.admin_fee,
           p.id_pegawai, 
           COALESCE(u.name, 'Pegawai Tanpa Akun') AS nama, t.admin_approved_at, t.admin_approved_by, COALESCE(p.perusahaan_induk, '') AS perusahaan_induk, 
           COALESCE(p.perusahaan, '-') AS perusahaan, 
           COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
           COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label,
           p.no_telp, p.jabatan
    FROM transactions t
    LEFT JOIN users u ON u.id = t.user_id -- 🟢 Ganti dari user_accounts ke users!
    LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email) -- 🟢 Samakan relasinya pakai email sesuai profil employee
    WHERE t.status='on-proses' AND t.product='reg'
    ORDER BY t.created_at ASC, t.id ASC
    """).fetchall()

    pending_urg = db.execute("""
        SELECT t.id, t.tanggal, t.created_at, t.nominal, t.admin_fee, t.product,
               COALESCE(p.id_pegawai, '') AS id_pegawai,
               COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
               COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label,
               COALESCE(p.no_telp, '') AS no_telp,
               u.name AS nama,
               COALESCE(p.jabatan, '') AS jabatan,
               COALESCE(p.perusahaan, '') AS perusahaan,
               COALESCE(p.perusahaan_induk, '') AS perusahaan_induk,
               t.admin_approved_at, t.admin_approved_by
        FROM transactions t
        LEFT JOIN users u ON u.id = t.user_id
        LEFT JOIN pegawai p ON LOWER(p.email) = LOWER(u.email)
        WHERE t.status='on-proses' AND t.product='urg'
        ORDER BY t.created_at ASC, t.id ASC
    """).fetchall()

    # ===== 2-LAYER APPROVAL: tandai antrian yang sudah / belum di-approve Admin PT =====
    pending_reg = decorate_pending_tx(db, pending_reg)
    pending_urg = decorate_pending_tx(db, pending_urg)
    for t in pending_reg: t["product_label"] = "REG"
    for t in pending_urg: t["product_label"] = "URG"

    # ===== Modul "Menunggu Transfer": pisahkan yang SUDAH di-approve Admin PT (layer 1 selesai) =====
    # dari REG/URG On-Proses. Yang belum punya admin approve TAPI memang PT-nya tidak punya admin
    # ber-hak_approval sama sekali (bypass) TETAP di REG/URG On-Proses dengan tombol Approve aktif -
    # klik langsung jadi sukses (Superadmin berperan admin+transfer sekaligus untuk PT semacam itu).
    pending_transfer, still_reg, still_urg = [], [], []
    for t in pending_reg:
        (pending_transfer if t["stage"] == "menunggu_superadmin" else still_reg).append(t)
    for t in pending_urg:
        (pending_transfer if t["stage"] == "menunggu_superadmin" else still_urg).append(t)
    pending_reg, pending_urg = still_reg, still_urg
    pending_transfer.sort(key=lambda t: t.get("admin_approved_at") or t.get("created_at") or t.get("tanggal") or "")
    pending_admin_wait = sum(1 for t in (pending_reg + pending_urg) if not t["can_final"])

    queue_stats = get_queue_stats()
    admin_avatar_url = None
    try:
        if session.get("admin_id"):
            row = db.execute(
                "SELECT avatar_path FROM admins WHERE id=?",
                (session["admin_id"],),
            ).fetchone()
        elif session.get("admin_email"):
            row = db.execute(
                "SELECT avatar_path FROM admins WHERE LOWER(email)=?",
                (session["admin_email"].lower(),),
            ).fetchone()
        else:
            row = None
        if row and row["avatar_path"]:
            admin_avatar_url = url_for("static", filename=row["avatar_path"])
    except Exception:
        admin_avatar_url = None

    dashboard_template = "superadmin_dashboard.html" if session.get("is_superadmin") else "admin_dashboard.html"

    weekly_cutoff_counts = get_weekly_cutoff_counts(db)
    return render_template(
        dashboard_template,
        mk=mk,
        total_pegawai=total_pegawai,
        total_register=total_register,
        reg_aktif=reg_aktif,
        eligible=eligible,
        trx_count=trx_count,
        trx_sum=trx_sum,
        cycle_a=cycle_a,
        cycle_b=cycle_b,
        cycle_c=cycle_c,
        cycle_d=cycle_d,
        weekly_cutoff_counts=weekly_cutoff_counts,
        not_registered=max(total_pegawai - total_register, 0),
        inactive_count=inactive_count,
        pending_count=pending_count,
        pending_admin_wait=pending_admin_wait,
        pending_transfer=pending_transfer,
        not_borrowed=not_borrowed,          # <-- sebelumnya kosong; sekarang diisi
        recent=recent,
        pending_reg=pending_reg,
        pending_urg=pending_urg,
        total_admin=admin_fee_total,
        admin_fee_reg_total=admin_fee_reg_total,
        admin_fee_urg_total=admin_fee_urg_total,
        periode_key=periode_key,
        chart_labels=chart_labels,
        chart_values=chart_values,
        trend_labels=trend_labels,
        trend_values=trend_values,
        cohort_rows=cohort_rows,
        admin_fee_month=admin_fee_month,
        admin_fee_recap_label=admin_fee_recap_label,
        admin_fee_recap_rows=admin_fee_recap_rows,
        admin_fee_recap_total=admin_fee_recap_total,
        admin_fee_recap_count=admin_fee_recap_count,
        queue_stats=queue_stats,
        enabled_products=enabled_products,
        ppn_enabled=ppn_enabled,
        companies=companies,
        company_selected=company_selected,
        avatar_url=admin_avatar_url,
        account_name=account_name
    )

@bp.route("/superadmin/settings", methods=["GET", "POST"])
def superadmin_settings():
    ret = require_superadmin()
    if ret:
        return ret

    db = get_db()
    # coba ambil admin dari session id/email; kalau tidak ada, ambil admin pertama
    adm = None
    if session.get("admin_id"):
        adm = db.execute("SELECT id, name, email, password_hash FROM admins WHERE id=?", (session["admin_id"],)).fetchone()
    if not adm and session.get("admin_email"):
        adm = db.execute("SELECT id, name, email, password_hash FROM admins WHERE LOWER(email)=?", (session["admin_email"].lower(),)).fetchone()
    env_account = session.get("auth_source") == "env_super" or session.get("admin_id") == 0
    if env_account:
        adm = {"name": "Superadmin ENV", "email": session.get("admin_email"), "password_hash": ""}
    if not adm:
        flash("Akun Superadmin tidak ditemukan. Silakan login kembali.", "error")
        return redirect(url_for("web.login"))

    def settings_context():
        return {
            "admin": adm,
            "ppn_enabled": get_ppn_enabled(),
            "is_superadmin": bool(session.get("is_superadmin")),
            "env_account": env_account,
            "runtime_force_limit": get_runtime_force_limit(),
        }

    if request.method == "POST":
        form_type = request.form.get("form_type") or "password"
        if form_type == "ppn":
            enabled = request.form.get("ppn_enabled") == "1"
            set_ppn_enabled(enabled)
            flash("Pengaturan PPN diperbarui.", "success")
            return redirect(url_for("web.superadmin_settings"))

        if form_type == "runtime_force_limit":
            if not session.get("is_superadmin"):
                return ("", 404)
            if request.form.get("force_confirmed") != "1":
                return redirect(url_for("web.superadmin_settings"))
            enabled = request.form.get("runtime_force_limit") == "1"
            set_runtime_force_limit(enabled)
            flash("Pengaturan tersimpan.", "success")
            return redirect(url_for("web.superadmin_settings"))

        old_pw = request.form.get("old_password") or ""
        new_pw = request.form.get("new_password") or ""
        new_pw2 = request.form.get("new_password2") or ""

        # izinkan verifikasi menggunakan hash di DB ATAU password ENV (untuk admin default)
        if env_account:
            flash("Password akun ENV diubah melalui konfigurasi VPS, bukan akun admin lain.", "info")
            return redirect(url_for("web.superadmin_settings"))
        if not check_password_hash(adm["password_hash"], old_pw):
            flash("Password lama tidak cocok.", "error")
            return render_template("superadmin_settings.html", **settings_context())

        if not password_ok(new_pw):
            flash("Password baru minimal 6 karakter.", "error")
            return render_template("superadmin_settings.html", **settings_context())

        if new_pw != new_pw2:
            flash("Konfirmasi password baru tidak cocok.", "error")
            return render_template("superadmin_settings.html", **settings_context())

        new_hash = generate_password_hash(new_pw)
        cursor = db.execute("UPDATE admins SET password_hash=? WHERE id=? AND password_hash=?",
                            (new_hash, adm["id"], adm["password_hash"]))
        db.commit()
        if cursor.rowcount != 1:
            session.clear()
            flash("Password akun berubah saat diproses. Silakan login kembali.", "error")
            return redirect(url_for("web.login"))
        session["auth_version"] = credential_version(new_hash)

        flash("Password SuperAdmin berhasil diperbarui. Mulai sekarang Anda bisa login menggunakan kredensial DB.", "success")
        return redirect(url_for("web.superadmin_dashboard"))

    return render_template("superadmin_settings.html", **settings_context())

from werkzeug.security import generate_password_hash

# --- A. TAMPILAN UTAMA KELOLA ADMIN + FITUR FILTER SAKTI ---
@bp.route("/superadmin/admins", methods=["GET"])
def superadmin_admins():
    if not session.get("is_superadmin"):
        flash("Akses ditolak! Menu ini hanya untuk Superadmin.", "danger")
        return redirect(url_for("web.login"))
        
    db = get_db()
    
    # 1. Tangkap parameter dari form filter HTML
    q = (request.args.get("q") or "").strip()
    f_company = (request.args.get("company") or "").strip()
    
    # Base query admin
    query = """SELECT id, name, email, company, no_telp, status_aktif, hak_approval,
                      COALESCE(cutoff_mingguan_aktif, 0) AS cutoff_mingguan_aktif,
                      COALESCE(cutoff_hari, 2) AS cutoff_hari,
                      COALESCE(produk_reg_aktif, 1) AS produk_reg_aktif,
                      COALESCE(produk_urg_aktif, 1) AS produk_urg_aktif
               FROM admins WHERE 1=1"""
    params = []
    
    # Pencarian parsial (Akan mencocokkan kata di tengah seperti 'jarumsuper')
    if q:
        query += " AND (id LIKE ? OR name LIKE ? OR email LIKE ? OR company LIKE ?)"
        like_str = f"%{q}%"  # Pake persen di depan belakang biar fleksibel
        params.extend([like_str, like_str, like_str, like_str])
        
    # 3. Jika user memilih filter 'Perusahaan' tertentu
    if f_company:
        query += " AND company = ?"
        params.append(f_company)
        
    # Urutkan dari yang terbaru
    query += " ORDER BY id DESC"
    
    # Eksekusi query filter
    rows = db.execute(query, params).fetchall()
    
    # 4. Ambil daftar perusahaan UNIK untuk dipasang di dropdown select HTML
    companies = [client["name"] for client in list_clients(db)]
    
    # Hitung total hasil filter
    total = len(rows)
    
    # 5. Oper semua variabel ke template HTML
    return render_template(
        "superadmin_admins.html", 
        rows=rows, 
        q=q, 
        f_company=f_company, 
        companies=companies, 
        total=total
    )

# --- B. EDIT ADMIN & COMPANY ---
@bp.route("/superadmin/admins/edit/<int:admin_id>", methods=["POST"])
@serialized_admin_write
def superadmin_edit_admin(admin_id):
    if not session.get("is_superadmin"):
        return "Akses Ditolak", 403
        
    name = (request.form.get("nama") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    company = (request.form.get("perusahaan") or "").strip()
    no_telp = (request.form.get("no_telp") or "").strip()
    if not name or not valid_email(email):
        flash("Nama dan email admin wajib valid.", "error")
        return redirect(url_for("web.superadmin_admins"))
    db = get_db()
    existing_admin = db.execute("SELECT company FROM admins WHERE id=?", (admin_id,)).fetchone()
    if not existing_admin:
        return "Admin tidak ditemukan", 404
    registered_client = client_by_name(db, company)
    if (company and not registered_client) or (not company and existing_admin["company"]):
        flash("Pilih perusahaan yang sudah terdaftar di Kelola Client.", "error")
        return redirect(url_for("web.superadmin_admins"))
    if registered_client:
        company = registered_client["name"]
    
    # 💡 Tangkap input status dari select HTML, paksa jadi integer (0 atau 1)
    status_aktif = 1 if request.form.get("status", "1") == "1" else 0
    # Hak approve/tolak tarik gaji (LAYER 1) - checkbox, tidak terkirim sama sekali kalau tidak dicentang
    hak_approval = 1 if request.form.get("hak_approval") in ("1", "on", "true") else 0
    cutoff_aktif = 1 if request.form.get("cutoff_mingguan_aktif") in ("1", "on", "true") else 0
    produk_reg_aktif = 1 if request.form.get("produk_reg_aktif") in ("1", "on", "true") else 0
    produk_urg_aktif = 1 if request.form.get("produk_urg_aktif") in ("1", "on", "true") else 0
    try:
        cutoff_hari = int(request.form.get("cutoff_hari", 2))
    except (TypeError, ValueError):
        cutoff_hari = 2
    if cutoff_hari not in range(7):
        flash("Hari cutoff tidak valid.", "danger")
        return redirect(url_for("web.superadmin_admins"))
    
    db = get_db()
    coverage_error = admin_coverage_error(db, admin_id, company, status_aktif, hak_approval)
    if coverage_error:
        flash(coverage_error, "error")
        return redirect(url_for("web.superadmin_admins"))

    # PT Windu Karya wajib selalu ada admin (lihat superadmin_final_gate) - kalau ini admin Windu Karya
    # SATU-SATUNYA yang hak_approval-nya masih menyala dan mau dimatikan, beri peringatan (tetap boleh
    # disimpan, sesuai keputusan bisnis - bukan diblokir keras).
    windu_warning = None
    if hak_approval == 0 and is_windu_company(company):
        masih_ada = db.execute("""
            SELECT COUNT(*) AS c FROM admins
            WHERE id != ? AND hak_approval = 1
              AND LOWER(COALESCE(role,'admin')) != 'superadmin'
              AND LOWER(TRIM(company)) = LOWER(TRIM(?))
        """, (admin_id, WINDU_COMPANY_NAME)).fetchone()["c"]
        if masih_ada == 0:
            windu_warning = ("PERHATIAN: ini admin PT Windu Karya TERAKHIR dengan hak approval. Setelah "
                              "disimpan, transaksi PT Windu Karya tidak akan bisa diproses sampai ada admin "
                              "Windu Karya lain yang hak approval-nya diaktifkan (Superadmin tidak bisa "
                              "menggantikan/bypass untuk PT Windu Karya).")

    try:
        # 💡 Tambahkan status_aktif=? ke dalam query UPDATE
        db.execute(
            "UPDATE admins SET name=?, email=?, company=?, no_telp=?, status_aktif=?, hak_approval=?, cutoff_mingguan_aktif=?, cutoff_hari=?, produk_reg_aktif=?, produk_urg_aktif=? WHERE id=?",
            (name, email, company, no_telp, status_aktif, hak_approval, cutoff_aktif, cutoff_hari, produk_reg_aktif, produk_urg_aktif, admin_id)
        )
        # Satu company memiliki satu konfigurasi cutoff bersama.
        db.execute("""
            UPDATE admins
            SET cutoff_mingguan_aktif=?, cutoff_hari=?
            WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))
        """, (cutoff_aktif, cutoff_hari, company))
        db.execute("""
            UPDATE admins SET produk_reg_aktif=?, produk_urg_aktif=?
            WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))
        """, (produk_reg_aktif, produk_urg_aktif, company))
        db.commit()
        flash("Data admin berhasil diperbarui!", "success")
        if windu_warning:
            flash(windu_warning, "warning")
    except Exception as e:
        db.rollback()
        current_app.logger.exception("Gagal mengupdate admin id=%s", admin_id)
        flash("Gagal mengupdate admin. Cek log server untuk detail.", "danger")
        
    return redirect(url_for("web.superadmin_admins"))


# --- C. HAPUS ADMIN ---
@bp.route("/superadmin/admins/delete/<int:admin_id>", methods=["POST"])
@serialized_admin_write
def superadmin_delete_admin(admin_id):
    if not session.get("is_superadmin"):
        return "Akses Ditolak", 403
        
    db = get_db()
    coverage_error = admin_coverage_error(db, admin_id)
    if coverage_error:
        flash(coverage_error, "error")
        return redirect(url_for("web.superadmin_admins"))
    db.execute("DELETE FROM admins WHERE id=?", (admin_id,))
    db.commit()
    
    flash("Akun admin sukses dihapus selamanya!", "success")
    return redirect(url_for("web.superadmin_admins"))

# --- D. PROSES TAMBAH ADMIN (RUTE TERPISAH) ---
@bp.route("/superadmin/admins/add", methods=["POST"])
@serialized_admin_write
def superadmin_admins_add():
    # Proteksi: Pastikan hanya superadmin yang bisa nge-post ke sini
    if not session.get("is_superadmin"):
        return "Akses Ditolak", 403
        
    db = get_db()
    
    # Tangkap data dari input form modal tambah
    name = (request.form.get("nama") or "").strip()
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    company = (request.form.get("perusahaan") or "").strip()
    no_telp = (request.form.get("no_telp") or "").strip()
    db = get_db()
    registered_client = client_by_name(db, company)
    if not registered_client:
        flash("Pilih perusahaan yang sudah terdaftar di Kelola Client.", "error")
        return redirect(url_for("web.superadmin_admins"))
    if registered_client:
        company = registered_client["name"]
    status_aktif = 1 if request.form.get("status", "1") == "1" else 0
    # Hak approve/tolak tarik gaji (LAYER 1) - default OFF kalau tidak dicentang, konsisten dgn
    # kebijakan "semua admin default tidak punya hak approval sampai dinyalakan manual Superadmin".
    hak_approval = 1 if request.form.get("hak_approval") in ("1", "on", "true") else 0
    cutoff_aktif = 1 if request.form.get("cutoff_mingguan_aktif") in ("1", "on", "true") else 0
    produk_reg_aktif = 1 if request.form.get("produk_reg_aktif") in ("1", "on", "true") else 0
    produk_urg_aktif = 1 if request.form.get("produk_urg_aktif") in ("1", "on", "true") else 0
    try:
        cutoff_hari = int(request.form.get("cutoff_hari", 2))
    except (TypeError, ValueError):
        cutoff_hari = 2
    if cutoff_hari not in range(7):
        flash("Hari cutoff tidak valid.", "warning")
        return redirect(url_for("web.superadmin_admins"))

    # Company hanya boleh memiliki satu konfigurasi cutoff. Jika company sudah
    # memiliki admin, konfigurasi yang sudah ada menjadi sumber kebenaran.
    existing_cutoff = db.execute("""
        SELECT cutoff_mingguan_aktif, cutoff_hari
        FROM admins
        WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))
        ORDER BY id ASC LIMIT 1
    """, (company,)).fetchone()
    if existing_cutoff:
        cutoff_aktif = int(existing_cutoff["cutoff_mingguan_aktif"] or 0)
        cutoff_hari = int(existing_cutoff["cutoff_hari"] if existing_cutoff["cutoff_hari"] is not None else 2)
    existing_products = db.execute("""
        SELECT COALESCE(produk_reg_aktif, 1) AS reg_on,
               COALESCE(produk_urg_aktif, 1) AS urg_on
        FROM admins
        WHERE LOWER(TRIM(company)) = LOWER(TRIM(?))
        ORDER BY id ASC LIMIT 1
    """, (company,)).fetchone()
    if existing_products:
        produk_reg_aktif = int(existing_products["reg_on"] or 0)
        produk_urg_aktif = int(existing_products["urg_on"] or 0)

    if not name or not valid_email(email) or not password_ok(password):
        flash("Semua field wajib diisi!", "warning")
        return redirect(url_for("web.superadmin_admins"))
        
    # Hash password biar aman di database
    pw_hash = generate_password_hash(password)
    
    try:
        # Tambahkan kolom role dan status_aktif ke dalam query INSERT (Sudah fix untuk MariaDB)
        db.execute(
            """
            INSERT INTO admins (name, email, password_hash, company, no_telp, role, status_aktif, hak_approval, cutoff_mingguan_aktif, cutoff_hari, produk_reg_aktif, produk_urg_aktif, created_at)
            VALUES (?, ?, ?, ?, ?, 'admin', ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, email, pw_hash, company, no_telp, status_aktif, hak_approval, cutoff_aktif, cutoff_hari, produk_reg_aktif, produk_urg_aktif, datetime.now().isoformat(timespec="seconds"))
        )
        db.commit()
        flash(f"Admin baru untuk Perusahaan '{company}' berhasil dibuat!", "success")
    except Exception as e:
        db.rollback()
        current_app.logger.exception("Gagal menambah admin")
        flash("Gagal menambah admin. Cek log server untuk detail.", "danger")
        
    return redirect(url_for("web.superadmin_admins"))

@bp.get("/superadmin/riwayat")
def superadmin_riwayat():
    if not session.get("is_superadmin"):
        return "Akses Ditolak", 403

    db = get_db()

    # 1. Ambil data Identitas & Set Rule Global Superadmin (1=1)
    admin_company = session.get("company")
    admin_email = session.get("admin_email", "").lower() or session.get("email", "").lower()
    is_super = bool(session.get("is_superadmin"))

    where_clause = "1=1"
    where_params = []

    # 2. Tangkap semua arguments dari filter form HTML
    q = (request.args.get("q") or "").strip()
    f_company = (request.args.get("company") or "").strip() 
    status = (request.args.get("status") or "").strip()
    product = (request.args.get("product") or "").strip()
    start_raw = (request.args.get("start") or "").strip()
    end_raw = (request.args.get("end") or "").strip()

    # 3. Handle penanggalan (Parsing Date) -> INI HARUS DI ATAS QUERY UTAMA
    today = business_today()
    default_start = add_months(date(today.year, today.month, 1), -5)
    default_end = today

    def parse_date(raw, fallback):
        if not raw:
            return fallback
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except Exception:
            return None

    start_dt = parse_date(start_raw, default_start)
    end_dt = parse_date(end_raw, default_end)
    
    if start_dt is None or end_dt is None:
        flash("Tanggal filter tidak valid. Gunakan format YYYY-MM-DD.", "error")
        start_dt, end_dt = default_start, default_end

    if start_dt > end_dt:
        start_dt, end_dt = end_dt, start_dt

    # 4. Bangun Query Utama SQL (start_dt dan end_dt aman digunakan di sini)
    sql = f"""
        SELECT t.id, t.tanggal, t.periode, t.nominal, t.admin_fee, t.status, t.product,
               t.keterangan, t.created_at,
               u.name AS nama, u.email AS email_user,
               COALESCE(p.perusahaan_induk, '-') AS company,
               COALESCE(p.perusahaan, '') AS project,
               COALESCE(p.id_pegawai,'') AS id_pegawai,
               COALESCE(p.jabatan,'') AS jabatan,
               COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
               COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label
        FROM transactions t
        JOIN users u ON u.id = t.user_id
        LEFT JOIN pegawai p ON LOWER(TRIM(p.email)) = LOWER(TRIM(u.email))
        WHERE t.tanggal >= ? AND t.tanggal <= ? AND {where_clause}
    """
    params = [start_dt.isoformat(), end_dt.isoformat()] + where_params

    # 5. Pasang dynamic filter tambahan (Dropdown f_company, q pencarian, dll)
    if f_company:
        sql += " AND LOWER(TRIM(p.perusahaan_induk)) = ?"
        params.append(f_company.lower())

    f_project = (request.args.get("project") or "").strip()
    if f_project:
        sql += " AND LOWER(TRIM(p.perusahaan)) = ?"
        params.append(f_project.lower())

    if q:
        sql += """ AND (
            LOWER(u.name) LIKE ? OR LOWER(u.email) LIKE ?
            OR LOWER(COALESCE(p.id_pegawai,'')) LIKE ?
            OR LOWER(COALESCE(p.perusahaan,'')) LIKE ?
            OR LOWER(COALESCE(p.jabatan,'')) LIKE ?
            OR LOWER(COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '')) LIKE ?
        )"""
        q_like = f"%{q.lower()}%"
        params.extend([q_like, q_like, q_like, q_like, q_like, q_like])

    if status:
        sql += " AND t.status = ?"
        params.append(status)

    if product:
        sql += " AND t.product = ?"
        params.append(product)
    
    sql += " ORDER BY t.tanggal DESC, t.id DESC"

    # 6. Jalankan Logic List Companies Pilihan Lu
    if is_super:
        company_rows = db.execute("""
            SELECT DISTINCT TRIM(perusahaan_induk) AS company FROM pegawai
            WHERE perusahaan_induk IS NOT NULL AND TRIM(perusahaan_induk) <> ''
        """).fetchall()
        companies = sorted({r["company"] for r in company_rows if r["company"]})
    elif admin_company:
        is_parent = _is_parent_company(db, admin_company)  # PT Windu Karya selalu induk

        if is_parent:
            company_rows = db.execute("""
                SELECT DISTINCT TRIM(perusahaan) AS company FROM pegawai
                WHERE LOWER(TRIM(perusahaan_induk)) = LOWER(TRIM(?))
                  AND perusahaan IS NOT NULL AND TRIM(perusahaan) <> ''
            """, (admin_company,)).fetchall()
            companies = sorted({r["company"] for r in company_rows if r["company"]})
        else:
            companies = [admin_company.strip()]
    else:
        companies = []

    # 7. Eksekusi data ke Database
    rows = db.execute(sql, params).fetchall()

    project_sql = "SELECT DISTINCT TRIM(perusahaan) AS project FROM pegawai WHERE perusahaan IS NOT NULL AND TRIM(perusahaan) <> ''"
    project_params = []
    if f_company:
        project_sql += " AND LOWER(TRIM(perusahaan_induk)) = LOWER(TRIM(?))"
        project_params.append(f_company)
    projects = sorted({r["project"] for r in db.execute(project_sql, project_params).fetchall() if r["project"]})

    total_nom = sum(int(r["nominal"] or 0) for r in rows if r["status"] == "sukses")
    total_admin = sum(int(r["admin_fee"] or 0) for r in rows if r["status"] == "sukses")
    total_guixu_fee = sum(
        8000 for r in rows
        if f_company.strip().lower() == "guixu"
    )

    return render_template(
        "superadmin_riwayat.html",
        rows=rows,
        companies=companies,       
        f_company=f_company,       
        q=q,
        status=status,
        product=product,
        start=start_dt.isoformat(),
        end=end_dt.isoformat(),
        total_nom=total_nom,
        total_admin=total_admin,
        total_guixu_fee=total_guixu_fee,
        projects=projects,
        f_project=f_project,
    )

# =========================================================
# ==================== ADMIN RESET ========================
# =========================================================
@bp.post("/admin/reset")
def admin_reset_data():
    require_destructive_reset()
    ret = require_admin()
    if ret:
        return ret

    db = get_db()
    # reset data operasional saja: users & transactions
    db.execute("DELETE FROM transactions")
    db.execute("DELETE FROM users")
    db.commit()
    flash("Data operasional (users & transaksi) dibersihkan.", "info")
    return redirect(url_for("web.admin_dashboard"))

@bp.post("/admin/reset_all")
def admin_reset_all():
    require_destructive_reset()
    ret = require_admin()
    if ret:
        return ret

    db = get_db()
    db.execute("DELETE FROM transactions")
    db.execute("DELETE FROM users")
    db.execute("DELETE FROM user_accounts")
    db.execute("CREATE TABLE IF NOT EXISTS pegawai_archive (pegawai_id INTEGER, snapshot TEXT, deleted_at TEXT)")
    db.execute("DELETE FROM pegawai_archive")
    db.execute("DELETE FROM pegawai")
    db.commit()
    flash("Data operasional dan master pegawai dibersihkan.", "info")
    return redirect(url_for("web.admin_dashboard"))


@bp.post("/superadmin/reset")
def superadmin_reset_data():
    require_destructive_reset()
    ret = require_superadmin()
    if ret:
        return ret

    db = get_db()
    db.execute("DELETE FROM transactions")
    db.execute("DELETE FROM users")
    db.commit()
    flash("Data operasional (users & transaksi) dibersihkan.", "info")
    return redirect(url_for("web.superadmin_dashboard"))


@bp.post("/superadmin/reset_all")
def superadmin_reset_all():
    require_destructive_reset()
    ret = require_superadmin()
    if ret:
        return ret

    db = get_db()
    db.execute("DELETE FROM transactions")
    db.execute("DELETE FROM users")
    db.execute("DELETE FROM user_accounts")
    db.execute("CREATE TABLE IF NOT EXISTS pegawai_archive (pegawai_id INTEGER, snapshot TEXT, deleted_at TEXT)")
    db.execute("DELETE FROM pegawai_archive")
    db.execute("DELETE FROM pegawai")
    db.commit()
    flash("Data operasional dan master pegawai dibersihkan.", "info")
    return redirect(url_for("web.superadmin_dashboard"))


@bp.post("/admin/products")
def admin_products():
    ret = require_superadmin()
    if ret:
        return ret

    enabled = request.form.getlist("products")
    enabled = [p for p in enabled if p in ("reg", "urg")]
    if not enabled:
        flash("Minimal satu produk harus aktif.", "error")
        return redirect(url_for("web.admin_dashboard"))

    ppn_enabled = "1" in request.form.getlist("ppn_enabled")
    set_enabled_products(enabled)
    set_ppn_enabled(ppn_enabled)
    flash("Pengaturan produk diperbarui.", "success")
    return redirect(url_for("web.admin_dashboard"))

@bp.post("/superadmin/products")
def superadmin_products():
    ret = require_superadmin()
    if ret:
        return ret

    enabled = request.form.getlist("products")
    enabled = [p for p in enabled if p in ("reg", "urg")]
    if not enabled:
        flash("Minimal satu produk harus aktif.", "error")
        return redirect(url_for("web.superadmin_dashboard"))

    ppn_enabled = "1" in request.form.getlist("ppn_enabled")
    set_enabled_products(enabled)
    set_ppn_enabled(ppn_enabled)
    flash("Pengaturan produk diperbarui.", "success")
    return redirect(url_for("web.superadmin_dashboard"))

@bp.get("/superadmin/export_range")
def superadmin_export_range():
    ret = require_superadmin()
    if ret:
        return ret

    start_raw = (request.args.get("start") or "").strip()
    end_raw = (request.args.get("end") or "").strip()
    company = (request.args.get("company") or "").strip()
    siklus = (request.args.get("siklus") or "all").strip().upper()

    def parse_date_in(ddmmyyyy_raw):
        raw = (ddmmyyyy_raw or "").strip()
        if not raw:
            return None
        for fmt in ["%d/%m/%Y", "%Y-%m-%d"]:
            try:
                return datetime.strptime(raw, fmt).date()
            except Exception:
                continue
        return None

    start_dt = parse_date_in(start_raw)
    end_dt = parse_date_in(end_raw)
    if not start_dt or not end_dt:
        flash("Tanggal awal/akhir tidak valid. Gunakan format DD/MM/YYYY.", "error")
        return redirect(url_for("web.superadmin_dashboard"))

    if start_dt > end_dt:
        flash("Tanggal awal tidak boleh lebih besar dari tanggal akhir.", "error")
        return redirect(url_for("web.superadmin_dashboard"))

    valid_weekly_filter = siklus.startswith("W") and siklus[1:].isdigit() and int(siklus[1:]) in range(7)
    if siklus not in (*VALID_SIKLUS, "ALL") and not valid_weekly_filter:
        siklus = "ALL"

    def format_ddmmyyyy(raw):
        try:
            if raw is None:
                return ""
            if isinstance(raw, date) and not isinstance(raw, datetime):
                return raw.strftime("%d/%m/%Y")
            text = str(raw).strip()
            if not text:
                return ""
            if "T" in text:
                text = text.split("T")[0]
            if " " in text:
                text = text.split(" ")[0]
            dt = datetime.strptime(text, "%Y-%m-%d").date()
            return dt.strftime("%d/%m/%Y")
        except Exception:
            return str(raw)

    def format_created_at(raw):
        try:
            if raw is None:
                return ""
            if isinstance(raw, datetime):
                return raw.strftime("%d/%m/%Y %H:%M:%S")
            text = str(raw).strip()
            if not text:
                return ""
            text = text.replace("T", " ").replace("Z", "")
            dt = datetime.fromisoformat(text)
            return dt.strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            try:
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                return str(raw)

    sql = """
        SELECT t.id, t.tanggal, t.periode,
               u.name   AS pegawai,
               u.email  AS email_user,
               COALESCE(p.id_pegawai,'') AS id_pegawai,
               COALESCE(p.perusahaan,'') AS perusahaan,
               COALESCE(p.jabatan,'')    AS jabatan,
               COALESCE(NULLIF(t.rekening_tujuan,''), p.no_rekening, '') AS no_rekening,
               COALESCE(NULLIF(t.rekening_tujuan_label,''), 'No_Rek Bank') AS rekening_tujuan_label,
               COALESCE(p.siklus_gaji,'A') AS siklus,
               t.product, t.nominal, t.admin_fee, t.status, t.keterangan, t.created_at
          FROM transactions t
          JOIN users u         ON u.id = t.user_id
          LEFT JOIN pegawai p  ON LOWER(p.email) = LOWER(u.email)
          LEFT JOIN admins a ON LOWER(TRIM(a.company)) = LOWER(TRIM(p.perusahaan_induk))
          WHERE t.tanggal >= ? AND t.tanggal <= ?
    """
    params = [start_dt.isoformat(), end_dt.isoformat()]

    weekly_siklus = None
    if siklus.startswith("W") and siklus[1:].isdigit() and int(siklus[1:]) in range(7):
        weekly_siklus = int(siklus[1:])
        sql += " AND COALESCE(a.cutoff_mingguan_aktif, 0)=1 AND a.cutoff_hari = ?"
        params.append(weekly_siklus)
    elif siklus in VALID_SIKLUS:
        sql += " AND COALESCE(p.siklus_gaji,'A') = ?"
        params.append(siklus)

    if company:
        sql += " AND LOWER(TRIM(COALESCE(p.perusahaan,''))) = ?"
        params.append(company.lower())

    sql += " ORDER BY t.tanggal DESC, t.id DESC"

    rows = get_db().execute(sql, params).fetchall()

    buf = io.StringIO()
    w = SafeCSVWriter(buf)
    w.writerow(["ID","Tanggal","Periode","ID Pegawai","Pegawai","Email","Perusahaan","Jabatan","Tipe Rekening","No_Rek Bank","Siklus",
                "Produk","Nominal","Admin","Status","Keterangan","Dibuat"])
    for r in rows:
        w.writerow([
            r["id"], format_ddmmyyyy(r["tanggal"]), format_period_label(r["periode"]), r["id_pegawai"], r["pegawai"], r["email_user"],
            r["perusahaan"], r["jabatan"], short_rekening_label(r["rekening_tujuan_label"]), r["no_rekening"], r["siklus"], r["product"], r["nominal"],
            r["admin_fee"], r["status"], (r["keterangan"] or ""), format_created_at(r["created_at"])
        ])

    data = buf.getvalue().encode("utf-8-sig")
    from flask import Response
    fname = f"export_dana_talangan_{start_dt.strftime('%d-%m-%Y')}_to_{end_dt.strftime('%d-%m-%Y')}"
    if company:
        safe_company = company.replace(' ', '_')[:50]
        fname += f"_company_{safe_company}"
    if siklus in VALID_SIKLUS or weekly_siklus is not None:
        fname += f"_siklus_{siklus}"
    fname += ".csv"

    return Response(
        data,
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )


# ===== Approve/Reject SuperAdmin (LAYER 2 / FINAL - Superadmin yang melakukan transfer) ======
@bp.post("/superadmin/tx/<int:txid>/approve")
@serialized_transaction
def superadmin_tx_approve(txid):
    ret = require_superadmin()
    if ret: return ret
    db = get_db()
    tx = get_tx_approval_row(db, txid)
    if not tx or _norm(tx["status"]) != "on-proses":
        flash("Transaksi tidak ditemukan atau sudah diproses.", "info")
        return redirect(url_for("web.superadmin_dashboard"))

    # 2 layer: pastikan layer 1 (Admin PT) sudah approve (PT Windu: selalu wajib)
    boleh, alasan = superadmin_final_gate(tx, load_admin_companies(db))
    if not boleh:
        flash(alasan, "error")
        return redirect(url_for("web.superadmin_dashboard"))

    who = session.get("admin_name") or session.get("admin_email") or "superadmin"
    cursor = db.execute(
        "UPDATE transactions SET status='sukses', final_approved_at=?, final_approved_by=? "
        "WHERE id=? AND status='on-proses'",
        (datetime.now().isoformat(timespec="seconds"), str(who)[:255], txid),
    )
    db.commit()
    clear_cache_prefix("admin_kpi:")
    flash("Status dicatat SUKSES. Pastikan transfer telah berhasil dan bukti transfer disimpan." if cursor.rowcount == 1 else "Transaksi sudah diproses.", "info")
    return redirect(url_for("web.superadmin_dashboard"))

@bp.post("/superadmin/tx/<int:txid>/reject")
@serialized_transaction
def superadmin_tx_reject(txid):
    ret = require_superadmin()
    if ret: return ret
    db = get_db()
    cursor = db.execute("UPDATE transactions SET status='ditolak' WHERE id=? AND status='on-proses'", (txid,))
    db.commit()
    clear_cache_prefix("admin_kpi:")
    flash("Transaksi ditolak." if cursor.rowcount == 1 else "Transaksi tidak ditemukan atau sudah diproses.", "info")
    return redirect(url_for("web.superadmin_dashboard"))

@bp.route("/reset", methods=["POST"], endpoint="reset")
def reset_db():
    require_destructive_reset()
    session.clear()
    db = get_db()
    db.execute("DELETE FROM transactions")
    db.execute("DELETE FROM users")
    db.commit()
    flash("Semua data sudah dibersihkan.", "info")
    return redirect(url_for("web.login"))

@bp.post("/superadmin/logout")
def superadmin_logout():
    session.clear()
    flash("Anda telah logout Superadmin.", "info")
    return redirect(url_for("web.login"))
