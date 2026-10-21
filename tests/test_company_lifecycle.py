import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import pytest
from werkzeug.security import generate_password_hash

os.environ["START_INLINE_WORKER"] = "0"
os.environ["EXPORT_CSV_ENABLED"] = "0"

from app import create_app
from app.db import get_db
from app.routes import web
from app.services import company_lifecycle as lifecycle
from app.utils.cache import _cache

PASSWORD = "FixtureCompanyPassword123"
HASH = generate_password_hash(PASSWORD)


def at(month=11, day=10, hour=12, year=2026, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=lifecycle.WIB)


class Harness:
    def __init__(self, app):
        self.app = app
        self.client = app.test_client()

    def query(self, sql, params=()):
        with self.app.app_context():
            db = get_db()
            cursor = db.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchall()] if cursor.description else []
            db.commit()
            return rows

    def login(self, email="super@fixture.test", client=None):
        client = client or self.client
        client.get("/login")
        response = self.post("/login", {"email": email, "password": PASSWORD}, client)
        assert response.status_code == 302
        assert client.get(response.location).status_code == 200
        return client

    def post(self, path, data=None, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            token = session.get("_csrf_token")
        return client.post(path, data={**(data or {}), "csrf_token": token})

    def action(self, operation, *args, client_id=1, now=None):
        with self.app.app_context():
            db = get_db()
            with lifecycle.locked_client(db, client_id) as client:
                operation(db, client, *args, actor="fixture-super", now=now)

    def sync(self, now):
        with self.app.app_context():
            lifecycle.sync_clients(get_db(), lifecycle.visible_clients(get_db(), True, None), now)

    def state(self, now=None, client_id=1):
        with self.app.app_context():
            return lifecycle.billing_state(get_db(), client_id, now)


@pytest.fixture
def harness(tmp_path, monkeypatch, database_config):
    _cache.clear()
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(10, 19))
    monkeypatch.setattr(web, "business_today", lambda: lifecycle.business_now().date())
    monkeypatch.setattr(web, "enqueue_email", lambda *args, **kwargs: None)
    app = create_app({"TESTING": True, "DB_TYPE": "sqlite", "DB_PATH": str(tmp_path / "company.db"),
                      "APP_SECRET": "isolated-company-fixture-not-production", "SESSION_COOKIE_SECURE": False,
                      "ADMIN_PASSWORD": "", "SUPERADMIN_PASSWORD": "", "SEED_DEMO_DATA": False,
                      "ALLOW_DATE_OVERRIDE": False, "ALLOW_DESTRUCTIVE_RESET": False, **database_config})
    result = Harness(app)
    with app.app_context():
        db = get_db()
        db.execute("DELETE FROM client_projects")
        db.execute("DELETE FROM clients")
        db.execute("DELETE FROM admins")
        for client_id, company in ((1, "Fixture PT One"), (2, "Fixture PT Two")):
            db.execute("INSERT INTO clients (id,name,name_key,allow_projects,created_at,updated_at) VALUES (?,?,?,1,?,?)",
                       (client_id, company, company.lower(), "2026-01-01", "2026-01-01"))
        for admin_id, email, company, role in ((1, "super@fixture.test", None, "superadmin"),
                                               (2, "admin@fixture.test", "Fixture PT One", "admin"),
                                               (3, "other@fixture.test", "Fixture PT Two", "admin"),
                                               (4, "colleague@fixture.test", "Fixture PT One", "admin")):
            db.execute("INSERT INTO admins (id,name,email,password_hash,company,role,status_aktif,hak_approval,created_at) "
                       "VALUES (?,?,?,?,?,?,1,1,?)", (admin_id, email, email, HASH, company, role, "2026-01-01"))
        for employee_id, company, active in ((1, "Fixture PT One", 1), (2, "Fixture PT Two", 1),
                                              (3, "Fixture PT One", 0), (4, "Fixture PT One", 1)):
            email = f"employee{employee_id}@fixture.test"
            db.execute("INSERT INTO pegawai (id,id_pegawai,nama,email,gaji,status_aktif,perusahaan,perusahaan_induk,no_rekening,siklus_gaji,created_at) "
                       "VALUES (?,?,?,?,6000000,?,?,?,?, 'A',?)",
                       (employee_id, f"E{employee_id}", f"Employee {employee_id}", email, active,
                        f"Project {employee_id}", company, "123456", "2026-01-01"))
            db.execute("INSERT INTO user_accounts (pegawai_id,name,email,password_hash,status_aktif,created_at) VALUES (?,?,?,?,?,?)",
                       (employee_id, email, email, HASH, active, "2026-01-01"))
            db.execute("INSERT INTO users (id,name,email,gaji,created_at) VALUES (?,?,?,6000000,?)",
                       (employee_id, email, email, "2026-01-01"))
        db.commit()
    return result


def test_unconfigured_and_first_due_are_not_retroactive(harness):
    assert not harness.state()["blocked"]
    harness.action(lifecycle.configure_billing, 10, now=at(10, 19))
    state = harness.state(at(10, 19))
    assert not state["blocked"]
    assert str(state["bills"][0]["due_on"]) == "2026-11-10"
    assert state["bills"][0]["period"] == "2026-10"
    assert not harness.state(client_id=2)["configured"]


@pytest.mark.parametrize("year,month,expected", [(2026, 2, 28), (2028, 2, 29), (2026, 4, 30), (2026, 12, 31)])
def test_short_months_and_leap_years(year, month, expected):
    assert lifecycle.due_date(year, month, 31) == date(year, month, expected)
    assert lifecycle.following_due(date(2026, 12, 31), 1) == date(2027, 1, 1)


@pytest.mark.parametrize("value", [0, 32, "", "10.5", "invalid"])
def test_invalid_billing_day(harness, value):
    with pytest.raises(ValueError):
        harness.action(lifecycle.configure_billing, value)
    assert not harness.state()["configured"]


def test_midnight_gate_without_scheduler_or_mutations(harness):
    harness.action(lifecycle.configure_billing, 10)
    assert not harness.state(at(11, 10, 23, minute=59, second=59))["blocked"]
    assert harness.state(at(11, 11, 0))["blocked"]
    assert harness.state(datetime(2026, 11, 10, 17, tzinfo=timezone.utc))["blocked"]
    assert harness.query("SELECT blocked_at FROM client_billing")[0]["blocked_at"] is None


def test_four_daily_warnings_are_idempotent(harness):
    harness.action(lifecycle.configure_billing, 10)
    harness.sync(at(11, 6))
    assert not harness.query("SELECT * FROM notifications")
    for day in range(7, 11):
        harness.sync(at(11, day))
        harness.sync(at(11, day, 23))
    events = harness.query("SELECT * FROM notifications ORDER BY id")
    assert len(events) == 4
    assert "3 hari lagi batas pembayaran" in events[0]["message"]
    assert "Hari ini batas akhir pembayaran" in events[-1]["message"]
    assert all("11-11-2026 pukul 00.00 WIB" in event["message"] for event in events)


def test_early_payment_prevents_block_but_next_cycle_still_applies(harness):
    harness.action(lifecycle.configure_billing, 10)
    bill_id = harness.state()["bills"][0]["id"]
    harness.action(lifecycle.mark_paid, bill_id, now=at(11, 5))
    for day in (7, 8, 9, 10, 11):
        harness.sync(at(11, day))
    assert not harness.state(at(11, 11))["blocked"]
    assert not harness.query("SELECT * FROM notifications")
    assert harness.state(at(12, 11))["blocked"]


def test_late_payment_needs_one_company_activation_preserving_personal_status(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    bill_id = harness.state()["bills"][0]["id"]
    harness.action(lifecycle.mark_paid, bill_id, now=at(11, 11))
    assert harness.state(at(11, 11))["blocked"]
    harness.action(lifecycle.activate_company, now=at(11, 11))
    assert not harness.state(at(11, 11))["blocked"]
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 11))
    for employee_id in (1, 3, 4):
        client = harness.login(f"employee{employee_id}@fixture.test", harness.app.test_client())
        with client.session_transaction() as session:
            formal_email = session["formal_email"]
        with harness.app.test_request_context():
            from flask import session
            session["formal_email"] = formal_email
            assert web.get_formal_status() == (0 if employee_id == 3 else 1)
    assert harness.query("SELECT status_aktif FROM pegawai WHERE id=3")[0]["status_aktif"] == 0
    harness.sync(at(11, 12))
    assert not harness.state(at(11, 12))["blocked"]
    assert len(harness.query("SELECT * FROM notifications WHERE kind='billing_activated'")) == 1


def test_all_arrears_must_be_paid_and_receipt_is_idempotent(harness):
    harness.action(lifecycle.configure_billing, 10)
    harness.sync(at(12, 11))
    bills = harness.state(at(12, 11))["overdue"]
    assert len(bills) == 2
    harness.action(lifecycle.mark_paid, bills[-1]["id"], now=at(12, 11))
    with pytest.raises(ValueError):
        harness.action(lifecycle.activate_company, now=at(12, 11))
    harness.action(lifecycle.mark_paid, bills[0]["id"], now=at(12, 11))
    receipt = harness.query("SELECT paid_at FROM client_bills WHERE id=?", (bills[0]["id"],))[0]
    harness.action(lifecycle.mark_paid, bills[0]["id"], now=at(12, 12))
    assert harness.query("SELECT paid_at FROM client_bills WHERE id=?", (bills[0]["id"],))[0] == receipt
    harness.action(lifecycle.activate_company, now=at(12, 12))
    assert not harness.state(at(12, 12))["blocked"]


def test_due_day_change_preserves_existing_bills(harness):
    harness.action(lifecycle.configure_billing, 10)
    harness.action(lifecycle.configure_billing, 20, now=at(11, 8))
    assert str(harness.state(at(11, 8))["bills"][0]["due_on"]) == "2026-11-10"
    assert str(harness.state(at(11, 11))["bills"][-1]["due_on"]) == "2026-12-20"
    assert harness.state(at(11, 11))["blocked"]


def test_mou_boundary_ignore_and_renewal(harness):
    harness.query("UPDATE clients SET mou_start='2026-01-01',mou_end='2026-12-10',mou_number='MOU-001' WHERE id=1")
    harness.sync(at(11, 9))
    assert not harness.query("SELECT * FROM notifications")
    harness.sync(at(11, 10))
    harness.sync(at(12, 11))
    assert len(harness.query("SELECT * FROM notifications")) == 1
    client = harness.query("SELECT * FROM clients WHERE id=1")[0]
    period = lifecycle.mou_period(client)
    harness.action(lifecycle.acknowledge_mou, period, now=at(11, 11))
    harness.sync(at(12, 12))
    assert harness.query("SELECT resolved_at FROM notifications")[0]["resolved_at"]
    harness.action(lifecycle.renew_mou, period, "2026-12-11", "2027-12-10", "MOU-002", now=at(11, 12))
    assert harness.query("SELECT action FROM client_mou_actions WHERE period_key=?", (period,))[0]["action"] == "renewed"
    with pytest.raises(ValueError):
        harness.action(lifecycle.acknowledge_mou, period)
    harness.sync(at(11, 10, year=2027))
    assert len(harness.query("SELECT * FROM notifications WHERE kind='mou'")) == 2
    assert harness.query("SELECT mou_number FROM clients WHERE id=1")[0]["mou_number"] == "MOU-002"


@pytest.mark.parametrize("start,end", [("bad", "2027-12-10"), ("2028-01-01", "2027-12-10"),
                                          ("2026-01-01", "2026-10-18"), ("2026-01-01", "2026-12-10")])
def test_invalid_renewal_cannot_suppress_mou(harness, start, end):
    harness.query("UPDATE clients SET mou_end='2026-12-10' WHERE id=1")
    period = lifecycle.mou_period(harness.query("SELECT * FROM clients WHERE id=1")[0])
    with pytest.raises(ValueError):
        harness.action(lifecycle.renew_mou, period, start, end, "")
    assert not harness.query("SELECT * FROM client_mou_actions")


@pytest.mark.parametrize("product", ["reg", "urg"])
def test_old_session_and_open_form_cannot_bypass_company_block(harness, monkeypatch, product):
    harness.action(lifecycle.configure_billing, 10)
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 10, 23, minute=59, second=59))
    harness.login("employee1@fixture.test")
    html = harness.client.get("/tarik-gaji").get_data(as_text=True)
    token = re.search(r'name="request_token" value="([^"]+)"', html).group(1)
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 11, 0))
    response = harness.post("/tarik-gaji", {"request_token": token, "produk": product,
                            "nominal": "100.000", "rekening_tujuan_key": "bank_utama", "tanggal": "1"})
    assert response.status_code == 302
    assert not harness.query("SELECT * FROM transactions")
    html = harness.client.get("/dashboard").get_data(as_text=True)
    assert "Nonaktif karena tagihan" in html
    assert harness.query("SELECT status_aktif FROM pegawai WHERE id=1")[0]["status_aktif"] == 1
    other = harness.login("employee2@fixture.test", harness.app.test_client())
    assert other.get("/tarik-gaji").status_code == 200


def test_request_crossing_midnight_is_checked_again_before_insert(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 10, 23, minute=59, second=59))
    harness.login("employee1@fixture.test")
    html = harness.client.get("/tarik-gaji").get_data(as_text=True)
    token = re.search(r'name="request_token" value="([^"]+)"', html).group(1)
    original = web.get_formal_status

    def crossing_midnight():
        status = original()
        monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 11, 0))
        return status

    monkeypatch.setattr(web, "get_formal_status", crossing_midnight)
    harness.post("/tarik-gaji", {"request_token": token, "produk": "reg", "nominal": "100.000",
                                "rekening_tujuan_key": "bank_utama"})
    assert not harness.query("SELECT * FROM transactions")


def test_pending_approval_is_not_frozen_and_case_normalized(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 11))
    harness.query("UPDATE pegawai SET perusahaan_induk=' fixture pt one ' WHERE id=1")
    with harness.app.app_context():
        assert lifecycle.company_blocked(get_db(), " FIXTURE PT ONE ")
    harness.query("INSERT INTO transactions (id,user_id,tanggal,periode,nominal,admin_fee,status,created_at,product) "
                  "VALUES (1,1,'2026-11-10','2026-11',100000,15000,'on-proses','2026-11-10T09:00:00','reg')")
    harness.login("admin@fixture.test")
    assert harness.post("/admin/tx/1/approve").status_code == 302
    harness.login()
    assert harness.post("/superadmin/tx/1/approve").status_code == 302
    assert harness.query("SELECT status FROM transactions WHERE id=1")[0]["status"] == "sukses"


def test_inbox_tenant_scope_and_read_receipts_are_per_account(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    harness.action(lifecycle.configure_billing, 10, client_id=2)
    harness.query("UPDATE clients SET mou_end='2026-12-07' WHERE id=1")
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 7))
    harness.sync(at(11, 7))
    harness.login("admin@fixture.test")
    html = harness.client.get("/admin/notifications").get_data(as_text=True)
    assert "Fixture PT One" in html
    assert "Fixture PT Two" not in html
    assert "Periode MoU perlu ditindaklanjuti" not in html
    notification = harness.query("SELECT id FROM notifications WHERE client_id=1 AND kind='billing_warning'")[0]["id"]
    foreign = harness.query("SELECT id FROM notifications WHERE client_id=2")[0]["id"]
    mou_id = harness.query("SELECT id FROM notifications WHERE kind='mou'")[0]["id"]
    assert harness.post(f"/notifications/{foreign}/read").status_code == 404
    assert harness.post(f"/notifications/{mou_id}/read").status_code == 404
    assert harness.post(f"/notifications/{notification}/read").status_code == 302
    summary = harness.client.get("/api/notifications/summary").get_json()
    assert summary["unread_count"] == 0
    assert summary["alerts"]
    colleague = harness.login("colleague@fixture.test", harness.app.test_client())
    assert colleague.get("/api/notifications/summary").get_json()["unread_count"] == 1
    harness.login()
    assert harness.client.get("/api/notifications/summary").get_json()["unread_count"] == 3


def test_pagination_read_all_cutoff_and_rendering(harness, monkeypatch):
    with harness.app.app_context():
        db = get_db()
        client = dict(db.execute("SELECT * FROM clients WHERE id=1").fetchone())
        for index in range(61):
            lifecycle.emit(db, client, "billing_warning", f"fixture:{index}", "2026-10", "Fixture notice", "Fixture message", at(11, 7))
        db.commit()
    harness.login("admin@fixture.test")
    response = harness.client.get("/admin/notifications?status=all&page=2")
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert html.count('class="card notification-item') == 25
    assert "Halaman 2 dari 3" in html
    assert "data-notification-bell" in html
    latest_id = harness.query("SELECT MAX(id) AS latest FROM notifications")[0]["latest"]
    with harness.app.app_context():
        db = get_db()
        lifecycle.emit(db, client, "billing_warning", "fixture:new", "2026-10", "New notice", "New message", at(11, 8))
        db.commit()
    harness.post("/notifications/read-all", {"through_id": latest_id})
    assert harness.client.get("/api/notifications/summary").get_json()["unread_count"] == 1
    assert harness.query("SELECT COUNT(*) AS total FROM notification_reads")[0]["total"] == 61
    assert "Halaman 1 dari 1" in harness.client.get("/admin/notifications").get_data(as_text=True)
    assert harness.client.get("/admin/notifications?status=read").get_data(as_text=True).count('class="card notification-item') == 25


def test_management_actions_require_superadmin_csrf_and_correct_company(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    harness.action(lifecycle.configure_billing, 10, client_id=2)
    other_bill = harness.state(client_id=2)["bills"][0]["id"]
    harness.login("admin@fixture.test")
    for path in ("billing", "activate", "mou/ignore", "mou/renew", f"billing/{other_bill}/paid"):
        assert harness.post("/superadmin/clients/1/" + path).status_code == 403
    assert harness.client.get("/superadmin/notifications").status_code == 403
    harness.login()
    assert harness.client.post("/superadmin/clients/1/billing", data={"due_day": "20"}).status_code == 400
    harness.post(f"/superadmin/clients/1/billing/{other_bill}/paid")
    assert harness.query("SELECT paid_at FROM client_bills WHERE id=?", (other_bill,))[0]["paid_at"] is None
    employee = harness.login("employee1@fixture.test", harness.app.test_client())
    assert employee.get("/api/notifications/summary").status_code == 403
    assert "data-notification-bell" not in employee.get("/dashboard").get_data(as_text=True)


def test_company_forms_and_manual_activation_end_to_end(harness, monkeypatch):
    harness.login()
    harness.post("/superadmin/clients/1/billing", {"due_day": "10"})
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 11))
    html = harness.client.get("/superadmin/dashboard").get_data(as_text=True)
    assert "Karyawan perusahaan Fixture PT One masih dalam keadaan nonaktif, aktifkan di sini." in html
    assert "/superadmin/clients/1#penagihan" in html
    assert html.index("company-alert-danger") < html.index("overview-banner")
    detail = harness.client.get("/superadmin/clients/1")
    assert detail.status_code == 200
    assert "Aktifkan Kembali" in detail.get_data(as_text=True)
    bill_id = harness.state()["overdue"][0]["id"]
    harness.post("/superadmin/clients/1/activate")
    assert harness.state()["blocked"]
    harness.post(f"/superadmin/clients/1/billing/{bill_id}/paid")
    assert harness.state()["blocked"]
    harness.post("/superadmin/clients/1/activate")
    assert not harness.state()["blocked"]
    assert not harness.client.get("/api/notifications/summary").get_json()["alerts"]


def test_mou_routes_search_and_read_do_not_dismiss(harness, monkeypatch):
    harness.query("UPDATE clients SET mou_start='2026-01-01',mou_end='2026-12-07',mou_number='UNIQUE-MOU-123' WHERE id=1")
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 7))
    harness.login()
    assert "Fixture PT One" in harness.client.get("/superadmin/clients?q=UNIQUE-MOU-123").get_data(as_text=True)
    event = harness.query("SELECT * FROM notifications WHERE kind='mou'")[0]
    harness.post(f"/notifications/{event['id']}/read")
    assert harness.client.get("/api/notifications/summary").get_json()["alerts"]
    period = lifecycle.mou_period(harness.query("SELECT * FROM clients WHERE id=1")[0])
    harness.post("/superadmin/clients/1/mou/ignore", {"period_key": period})
    assert not harness.client.get("/api/notifications/summary").get_json()["alerts"]
    harness.post("/superadmin/clients/1/mou/renew", {"period_key": period, "mou_start": "2026-12-08",
                                                "mou_end": "2027-12-07", "mou_number": "RENEWED-123"})
    assert harness.query("SELECT mou_number FROM clients WHERE id=1")[0]["mou_number"] == "RENEWED-123"
    assert len(harness.query("SELECT * FROM notifications WHERE kind='mou'")) == 1


def test_concurrent_runner_and_schema_retries_are_idempotent(harness):
    harness.action(lifecycle.configure_billing, 10)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(harness.sync, [at(11, 11)] * 3))
    assert len(harness.query("SELECT * FROM notifications WHERE kind='billing_blocked'")) == 1
    assert len(harness.query("SELECT * FROM client_bills")) == 2
    with harness.app.app_context():
        lifecycle.ensure_lifecycle_schema(get_db(), harness.app.config["DB_TYPE"])
        lifecycle.ensure_lifecycle_schema(get_db(), harness.app.config["DB_TYPE"])
    assert len(harness.query("SELECT * FROM client_bills")) == 2


def test_cli_runner_success_and_failure_are_observable(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    result = harness.app.test_cli_runner().invoke(args=["sync-company-lifecycle"])
    assert result.exit_code == 0
    assert harness.query("SELECT value FROM app_settings WHERE `key`='company_lifecycle_last_success'")
    from app.tasks import company_lifecycle as task

    def fail(*args, **kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(task, "sync_clients", fail)
    assert harness.app.test_cli_runner().invoke(args=["sync-company-lifecycle"]).exit_code != 0


def test_first_of_month_warnings_cross_previous_month(harness):
    harness.action(lifecycle.configure_billing, 1)
    for moment in (at(10, 29), at(10, 30), at(10, 31), at(11, 1)):
        harness.sync(moment)
    assert len(harness.query("SELECT * FROM notifications WHERE kind='billing_warning'")) == 4
    assert not harness.state(at(11, 1, 23, minute=59, second=59))["blocked"]
    assert harness.state(at(11, 2, 0))["blocked"]


def test_new_employee_and_individual_status_edit_cannot_bypass_block(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 11))
    harness.login("admin@fixture.test")
    harness.post("/admin/pegawai/1/update", {"id_pegawai": "E1", "nama": "Employee 1", "email": "employee1@fixture.test",
                 "gaji": "6000000", "status": "1", "perusahaan_induk": "Fixture PT One", "perusahaan": "Project 1"})
    assert harness.query("SELECT status_aktif FROM pegawai WHERE id=1")[0]["status_aktif"] == 1
    harness.query("INSERT INTO pegawai (id,id_pegawai,nama,email,gaji,status_aktif,perusahaan_induk,created_at) "
                  "VALUES (5,'E5','New Fixture','new@fixture.test',6000000,1,'Fixture PT One','2026-11-11')")
    harness.query("INSERT INTO user_accounts (pegawai_id,name,email,password_hash,status_aktif,created_at) VALUES (5,'New Fixture','new@fixture.test',?,1,'2026-11-11')", (HASH,))
    for email in ("employee1@fixture.test", "new@fixture.test"):
        client = harness.login(email, harness.app.test_client())
        assert client.get("/tarik-gaji").status_code == 302


def test_admin_company_changes_immediately_rescope_history(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    harness.action(lifecycle.configure_billing, 10, client_id=2)
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 7))
    harness.login("admin@fixture.test")
    old_id = harness.query("SELECT id FROM notifications WHERE client_id=1")[0]["id"]
    harness.query("UPDATE admins SET company='Fixture PT Two' WHERE id=2")
    html = harness.client.get("/admin/notifications").get_data(as_text=True)
    assert "Fixture PT Two" in html and "Fixture PT One" not in html
    assert harness.post(f"/notifications/{old_id}/read").status_code == 404


def test_environment_superadmin_has_own_stable_read_receipts(harness, monkeypatch):
    harness.action(lifecycle.configure_billing, 10)
    monkeypatch.setattr(lifecycle, "business_now", lambda: at(11, 7))
    harness.app.config.update(SUPERADMIN_USERNAME="envsuper@fixture.test", SUPERADMIN_PASSWORD=PASSWORD)
    env_client = harness.login("envsuper@fixture.test", harness.app.test_client())
    event_id = harness.query("SELECT id FROM notifications")[0]["id"]
    harness.post(f"/notifications/{event_id}/read", client=env_client)
    assert env_client.get("/api/notifications/summary").get_json()["unread_count"] == 0
    harness.login()
    assert harness.client.get("/api/notifications/summary").get_json()["unread_count"] == 1


def test_mysql_schema_statements_preserve_indexes_and_unique_keys():
    class RecordingConnection:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)

        def commit(self):
            pass

    connection = RecordingConnection()
    lifecycle.ensure_lifecycle_schema(connection, "mysql")
    assert len(connection.statements) == 5
    assert all("ENGINE=InnoDB" in statement for statement in connection.statements)
    notification_sql = next(statement for statement in connection.statements if "notifications (" in statement)
    assert "INDEX ix_notifications_client (client_id,audience,created_at)" in notification_sql
    assert "event_key VARCHAR(191) NOT NULL UNIQUE" in notification_sql
