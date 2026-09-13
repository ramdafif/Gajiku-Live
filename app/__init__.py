import os
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

from flask import Flask, redirect, url_for, session, request, g, jsonify

from app.db import close_db, ensure_db, get_db
from app.routes.web import bp as web_bp
from app.tasks.queue import start_worker
from app.tasks.export_scheduler import start_export_scheduler
from app.utils.logging import configure_logging

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


def create_app():
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
    app.secret_key = app.config["APP_SECRET"]
    app.config["DB_PATH"] = os.environ.get("DB_PATH", app.config["DB_PATH"])

    configure_logging(app)

    app.register_blueprint(web_bp)

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

    @app.before_request
    def attach_request_id():
        g.request_id = uuid.uuid4().hex[:12]

    # @app.before_request
    # def runtime_gate():
    #     if _runtime_forced() or not _runtime_enabled():
    #         return ("", 500)

    @app.before_request
    def protect_admin_routes():
        if request.path.startswith("/admin"):
            allowed = {"web.login", "web.admin_login", "static"}
            if request.endpoint not in allowed and not session.get("is_admin"):
                return redirect(url_for("web.login"))

    @app.before_request
    def csrf_protect():
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.endpoint in {
                "web.login",
                "web.forgot_password",
                "web.register",
                "ping",
                "health_db",
                "txn_test",
            }:
                return None
            token = session.get("_csrf_token") or ""
            supplied = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token") or ""
            if not token or not supplied or supplied != token:
                return ("CSRF token invalid.", 400)

    @app.after_request
    def add_no_cache(resp):
        if request.path in ("/dashboard",):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        if hasattr(g, "request_id"):
            resp.headers["X-Request-Id"] = g.request_id
        return resp

    app.teardown_appcontext(close_db)

    with app.app_context():
        ensure_db()

    if os.environ.get("START_INLINE_WORKER", "1") == "1":
        start_worker(app)
    if os.environ.get("EXPORT_CSV_ENABLED", "1") == "1":
        start_export_scheduler(app)

    return app
