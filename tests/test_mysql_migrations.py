import os
import subprocess
import sys
from pathlib import Path

import pytest


os.environ["START_INLINE_WORKER"] = "0"
os.environ["EXPORT_CSV_ENABLED"] = "0"


def test_mysql_migration_retries_keep_existing_records(database_config):
    if database_config.get("DB_TYPE") != "mysql":
        pytest.skip("Memerlukan --mysql-fixture-instance; tidak boleh memakai database production.")
    from mysql import connector
    from app.db import MySQLConnectionWrapper

    connection = connector.connect(host=database_config["MYSQL_HOST"], port=database_config["MYSQL_PORT"],
                                   user=database_config["MYSQL_USER"], password=database_config["MYSQL_PASS"],
                                   database=database_config["MYSQL_DB"])
    wrapper = MySQLConnectionWrapper(connection)
    try:
        cursor = wrapper.execute("INSERT INTO clients (name,name_key,created_at,updated_at) VALUES (?,?,?,?)",
                                 ("Fixture Migration", "fixture migration", "2026-10-19", "2026-10-19"))
        client_id = cursor.lastrowid
        wrapper.execute("INSERT INTO client_billing (client_id,due_day,first_due,updated_by,updated_at) VALUES (?,10,'2026-11-10','fixture','2026-10-19')",
                        (client_id,))
        wrapper.execute("INSERT INTO client_bills (client_id,period,due_on,paid_at,paid_by,created_at) VALUES (?,'2026-10','2026-11-10','2026-11-09','fixture','2026-10-19')",
                        (client_id,))
        cursor = wrapper.execute("INSERT INTO notifications (event_key,client_id,kind,audience,period_key,title,message,created_at) "
                                 "VALUES ('fixture-event',?,'billing_warning','management','2026-10','Fixture','Fixture','2026-11-07')",
                                 (client_id,))
        wrapper.execute("INSERT INTO notification_reads (notification_id,principal,read_at) VALUES (?,'admin:fixture','2026-11-08')",
                        (cursor.lastrowid,))
        connection.commit()
        tables = ("clients", "client_projects", "client_billing", "client_bills", "client_mou_actions", "notifications", "notification_reads")
        before = {table: wrapper.execute(f"SELECT * FROM `{table}` ORDER BY 1").fetchall() for table in tables}
        source_root = Path(__file__).resolve().parents[1]
        for repeat in range(2):
            for name in ("migrasi_kelola_client.sql", "migrasi_security_hardening.sql", "migrasi_company_lifecycle.sql"):
                content = "\n".join(line for line in (source_root / "migrasidb" / name).read_text(encoding="utf-8").splitlines()
                                    if not line.lstrip().startswith("--"))
                wrapper.executescript(content)
        connection.commit()
        after = {table: wrapper.execute(f"SELECT * FROM `{table}` ORDER BY 1").fetchall() for table in tables}
        assert after == before
        wrapper.execute("INSERT IGNORE INTO client_bills (client_id,period,due_on,created_at) VALUES (?,'2026-10','2026-11-10','2026-10-19')",
                        (client_id,))
        assert wrapper.execute("SELECT COUNT(*) AS total FROM client_bills WHERE client_id=?", (client_id,)).fetchone()["total"] == 1
    finally:
        connection.close()


def test_wsgi_cli_sync_uses_isolated_mysql(database_config):
    if database_config.get("DB_TYPE") != "mysql":
        pytest.skip("Memerlukan --mysql-fixture-instance; tidak boleh memakai database production.")
    from mysql import connector

    environment = {**os.environ, **{key: str(value) for key, value in database_config.items()},
                   "APP_SECRET": "isolated-cli-fixture-not-production", "START_INLINE_WORKER": "0",
                   "EXPORT_CSV_ENABLED": "0", "SEED_DEMO_DATA": "0", "ALLOW_DATE_OVERRIDE": "0"}
    result = subprocess.run([sys.executable, "-m", "flask", "--app", "wsgi:app", "sync-company-lifecycle"],
                            cwd=Path(__file__).resolve().parents[1], env=environment,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Sinkronisasi selesai:" in result.stdout
    connection = connector.connect(host=database_config["MYSQL_HOST"], port=database_config["MYSQL_PORT"],
                                   user=database_config["MYSQL_USER"], password=database_config["MYSQL_PASS"],
                                   database=database_config["MYSQL_DB"])
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT value FROM app_settings WHERE `key`='company_lifecycle_last_success'")
        assert cursor.fetchone()[0]
    finally:
        connection.close()
