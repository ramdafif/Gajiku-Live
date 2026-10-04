import os
import sqlite3
from datetime import datetime

from flask import current_app, g
from werkzeug.security import generate_password_hash

try:
    import mysql.connector
except ImportError:
    mysql = None


class MySQLConnectionWrapper:
    def __init__(self, conn):
        self._conn = conn

    def cursor(self, *args, **kwargs):
        kwargs.setdefault("dictionary", True)
        return self._conn.cursor(*args, **kwargs)

    def execute(self, sql, params=None):
        sql = sql.replace("?", "%s")
        cursor = self.cursor()
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
        return cursor

    def executemany(self, sql, seq):
        sql = sql.replace("?", "%s")
        cursor = self.cursor()
        cursor.executemany(sql, seq)
        return cursor

    def executescript(self, script):
        statements = [s.strip() for s in script.split(";") if s.strip()]
        cursor = self.cursor()
        for stmt in statements:
            cursor.execute(stmt)
        return cursor

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)


def get_db():
    """Get database connection (SQLite atau MySQL)"""
    if "db" not in g:
        db_type = current_app.config.get("DB_TYPE", "mysql")
        
        if db_type == "sqlite":
            g.db = sqlite3.connect(
                current_app.config["DB_PATH"],
                detect_types=sqlite3.PARSE_DECLTYPES,
                timeout=30.0,
                check_same_thread=False,
            )
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys=OFF;")
            g.db.execute("PRAGMA journal_mode=WAL;")
            g.db.execute("PRAGMA synchronous=NORMAL;")
            g.db.execute("PRAGMA busy_timeout=30000;")
        else:  # MySQL
            if not mysql:
                raise ImportError("mysql-connector-python is required for MySQL backend")
            
            mysql_conn = mysql.connector.connect(
                host=current_app.config["MYSQL_HOST"],
                user=current_app.config["MYSQL_USER"],
                password=current_app.config["MYSQL_PASS"],
                database=current_app.config["MYSQL_DB"],
                port=current_app.config["MYSQL_PORT"],
                autocommit=False,
            )
            g.db = MySQLConnectionWrapper(mysql_conn)
    
    return g.db


def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db_type = current_app.config.get("DB_TYPE", "mysql")

    if db_type == "sqlite":
        db.executescript("""
            -- AKUN MODE PROYEK (lama): tetap dipakai untuk simulasi gaji langsung
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE,
                gaji INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

        -- Tambahkan kolom 'product' & 'cancel_until' bila belum ada
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tanggal TEXT NOT NULL,            -- YYYY-MM-DD
            periode TEXT NOT NULL,            -- YYYY-MM
            nominal INTEGER NOT NULL,
            admin_fee INTEGER NOT NULL,
            status TEXT NOT NULL,             -- 'sukses' | 'ditolak' | 'on-proses' | 'dibatalkan'
            keterangan TEXT,
            rekening_tujuan TEXT DEFAULT '',
            rekening_tujuan_label TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            product TEXT DEFAULT 'reg',       -- 'reg' | 'urg'
            cancel_until TEXT,                -- ISO timestamp, window pembatalan
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        -- === Admin & Pegawai master ===
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            avatar_path TEXT DEFAULT '',
            company TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS pegawai (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_pegawai TEXT DEFAULT '',
            nama TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            jabatan TEXT,
            gaji INTEGER DEFAULT 0,
            status_aktif INTEGER DEFAULT 0,
            perusahaan TEXT DEFAULT '',
            no_rekening TEXT DEFAULT '',
            no_rekening_lain TEXT DEFAULT '',
            rekening_ewallet TEXT DEFAULT '',
            no_telp TEXT DEFAULT '',
            admin_fee_flat INTEGER DEFAULT 15000,
            created_at TEXT NOT NULL
        );

        -- === Akun pegawai formal (email+password) ===
        CREATE TABLE IF NOT EXISTS user_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pegawai_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            status_aktif INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            register_ip TEXT,
            avatar_path TEXT DEFAULT '',
            company TEXT DEFAULT '',
            FOREIGN KEY (pegawai_id) REFERENCES pegawai(id)
        );
                     
        -- === App settings (global) ===
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- === Lightweight test writes (for /txn/test) ===
        CREATE TABLE IF NOT EXISTS txn_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL
        );

        -- === App settings (global) ===
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        -- ==== Indexes to speed up first-time queries / lookups ====
        CREATE INDEX IF NOT EXISTS idx_trx_user_periode
            ON transactions(user_id, periode);

        CREATE INDEX IF NOT EXISTS idx_trx_user_periode_status_tanggal
            ON transactions(user_id, periode, status, tanggal);

        CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

        CREATE INDEX IF NOT EXISTS idx_trx_tanggal
            ON transactions(tanggal);

        CREATE INDEX IF NOT EXISTS idx_trx_status
            ON transactions(status);

        CREATE INDEX IF NOT EXISTS idx_trx_status_tanggal
            ON transactions(status, tanggal);

        CREATE INDEX IF NOT EXISTS idx_trx_product
            ON transactions(product);
    """)

    db.commit()

    _migrate_users_email_unique(db)
    _fix_transactions_fk_users_old(db)

    # === MIGRASI: tambahkan kolom id_pegawai bila belum ada ===
    try:
        db.execute("ALTER TABLE pegawai ADD COLUMN id_pegawai TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom id_pegawai ditambahkan.")
    except Exception:
        pass
    try:
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_pegawai_id_pegawai "
            "ON pegawai(id_pegawai) WHERE id_pegawai <> ''"
        )
        db.commit()
    except Exception:
        pass

    # === MIGRASI: tambahkan kolom siklus_gaji bila belum ada ===
    try:
        db.execute("ALTER TABLE pegawai ADD COLUMN siklus_gaji TEXT DEFAULT 'A'")
        db.commit()
        print("[DB] kolom siklus_gaji ditambahkan.")
    except sqlite3.OperationalError:
        pass
    # === MIGRASI: tambahkan kolom no_rekening bila belum ada ===
    try:
        db.execute("ALTER TABLE pegawai ADD COLUMN no_rekening TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom no_rekening ditambahkan.")
    except sqlite3.OperationalError:
        pass
    # === MIGRASI: tambahkan kolom rekening bank lain bila belum ada ===
    try:
        db.execute("ALTER TABLE pegawai ADD COLUMN no_rekening_lain TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom no_rekening_lain ditambahkan.")
    except sqlite3.OperationalError:
        pass
    # === MIGRASI: tambahkan kolom rekening e-wallet bila belum ada ===
    try:
        db.execute("ALTER TABLE pegawai ADD COLUMN rekening_ewallet TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom rekening_ewallet ditambahkan.")
    except sqlite3.OperationalError:
        pass
    # === MIGRASI: tambahkan kolom no_telp bila belum ada ===
    try:
        db.execute("ALTER TABLE pegawai ADD COLUMN no_telp TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom no_telp ditambahkan.")
    except sqlite3.OperationalError:
        pass
    # === MIGRASI: tambahkan kolom admin_fee_flat bila belum ada ===
    try:
        db.execute("ALTER TABLE pegawai ADD COLUMN admin_fee_flat INTEGER DEFAULT 15000")
        db.commit()
        print("[DB] kolom admin_fee_flat ditambahkan.")
    except sqlite3.OperationalError:
        pass
    # === MIGRASI: tambahkan kolom avatar_path bila belum ada ===
    try:
        db.execute("ALTER TABLE user_accounts ADD COLUMN avatar_path TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom avatar_path (user_accounts) ditambahkan.")
    except sqlite3.OperationalError:
        pass
    try:
        db.execute("ALTER TABLE admins ADD COLUMN avatar_path TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom avatar_path (admins) ditambahkan.")
    except sqlite3.OperationalError:
        pass

    # seed admin default jika kosong
    admin = db.execute("SELECT id FROM admins LIMIT 1").fetchone()
    if not admin:
        # Akun Superadmin (menggantikan admin default)
        db.execute(
            "INSERT INTO admins (name, email, password_hash, created_at) VALUES (?,?,?,?)",
            (
                "Superadmin Utama",
                "superadmin@example.com",
                generate_password_hash("superadmin123"),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        # Akun Admin Biasa
        db.execute(
            "INSERT INTO admins (name, email, password_hash, created_at) VALUES (?,?,?,?)",
            (
                "Admin Biasa",
                "admin@example.com",
                generate_password_hash("admin123"),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        db.commit()

    # seed pegawai dan user_accounts default jika kosong
    pegawai_user = db.execute("SELECT id FROM pegawai LIMIT 1").fetchone()
    if not pegawai_user:
        # Buat entri di tabel 'pegawai'
        db.execute(
            "INSERT INTO pegawai (nama, email, jabatan, gaji, status_aktif, created_at) VALUES (?,?,?,?,?,?)",
            (
                "Budi Santoso",
                "user@example.com",
                "Karyawan",
                5000000,
                1, # Aktif
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        pegawai_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Buat entri di tabel 'user_accounts' yang terhubung ke pegawai
        db.execute(
            "INSERT INTO user_accounts (pegawai_id, name, email, password_hash, status_aktif, created_at) VALUES (?,?,?,?,?,?)",
            (
                pegawai_id,
                "Budi Santoso",
                "user@example.com",
                generate_password_hash("user123"),
                1, # Aktif
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        db.commit()


    # seed default product visibility (reg, urg)
    has_setting = db.execute(
        "SELECT 1 FROM app_settings WHERE key='enabled_products' LIMIT 1"
    ).fetchone()
    if not has_setting:
        db.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            ("enabled_products", "reg,urg"),
        )
        db.commit()

    # --- harden: tambah kolom 'notified_onproses' bila belum ada ---
    try:
        db.execute("ALTER TABLE transactions ADD COLUMN notified_onproses INTEGER DEFAULT 0")
        db.commit()
    except Exception:
        pass

    # --- kolom lock URG (tanggal sampai kapan URG mengunci hak ke depan) ---
    try:
        db.execute("ALTER TABLE transactions ADD COLUMN urg_lock_until TEXT")
        db.commit()
    except Exception:
        pass

    # --- rekening tujuan yang dipilih saat transaksi dibuat ---
    try:
        db.execute("ALTER TABLE transactions ADD COLUMN rekening_tujuan TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom rekening_tujuan ditambahkan.")
    except Exception:
        pass
    try:
        db.execute("ALTER TABLE transactions ADD COLUMN rekening_tujuan_label TEXT DEFAULT ''")
        db.commit()
        print("[DB] kolom rekening_tujuan_label ditambahkan.")
    except Exception:
        pass


# Kolom audit untuk 2-layer approval (Admin -> Superadmin) pada tabel transactions.
#   admin_approved_at/by : layer 1, di-approve Admin PT (status transaksi TETAP 'on-proses')
#   final_approved_at/by : layer 2, di-approve Superadmin (status jadi 'sukses' = sudah ditransfer)
APPROVAL_COLUMNS = (
    ("admin_approved_at", "DATETIME NULL", "TEXT"),
    ("admin_approved_by", "VARCHAR(255) NULL", "TEXT"),
    ("final_approved_at", "DATETIME NULL", "TEXT"),
    ("final_approved_by", "VARCHAR(255) NULL", "TEXT"),
)


def ensure_hak_approval_column(db):
    """Kolom admins.hak_approval: siapa saja admin yang boleh approve/tolak tarik gaji (LAYER 1).
    Default 0 (OFF) untuk semua - sengaja begitu supaya begitu fitur ini di-deploy, perilaku sistem
    TIDAK berubah untuk PT yang belum ada admin di-ON-kan (tetap bypass langsung ke Superadmin,
    persis seperti sebelum fitur ini ada). Kecuali PT Windu Karya, yang memang selalu wajib admin
    approve dulu terlepas dari kolom ini (lihat is_windu_company & superadmin_final_gate)."""
    db_type = current_app.config.get("DB_TYPE", "mysql")
    try:
        if db_type == "sqlite":
            cols = [r[1] for r in db.execute("PRAGMA table_info('admins')").fetchall()]
            if "hak_approval" not in cols:
                db.execute("ALTER TABLE admins ADD COLUMN hak_approval INTEGER NOT NULL DEFAULT 0")
        else:
            if not db.execute("SHOW COLUMNS FROM admins LIKE 'hak_approval'").fetchone():
                db.execute("ALTER TABLE admins ADD COLUMN hak_approval TINYINT(1) NOT NULL DEFAULT 0")
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[DB] ensure kolom hak_approval (admins) dilewati: {e}")


def ensure_cutoff_mingguan_columns(db):
    """Kolom konfigurasi cutoff mingguan pada admins (berlaku per company)."""
    db_type = current_app.config.get("DB_TYPE", "mysql")
    columns = (
        ("cutoff_mingguan_aktif", "INTEGER NOT NULL DEFAULT 0", "TINYINT(1) NOT NULL DEFAULT 0"),
        ("cutoff_hari", "INTEGER NOT NULL DEFAULT 2", "TINYINT NOT NULL DEFAULT 2"),
    )
    try:
        if db_type == "sqlite":
            existing = {r[1] for r in db.execute("PRAGMA table_info('admins')").fetchall()}
            for name, sqlite_type, _ in columns:
                if name not in existing:
                    db.execute(f"ALTER TABLE admins ADD COLUMN {name} {sqlite_type}")
        else:
            for name, _, mysql_type in columns:
                if not db.execute(f"SHOW COLUMNS FROM admins LIKE '{name}'").fetchone():
                    db.execute(f"ALTER TABLE admins ADD COLUMN {name} {mysql_type}")
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[DB] ensure kolom cutoff mingguan dilewati: {e}")


def ensure_windu_projects_table(db):
    """Tabel daftar project/anak-perusahaan resmi PT Windu Karya. Dipakai sebagai sumber dropdown
    'Perusahaan' saat Admin PT Windu Karya (atau Superadmin) menambah/mengubah pegawai, supaya nama
    project tidak diketik manual (rawan typo/duplikat). Nama project baru WAJIB didaftarkan lebih
    dulu lewat menu 'Kelola Project Windu Karya' oleh Superadmin atau Admin PT Windu Karya sendiri."""
    db_type = current_app.config.get("DB_TYPE", "mysql")
    try:
        if db_type == "sqlite":
            db.execute("""
                CREATE TABLE IF NOT EXISTS windu_projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nama_project TEXT NOT NULL,
                    created_by VARCHAR(255) DEFAULT '',
                    created_at TEXT NOT NULL
                )
            """)
        else:
            db.execute("""
                CREATE TABLE IF NOT EXISTS windu_projects (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    nama_project VARCHAR(255) NOT NULL,
                    created_by VARCHAR(255) DEFAULT '',
                    created_at DATETIME NOT NULL
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
            """)
        db.commit()
    except Exception as e:
        try:
            db.rollback()
        except Exception:
            pass
        print(f"[DB] ensure tabel windu_projects dilewati: {e}")


def ensure_approval_columns(db):
    """Tambah kolom 2-layer approval bila belum ada (idempotent, aman dipanggil berulang)."""
    db_type = current_app.config.get("DB_TYPE", "mysql")
    missing = []
    for name, mysql_type, sqlite_type in APPROVAL_COLUMNS:
        try:
            if db_type == "sqlite":
                cols = [r[1] for r in db.execute("PRAGMA table_info('transactions')").fetchall()]
                if name in cols:
                    continue
                db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {sqlite_type}")
            else:
                if db.execute(f"SHOW COLUMNS FROM transactions LIKE '{name}'").fetchone():
                    continue
                db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {mysql_type}")
            db.commit()
            print(f"[DB] kolom {name} (transactions) ditambahkan.")
        except Exception as e:
            # Bisa terjadi bila 2 worker start bersamaan (duplicate column) atau user DB tak punya hak ALTER.
            # Kalau tak punya hak ALTER, jalankan manual: migrasi_2layer_approval.sql
            try:
                db.rollback()
            except Exception:
                pass
            print(f"[DB] ensure kolom {name} dilewati: {e}")
            missing.append(name)

    # Jangan biarkan aplikasi start lalu gagal jauh di dalam route dengan
    # ProgrammingError 1054. Ini juga membuat penyebab deploy yang lupa
    # menjalankan migrasi terlihat langsung di log startup.
    try:
        if db_type == "sqlite":
            existing = {r[1] for r in db.execute("PRAGMA table_info('transactions')").fetchall()}
        else:
            existing = {
                row["Field"] if isinstance(row, dict) else row[0]
                for row in db.execute("SHOW COLUMNS FROM transactions").fetchall()
            }
        missing = [name for name, _, _ in APPROVAL_COLUMNS if name not in existing]
    except Exception as e:
        raise RuntimeError(f"Tidak bisa memverifikasi schema tabel transactions: {e}") from e

    if missing:
        raise RuntimeError(
            "Kolom approval belum tersedia di tabel transactions: "
            + ", ".join(missing)
            + ". Jalankan migrasi_2layer_approval.sql atau berikan user MySQL hak ALTER TABLE."
        )


def ensure_db():
    db_type = current_app.config.get("DB_TYPE", "mysql")

    if db_type == "sqlite":
        db_path = current_app.config["DB_PATH"]
        db_dir = os.path.dirname(os.path.abspath(db_path))

        # 🔥 FIX UNTUK VERCEL: Cek apakah aplikasi berjalan di Vercel
        if os.environ.get("VERCEL"):
            # Jika di Vercel, paksa path database pindah ke folder /tmp
            # Karena hanya folder /tmp yang diizinkan untuk dibaca dan ditulis (Writable)
            db_path = os.path.join("/tmp", os.path.basename(db_path))
            current_app.config["DB_PATH"] = db_path
            # Di Vercel /tmp sudah pasti ada, jadi kita tidak perlu os.makedirs
        else:
            # Jika di lokal laptop lu, tetap buat foldernya seperti biasa
            os.makedirs(db_dir, exist_ok=True)

        init_db()
        ensure_approval_columns(get_db())
        ensure_windu_projects_table(get_db())
        ensure_hak_approval_column(get_db())
        ensure_cutoff_mingguan_columns(get_db())
    else:
        # MySQL mode: skip SQLite init_db() because schema is managed by MySQL migration
        db = get_db()
        db.execute("SELECT 1")
        ensure_approval_columns(db)
        ensure_windu_projects_table(db)
        ensure_hak_approval_column(db)
        ensure_cutoff_mingguan_columns(db)


def _migrate_users_email_unique(db):
    try:
        idx_list = db.execute("PRAGMA index_list('users')").fetchall()
        unique_indexes = [r for r in idx_list if int(r[2] or 0) == 1]
        has_unique_email = False
        has_unique_name = False
        for idx in unique_indexes:
            cols = [c[2] for c in db.execute(f"PRAGMA index_info('{idx[1]}')").fetchall()]
            if "email" in cols:
                has_unique_email = True
            if "name" in cols:
                has_unique_name = True

        if has_unique_email or not has_unique_name:
            return

        rows = db.execute("SELECT id, name, email, gaji, created_at FROM users ORDER BY id").fetchall()

        keep_by_email = {}
        duplicates = {}
        for r in rows:
            email = (r["email"] or "").strip()
            if not email:
                continue
            key = email.lower()
            if key in keep_by_email:
                duplicates[r["id"]] = keep_by_email[key]["id"]
            else:
                keep_by_email[key] = r

        db.execute("PRAGMA foreign_keys=OFF;")
        db.execute("ALTER TABLE users RENAME TO users_old;")
        db.execute("""
            CREATE TABLE users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE,
                gaji INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
        """)

        for r in rows:
            if r["id"] in duplicates:
                continue
            db.execute(
                "INSERT INTO users(id, name, email, gaji, created_at) VALUES (?,?,?,?,?)",
                (r["id"], r["name"], r["email"], r["gaji"], r["created_at"]),
            )

        for dup_id, keep_id in duplicates.items():
            db.execute("UPDATE transactions SET user_id=? WHERE user_id=?", (keep_id, dup_id))

        db.execute("DROP TABLE users_old;")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);")

        try:
            db.execute(
                "UPDATE sqlite_sequence SET seq=(SELECT MAX(id) FROM users) WHERE name='users'"
            )
        except Exception:
            pass

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[DB] users migration skipped: {e}")


def _fix_transactions_fk_users_old(db):
    """
    Perbaiki foreign key transactions yang masih mengarah ke users_old
    akibat proses rename users pada migrasi sebelumnya.
    """
    try:
        fks = db.execute("PRAGMA foreign_key_list('transactions')").fetchall()
        if not any(r[2] == "users_old" for r in fks):
            return

        db.execute("PRAGMA foreign_keys=OFF;")
        db.execute("ALTER TABLE transactions RENAME TO transactions_old;")

        db.execute("""
            CREATE TABLE transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                tanggal TEXT NOT NULL,
                periode TEXT NOT NULL,
                nominal INTEGER NOT NULL,
                admin_fee INTEGER NOT NULL,
                status TEXT NOT NULL,
                keterangan TEXT,
                rekening_tujuan TEXT DEFAULT '',
                rekening_tujuan_label TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                product TEXT DEFAULT 'reg',
                cancel_until TEXT,
                notified_onproses INTEGER DEFAULT 0,
                urg_lock_until TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """)

        cols_old = [r[1] for r in db.execute("PRAGMA table_info('transactions_old')").fetchall()]
        cols_new = [
            "id",
            "user_id",
            "tanggal",
            "periode",
            "nominal",
            "admin_fee",
            "status",
            "keterangan",
            "rekening_tujuan",
            "rekening_tujuan_label",
            "created_at",
            "product",
            "cancel_until",
            "notified_onproses",
            "urg_lock_until",
        ]
        cols_copy = [c for c in cols_new if c in cols_old]
        if cols_copy:
            cols_csv = ",".join(cols_copy)
            db.execute(
                f"INSERT INTO transactions ({cols_csv}) SELECT {cols_csv} FROM transactions_old WHERE user_id IN (SELECT id FROM users)"
            )

        db.execute("DROP TABLE transactions_old;")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trx_user_periode ON transactions(user_id, periode);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trx_user_periode_status_tanggal ON transactions(user_id, periode, status, tanggal);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trx_tanggal ON transactions(tanggal);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trx_status ON transactions(status);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trx_status_tanggal ON transactions(status, tanggal);")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trx_product ON transactions(product);")

        try:
            db.execute(
                "UPDATE sqlite_sequence SET seq=(SELECT MAX(id) FROM transactions) WHERE name='transactions'"
            )
        except Exception:
            pass

        db.commit()
        print("[DB] transaksi FK users_old diperbaiki.")
    except Exception as e:
        db.rollback()
        print(f"[DB] perbaikan FK transactions gagal: {e}")
