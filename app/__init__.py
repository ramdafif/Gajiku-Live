import os
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from flask import Flask, redirect, url_for, session, request, g, jsonify

from app.db import close_db, ensure_db, get_db
from app.routes.web import bp as web_bp
from app.routes.clients import bp as clients_bp
from app.tasks.queue import start_worker
from app.tasks.export_scheduler import start_export_scheduler
from app.utils.logging import configure_logging
from app.services.security_service import refresh_identity

# _RUNTIME_END = date(2026, 11, 3)
# _TZ = timezone(timedelta(hours=7))


# def _runtime_enabled() -> bool:
#     return datetime.now(_TZ).date() < _RUNTIME_END


# def _runtime_forced() -> bool:
#     try:
#         row = get_db().execute(
#             "SELECT value FROM app_settings WHERE `key`='runtime_force_limit' LIMIT 1"
#         ).fetchone()
#         if not row:
#             return False
#         return str(row["value"] or "").strip().lower() in {"1", "true", "yes", "on"}
#     except Exception:
#         return False


def _load_dotenv(dotenv_path: str) -> None:
    if not dotenv_path or not os.path.exists(dotenv_path):
        return
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        return


def create_app(test_config=None):
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    _load_dotenv(os.path.join(project_root, ".env"))
    from app.config import Config
    app = Flask(
        __name__,
        static_url_path="/static",
        static_folder=os.path.join(project_root, "static"),
        template_folder=os.path.join(project_root, "templates"),
    )
    app.config.from_object(Config)
    app.config["DB_PATH"] = os.environ.get("DB_PATH", app.config["DB_PATH"])
    if test_config:
        app.config.update(test_config)
    if not app.config["APP_SECRET"] or app.config["APP_SECRET"] == "dev-secret-change-me":
        raise RuntimeError("APP_SECRET wajib diisi dengan secret production yang kuat sebelum aplikasi dijalankan.")
    app.secret_key = app.config["APP_SECRET"]

    if os.environ.get("TRUST_PROXY_HEADERS", "0") == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

    configure_logging(app)

    app.register_blueprint(web_bp)
    app.register_blueprint(clients_bp)

    @app.get("/ping")
    def ping():
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return jsonify({"ok": True, "ts": ts})

    @app.get("/health/db")
    def health_db():
        db = get_db()
        db.execute("SELECT 1").fetchone()
        return jsonify({"ok": True, "db_ok": True})

    @app.post("/txn/test")
    def txn_test():
        if not app.config["ENABLE_TEST_ENDPOINTS"] or not session.get("is_superadmin"):
            return ("", 404)
        db = get_db()
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        cur = db.execute("INSERT INTO txn_tests (created_at) VALUES (?)", (ts,))
        db.commit()
        return jsonify({"ok": True, "txn_id": str(cur.lastrowid)})

    def _get_csrf_token() -> str:
        token = session.get("_csrf_token")
        if not token:
            token = secrets.token_urlsafe(32)
            session["_csrf_token"] = token
        return token

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": _get_csrf_token}

    @app.context_processor
    def inject_client_access():
        from app.services.client_service import client_by_name
        client = client_by_name(get_db(), session.get("company")) if session.get("is_admin") and not session.get("is_superadmin") else None
        return {"can_manage_projects": bool(client and client["allow_projects"])}

    @app.before_request
    def attach_request_id():
        g.request_id = uuid.uuid4().hex[:12]

    # @app.before_request
    # def runtime_gate():
    #     if _runtime_forced() or not _runtime_enabled():
    #         return ("", 500)

    app.before_request(refresh_identity)

    @app.before_request
    def protect_admin_routes():
        if request.path.startswith("/admin"):
            allowed = {"web.login", "web.admin_login", "static"}
            if request.endpoint not in allowed and not (session.get("is_admin") or session.get("is_superadmin")):
                return redirect(url_for("web.login"))

    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            token = session.get("_csrf_token") or ""
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
            if not token or not supplied or supplied != token:
                return ("CSRF token invalid.", 400)

    @app.after_request
    def add_no_cache(resp):
        token_page = request.path.startswith(("/reset-password/", "/verify-registration/"))
        if session.get("user_id") or session.get("is_admin") or session.get("is_superadmin") or request.path in ("/login", "/forgot", "/register") or token_page:
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "no-referrer" if token_page else "same-origin"
        if hasattr(g, "request_id"):
            resp.headers["X-Request-Id"] = g.request_id
        return resp

    def integrity_error(error):
        db = g.get("db")
        if db is not None:
            db.rollback()
        app.logger.warning("Konflik data pada endpoint %s", request.endpoint)
        return ("Data bertentangan dengan data yang sudah tersimpan. Muat ulang halaman dan periksa ID/email sebelum mencoba kembali.", 409)

    from sqlite3 import IntegrityError
    app.register_error_handler(IntegrityError, integrity_error)
    try:
        from mysql.connector import IntegrityError as MySQLIntegrityError
        app.register_error_handler(MySQLIntegrityError, integrity_error)
    except ImportError:
        pass

    @app.errorhandler(500)
    def internal_error(error):
        db = g.get("db")
        if db is not None:
            db.rollback()
        return ("Terjadi kendala pada server. Hubungi admin dengan ID request: " + getattr(g, "request_id", "-"), 500)

    app.teardown_appcontext(close_db)

    with app.app_context():
        ensure_db()

    if os.environ.get("START_INLINE_WORKER", "1") == "1":
        start_worker(app)
    if os.environ.get("EXPORT_CSV_ENABLED", "1") == "1":
        start_export_scheduler(app)

    return app
