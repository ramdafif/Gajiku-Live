import json
import os
from pathlib import Path
from uuid import uuid4

import pytest


def pytest_addoption(parser):
    parser.addoption("--mysql-fixture-instance", help="Manifest JSON server MySQL/MariaDB terisolasi di folder tmp repository.")


@pytest.fixture
def database_config(request):
    manifest = request.config.getoption("--mysql-fixture-instance")
    if not manifest:
        yield {}
        return
    from mysql import connector

    temporary_root = (Path(__file__).resolve().parents[1] / "tmp").resolve()
    manifest_path = Path(manifest).resolve()
    if not manifest_path.is_relative_to(temporary_root):
        pytest.fail("Manifest MySQL fixture harus berada di tmp repository, bukan konfigurasi production.")
    instance = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    data_path = Path(instance["data"]).resolve()
    port = int(instance["port"])
    password = os.environ.get("GAJIKU_MYSQL_FIXTURE_PASSWORD")
    if not data_path.is_relative_to(temporary_root) or not 10000 <= port <= 65535 or not password:
        pytest.fail("Fixture membutuhkan datadir tmp terisolasi, port tinggi, dan GAJIKU_MYSQL_FIXTURE_PASSWORD.")
    connection = connector.connect(host="127.0.0.1", port=port, user="root", password=password, connection_timeout=10)
    database = "gajiku_fixture_" + uuid4().hex
    created = False
    try:
        cursor = connection.cursor()
        cursor.execute("SELECT @@datadir")
        if Path(cursor.fetchone()[0]).resolve() != data_path:
            pytest.fail("Datadir server berbeda dari manifest; pengujian dibatalkan sebelum membuat database.")
        cursor.execute(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci")
        created = True
        cursor.execute(f"USE `{database}`")
        from app.db import MySQLConnectionWrapper
        wrapper = MySQLConnectionWrapper(connection)
        source_root = Path(__file__).resolve().parents[1]
        for script in (source_root / "tests/fixtures/mysql_legacy_schema.sql",
                       source_root / "migrasidb/migrasi_kelola_client.sql",
                       source_root / "migrasidb/migrasi_security_hardening.sql",
                       source_root / "migrasidb/migrasi_company_lifecycle.sql"):
            content = "\n".join(line for line in script.read_text(encoding="utf-8").splitlines()
                                if not line.lstrip().startswith("--"))
            wrapper.executescript(content)
        connection.commit()
        yield {"DB_TYPE": "mysql", "MYSQL_HOST": "127.0.0.1", "MYSQL_PORT": port,
               "MYSQL_USER": "root", "MYSQL_PASS": password, "MYSQL_DB": database}
    finally:
        if created:
            connection.cursor().execute(f"DROP DATABASE `{database}`")
        connection.close()
