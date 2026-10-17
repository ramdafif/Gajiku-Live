import os
from getpass import getpass
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "data", "tarikgaji-live.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def add_column_if_not_exists(conn, table_name, column_name, column_def):
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")]
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")
        conn.commit()


def create_new_superadmin():
    if not os.path.exists(DB_PATH):
        print(f"Database tidak ditemukan: {DB_PATH}")
        return

    conn = get_db()
    try:
        add_column_if_not_exists(conn, "admins", "role", "TEXT DEFAULT 'admin'")
        add_column_if_not_exists(conn, "admins", "status_aktif", "INTEGER DEFAULT 1")
        add_column_if_not_exists(conn, "admins", "company", "TEXT DEFAULT ''")
        add_column_if_not_exists(conn, "admins", "no_telp", "TEXT DEFAULT ''")

        name = input("Masukkan nama admin baru: ").strip()
        email = input("Masukkan email admin baru: ").strip()
        password = getpass("Masukkan password admin baru: ")

        if not name or not email or not 6 <= len(password) <= 256:
            print("Nama, email, dan password wajib diisi.")
            return

        exists = conn.execute(
            "SELECT 1 FROM admins WHERE LOWER(email)=LOWER(?)",
            (email,),
        ).fetchone()
        if exists:
            print(f"Email '{email}' sudah ada di database.")
            return

        conn.execute(
            """
            INSERT INTO admins (name, email, password_hash, company, no_telp, role, status_aktif, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                email,
                generate_password_hash(password),
                "",
                "",
                "superadmin",
                1,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        print(f"Berhasil menambahkan admin baru: {name} ({email}) dengan role superadmin")

    except Exception as e:
        print(f"Terjadi error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    create_new_superadmin()
