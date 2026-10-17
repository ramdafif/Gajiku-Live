import sqlite3
from pathlib import Path


def main():
    path = Path(__file__).resolve().parents[1] / "data" / "tarikgaji-live.db"
    if not path.is_file():
        raise SystemExit("Database SQLite tidak ditemukan. Tidak ada database yang dibuat atau diubah.")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        for table in ["admins", "pegawai", "user_accounts", "transactions", "app_settings", "users"]:
            print("TABLE", table)
            for column in connection.execute(f"PRAGMA table_info('{table}')").fetchall():
                print(column[1], column[2])
            total = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            print("row_count", total)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
