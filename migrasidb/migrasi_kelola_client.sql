-- GajiKu client/project catalog migration for MySQL and MariaDB.
-- Backfills legacy company/project values without deleting or merging employees.
-- Safe to rerun; migration data changes run once via app_settings marker.

CREATE TABLE IF NOT EXISTS clients (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    name_key VARCHAR(255) NOT NULL,
    address TEXT,
    email VARCHAR(255) DEFAULT '',
    phone VARCHAR(50) DEFAULT '',
    npwp VARCHAR(50) DEFAULT '',
    pic_name VARCHAR(255) DEFAULT '',
    pic_role VARCHAR(100) DEFAULT '',
    pic_email VARCHAR(255) DEFAULT '',
    pic_phone VARCHAR(50) DEFAULT '',
    allow_projects TINYINT NOT NULL DEFAULT 0,
    mou_number VARCHAR(255) DEFAULT '',
    mou_start DATE NULL,
    mou_end DATE NULL,
    notes TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_clients_name_key (name_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS client_projects (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    client_id INT NOT NULL,
    name VARCHAR(255) NOT NULL,
    name_key VARCHAR(255) NOT NULL,
    location VARCHAR(255) DEFAULT '',
    pic_name VARCHAR(255) DEFAULT '',
    pic_email VARCHAR(255) DEFAULT '',
    pic_phone VARCHAR(50) DEFAULT '',
    notes TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE KEY uq_client_projects_name (client_id, name_key),
    CONSTRAINT fk_client_projects_client FOREIGN KEY (client_id) REFERENCES clients(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

SET @gajiku_parent_column_exists := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'pegawai' AND column_name = 'perusahaan_induk'
);
SET @gajiku_parent_column_ddl := IF(
    @gajiku_parent_column_exists = 0,
    'ALTER TABLE pegawai ADD COLUMN perusahaan_induk VARCHAR(255) NOT NULL DEFAULT ''''',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_parent_column_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;

SET @gajiku_clients_backfill := NOT EXISTS (
    SELECT 1 FROM app_settings WHERE `key` = 'clients_catalog_v1'
);

UPDATE pegawai SET perusahaan_induk = 'Guixu'
WHERE @gajiku_clients_backfill AND LOWER(TRIM(perusahaan)) = 'guixu';

UPDATE pegawai SET perusahaan_induk = 'Springhill'
WHERE @gajiku_clients_backfill AND LOWER(TRIM(perusahaan)) IN ('spring hill', 'springhill');

UPDATE pegawai SET perusahaan = CASE LOWER(TRIM(perusahaan))
    WHEN 'cakra' THEN 'Cakra'
    WHEN 'ptpi' THEN 'PTPI'
    WHEN 'inp' THEN 'INP'
    WHEN 'sii' THEN 'SII'
    WHEN 'pmr bitung' THEN 'PMR Bitung'
    WHEN 'budidharma' THEN 'Budidharma'
    WHEN 'paul' THEN 'Paul'
    WHEN 'fleminh' THEN 'Fleming'
    WHEN 'fleming' THEN 'Fleming'
    WHEN 'spring hill' THEN 'Springhill'
    WHEN 'springhill' THEN 'Springhill'
    ELSE perusahaan
END
WHERE @gajiku_clients_backfill AND perusahaan IS NOT NULL;

INSERT IGNORE INTO clients (name, name_key, created_at, updated_at)
SELECT company_name, LOWER(TRIM(company_name)), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM (
    SELECT company AS company_name FROM admins
    WHERE company IS NOT NULL AND TRIM(company) <> ''
    UNION
    SELECT perusahaan_induk AS company_name FROM pegawai
    WHERE perusahaan_induk IS NOT NULL AND TRIM(perusahaan_induk) <> ''
) AS legacy_companies
WHERE @gajiku_clients_backfill;

-- Retain and adopt the legacy Windu project catalog if it exists.
SET @gajiku_windu_table_exists := (
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = DATABASE() AND table_name = 'windu_projects'
);
SET @gajiku_windu_client_ddl := IF(
    @gajiku_windu_table_exists > 0 AND @gajiku_clients_backfill,
    'INSERT IGNORE INTO clients (name, name_key, created_at, updated_at) VALUES (''Windu Karya'', ''windu karya'', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_windu_client_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;

INSERT IGNORE INTO client_projects (client_id, name, name_key, created_at, updated_at)
SELECT c.id, TRIM(p.perusahaan), LOWER(TRIM(p.perusahaan)), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM pegawai AS p
JOIN clients AS c ON c.name_key = LOWER(TRIM(p.perusahaan_induk))
WHERE @gajiku_clients_backfill
  AND TRIM(COALESCE(p.perusahaan, '')) <> ''
  AND TRIM(COALESCE(p.perusahaan_induk, '')) <> '';

SET @gajiku_windu_projects_ddl := IF(
    @gajiku_windu_table_exists > 0 AND @gajiku_clients_backfill,
    'INSERT IGNORE INTO client_projects (client_id, name, name_key, created_at, updated_at) SELECT c.id, TRIM(w.nama_project), LOWER(TRIM(w.nama_project)), CURRENT_TIMESTAMP, CURRENT_TIMESTAMP FROM windu_projects AS w JOIN clients AS c ON c.name_key = ''windu karya'' WHERE TRIM(COALESCE(w.nama_project, '''')) <> ''''',
    'SELECT 1'
);
PREPARE gajiku_stmt FROM @gajiku_windu_projects_ddl;
EXECUTE gajiku_stmt;
DEALLOCATE PREPARE gajiku_stmt;

UPDATE clients AS c
SET c.allow_projects = 1,
    c.notes = COALESCE(NULLIF(c.notes, ''), 'Diimpor dari data lama. Verifikasi penggunaan project sesuai MoU.')
WHERE @gajiku_clients_backfill
  AND EXISTS (SELECT 1 FROM client_projects AS p WHERE p.client_id = c.id);

INSERT IGNORE INTO app_settings (`key`, value)
SELECT 'clients_catalog_v1', DATE_FORMAT(CURRENT_TIMESTAMP, '%Y-%m-%d %H:%i:%s')
WHERE @gajiku_clients_backfill;
