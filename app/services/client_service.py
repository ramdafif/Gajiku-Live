from datetime import datetime
import sqlite3


PROJECT_NAMES = {
    "cakra": "Cakra", "ptpi": "PTPI", "inp": "INP", "sii": "SII",
    "pmr bitung": "PMR Bitung", "budidharma": "Budidharma",
    "paul": "Paul", "fleminh": "Fleming", "fleming": "Fleming",
    "spring hill": "Springhill", "springhill": "Springhill",
}


def name_key(value):
    return (value or "").strip().lower()


def client_by_name(db, name, lock=False):
    suffix = " FOR UPDATE" if lock and not isinstance(db, sqlite3.Connection) else ""
    row = db.execute("SELECT * FROM clients WHERE name_key=?" + suffix, (name_key(name),)).fetchone()
    return dict(row) if row else None


def project_in_use(db, client, project_key=None):
    sql = "SELECT 1 FROM pegawai WHERE LOWER(TRIM(perusahaan_induk))=?"
    params = [client["name_key"]]
    if project_key is None:
        sql += " AND TRIM(COALESCE(perusahaan,''))<>''"
    else:
        sql += " AND LOWER(TRIM(perusahaan))=?"
        params.append(project_key)
    sql += " LIMIT 1"
    if not isinstance(db, sqlite3.Connection):
        sql += " FOR UPDATE"
    return bool(db.execute(sql, params).fetchone())


def list_clients(db):
    return [dict(row) for row in db.execute("SELECT * FROM clients ORDER BY name_key").fetchall()]


def list_projects(db, client_id):
    return [dict(row) for row in db.execute(
        "SELECT * FROM client_projects WHERE client_id=? ORDER BY name_key", (client_id,)
    ).fetchall()]


def insert_ignoring_duplicates(db, db_type, table, columns, values):
    verb = "INSERT OR IGNORE" if db_type == "sqlite" else "INSERT IGNORE"
    placeholders = ",".join("?" for column in columns)
    column_names = ",".join(f"`{column}`" for column in columns)
    db.execute(f"{verb} INTO {table} ({column_names}) VALUES ({placeholders})", values)


def ensure_clients_schema(db, db_type):
    identity = "INTEGER PRIMARY KEY AUTOINCREMENT" if db_type == "sqlite" else "INT AUTO_INCREMENT PRIMARY KEY"
    suffix = "" if db_type == "sqlite" else " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
    client_schema = f"""
        CREATE TABLE IF NOT EXISTS clients (
            id {identity}, name VARCHAR(255) NOT NULL,
            name_key VARCHAR(255) NOT NULL UNIQUE,
            address TEXT, email VARCHAR(255) DEFAULT '', phone VARCHAR(50) DEFAULT '',
            npwp VARCHAR(50) DEFAULT '', pic_name VARCHAR(255) DEFAULT '',
            pic_role VARCHAR(100) DEFAULT '', pic_email VARCHAR(255) DEFAULT '',
            pic_phone VARCHAR(50) DEFAULT '', allow_projects INTEGER NOT NULL DEFAULT 0,
            mou_number VARCHAR(255) DEFAULT '', mou_start DATE, mou_end DATE,
            notes TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL
        ){suffix}
    """
    project_schema = f"""
        CREATE TABLE IF NOT EXISTS client_projects (
            id {identity}, client_id INTEGER NOT NULL,
            name VARCHAR(255) NOT NULL, name_key VARCHAR(255) NOT NULL,
            location VARCHAR(255) DEFAULT '', pic_name VARCHAR(255) DEFAULT '',
            pic_email VARCHAR(255) DEFAULT '', pic_phone VARCHAR(50) DEFAULT '',
            notes TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL,
            UNIQUE (client_id, name_key),
            FOREIGN KEY (client_id) REFERENCES clients(id)
        ){suffix}
    """
    for table, schema in (("clients", client_schema), ("client_projects", project_schema)):
        if db_type == "sqlite":
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        else:
            exists = db.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=?", (table,)).fetchone()
        if not exists:
            db.execute(schema)
    if db_type == "sqlite":
        columns = {row[1] for row in db.execute("PRAGMA table_info(pegawai)").fetchall()}
        if "perusahaan_induk" not in columns:
            db.execute("ALTER TABLE pegawai ADD COLUMN perusahaan_induk TEXT DEFAULT ''")
    db.commit()
    if db_type == "sqlite":
        db.execute("BEGIN IMMEDIATE")
    marker = "clients_catalog_v1"
    if db.execute("SELECT value FROM app_settings WHERE `key`=?", (marker,)).fetchone():
        db.commit()
        return
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        db.execute("""UPDATE pegawai SET perusahaan_induk='Guixu'
                      WHERE LOWER(TRIM(perusahaan))='guixu'""")
        db.execute("""UPDATE pegawai SET perusahaan_induk='Springhill'
                      WHERE LOWER(TRIM(perusahaan)) IN ('spring hill', 'springhill')""")
        for original, canonical in PROJECT_NAMES.items():
            db.execute("UPDATE pegawai SET perusahaan=? WHERE LOWER(TRIM(perusahaan))=?",
                       (canonical, original))
        company_names = {}
        for row in db.execute("SELECT DISTINCT company FROM admins").fetchall():
            if name_key(row["company"]):
                company_names.setdefault(name_key(row["company"]), row["company"].strip())
        employees = db.execute("SELECT DISTINCT perusahaan_induk, perusahaan FROM pegawai").fetchall()
        for row in employees:
            if name_key(row["perusahaan_induk"]):
                company_names.setdefault(name_key(row["perusahaan_induk"]), row["perusahaan_induk"].strip())
        if db_type == "sqlite":
            legacy_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='windu_projects'").fetchone()
        else:
            legacy_exists = db.execute("SHOW TABLES LIKE 'windu_projects'").fetchone()
        legacy_projects = db.execute("SELECT nama_project FROM windu_projects").fetchall() if legacy_exists else []
        if legacy_projects:
            company_names.setdefault("windu karya", "Windu Karya")
        for key, name in company_names.items():
            insert_ignoring_duplicates(db, db_type, "clients",
                                      ("name", "name_key", "created_at", "updated_at"),
                                      (name, key, timestamp, timestamp))
        for row in employees:
            client = client_by_name(db, row["perusahaan_induk"])
            if client and name_key(row["perusahaan"]):
                project = row["perusahaan"].strip()
                insert_ignoring_duplicates(db, db_type, "client_projects",
                                          ("client_id", "name", "name_key", "created_at", "updated_at"),
                                          (client["id"], project, name_key(project), timestamp, timestamp))
        for row in legacy_projects:
            project = row["nama_project"].strip()
            if project:
                client = client_by_name(db, "Windu Karya")
                insert_ignoring_duplicates(db, db_type, "client_projects",
                                          ("client_id", "name", "name_key", "created_at", "updated_at"),
                                          (client["id"], project, name_key(project), timestamp, timestamp))
        db.execute("""UPDATE clients SET allow_projects=1,
                      notes='Diimpor dari data lama. Verifikasi penggunaan project sesuai MoU.'
                      WHERE EXISTS (SELECT 1 FROM client_projects WHERE client_id=clients.id)""")
        insert_ignoring_duplicates(db, db_type, "app_settings", ("key", "value"), (marker, timestamp))
        db.commit()
    except Exception:
        db.rollback()
        raise


def employee_assignment(db, company, project, is_superadmin, admin_company, existing=None):
    company = (company or "").strip()
    project = (project or "").strip()
    if not is_superadmin:
        if not name_key(admin_company):
            raise ValueError("Akun admin belum ditautkan ke perusahaan.")
        if name_key(company) != name_key(admin_company):
            raise ValueError("Perusahaan terkunci sesuai perusahaan akun admin.")
        if existing and name_key(existing["perusahaan_induk"]) != name_key(admin_company):
            raise ValueError("Anda tidak berwenang mengubah pegawai perusahaan ini.")
    if existing and company == (existing["perusahaan_induk"] or "") and project == (existing["perusahaan"] or ""):
        return existing["perusahaan_induk"], existing["perusahaan"]
    if not company:
        raise ValueError("Pilih perusahaan yang sudah terdaftar di Kelola Client.")
    client = client_by_name(db, company, lock=True)
    if not client:
        raise ValueError("Perusahaan belum terdaftar di Kelola Client.")
    if client["allow_projects"]:
        registered = db.execute("SELECT name FROM client_projects WHERE client_id=? AND name_key=?",
                                (client["id"], name_key(project))).fetchone()
        if not registered:
            raise ValueError("Pilih project resmi perusahaan ini. Tambahkan project di Kelola Projects terlebih dahulu.")
        project = registered["name"]
    elif project:
        raise ValueError("Perusahaan ini tidak menggunakan project sesuai pengaturan MoU.")
    return client["name"], project
