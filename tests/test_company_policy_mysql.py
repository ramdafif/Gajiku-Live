import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from unittest.mock import patch

import pytest
from flask import Flask, g

from app.db import MySQLConnectionWrapper
from app.routes import web
from app.services.company_policy import ensure_company_policy_schema, guard_cutoff_change, lock_policy

pytestmark = pytest.mark.skipif(os.environ.get("GAJIKU_POLICY_MYSQL_TEST") != "1",
                                reason="Opt-in MySQL integration: dedicated test server required")


@pytest.fixture
def mysql_policy():
    connector = pytest.importorskip("mysql.connector")
    config = {"host": os.environ.get("GAJIKU_MYSQL_TEST_HOST", "127.0.0.1"),
              "port": int(os.environ.get("GAJIKU_MYSQL_TEST_PORT", "3306")),
              "user": os.environ["GAJIKU_MYSQL_TEST_USER"],
              "password": os.environ.get("GAJIKU_MYSQL_TEST_PASSWORD", ""), "autocommit": False}
    name = "gajiku_policy_test_" + uuid.uuid4().hex
    control = connector.connect(**config)
    cursor = control.cursor()
    cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    connections = []
    def connect():
        db = MySQLConnectionWrapper(connector.connect(**config, database=name))
        connections.append(db)
        return db
    try:
        db = connect()
        for statement in [
            "CREATE TABLE app_settings (`key` VARCHAR(190) PRIMARY KEY,value TEXT NOT NULL) ENGINE=InnoDB",
            "CREATE TABLE clients (id INT PRIMARY KEY,name VARCHAR(255),name_key VARCHAR(255),reg_withdrawal_max INT NOT NULL DEFAULT 1000000) ENGINE=InnoDB",
            "CREATE TABLE admins (id INT PRIMARY KEY,company VARCHAR(255),cutoff_mingguan_aktif INT,cutoff_hari INT,produk_reg_aktif INT,produk_urg_aktif INT) ENGINE=InnoDB",
            "CREATE TABLE pegawai (id INT PRIMARY KEY,email VARCHAR(255),siklus_gaji VARCHAR(1),perusahaan_induk VARCHAR(255)) ENGINE=InnoDB",
            "CREATE TABLE users (id INT PRIMARY KEY,email VARCHAR(255),gaji INT) ENGINE=InnoDB",
            "CREATE TABLE transactions (id INT AUTO_INCREMENT PRIMARY KEY,user_id INT,tanggal DATE,periode VARCHAR(30),nominal INT,status VARCHAR(30),product VARCHAR(10)) ENGINE=InnoDB",
        ]:
            db.execute(statement)
        db.execute("INSERT INTO clients VALUES (1,'Test PT','test pt',1000000)")
        db.execute("INSERT INTO admins VALUES (1,'Test PT',1,2,0,1),(2,'Test PT',0,6,1,0)")
        db.execute("INSERT INTO pegawai VALUES (1,'test@fixture.test','D','Test PT')")
        db.execute("INSERT INTO users VALUES (1,'test@fixture.test',6000000)")
        db.execute("INSERT INTO transactions(user_id,tanggal,periode,nominal,status,product) VALUES (1,'2026-12-30','W:2026-12-24',10000,'sukses','reg'),(1,'2027-01-01','W:2026-12-31',20000,'on-proses','urg')")
        db.commit()
        yield db, connect
    finally:
        for connection in connections:
            connection.close()
        assert name.startswith("gajiku_policy_test_") and len(name) == len("gajiku_policy_test_") + 32
        cursor.execute(f"DROP DATABASE `{name}`")
        cursor.close()
        control.close()


def test_mysql_weekly_migration_financial_parity_and_no_history_changes(mysql_policy):
    db, connect = mysql_policy
    app = Flask("mysql-policy-fixture")
    days = [date(2026, 12, 24) + timedelta(days=offset) for offset in range(18)]
    history = db.execute("SELECT * FROM transactions ORDER BY id").fetchall()
    with app.app_context():
        g.db = db
        with patch.object(web, "get_weekly_cutoff_config", return_value={"cutoff_hari": 2}):
            before = [web.compute_limits(6000000, day, 1) for day in days]
        ensure_company_policy_schema(db, "mysql")
        after = [web.compute_limits(6000000, day, 1) for day in days]
    assert before == after
    assert db.execute("SELECT * FROM transactions ORDER BY id").fetchall() == history
    assert db.execute("SELECT reg_withdrawal_max_override FROM pegawai").fetchone()["reg_withdrawal_max_override"] is None
    assert db.execute("SELECT cutoff_hari,produk_reg_aktif,produk_urg_aktif FROM clients").fetchone() == {"cutoff_hari": 2, "produk_reg_aktif": 1, "produk_urg_aktif": 0}


def test_mysql_mutex_serializes_cutoff_and_pending_request(mysql_policy):
    db, connect = mysql_policy
    ensure_company_policy_schema(db, "mysql")
    db.execute("DELETE FROM transactions")
    db.commit()
    entered = threading.Event()
    release = threading.Event()
    second_attempt = threading.Event()
    def withdrawal():
        connection = connect()
        lock_policy(connection)
        connection.execute("INSERT INTO transactions(user_id,tanggal,periode,nominal,status,product) VALUES (1,'2026-12-30','W:2026-12-24',10000,'on-proses','reg')")
        entered.set()
        assert release.wait(10)
        connection.commit()
    def cutoff():
        connection = connect()
        second_attempt.set()
        lock_policy(connection)
        current = connection.execute("SELECT * FROM clients WHERE id=1").fetchone()
        try:
            with pytest.raises(ValueError, match="on-proses"):
                guard_cutoff_change(connection, "Test PT", current, {"cutoff_mingguan_aktif": 1, "cutoff_hari": 4}, today=date(2026, 12, 30))
        finally:
            connection.rollback()
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(withdrawal)
        assert entered.wait(10)
        second = executor.submit(cutoff)
        assert second_attempt.wait(10)
        time.sleep(0.15)
        assert not second.done()
        release.set()
        first.result(timeout=15)
        second.result(timeout=15)
