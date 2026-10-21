import hashlib
import math
import sqlite3
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, session, url_for

from app.db import get_db
from app.services import company_lifecycle as lifecycle

bp = Blueprint("notifications", __name__)


def principal():
    if session.get("auth_source") in {"env_super", "env_admin"}:
        identity = (session.get("admin_email") or "").strip().lower()
        role = "superadmin" if session.get("is_superadmin") else "admin"
        return f"env:{role}:" + hashlib.sha256(identity.encode()).hexdigest()
    return f"admin:{session.get('admin_id')}"


def actor():
    return f"{principal()} {session.get('admin_email') or ''}"[:191]


def staff_access():
    if not (session.get("is_admin") or session.get("is_superadmin")):
        abort(403)
    if request.path.startswith("/superadmin/") and not session.get("is_superadmin"):
        abort(403)


@bp.before_request
def check_access():
    staff_access()


def refresh_management_notices():
    if request.method != "GET" or not (session.get("is_admin") or session.get("is_superadmin")):
        return
    if not request.path.startswith(("/admin/", "/superadmin/", "/api/notifications/")):
        return
    db = get_db()
    clients = lifecycle.visible_clients(db, bool(session.get("is_superadmin")), session.get("company"))
    lifecycle.sync_clients(db, clients)


def management_context():
    if not (session.get("is_admin") or session.get("is_superadmin")):
        return {"notification_count": 0, "company_alerts": []}
    if not hasattr(g, "management_notices"):
        db = get_db()
        is_super = bool(session.get("is_superadmin"))
        clients = lifecycle.visible_clients(db, is_super, session.get("company"))
        g.management_notices = {
            "notification_count": lifecycle.unread_count(db, principal(), is_super, session.get("company")),
            "company_alerts": lifecycle.active_alerts(db, clients, is_super),
        }
    return g.management_notices


def inbox_endpoint():
    return "notifications.superadmin_inbox" if session.get("is_superadmin") else "notifications.admin_inbox"


def target(notification):
    if notification['kind'].startswith('employee_'):
        return url_for('employees.detail', batch_id=int(notification['period_key'])) if notification['period_key'] else url_for('employees.inbox')
    if not session.get("is_superadmin"):
        return url_for("web.admin_dashboard")
    anchor = "mou-actions" if notification["kind"] == "mou" else "penagihan"
    return url_for("clients.superadmin_client_detail", client_id=notification["client_id"]) + "#" + anchor


@bp.get("/superadmin/notifications")
def superadmin_inbox():
    return inbox()


@bp.get("/admin/notifications")
def admin_inbox():
    return inbox()


def inbox():
    db = get_db()
    status = request.args.get("status", "unread")
    if status not in {"all", "read", "unread"}:
        status = "unread"
    scope, params = lifecycle.notification_scope(bool(session.get("is_superadmin")), session.get("company"))
    joined = " FROM notifications n LEFT JOIN notification_reads r ON r.notification_id=n.id AND r.principal=? WHERE " + scope
    values = [principal(), *params]
    filtered = joined
    if status != "all":
        filtered += " AND r.notification_id IS " + ("NOT NULL" if status == "read" else "NULL")
    total = db.execute("SELECT COUNT(*) AS total" + filtered, values).fetchone()["total"]
    pages = max(1, math.ceil(total / 25))
    page = min(max(request.args.get("page", 1, type=int), 1), pages)
    rows = [dict(row) for row in db.execute(
        "SELECT n.*,r.read_at" + filtered + " ORDER BY n.created_at DESC,n.id DESC LIMIT 25 OFFSET ?",
        (*values, (page - 1) * 25),
    ).fetchall()]
    latest_id = db.execute("SELECT COALESCE(MAX(n.id),0) AS latest_id FROM notifications n WHERE " + scope, params).fetchone()["latest_id"]
    for row in rows:
        row["target"] = target(row)
    return render_template("shared/notifications.html", notifications=rows, status=status,
                           page=page, pages=pages, total=total, latest_id=latest_id,
                           inbox_endpoint=inbox_endpoint())


@bp.get("/api/notifications/summary")
def summary():
    data = management_context()
    return jsonify(unread_count=data["notification_count"], alerts=data["company_alerts"])


@bp.post("/notifications/<int:notification_id>/read")
def read_one(notification_id):
    db = get_db()
    scope, params = lifecycle.notification_scope(bool(session.get("is_superadmin")), session.get("company"))
    row = db.execute("SELECT n.* FROM notifications n WHERE n.id=? AND " + scope,
                     (notification_id, *params)).fetchone()
    if not row:
        abort(404)
    lifecycle.insert_once(db, "notification_reads", {"notification_id": notification_id,
                          "principal": principal(), "read_at": lifecycle.timestamp(lifecycle.business_now())})
    db.commit()
    if request.form.get("open") == "1":
        return redirect(target(row))
    return redirect(url_for(inbox_endpoint(), status=request.form.get("status", "unread")))


@bp.post("/notifications/read-all")
def read_all():
    db = get_db()
    scope, params = lifecycle.notification_scope(bool(session.get("is_superadmin")), session.get("company"))
    through_id = request.form.get("through_id", 0, type=int)
    verb = "INSERT OR IGNORE" if isinstance(db, sqlite3.Connection) else "INSERT IGNORE"
    db.execute(verb + " INTO notification_reads (notification_id,principal,read_at) "
               "SELECT n.id,?,? FROM notifications n WHERE " + scope + " AND n.id<=?",
               (principal(), lifecycle.timestamp(lifecycle.business_now()), *params, through_id))
    db.commit()
    return redirect(url_for(inbox_endpoint(), status=request.form.get("status", "unread")))


def change_company(client_id, operation, *args):
    if not session.get("is_superadmin"):
        abort(403)
    db = get_db()
    if not db.execute("SELECT 1 FROM clients WHERE id=?", (client_id,)).fetchone():
        abort(404)
    try:
        with lifecycle.locked_client(db, client_id) as client:
            operation(db, client, *args, actor=actor())
    except ValueError as error:
        flash(str(error), "error")
    else:
        flash("Pengaturan perusahaan berhasil diperbarui.", "success")
    return redirect(url_for("clients.superadmin_client_detail", client_id=client_id) +
                    ("#mou-actions" if operation in {lifecycle.acknowledge_mou, lifecycle.renew_mou} else "#penagihan"))


@bp.post("/superadmin/clients/<int:client_id>/billing")
def configure_billing(client_id):
    return change_company(client_id, lifecycle.configure_billing, request.form.get("due_day"))


@bp.post("/superadmin/clients/<int:client_id>/billing/<int:bill_id>/paid")
def mark_paid(client_id, bill_id):
    return change_company(client_id, lifecycle.mark_paid, bill_id)


@bp.post("/superadmin/clients/<int:client_id>/activate")
def activate(client_id):
    return change_company(client_id, lifecycle.activate_company)


@bp.post("/superadmin/clients/<int:client_id>/mou/ignore")
def ignore_mou(client_id):
    return change_company(client_id, lifecycle.acknowledge_mou, request.form.get("period_key"))


@bp.post("/superadmin/clients/<int:client_id>/mou/renew")
def renew_mou(client_id):
    return change_company(client_id, lifecycle.renew_mou, request.form.get("period_key"),
                          request.form.get("mou_start"), request.form.get("mou_end"), request.form.get("mou_number"))


@bp.app_template_filter("notification_time")
def notification_time(value):
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    return value.replace(tzinfo=timezone.utc).astimezone(lifecycle.WIB).strftime("%d-%m-%Y %H:%M WIB")
