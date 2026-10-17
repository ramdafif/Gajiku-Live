import os
from getpass import getpass
import sqlite3
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "..", "data", "tarikgaji-live.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def update_password():
    if not os.path.exists(DB_PATH):
        print(f"Database tidak ditemukan: {DB_PATH}")
        return

    print("Pilih jenis akun yang password-nya ingin diubah:")
    print("1. Admin")
    print("2. Pegawai")
    choice = input("Masukkan pilihan (1/2): ").strip()

    if choice == "1":
        table = "admins"
        email_field = "email"
        label = "Admin"
        print("Mode: Admin")
    elif choice == "2":
        table = "user_accounts"
        email_field = "email"
        label = "Pegawai"
        print("Mode: Pegawai")
    else:
        print("Pilihan tidak valid.")
        return

    email = input("Masukkan email akun: ").strip()
    new_password = getpass("Masukkan password baru: ")

    if not email or not new_password:
        print("Email dan password baru wajib diisi.")
        return

    if not 6 <= len(new_password) <= 256:
        print("Password minimal 6 karakter.")
        return

    conn = get_db()
    try:
        existing = conn.execute(
            f"SELECT id, name, {email_field} FROM {table} WHERE LOWER({email_field}) = LOWER(?)",
            (email,),
        ).fetchone()

        if not existing:
            print(f"Akun dengan email '{email}' tidak ditemukan di tabel {table}.")
            return

        password_hash = generate_password_hash(new_password)

        if table == "admins":
            conn.execute(
                "UPDATE admins SET password_hash=? WHERE id=?",
                (password_hash, existing["id"]),
            )
        else:
            conn.execute(
                "UPDATE user_accounts SET password_hash=? WHERE id=?",
                (password_hash, existing["id"]),
            )

        conn.commit()
        print(f"Berhasil mengubah password untuk {label}: {existing['name']} ({existing[email_field]})")
    except Exception as e:
        print(f"Terjadi error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    update_password()
