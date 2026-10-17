"""
Script migrasi database dari SQLite (tarikgaji-live.db) ke MySQL (gajiku_db)

Cara pakai:
    python -m app.migrasi_local

Pastikan MySQL sudah running dan database gajiku_db sudah ada.
"""

import sqlite3
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    import mysql.connector
except ImportError:
    print("ERROR: mysql-connector-python belum terinstall")
    print("Run: pip install mysql-connector-python")
    sys.exit(1)


# ============= CONFIG =============
SQLITE_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "tarikgaji-live.db"
)

MYSQL_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "root"),
    "password": os.environ.get("MYSQL_PASS", ""),
    "database": os.environ.get("MYSQL_DB", "gajiku_db"),
    "port": int(os.environ.get("MYSQL_PORT", "3306")),
    "autocommit": False,
}


def log(msg, level="INFO"):
    """Print dengan timestamp"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {msg}")


def ensure_mysql_columns(cursor, table_name, columns):
    cursor.execute(f"DESCRIBE {table_name}")
    existing = {row[0] for row in cursor.fetchall()}
    for name, definition in columns.items():
        if name not in existing:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {definition}")


def create_mysql_tables(mysql_conn):
    """Buat struktur tabel di MySQL"""
    cursor = mysql_conn.cursor()
    
    log("Creating tables in MySQL...")
    
    try:
        # Users table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) UNIQUE,
                gaji INT DEFAULT 0,
                created_at DATETIME NOT NULL,
                INDEX idx_users_name (name),
                INDEX idx_users_email (email)
            )
        """)
        
        # Transactions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INT PRIMARY KEY AUTO_INCREMENT,
                user_id INT NOT NULL,
                tanggal DATE NOT NULL,
                periode VARCHAR(7) NOT NULL,
                nominal INT NOT NULL,
                admin_fee INT NOT NULL,
                status VARCHAR(50) NOT NULL,
                keterangan TEXT,
                rekening_tujuan VARCHAR(255) DEFAULT '',
                rekening_tujuan_label VARCHAR(255) DEFAULT '',
                created_at DATETIME NOT NULL,
                product VARCHAR(10) DEFAULT 'reg',
                cancel_until DATETIME,
                notified_onproses INT DEFAULT 0,
                urg_lock_until DATETIME,
                FOREIGN KEY (user_id) REFERENCES users(id),
                INDEX idx_trx_user_periode (user_id, periode),
                INDEX idx_trx_user_periode_status_tanggal (user_id, periode, status, tanggal),
                INDEX idx_trx_tanggal (tanggal),
                INDEX idx_trx_status (status),
                INDEX idx_trx_status_tanggal (status, tanggal),
                INDEX idx_trx_product (product)
            )
        """)
        
        # Admins table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INT PRIMARY KEY AUTO_INCREMENT,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                created_at DATETIME NOT NULL,
                avatar_path VARCHAR(255) DEFAULT '',
                company VARCHAR(255) DEFAULT '',
                role VARCHAR(50) DEFAULT 'admin',
                status_aktif VARCHAR(50) DEFAULT 'aktif',
                no_telp VARCHAR(100) DEFAULT ''
            )
        """)
        
        # Pegawai table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pegawai (
                id INT PRIMARY KEY AUTO_INCREMENT,
                id_pegawai VARCHAR(100) DEFAULT '',
                nama VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                jabatan VARCHAR(255),
                gaji INT DEFAULT 0,
                status_aktif INT DEFAULT 0,
                perusahaan VARCHAR(255) DEFAULT '',
                perusahaan_induk VARCHAR(255) DEFAULT '',
                no_rekening VARCHAR(100) DEFAULT '',
                no_rekening_lain VARCHAR(100) DEFAULT '',
                rekening_ewallet VARCHAR(255) DEFAULT '',
                no_telp VARCHAR(20) DEFAULT '',
                admin_fee_flat INT DEFAULT 15000,
                siklus_gaji VARCHAR(10) DEFAULT 'A',
                created_at DATETIME NOT NULL,
                UNIQUE KEY unique_id_pegawai (id_pegawai),
                UNIQUE KEY unique_email (email)
            )
        """)
        
        # User accounts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_accounts (
                id INT PRIMARY KEY AUTO_INCREMENT,
                pegawai_id INT NOT NULL,
                name VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                status_aktif INT NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL,
                register_ip VARCHAR(50),
                avatar_path VARCHAR(255) DEFAULT '',
                company VARCHAR(255) DEFAULT '',
                FOREIGN KEY (pegawai_id) REFERENCES pegawai(id),
                INDEX idx_pegawai_id (pegawai_id)
            )
        """)
        
        # App settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                `key` VARCHAR(255) PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        
        # Transaction tests table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS txn_tests (
                id INT PRIMARY KEY AUTO_INCREMENT,
                created_at DATETIME NOT NULL
            )
        """)
        
        # Pegawai archive table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pegawai_archive (
                id INT PRIMARY KEY AUTO_INCREMENT,
                pegawai_id INT,
                snapshot TEXT,
                deleted_at DATETIME
            )
        """)
        
        # Ensure missing columns are added if an old MySQL schema exists
        ensure_mysql_columns(cursor, "admins", {
            "role": "role VARCHAR(50) DEFAULT 'admin'",
            "status_aktif": "status_aktif VARCHAR(50) DEFAULT 'aktif'",
            "no_telp": "no_telp VARCHAR(100) DEFAULT ''"
        })
        ensure_mysql_columns(cursor, "pegawai", {
            "perusahaan_induk": "perusahaan_induk VARCHAR(255) DEFAULT ''",
            "siklus_gaji": "siklus_gaji VARCHAR(10) DEFAULT 'A'",
            "no_rekening": "no_rekening VARCHAR(100) DEFAULT ''",
            "no_rekening_lain": "no_rekening_lain VARCHAR(100) DEFAULT ''",
            "rekening_ewallet": "rekening_ewallet VARCHAR(255) DEFAULT ''",
            "no_telp": "no_telp VARCHAR(20) DEFAULT ''",
            "admin_fee_flat": "admin_fee_flat INT DEFAULT 15000"
        })
        ensure_mysql_columns(cursor, "user_accounts", {
            "avatar_path": "avatar_path VARCHAR(255) DEFAULT ''",
            "company": "company VARCHAR(255) DEFAULT ''"
        })
        ensure_mysql_columns(cursor, "transactions", {
            "rekening_tujuan": "rekening_tujuan VARCHAR(255) DEFAULT ''",
            "rekening_tujuan_label": "rekening_tujuan_label VARCHAR(255) DEFAULT ''",
            "product": "product VARCHAR(10) DEFAULT 'reg'",
            "cancel_until": "cancel_until DATETIME",
            "notified_onproses": "notified_onproses INT DEFAULT 0",
            "urg_lock_until": "urg_lock_until DATETIME"
        })
        ensure_mysql_columns(cursor, "app_settings", {
            # no-op: table created above
        })
        ensure_mysql_columns(cursor, "pegawai_archive", {
            "pegawai_id": "pegawai_id INT",
            "snapshot": "snapshot TEXT",
            "deleted_at": "deleted_at DATETIME"
        })

        mysql_conn.commit()
        log("Tables created successfully")
        
    except Exception as e:
        log(f"Error creating tables: {e}", "ERROR")
        mysql_conn.rollback()
        raise


def migrate_table(sqlite_conn, mysql_conn, table_name):
    """Migrasi satu tabel dari SQLite ke MySQL"""
    
    log(f"Migrating table: {table_name}...")
    
    sqlite_cursor = sqlite_conn.cursor()
    mysql_cursor = mysql_conn.cursor()
    
    try:
        # Ambil data dari SQLite
        sqlite_cursor.execute(f"SELECT * FROM {table_name}")
        rows = sqlite_cursor.fetchall()
        
        # Ambil nama kolom dari SQLite
        sqlite_columns = [desc[0] for desc in sqlite_cursor.description]
        
        if not rows:
            log(f"  → Table {table_name} is empty")
            return 0
        
        # Ambil nama kolom dari MySQL
        mysql_cursor.execute(f"DESCRIBE {table_name}")
        mysql_columns = set([desc[0] for desc in mysql_cursor.fetchall()])
        
        # Filter kolom yang ada di MySQL
        valid_columns = [col for col in sqlite_columns if col in mysql_columns]
        
        if not valid_columns:
            log(f"  Warning: No matching columns found for {table_name}")
            return 0
        
        # Disable foreign key checks sementara
        mysql_cursor.execute("SET FOREIGN_KEY_CHECKS=0")
        
        # Clear tabel di MySQL (untuk reset)
        mysql_cursor.execute(f"TRUNCATE TABLE {table_name}")
        
        # Insert rows
        placeholders = ", ".join(["%s"] * len(valid_columns))
        # Escape column names with backticks
        escaped_columns = ", ".join([f"`{col}`" for col in valid_columns])
        insert_sql = f"INSERT INTO {table_name} ({escaped_columns}) VALUES ({placeholders})"
        
        inserted = 0
        for row in rows:
            try:
                # Extract values for valid columns only
                row_values = []
                for i, col in enumerate(sqlite_columns):
                    if col in valid_columns:
                        row_values.append(row[i])
                
                mysql_cursor.execute(insert_sql, row_values)
                inserted += 1
            except Exception as e:
                log(f"  Warning: Skipped row in {table_name}: {e}", "WARN")
                continue
        
        # Re-enable foreign key checks
        mysql_cursor.execute("SET FOREIGN_KEY_CHECKS=1")
        mysql_conn.commit()
        
        log(f"  ✓ {table_name}: {inserted} rows migrated")
        return inserted
        
    except Exception as e:
        log(f"Error migrating {table_name}: {e}", "ERROR")
        mysql_conn.rollback()
        raise


def reset_auto_increment(mysql_conn):
    """Reset auto_increment IDs ke nilai tertinggi"""
    log("Resetting auto_increment counters...")
    
    cursor = mysql_conn.cursor()
    tables = ['users', 'transactions', 'admins', 'pegawai', 'user_accounts', 'txn_tests']
    
    try:
        for table in tables:
            try:
                cursor.execute(f"SELECT MAX(id) FROM {table}")
                max_id = cursor.fetchone()[0]
                
                if max_id:
                    cursor.execute(f"ALTER TABLE {table} AUTO_INCREMENT = {max_id + 1}")
                    log(f"  ✓ {table}: AUTO_INCREMENT = {max_id + 1}")
            except Exception as e:
                log(f"  Warning: Could not reset {table}: {e}", "WARN")
        
        mysql_conn.commit()
        
    except Exception as e:
        log(f"Error resetting auto_increment: {e}", "ERROR")
        mysql_conn.rollback()
        raise


def main():
    if os.environ.get("ALLOW_LEGACY_DB_REPLACE") != "1":
        raise SystemExit("Migrasi legacy ini menghapus isi tabel tujuan. Dinonaktifkan secara default; gunakan migrasidb/ untuk update production. Backup dan izin ALLOW_LEGACY_DB_REPLACE=1 wajib untuk pemindahan database penuh.")
    log("=" * 60)
    log("DATABASE MIGRATION: SQLite → MySQL")
    log("=" * 60)
    log(f"SQLite DB: {SQLITE_DB_PATH}")
    log(f"MySQL DB: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")
    log("")
    
    # Check SQLite file exists
    if not os.path.exists(SQLITE_DB_PATH):
        log(f"ERROR: SQLite database not found: {SQLITE_DB_PATH}", "ERROR")
        sys.exit(1)
    
    # Connect to SQLite
    try:
        sqlite_conn = sqlite3.connect(SQLITE_DB_PATH)
        log("✓ Connected to SQLite")
    except Exception as e:
        log(f"Failed to connect to SQLite: {e}", "ERROR")
        sys.exit(1)
    
    # Connect to MySQL
    try:
        mysql_conn = mysql.connector.connect(**MYSQL_CONFIG)
        log("✓ Connected to MySQL")
    except Exception as e:
        log(f"Failed to connect to MySQL: {e}", "ERROR")
        log("", "INFO")
        log("Make sure:", "INFO")
        log("  1. MySQL is running", "INFO")
        log("  2. Database 'gajiku_db' exists", "INFO")
        log("  3. MySQL credentials are correct", "INFO")
        log("", "INFO")
        log("Or set environment variables:", "INFO")
        log("  MYSQL_HOST=localhost", "INFO")
        log("  MYSQL_USER=root", "INFO")
        log("  MYSQL_PASS=yourpassword", "INFO")
        log("  MYSQL_DB=gajiku_db", "INFO")
        log("  MYSQL_PORT=3306", "INFO")
        sys.exit(1)
    
    try:
        log("")
        
        # 1. Create tables
        create_mysql_tables(mysql_conn)
        log("")
        
        # 2. Migrate data
        log("Migrating data...")
        total_rows = 0
        tables_to_migrate = [
            'users',
            'admins',
            'pegawai',
            'user_accounts',
            'transactions',
            'app_settings',
            'txn_tests',
            'pegawai_archive'
        ]
        
        for table in tables_to_migrate:
            try:
                rows = migrate_table(sqlite_conn, mysql_conn, table)
                total_rows += rows
            except Exception as e:
                log(f"Failed to migrate {table}: {e}", "ERROR")
                # Continue dengan table berikutnya
                continue
        
        log("")
        
        # 3. Reset auto_increment
        reset_auto_increment(mysql_conn)
        log("")
        
        # Summary
        log("=" * 60)
        log(f"✓ MIGRATION COMPLETE!", "SUCCESS")
        log(f"  Total rows migrated: {total_rows}")
        log("=" * 60)
        
    except Exception as e:
        log(f"Migration failed: {e}", "ERROR")
        sys.exit(1)
        
    finally:
        sqlite_conn.close()
        mysql_conn.close()
        log("Connections closed")


if __name__ == "__main__":
    main()
