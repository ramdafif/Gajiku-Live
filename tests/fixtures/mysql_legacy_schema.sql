CREATE TABLE users (
    id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL,
    email VARCHAR(255) UNIQUE, gaji BIGINT DEFAULT 0, created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE transactions (
    id INT AUTO_INCREMENT PRIMARY KEY, user_id INT NOT NULL,
    tanggal DATE NOT NULL, periode VARCHAR(32) NOT NULL,
    nominal BIGINT NOT NULL, admin_fee BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL, keterangan TEXT,
    rekening_tujuan VARCHAR(255) DEFAULT '', rekening_tujuan_label VARCHAR(255) DEFAULT '',
    created_at DATETIME NOT NULL, product VARCHAR(16) DEFAULT 'reg',
    cancel_until VARCHAR(40), notified_onproses INT DEFAULT 0, notified_sukses INT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE admins (
    id INT AUTO_INCREMENT PRIMARY KEY, name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL,
    created_at DATETIME NOT NULL, avatar_path VARCHAR(255) DEFAULT '', company VARCHAR(255) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE pegawai (
    id INT AUTO_INCREMENT PRIMARY KEY, id_pegawai VARCHAR(32) DEFAULT '',
    nama VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
    jabatan VARCHAR(255), gaji BIGINT DEFAULT 0, status_aktif INT DEFAULT 0,
    perusahaan VARCHAR(255) DEFAULT '', perusahaan_induk VARCHAR(255) DEFAULT '',
    no_rekening VARCHAR(255) DEFAULT '', no_rekening_lain VARCHAR(255) DEFAULT '',
    rekening_ewallet VARCHAR(255) DEFAULT '', no_telp VARCHAR(50) DEFAULT '',
    admin_fee_flat INT DEFAULT 15000, created_at DATETIME NOT NULL,
    siklus_gaji VARCHAR(8) DEFAULT 'A', jam_cutoff VARCHAR(8) DEFAULT '1500'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE user_accounts (
    id INT AUTO_INCREMENT PRIMARY KEY, pegawai_id INT NOT NULL,
    name VARCHAR(255) NOT NULL, email VARCHAR(255) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL, status_aktif INT DEFAULT 0,
    created_at DATETIME NOT NULL, register_ip VARCHAR(255),
    avatar_path VARCHAR(255) DEFAULT '', company VARCHAR(255) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE app_settings (`key` VARCHAR(191) PRIMARY KEY, value TEXT NOT NULL)
    ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE txn_tests (id INT AUTO_INCREMENT PRIMARY KEY, created_at DATETIME NOT NULL)
    ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
