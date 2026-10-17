-- GajiKu security schema migration for MySQL and MariaDB.
-- Adds verification/rate-limit/idempotency/archive structures without removing data.
-- Safe to rerun; conditionally adds legacy columns and indexes.

CREATE TABLE IF NOT EXISTS security_rate_limits (
    scope_key VARCHAR(64) NOT NULL PRIMARY KEY,
    window_start BIGINT NOT NULL,
    attempts INT NOT NULL,
    KEY idx_security_rate_window (window_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS pending_registrations (
    email VARCHAR(255) NOT NULL PRIMARY KEY,
    token_hash VARCHAR(64) NOT NULL,
    pegawai_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    expires_at BIGINT NOT NULL,
    UNIQUE KEY uq_pending_registrations_token (token_hash),
    KEY idx_pending_registrations_expiry (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS pegawai_archive (
    pegawai_id INT NULL,
    snapshot LONGTEXT NULL,
    deleted_at VARCHAR(40) NULL,
    KEY idx_pegawai_archive_id (pegawai_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

SET @gajiku_request_key_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'transactions' AND column_name = 'request_key'
);
SET @gajiku_request_key_ddl := IF(
    @gajiku_request_key_exists = 0,
    'ALTER TABLE transactions ADD COLUMN request_key VARCHAR(64) NULL',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_request_key_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;

SET @gajiku_request_key_index_exists := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE() AND table_name = 'transactions'
      AND index_name = 'idx_transactions_request_key'
);
SET @gajiku_request_key_index_ddl := IF(
    @gajiku_request_key_index_exists = 0,
    'CREATE UNIQUE INDEX idx_transactions_request_key ON transactions (request_key)',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_request_key_index_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;

SET @gajiku_admin_role_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admins' AND column_name = 'role'
);
SET @gajiku_admin_role_ddl := IF(
    @gajiku_admin_role_exists = 0,
    'ALTER TABLE admins ADD COLUMN role VARCHAR(50) NOT NULL DEFAULT ''admin''',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_admin_role_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;

SET @gajiku_admin_active_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admins' AND column_name = 'status_aktif'
);
SET @gajiku_admin_active_ddl := IF(
    @gajiku_admin_active_exists = 0,
    'ALTER TABLE admins ADD COLUMN status_aktif TINYINT NOT NULL DEFAULT 1',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_admin_active_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;

SET @gajiku_admin_phone_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'admins' AND column_name = 'no_telp'
);
SET @gajiku_admin_phone_ddl := IF(
    @gajiku_admin_phone_exists = 0,
    'ALTER TABLE admins ADD COLUMN no_telp VARCHAR(50) DEFAULT ''''',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_admin_phone_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;
