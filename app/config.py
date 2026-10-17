import os

class Config:
    APP_VERSION = "v1.2.0-register"
    APP_SECRET = os.environ.get("APP_SECRET", "")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
    PERMANENT_SESSION_LIFETIME = 8 * 60 * 60
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    MAX_FORM_MEMORY_SIZE = 64 * 1024
    MAX_FORM_PARTS = 100
    ALLOW_DATE_OVERRIDE = os.environ.get("ALLOW_DATE_OVERRIDE", "0") == "1"
    ALLOW_DESTRUCTIVE_RESET = os.environ.get("ALLOW_DESTRUCTIVE_RESET", "0") == "1"
    ENABLE_TEST_ENDPOINTS = os.environ.get("ENABLE_TEST_ENDPOINTS", "0") == "1"
    SEED_DEMO_DATA = os.environ.get("SEED_DEMO_DATA", "0") == "1"
    TEMPLATES_AUTO_RELOAD = True

    PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    # Database config (MySQL)
    DB_TYPE = os.environ.get("DB_TYPE", "mysql")  # 'sqlite' atau 'mysql'
    DB_PATH = os.environ.get("DB_PATH", os.path.join(PROJECT_ROOT, "data", "tarikgaji-live.db"))
    
    MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
    MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
    MYSQL_USER = os.environ.get("MYSQL_USER", "root")
    MYSQL_PASS = os.environ.get("MYSQL_PASS", "")
    MYSQL_DB = os.environ.get("MYSQL_DB", "gajiku_db")

    # biaya REG flat per transaksi
    ADMIN_FEE = 15000
    # biaya URG per hari (dipakai untuk hitung (hari_dipakai - hari_berjalan) x fee/hari)
    ADMIN_FEE_PER_DAY = 15000

    # EMAIL/SMTP (gunakan ENV untuk keamanan)
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip()
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "465"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")
    EMAIL_FROM = os.environ.get("EMAIL_FROM", "")
    EMAIL_ADMIN_LIST = [
        e.strip() for e in os.environ.get("EMAIL_ADMIN_LIST", "").split(",") if e.strip()
    ]

    # Admin credentials (bisa override via ENV)
    ADMIN_USERNAME = os.environ.get("ADMIN_USER", "admin")
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    ADMIN_PASSWORD = os.environ.get("ADMIN_PASS", "")
    ADMIN_COMPANY = os.environ.get("ADMIN_COMPANY", "")

    SUPERADMIN_USERNAME = os.environ.get("SUPERADMIN_USERNAME", "")
    SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "")
