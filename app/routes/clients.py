from datetime import date, datetime
import sqlite3

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from app.db import get_db
from app.services.client_service import PROJECT_NAMES, client_by_name, list_clients, list_projects, name_key, project_in_use
from app.services.client_service import DEFAULT_REG_WITHDRAWAL_MAX, parse_reg_withdrawal_max
from app.services.company_lifecycle import billing_state, local_now, mou_period
from app.services.company_policy import (StalePolicy, check_policy_version, serialized_policy_write,
    guard_cutoff_change, override_employees, record_policy_event)
from app.utils.cache import clear_cache_prefix


bp = Blueprint("clients", __name__)
CLIENT_FIELDS = {
    "name": 255, "address": 5000, "email": 255, "phone": 50, "npwp": 50,
    "pic_name": 255, "pic_role": 100, "pic_email": 255, "pic_phone": 50,
    "mou_number": 255, "notes": 5000,
}
PROJECT_FIELDS = {"name": 255, "location": 255, "pic_name": 255,
                  "pic_email": 255, "pic_phone": 50, "notes": 5000}


@bp.before_request
def check_access():
    if not (session.get("is_superadmin") or session.get("is_admin")):
        return redirect(url_for("web.login"))
    if request.endpoint.startswith("clients.superadmin_") and not session.get("is_superadmin"):
        abort(403)


def read_fields(fields):
    values = {}
    for field, maximum in fields.items():
        value = (request.form.get(field) or "").strip()
        if len(value) > maximum:
            raise ValueError(f"{field}: maksimal {maximum} karakter.")
        if field in {"email", "pic_email"} and value and ("@" not in value or " " in value):
            raise ValueError("Format email tidak valid.")
        values[field] = value
    if not values["name"]:
        raise ValueError("Nama wajib diisi.")
    values["name_key"] = name_key(values["name"])
    return values


def find_client(client_id):
    row = get_db().execute("SELECT * FROM clients WHERE id=?", (client_id,)).fetchone()
    if not row:
        abort(404)
    return dict(row)


def admin_client():
    client = client_by_name(get_db(), session.get("company"))
    if not client:
        abort(403, description="Akun admin belum ditautkan ke client terdaftar.")
    return client


def mou_status(client):
    today = local_now().date()
    end = date.fromisoformat(str(client["mou_end"])) if client["mou_end"] else None
    start = date.fromisoformat(str(client["mou_start"])) if client["mou_start"] else None
    if end and end < today:
        return "ended", "Berakhir"
    if start and start > today:
        return "upcoming", "Belum Dimulai"
    if not end:
        return "missing", "Belum Dicatat"
    if (end - today).days <= 30:
        return "soon", "Segera Berakhir"
    return "valid", "Berlaku"


def client_admins(db, client):
    return [dict(row) for row in db.execute("""
        SELECT id, name, email, role, status_aktif, hak_approval FROM admins
        WHERE LOWER(TRIM(company))=? ORDER BY name, id
    """, (client["name_key"],)).fetchall()]


def duplicate_error(error):
    return isinstance(error, sqlite3.IntegrityError) or getattr(error, "errno", None) == 1062


def save_client(existing=None):
    db = get_db()
    values = read_fields(CLIENT_FIELDS)
    if existing:
        existing = client_by_name(db, existing["name"], lock=True)
    if existing and values["name"] != existing["name"]:
        raise ValueError("Nama PT existing dikunci untuk menjaga relasi pegawai dan akun admin.")
    if existing:
        check_policy_version(request.form, existing)
    raw_limit = request.form.get("reg_withdrawal_max") if not existing else None
    initial_limit = (
        parse_reg_withdrawal_max(raw_limit) if raw_limit is not None
        else existing["reg_withdrawal_max"] if existing else DEFAULT_REG_WITHDRAWAL_MAX
    )
    if not existing:
        values["reg_withdrawal_max"] = initial_limit
    values["allow_projects"] = 1 if request.form.get("allow_projects") == "1" else 0
    for field in ("mou_start", "mou_end"):
        raw = request.form.get(field) or ""
        try:
            values[field] = date.fromisoformat(raw).isoformat() if raw else None
        except ValueError:
            raise ValueError("Tanggal MoU tidak valid.") from None
    if values["mou_start"] and values["mou_end"] and values["mou_start"] > values["mou_end"]:
        raise ValueError("Tanggal akhir MoU tidak boleh sebelum tanggal mulai.")
    if existing and existing["allow_projects"] and not values["allow_projects"]:
        used = project_in_use(db, existing)
        if used:
            raise ValueError("Project masih dipakai pegawai. Selesaikan pemindahan data secara eksplisit sebelum Disallow Projects.")
    values["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if existing:
        values["policy_version"] = existing["policy_version"] + 1
        assignment = ",".join(f"{field}=?" for field in values)
        db.execute(f"UPDATE clients SET {assignment} WHERE id=?", (*values.values(), existing["id"]))
        client_id = existing["id"]
    else:
        values["created_at"] = values["updated_at"]
        columns = ",".join(values)
        placeholders = ",".join("?" for field in values)
        cursor = db.execute(f"INSERT INTO clients ({columns}) VALUES ({placeholders})", tuple(values.values()))
        client_id = cursor.lastrowid
    record_policy_event(db, client_id, "profile", existing or {}, values)
    db.commit()
    clear_cache_prefix("")
    return client_id


@bp.route("/superadmin/clients", methods=["GET", "POST"])
@serialized_policy_write
def superadmin_clients():
    db = get_db()
    failed = False
    if request.method == "POST":
        try:
            client_id = save_client()
        except Exception as error:
            db.rollback()
            if not isinstance(error, ValueError) and not duplicate_error(error):
                raise
            flash(str(error) if isinstance(error, ValueError) else "Nama PT sudah terdaftar.", "error")
        else:
            flash("Client ditambahkan. Atur MoU, akun admin, dan project sebelum menambah karyawan.", "success")
            return redirect(url_for("clients.superadmin_client_detail", client_id=client_id))
        failed = True
    query = (request.args.get("q") or "").strip().lower()
    status = request.args.get("mou") or ""
    clients = []
    for client in list_clients(db):
        client["admins"] = client_admins(db, client)
        client["project_count"] = db.execute("SELECT COUNT(*) AS total FROM client_projects WHERE client_id=?", (client["id"],)).fetchone()["total"]
        client["employee_count"] = db.execute("SELECT COUNT(*) AS total FROM pegawai WHERE LOWER(TRIM(perusahaan_induk))=?", (client["name_key"],)).fetchone()["total"]
        client["mou_status"], client["mou_label"] = mou_status(client)
        client["billing_blocked"] = billing_state(db, client["id"])["blocked"]
        haystack = " ".join([client["name"], client["mou_number"] or "", client["pic_name"] or "", client["email"] or ""] + [admin["email"] for admin in client["admins"]]).lower()
        if (not query or query in haystack) and (not status or status == client["mou_status"]):
            clients.append(client)
    return render_template("superadmin_clients.html", clients=clients, q=query, mou_filter=status,
                           form_restore={"dialog": "client-add", "values": request.form.to_dict()} if failed else None), 422 if failed else 200


@bp.route("/superadmin/clients/<int:client_id>", methods=["GET", "POST"])
@serialized_policy_write
def superadmin_client_detail(client_id):
    client = find_client(client_id)
    db = get_db()
    failed = False
    response_status = 200
    if request.method == "POST":
        try:
            section = request.form.get("section", "profile")
            if section == "profile":
                save_client(client)
            else:
                check_policy_version(request.form, client)
                if section == "reg_limit":
                    values = {"reg_withdrawal_max": parse_reg_withdrawal_max(request.form.get("reg_withdrawal_max"))}
                elif section == "cutoff":
                    day = request.form.get("cutoff_hari", "")
                    if day not in [str(number) for number in range(7)]:
                        raise ValueError("Hari cutoff tidak valid.")
                    values = {"cutoff_mingguan_aktif": int(request.form.get("cutoff_mingguan_aktif") == "1"), "cutoff_hari": int(day)}
                    guard_cutoff_change(db, client["name"], client, values)
                elif section == "products":
                    values = {"produk_reg_aktif": int(request.form.get("produk_reg_aktif") == "1"),
                              "produk_urg_aktif": int(request.form.get("produk_urg_aktif") == "1")}
                else:
                    raise ValueError("Bagian konfigurasi tidak dikenal.")
                assignment = ",".join(f"{field}=?" for field in values)
                db.execute(f"UPDATE clients SET {assignment},policy_version=policy_version+1 WHERE id=?",
                           (*values.values(), client_id))
                record_policy_event(db, client_id, section, {field: client[field] for field in values}, values)
                db.commit()
                clear_cache_prefix("")
        except Exception as error:
            db.rollback()
            if not isinstance(error, ValueError) and not duplicate_error(error):
                raise
            flash(str(error) if isinstance(error, ValueError) else "Nama PT sudah terdaftar.", "error")
            failed = True
            response_status = 409 if isinstance(error, StalePolicy) else 422
        else:
            flash("Konfigurasi perusahaan diperbarui.", "success")
            return redirect(url_for("clients.superadmin_client_detail", client_id=client_id))
    return client_detail_response(client, response_status=response_status,
        form_restore={"dialog": "client-edit", "values": request.form.to_dict()} if failed and request.form.get("section", "profile") == "profile" else None,
        policy_draft=request.form if failed else {})

def client_detail_response(client, form_restore=None, policy_draft=None, response_status=200):
    db = get_db()
    client_id = client["id"]
    status, label = mou_status(client)
    return render_template("client_detail.html", client=client, projects=list_projects(db, client_id),
                           admins=client_admins(db, client), mou_label=label, mou_status=status,
                           overrides=override_employees(db, client["name"]),
                           form_restore=form_restore,
                           policy_draft=policy_draft or {},
                           is_superadmin=bool(session.get("is_superadmin")), billing=billing_state(db, client_id),
                           mou_period_key=mou_period(client),
                           mou_acknowledged=bool(db.execute("SELECT 1 FROM client_mou_actions WHERE client_id=? AND period_key=?",
                                                          (client_id, mou_period(client))).fetchone())), response_status


def project_change(client, project_id=None, delete=False):
    db = get_db()
    client = client_by_name(db, client["name"], lock=True)
    if not client["allow_projects"]:
        abort(403, description="Perusahaan ini tidak mengizinkan pengelolaan project.")
    project = None
    if project_id is not None:
        project = db.execute("SELECT * FROM client_projects WHERE id=? AND client_id=?",
                             (project_id, client["id"])).fetchone()
        if not project:
            abort(404)
    target = url_for("clients.superadmin_client_detail", client_id=client["id"]) if session.get("is_superadmin") else url_for("clients.admin_projects")
    try:
        values = {} if delete else read_fields(PROJECT_FIELDS)
        if not delete:
            values["name"] = PROJECT_NAMES.get(values["name_key"], values["name"])
            values["name_key"] = name_key(values["name"])
        if project:
            used = project_in_use(db, client, project["name_key"])
            if used and (delete or values["name"] != project["name"]):
                raise ValueError("Project masih dipakai pegawai, termasuk yang nonaktif. Nama tidak dapat diubah dan project tidak dapat dihapus.")
        if delete:
            db.execute("DELETE FROM client_projects WHERE id=? AND client_id=?", (project_id, client["id"]))
        else:
            values["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if project:
                assignment = ",".join(f"{field}=?" for field in values)
                db.execute(f"UPDATE client_projects SET {assignment} WHERE id=? AND client_id=?",
                           (*values.values(), project_id, client["id"]))
            else:
                values["client_id"] = client["id"]
                values["created_at"] = values["updated_at"]
                columns = ",".join(values)
                placeholders = ",".join("?" for field in values)
                db.execute(f"INSERT INTO client_projects ({columns}) VALUES ({placeholders})", tuple(values.values()))
        db.commit()
        flash("Project dihapus." if delete else "Project disimpan.", "success")
    except Exception as error:
        db.rollback()
        if not isinstance(error, ValueError) and not duplicate_error(error):
            raise
        flash(str(error) if isinstance(error, ValueError) else "Project dengan nama tersebut sudah terdaftar di PT ini.", "error")
        if not delete:
            return client_detail_response(client, response_status=422,
                form_restore={"dialog": "project-edit" if project_id else "project-add", "action": request.path, "values": request.form.to_dict()})
    return redirect(target)


@bp.post("/superadmin/clients/<int:client_id>/projects/add")
def superadmin_project_add(client_id):
    return project_change(find_client(client_id))


@bp.post("/superadmin/clients/<int:client_id>/projects/<int:project_id>/edit")
def superadmin_project_edit(client_id, project_id):
    return project_change(find_client(client_id), project_id)


@bp.post("/superadmin/clients/<int:client_id>/projects/<int:project_id>/delete")
def superadmin_project_delete(client_id, project_id):
    return project_change(find_client(client_id), project_id, delete=True)


@bp.get("/admin/projects")
def admin_projects():
    client = admin_client()
    if not client["allow_projects"]:
        abort(403)
    return render_template("client_detail.html", client=client, projects=list_projects(get_db(), client["id"]),
                           admins=[], is_superadmin=False)


@bp.post("/admin/projects/add")
def admin_project_add():
    return project_change(admin_client())


@bp.post("/admin/projects/<int:project_id>/edit")
def admin_project_edit(project_id):
    return project_change(admin_client(), project_id)


@bp.post("/admin/projects/<int:project_id>/delete")
def admin_project_delete(project_id):
    return project_change(admin_client(), project_id, delete=True)
