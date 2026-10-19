import calendar
import hashlib
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from app.services.client_service import insert_ignoring_duplicates, name_key

WIB = timezone(timedelta(hours=7))


def business_now():
    return datetime.now(WIB)


def local_now(now=None):
    value = now or business_now()
    if value.tzinfo is None:
        raise ValueError("Waktu penagihan wajib menyertakan zona waktu.")
    return value.astimezone(WIB)


def timestamp(now):
    return now.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def due_date(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def following_due(previous, day):
    return due_date(previous.year + (previous.month == 12), previous.month % 12 + 1, day)


def service_period(due):
    return (due.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")


def mou_period(client):
    raw = f"{client['mou_start'] or ''}|{client['mou_end'] or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def insert_once(db, table, values):
    backend = "sqlite" if isinstance(db, sqlite3.Connection) else "mysql"
    insert_ignoring_duplicates(db, backend, table, tuple(values), tuple(values.values()))


def ensure_lifecycle_schema(db, db_type):
    identity = "INTEGER PRIMARY KEY AUTOINCREMENT" if db_type == "sqlite" else "INT AUTO_INCREMENT PRIMARY KEY"
    suffix = "" if db_type == "sqlite" else " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
    definitions = [
        """client_billing (
            client_id INTEGER PRIMARY KEY, due_day INTEGER NOT NULL,
            first_due DATE NOT NULL, blocked_at DATETIME,
            activated_at DATETIME, updated_by VARCHAR(191) NOT NULL,
            updated_at DATETIME NOT NULL)""",
        f"""client_bills (
            id {identity}, client_id INTEGER NOT NULL,
            period VARCHAR(7) NOT NULL, due_on DATE NOT NULL,
            paid_at DATETIME, paid_by VARCHAR(191), created_at DATETIME NOT NULL,
            UNIQUE (client_id, period))""",
        """client_mou_actions (
            client_id INTEGER NOT NULL, period_key VARCHAR(64) NOT NULL,
            action VARCHAR(16) NOT NULL, acted_by VARCHAR(191) NOT NULL,
            acted_at DATETIME NOT NULL, PRIMARY KEY (client_id, period_key))""",
        f"""notifications (
            id {identity}, event_key VARCHAR(191) NOT NULL UNIQUE,
            client_id INTEGER NOT NULL, kind VARCHAR(32) NOT NULL,
            audience VARCHAR(16) NOT NULL, period_key VARCHAR(64) NOT NULL,
            title VARCHAR(255) NOT NULL, message TEXT NOT NULL,
            created_at DATETIME NOT NULL, resolved_at DATETIME)""",
        """notification_reads (
            notification_id INTEGER NOT NULL, principal VARCHAR(191) NOT NULL,
            read_at DATETIME NOT NULL, PRIMARY KEY (notification_id, principal))""",
    ]
    for definition in definitions:
        if db_type != "sqlite" and definition.startswith("notifications ("):
            definition = definition[:-1] + ", INDEX ix_notifications_client (client_id,audience,created_at))"
        db.execute("CREATE TABLE IF NOT EXISTS " + definition + suffix)
    if db_type == "sqlite":
        db.execute("CREATE INDEX IF NOT EXISTS ix_notifications_client ON notifications (client_id,audience,created_at)")
    db.commit()


@contextmanager
def locked_client(db, client_id):
    db.commit()
    try:
        if isinstance(db, sqlite3.Connection):
            db.execute("BEGIN IMMEDIATE")
            suffix = ""
        else:
            suffix = " FOR UPDATE"
        row = db.execute("SELECT * FROM clients WHERE id=?" + suffix, (client_id,)).fetchone()
        if not row:
            raise ValueError("Perusahaan tidak ditemukan.")
        yield dict(row)
        db.commit()
    except Exception:
        db.rollback()
        raise


def billing_state(db, client_id, now=None):
    today = local_now(now).date()
    setting = db.execute("SELECT * FROM client_billing WHERE client_id=?", (client_id,)).fetchone()
    if not setting:
        return {"configured": False, "blocked": False, "bills": [], "overdue": [], "setting": None}
    setting = dict(setting)
    bills = [dict(row) for row in db.execute(
        "SELECT * FROM client_bills WHERE client_id=? ORDER BY due_on", (client_id,)
    ).fetchall()]
    next_due = following_due(as_date(bills[-1]["due_on"]), setting["due_day"]) if bills else as_date(setting["first_due"])
    while not bills or as_date(bills[-1]["due_on"]) < today:
        bills.append({"id": None, "client_id": client_id, "period": service_period(next_due),
                      "due_on": next_due, "paid_at": None, "paid_by": None})
        next_due = following_due(next_due, setting["due_day"])
    overdue = [bill for bill in bills if not bill["paid_at"] and as_date(bill["due_on"]) < today]
    return {"configured": True, "blocked": bool(setting["blocked_at"] or overdue),
            "bills": bills, "overdue": overdue, "setting": setting}


def company_blocked(db, company, now=None):
    client = db.execute("SELECT id FROM clients WHERE name_key=?", (name_key(company),)).fetchone()
    return bool(client and billing_state(db, client["id"], now)["blocked"])


def emit(db, client, kind, event_key, period, title, message, now, audience="management"):
    insert_once(db, "notifications", {
        "event_key": event_key, "client_id": client["id"], "kind": kind,
        "audience": audience, "period_key": period, "title": title,
        "message": message, "created_at": timestamp(now),
    })


def payment_warning(client, bill, today):
    due = as_date(bill["due_on"])
    remaining = (due - today).days
    deadline = "Hari ini batas akhir pembayaran" if remaining == 0 else f"{remaining} hari lagi batas pembayaran"
    block_on = due + timedelta(days=1)
    return (f"{deadline} perusahaan {client['name']} untuk periode {bill['period']}. "
            f"Perusahaan Anda akan dinonaktifkan mulai {block_on.strftime('%d-%m-%Y')} pukul 00.00 WIB. "
            "Apabila ada administrasi yang belum diselesaikan, silakan hubungi pihak GajiKu.")


def sync_client(db, client, now=None):
    now = local_now(now)
    today = now.date()
    state = billing_state(db, client["id"], now)
    if state["configured"]:
        for bill in state["bills"]:
            if bill["id"] is None:
                insert_once(db, "client_bills", {"client_id": client["id"], "period": bill["period"],
                            "due_on": as_date(bill["due_on"]).isoformat(), "created_at": timestamp(now)})
        state = billing_state(db, client["id"], now)
        if state["overdue"] and not state["setting"]["blocked_at"]:
            db.execute("UPDATE client_billing SET blocked_at=? WHERE client_id=?", (timestamp(now), client["id"]))
            first = state["overdue"][0]
            emit(db, client, "billing_blocked", f"blocked:{client['id']}:{first['id']}", first["period"],
                 "Perusahaan dinonaktifkan", f"Karyawan perusahaan {client['name']} masih dalam keadaan nonaktif. "
                 "Pengajuan baru terkunci sampai tagihan dilunasi dan superadmin mengaktifkan kembali perusahaan.", now)
        for bill in state["bills"]:
            remaining = (as_date(bill["due_on"]) - today).days
            if not bill["paid_at"] and 0 <= remaining <= 3:
                emit(db, client, "billing_warning", f"bill:{bill['id']}:h{remaining}", bill["period"],
                     "Pengingat pembayaran perusahaan", payment_warning(client, bill, today), now)
    period = mou_period(client)
    db.execute("UPDATE notifications SET resolved_at=? WHERE client_id=? AND kind='mou' "
               "AND period_key<>? AND resolved_at IS NULL", (timestamp(now), client["id"], period))
    ignored = db.execute("SELECT 1 FROM client_mou_actions WHERE client_id=? AND period_key=?",
                         (client["id"], period)).fetchone()
    if client["mou_end"] and (as_date(client["mou_end"]) - today).days <= 30 and not ignored:
        emit(db, client, "mou", f"mou:{client['id']}:{period}", period, "Periode MoU perlu ditindaklanjuti",
             f"MoU perusahaan {client['name']} (nomor {client['mou_number'] or '-'}) berakhir pada "
             f"{as_date(client['mou_end']).strftime('%d-%m-%Y')}. Pilih Abaikan atau catat perpanjangan kontrak.",
             now, audience="superadmin")


def sync_clients(db, clients, now=None):
    now = local_now(now)
    for candidate in clients:
        with locked_client(db, candidate["id"]) as client:
            sync_client(db, client, now)


def configure_billing(db, client, day, actor, now=None):
    now = local_now(now)
    try:
        day = int(day)
    except (ValueError, TypeError):
        raise ValueError("Tanggal tagihan wajib angka 1 sampai 31.") from None
    if not 1 <= day <= 31:
        raise ValueError("Tanggal tagihan wajib angka 1 sampai 31.")
    sync_client(db, client, now)
    setting = db.execute("SELECT * FROM client_billing WHERE client_id=?", (client["id"],)).fetchone()
    if setting:
        db.execute("UPDATE client_billing SET due_day=?,updated_by=?,updated_at=? WHERE client_id=?",
                   (day, actor, timestamp(now), client["id"]))
    else:
        first = due_date(now.year, now.month, day)
        if first < now.date():
            first = following_due(first, day)
        db.execute("INSERT INTO client_billing (client_id,due_day,first_due,updated_by,updated_at) VALUES (?,?,?,?,?)",
                   (client["id"], day, first.isoformat(), actor, timestamp(now)))
    sync_client(db, client, now)


def mark_paid(db, client, bill_id, actor, now=None):
    now = local_now(now)
    sync_client(db, client, now)
    bill = db.execute("SELECT * FROM client_bills WHERE id=? AND client_id=?", (bill_id, client["id"])).fetchone()
    if not bill:
        raise ValueError("Tagihan tidak ditemukan pada perusahaan ini.")
    db.execute("UPDATE client_bills SET paid_at=?,paid_by=? WHERE id=? AND paid_at IS NULL",
               (timestamp(now), actor, bill_id))
    db.execute("UPDATE notifications SET resolved_at=? WHERE client_id=? AND kind='billing_warning' "
               "AND period_key=? AND resolved_at IS NULL", (timestamp(now), client["id"], bill["period"]))


def activate_company(db, client, actor, now=None):
    now = local_now(now)
    sync_client(db, client, now)
    state = billing_state(db, client["id"], now)
    if not state["configured"] or state["overdue"]:
        raise ValueError("Lunasi seluruh tagihan jatuh tempo sebelum mengaktifkan kembali perusahaan.")
    blocked_at = state["setting"]["blocked_at"]
    if not blocked_at:
        return
    db.execute("UPDATE client_billing SET blocked_at=NULL,activated_at=?,updated_by=?,updated_at=? WHERE client_id=?",
               (timestamp(now), actor, timestamp(now), client["id"]))
    db.execute("UPDATE notifications SET resolved_at=? WHERE client_id=? AND kind='billing_blocked' "
               "AND resolved_at IS NULL", (timestamp(now), client["id"]))
    emit(db, client, "billing_activated", f"activated:{client['id']}:{blocked_at}", "",
         "Perusahaan diaktifkan kembali", f"Perusahaan {client['name']} telah diaktifkan kembali oleh {actor}. "
         "Karyawan yang aktif secara pribadi otomatis dapat mengajukan tarik gaji lagi.", now)


def acknowledge_mou(db, client, expected_period, actor, action="ignored", now=None):
    now = local_now(now)
    period = mou_period(client)
    if not client["mou_end"] or expected_period != period:
        raise ValueError("Periode MoU berubah. Muat ulang halaman sebelum melanjutkan.")
    insert_once(db, "client_mou_actions", {"client_id": client["id"], "period_key": period,
                "action": action, "acted_by": actor, "acted_at": timestamp(now)})
    if action == "renewed":
        db.execute("UPDATE client_mou_actions SET action='renewed',acted_by=?,acted_at=? WHERE client_id=? AND period_key=?",
                   (actor, timestamp(now), client["id"], period))
    db.execute("UPDATE notifications SET resolved_at=? WHERE client_id=? AND kind='mou' "
               "AND period_key=? AND resolved_at IS NULL", (timestamp(now), client["id"], period))


def renew_mou(db, client, expected_period, start, end, number, actor, now=None):
    now = local_now(now)
    try:
        start, end = as_date(start), as_date(end)
    except (TypeError, ValueError):
        raise ValueError("Tanggal mulai dan akhir MoU baru wajib valid.") from None
    if start > end or end <= now.date() or (client["mou_end"] and end <= as_date(client["mou_end"])):
        raise ValueError("Akhir MoU baru harus setelah akhir sebelumnya dan berada di masa depan.")
    number = (number or client["mou_number"] or "").strip()
    if len(number) > 255:
        raise ValueError("Nomor MoU maksimal 255 karakter.")
    acknowledge_mou(db, client, expected_period, actor, "renewed", now)
    db.execute("UPDATE clients SET mou_start=?,mou_end=?,mou_number=?,updated_at=? WHERE id=?",
               (start.isoformat(), end.isoformat(), number, timestamp(now), client["id"]))


def visible_clients(db, is_superadmin, company):
    if is_superadmin:
        return [dict(row) for row in db.execute("SELECT * FROM clients ORDER BY name_key").fetchall()]
    row = db.execute("SELECT * FROM clients WHERE name_key=?", (name_key(company),)).fetchone()
    return [dict(row)] if row else []


def active_alerts(db, clients, is_superadmin, now=None):
    now = local_now(now)
    alerts = []
    for client in clients:
        target = f"/superadmin/clients/{client['id']}#penagihan" if is_superadmin else "/admin/notifications"
        state = billing_state(db, client["id"], now)
        if state["blocked"]:
            message = (f"Karyawan perusahaan {client['name']} masih dalam keadaan nonaktif, aktifkan di sini."
                       if is_superadmin else f"Perusahaan {client['name']} masih dalam keadaan nonaktif. "
                       "Pengajuan baru karyawan terkunci. Silakan hubungi pihak GajiKu untuk menyelesaikan administrasi.")
            alerts.append({"kind": "danger", "message": message, "url": target})
        else:
            for bill in state["bills"]:
                if not bill["paid_at"] and 0 <= (as_date(bill["due_on"]) - now.date()).days <= 3:
                    alerts.append({"kind": "warning", "message": payment_warning(client, bill, now.date()), "url": target})
        period = mou_period(client)
        ignored = db.execute("SELECT 1 FROM client_mou_actions WHERE client_id=? AND period_key=?",
                             (client["id"], period)).fetchone()
        if is_superadmin and client["mou_end"] and (as_date(client["mou_end"]) - now.date()).days <= 30 and not ignored:
            alerts.append({"kind": "warning", "message": f"MoU perusahaan {client['name']} "
                           f"berakhir pada {as_date(client['mou_end']).strftime('%d-%m-%Y')}. Tindak lanjuti kontrak.",
                           "url": f"/superadmin/clients/{client['id']}#mou-actions"})
    return alerts


def notification_scope(is_superadmin, company, alias="n"):
    if is_superadmin:
        return "1=1", []
    return f"{alias}.audience='management' AND EXISTS (SELECT 1 FROM clients c WHERE c.id={alias}.client_id AND c.name_key=?)", [name_key(company)]


def unread_count(db, principal, is_superadmin, company):
    scope, params = notification_scope(is_superadmin, company)
    return db.execute("SELECT COUNT(*) AS total FROM notifications n WHERE " + scope +
                      " AND NOT EXISTS (SELECT 1 FROM notification_reads r WHERE r.notification_id=n.id AND r.principal=?)",
                      (*params, principal)).fetchone()["total"]
