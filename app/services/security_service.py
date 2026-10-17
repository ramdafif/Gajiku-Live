import hashlib
import hmac
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import abort, current_app, flash, redirect, request, session, url_for
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


def business_today():
    return datetime.now(timezone(timedelta(hours=7))).date()


def credential_version(credential):
    return hmac.new(str(current_app.secret_key).encode(), str(credential).encode(), hashlib.sha256).hexdigest()


def token_serializer(salt):
    return URLSafeTimedSerializer(current_app.secret_key, salt=salt)


def ensure_security_schema(db, db_type):
    if db_type == "sqlite":
        db.execute("""CREATE TABLE IF NOT EXISTS security_rate_limits (
            scope_key TEXT PRIMARY KEY, window_start INTEGER NOT NULL, attempts INTEGER NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS pending_registrations (
            email TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, pegawai_id INTEGER NOT NULL,
            name TEXT NOT NULL, password_hash TEXT NOT NULL, expires_at INTEGER NOT NULL
        )""")
        columns = {row[1] for row in db.execute("PRAGMA table_info('transactions')").fetchall()}
        if "request_key" not in columns:
            db.execute("ALTER TABLE transactions ADD COLUMN request_key TEXT")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_request_key ON transactions(request_key)")
        db.execute("CREATE TABLE IF NOT EXISTS pegawai_archive (pegawai_id INTEGER, snapshot TEXT, deleted_at TEXT)")
        admin_columns = {row[1] for row in db.execute("PRAGMA table_info('admins')").fetchall()}
        for name, definition in (("role", "TEXT NOT NULL DEFAULT 'admin'"),
                                 ("status_aktif", "INTEGER NOT NULL DEFAULT 1"),
                                 ("no_telp", "TEXT DEFAULT ''")):
            if name not in admin_columns:
                db.execute(f"ALTER TABLE admins ADD COLUMN {name} {definition}")
    else:
        exists = db.execute("""SELECT 1 FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name='security_rate_limits'""").fetchone()
        if not exists:
            db.execute("""CREATE TABLE IF NOT EXISTS security_rate_limits (
                scope_key VARCHAR(64) PRIMARY KEY, window_start BIGINT NOT NULL, attempts INTEGER NOT NULL,
                INDEX idx_security_rate_window (window_start)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        pending_exists = db.execute("""SELECT 1 FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name='pending_registrations'""").fetchone()
        if not pending_exists:
            db.execute("""CREATE TABLE IF NOT EXISTS pending_registrations (
                email VARCHAR(255) PRIMARY KEY, token_hash VARCHAR(64) NOT NULL UNIQUE, pegawai_id INTEGER NOT NULL,
                name VARCHAR(255) NOT NULL, password_hash VARCHAR(255) NOT NULL, expires_at BIGINT NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
        columns = {row["Field"] for row in db.execute("SHOW COLUMNS FROM transactions").fetchall()}
        if "request_key" not in columns:
            db.execute("ALTER TABLE transactions ADD COLUMN request_key VARCHAR(64) NULL")
        archive_exists = db.execute("""SELECT 1 FROM information_schema.tables
            WHERE table_schema=DATABASE() AND table_name='pegawai_archive'""").fetchone()
        if not archive_exists:
            db.execute("CREATE TABLE IF NOT EXISTS pegawai_archive (pegawai_id INTEGER, snapshot LONGTEXT, deleted_at VARCHAR(40)) ENGINE=InnoDB")
        indexes = db.execute("SHOW INDEX FROM transactions WHERE Key_name='idx_transactions_request_key'").fetchall()
        if not indexes:
            db.execute("CREATE UNIQUE INDEX idx_transactions_request_key ON transactions(request_key)")
    db.commit()


def rate_limit(action, identity="", limit=20, window=900):
    from app.db import get_db
    db = get_db()
    now = int(time.time())
    window_start = now - now % window
    address = request.remote_addr or "unknown"
    scopes = [(f"{action}:ip:{address}", 300)]
    if identity:
        scopes.append((f"{action}:identity:{address}:{identity.strip().lower()}", limit))
    else:
        scopes[0] = (scopes[0][0], limit)
    blocked = False
    for scope, maximum in scopes:
        key = hashlib.sha256(scope.encode()).hexdigest()
        if isinstance(db, sqlite3.Connection):
            db.execute("""INSERT INTO security_rate_limits (scope_key,window_start,attempts) VALUES (?,?,1)
                ON CONFLICT(scope_key) DO UPDATE SET
                attempts=CASE WHEN window_start=excluded.window_start THEN attempts+1 ELSE 1 END,
                window_start=excluded.window_start""", (key, window_start))
        else:
            db.execute("""INSERT INTO security_rate_limits (scope_key,window_start,attempts) VALUES (?,?,1)
                ON DUPLICATE KEY UPDATE attempts=IF(window_start=VALUES(window_start),attempts+1,1),
                window_start=VALUES(window_start)""", (key, window_start))
        row = db.execute("SELECT attempts FROM security_rate_limits WHERE scope_key=?", (key,)).fetchone()
        blocked = blocked or int(row["attempts"]) > maximum
    if secrets.randbelow(100) == 0:
        db.execute("DELETE FROM security_rate_limits WHERE window_start < ?", (now - 86400,))
    db.commit()
    if blocked:
        return ("Terlalu banyak percobaan. Silakan coba kembali nanti.", 429,
                {"Retry-After": str(window_start + window - now)})
    return None


def refresh_identity():
    from app.db import get_db
    if request.endpoint == "static" or not (session.get("is_admin") or session.get("is_superadmin") or session.get("user_id")):
        return None
    if time.time() - session.get("authenticated_at", time.time()) > current_app.permanent_session_lifetime.total_seconds():
        session.clear()
        return redirect(url_for("web.login"))
    credential = None
    if session.get("is_admin") or session.get("is_superadmin"):
        admin_id = session.get("admin_id")
        source = session.get("auth_source")
        if source == "env_super" or (admin_id == 0 and session.get("is_superadmin")):
            email = str(current_app.config.get("SUPERADMIN_USERNAME") or "").strip().lower()
            if email and email == (session.get("admin_email") or "").lower():
                credential = current_app.config.get("SUPERADMIN_PASSWORD") or None
                session.update(is_admin=True, is_superadmin=True, company=None)
        else:
            db = get_db()
            if admin_id:
                admin = db.execute("SELECT * FROM admins WHERE id=?", (admin_id,)).fetchone()
            else:
                admin = db.execute("SELECT * FROM admins WHERE LOWER(email)=?", ((session.get("admin_email") or "").lower(),)).fetchone()
            if admin and int(admin["status_aktif"] or 0) == 1:
                is_super = str(admin["role"] or "").strip().lower() == "superadmin"
                session.update(is_admin=True, is_superadmin=is_super, admin_id=admin["id"],
                               admin_name=admin["name"], admin_email=admin["email"],
                               company=None if is_super else admin["company"], hak_approval=bool(admin["hak_approval"]))
                credential = admin["password_hash"]
            elif not admin and not admin_id and source == "env_admin":
                email = str(current_app.config.get("ADMIN_EMAIL") or "").strip().lower()
                if email and email == (session.get("admin_email") or "").lower():
                    credential = current_app.config.get("ADMIN_PASSWORD") or None
                    session.update(is_admin=True, is_superadmin=False, company=current_app.config.get("ADMIN_COMPANY", ""), hak_approval=False)
    else:
        account = get_db().execute("""SELECT ua.password_hash, ua.email FROM users u
            JOIN pegawai p ON LOWER(p.email)=LOWER(u.email)
            JOIN user_accounts ua ON ua.pegawai_id=p.id AND LOWER(ua.email)=LOWER(p.email)
            WHERE u.id=?""", (session["user_id"],)).fetchone()
        if account:
            credential = account["password_hash"]
            session["formal_email"] = account["email"]
    version = credential_version(credential) if credential else None
    previous = session.get("auth_version")
    if not version or (previous and not hmac.compare_digest(previous, version)):
        session.clear()
        flash("Sesi berakhir atau akses akun berubah. Silakan login kembali.", "info")
        return redirect(url_for("web.login"))
    session["auth_version"] = version
    session.setdefault("authenticated_at", int(time.time()))
    session.permanent = True
    return None


def withdrawal_token():
    return token_serializer("withdrawal").dumps({"uid": session["user_id"], "nonce": secrets.token_urlsafe(24)})


def withdrawal_key(token):
    try:
        payload = token_serializer("withdrawal").loads(token or "", max_age=7200)
        if payload.get("uid") != session.get("user_id"):
            return None
    except (BadSignature, SignatureExpired, TypeError, AttributeError):
        return None
    return hashlib.sha256(token.encode()).hexdigest()


def serialized_withdrawal(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if request.method != "POST" or not session.get("user_id"):
            return function(*args, **kwargs)
        from app.db import get_db
        db = get_db()
        db.commit()
        try:
            if isinstance(db, sqlite3.Connection):
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("""SELECT p.id FROM pegawai p JOIN users u ON LOWER(p.email)=LOWER(u.email)
                    WHERE u.id=? FOR UPDATE""", (session["user_id"],)).fetchall()
            key = withdrawal_key(request.form.get("request_token"))
            if not key:
                flash("Form pengajuan kedaluwarsa. Silakan buka ulang halaman tarik gaji.", "error")
                return redirect(url_for("web.tarik_gaji"))
            duplicate = db.execute("SELECT id FROM transactions WHERE user_id=? AND request_key=?",
                                   (session["user_id"], key)).fetchone()
            if duplicate:
                flash("Pengajuan ini sudah diterima; tidak dibuat dua kali.", "info")
                return redirect(url_for("web.dashboard"))
            return function(*args, **kwargs)
        finally:
            db.rollback()
    return wrapped


def serialized_admin_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not session.get("is_superadmin"):
            return function(*args, **kwargs)
        from app.db import get_db
        db = get_db()
        db.commit()
        try:
            if isinstance(db, sqlite3.Connection):
                db.execute("BEGIN IMMEDIATE")
            else:
                db.execute("SELECT id FROM admins ORDER BY id FOR UPDATE").fetchall()
            return function(*args, **kwargs)
        finally:
            db.rollback()
    return wrapped


def serialized_employee_write(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not (session.get("is_admin") or session.get("is_superadmin")):
            return function(*args, **kwargs)
        from app.db import get_db
        db = get_db()
        db.commit()
        try:
            if isinstance(db, sqlite3.Connection):
                db.execute("BEGIN IMMEDIATE")
            else:
                employee_id = kwargs.get("pid") if "pid" in kwargs else args[0]
                db.execute("SELECT id FROM pegawai WHERE id=? FOR UPDATE", (employee_id,)).fetchall()
            return function(*args, **kwargs)
        finally:
            db.rollback()
    return wrapped


def serialized_transaction(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if not (session.get("user_id") or session.get("is_admin") or session.get("is_superadmin")):
            return function(*args, **kwargs)
        from app.db import get_db
        db = get_db()
        db.commit()
        try:
            if isinstance(db, sqlite3.Connection):
                db.execute("BEGIN IMMEDIATE")
            else:
                txid = kwargs.get("txid") if "txid" in kwargs else args[0]
                db.execute("SELECT id FROM transactions WHERE id=? FOR UPDATE", (txid,)).fetchall()
            return function(*args, **kwargs)
        finally:
            db.rollback()
    return wrapped


def require_destructive_reset():
    if not session.get("is_superadmin"):
        abort(403)
    if not current_app.config.get("ALLOW_DESTRUCTIVE_RESET"):
        abort(403, description="Reset massal dinonaktifkan. Gunakan prosedur maintenance dan backup.")


def safe_return_url(fallback):
    from urllib.parse import urlsplit
    target = request.referrer or ""
    parsed = urlsplit(target)
    origin = urlsplit(request.host_url)
    if parsed.scheme in ("http", "https") and parsed.netloc == origin.netloc and not parsed.path.startswith("//"):
        return parsed.path + ("?" + parsed.query if parsed.query else "")
    return url_for(fallback)
