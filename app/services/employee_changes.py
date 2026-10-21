import hashlib
import hmac
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager

from flask import current_app, session

from app.db import get_db
from app.services.client_service import client_by_name, employee_assignment, name_key
from app.services.employee_finance import EMPLOYEE_FIELDS, FINANCIAL_FIELDS, financial_values, money
from app.services import company_lifecycle as lifecycle


def actor():
    from app.routes.notifications import principal
    return principal()


def encoded(value):
    return json.dumps(value, sort_keys=True, default=str)


def snapshot(row):
    result = {key: row[key] for key in EMPLOYEE_FIELDS}
    if result['plafon_persen'] is not None:
        from decimal import Decimal
        result['plafon_persen'] = str(Decimal(str(result['plafon_persen'])).normalize())
    return result


@contextmanager
def write_transaction(client_ids=()):
    db = get_db()
    db.commit()
    try:
        if isinstance(db, sqlite3.Connection):
            db.execute('BEGIN IMMEDIATE')
        else:
            db.execute("SELECT value FROM app_settings WHERE `key`='employee_write_lock' FOR UPDATE").fetchone()
            for client_id in sorted(set(client_ids)):
                db.execute('SELECT id FROM clients WHERE id=? FOR UPDATE', (client_id,)).fetchone()
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise


def audit(db, client_id, employee_id, action, before=None, after=None):
    changes = {}
    secret = str(current_app.secret_key).encode()
    for field in set(before or {}) | set(after or {}):
        old, new = (before or {}).get(field), (after or {}).get(field)
        if old == new:
            continue
        if field in {'email', 'no_rekening', 'no_rekening_lain', 'rekening_ewallet', 'no_telp'}:
            def protected(value):
                text = str(value or '')
                return {'last4': text[-4:] if field != 'email' else '',
                        'digest': hmac.new(secret, text.encode(), hashlib.sha256).hexdigest()}
            old, new = protected(old), protected(new)
        changes[field] = {'before': old, 'after': new}
    db.execute('''INSERT INTO employee_audit (actor,client_id,employee_id,action,changes_json,created_at)
        VALUES (?,?,?,?,?,?)''', (actor(), client_id, employee_id, action, encoded(changes),
                                lifecycle.timestamp(lifecycle.local_now())))


def notify(db, client_id, kind, title, message, batch_id=None, audience='superadmin'):
    lifecycle.insert_once(db, 'notifications', {
        'event_key': 'employee:' + uuid.uuid4().hex, 'client_id': client_id, 'kind': kind,
        'audience': audience, 'period_key': str(batch_id or ''), 'title': title,
        'message': message, 'created_at': lifecycle.timestamp(lifecycle.local_now())})


def scoped_client(db, name):
    client = client_by_name(db, name)
    if not client:
        raise ValueError('Perusahaan belum terdaftar.')
    if not session.get('is_superadmin') and name_key(session.get('company')) != client['name_key']:
        raise ValueError('Pegawai berada di luar perusahaan Anda.')
    return client


def assert_identity(db, values, employee_id=None, reservation_item=None):
    for field in ('email', 'id_pegawai'):
        value = values[field].strip().lower()
        found = db.execute(f'SELECT id FROM pegawai WHERE LOWER({field})=? AND id<>?',
                           (value, employee_id or 0)).fetchone()
        reserved = db.execute('SELECT item_id FROM employee_identity_reservations WHERE identity_key=?',
                              (field + ':' + value,)).fetchone()
        if found or (reserved and reserved['item_id'] != reservation_item):
            raise ValueError('Email atau ID pegawai sudah dipakai/menunggu persetujuan.')
    for table, association in (('users', 'pegawai_id'), ('user_accounts', 'pegawai_id')):
        found = db.execute(f'SELECT id,{association} FROM {table} WHERE LOWER(email)=?',
                           (values['email'].lower(),)).fetchone()
        if found and found[association] != employee_id:
            raise ValueError('Email sudah terkait akun lain; selesaikan relasi akun terlebih dahulu.')


def validate_employee(db, incoming, existing=None):
    existing = dict(existing or {})
    values = {key: existing.get(key) for key in EMPLOYEE_FIELDS}
    values.update({key: incoming[key] for key in EMPLOYEE_FIELDS if key in incoming})
    for field in ('id_pegawai', 'nama', 'email', 'jabatan', 'no_telp', 'no_rekening',
                  'no_rekening_lain', 'rekening_ewallet', 'perusahaan_induk', 'perusahaan'):
        values[field] = str(values.get(field) or '').strip()
        maximum = 50 if field == 'no_telp' else 255
        if len(values[field]) > maximum:
            raise ValueError(f'{field} terlalu panjang.')
    values['id_pegawai'] = values['id_pegawai'].upper()
    values['email'] = values['email'].lower()
    if not re.fullmatch(r'[A-Z0-9]{1,16}', values['id_pegawai']):
        raise ValueError('ID pegawai wajib 1-16 karakter alfanumerik.')
    if not values['nama'] or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
        raise ValueError('Nama dan email valid wajib diisi.')
    if not re.fullmatch(r'[A-Za-z0-9 ]*', values['rekening_ewallet']):
        raise ValueError('E-wallet hanya boleh huruf, angka, dan spasi.')
    values['perusahaan_induk'], values['perusahaan'] = employee_assignment(
        db, values['perusahaan_induk'], values['perusahaan'], bool(session.get('is_superadmin')),
        session.get('company'), existing=existing or None)
    values.update(financial_values(values))
    values['siklus_gaji'] = values.get('siklus_gaji') or 'B'
    if values['siklus_gaji'] not in ('A', 'B', 'C', 'D'):
        raise ValueError('Siklus gaji tidak valid.')
    if str(values.get('status_aktif', 1)) not in ('0', '1'):
        raise ValueError('Status pegawai tidak valid.')
    values['status_aktif'] = int(values.get('status_aktif', 1))
    if session.get('is_superadmin'):
        values['admin_fee_flat'] = money(values.get('admin_fee_flat'), nullable=True, maximum=1_000_000)
    else:
        if 'admin_fee_flat' in incoming:
            raise ValueError('Hanya superadmin dapat mengubah fee.')
        values['admin_fee_flat'] = existing.get('admin_fee_flat')
    return values


def create_employee(db, values, reservation_item=None):
    assert_identity(db, values, reservation_item=reservation_item)
    names = list(EMPLOYEE_FIELDS) + ['created_at']
    cursor = db.execute(f"INSERT INTO pegawai ({','.join(names)}) VALUES ({','.join('?' for field in names)})",
                        [values[field] for field in EMPLOYEE_FIELDS] + [lifecycle.timestamp(lifecycle.local_now())])
    client = client_by_name(db, values['perusahaan_induk'])
    audit(db, client['id'], cursor.lastrowid, 'created', after=values)
    return cursor.lastrowid


def apply_update(db, employee_id, old, patch):
    merged = dict(old)
    merged.update(patch)
    assert_identity(db, merged, employee_id)
    fields = [field for field in EMPLOYEE_FIELDS if field in patch]
    if not fields:
        return
    db.execute('UPDATE pegawai SET ' + ','.join(field + '=?' for field in fields) + ' WHERE id=?',
               [patch[field] for field in fields] + [employee_id])
    db.execute('UPDATE user_accounts SET name=?,email=?,status_aktif=? WHERE pegawai_id=?',
               (merged['nama'], merged['email'], merged['status_aktif'], employee_id))
    db.execute('UPDATE users SET name=?,email=?,gaji=?,pegawai_id=? WHERE pegawai_id=?',
               (merged['nama'], merged['email'], merged['gaji'], employee_id, employee_id))
    client = client_by_name(db, merged['perusahaan_induk'])
    audit(db, client['id'], employee_id, 'updated', old, merged)


def new_batch(db, client_id, source):
    return db.execute('''INSERT INTO employee_change_batches (client_id,actor,source,created_at)
        VALUES (?,?,?,?)''', (client_id, actor(), source, lifecycle.timestamp(lifecycle.local_now()))).lastrowid


def queue_item(db, batch_id, operation, values, employee_id=None, before=None):
    item_id = db.execute('''INSERT INTO employee_change_items
        (batch_id,employee_id,operation,before_json,proposed_json) VALUES (?,?,?,?,?)''',
        (batch_id, employee_id, operation, encoded(before or {}), encoded(values))).lastrowid
    if operation == 'create':
        for field in ('email', 'id_pegawai'):
            db.execute('INSERT INTO employee_identity_reservations (identity_key,item_id) VALUES (?,?)',
                       (field + ':' + values[field].lower(), item_id))
    return item_id


def cancel_employee_requests(db, employee_id):
    rows = db.execute("SELECT id,batch_id FROM employee_change_items WHERE employee_id=? AND status='pending'",
                      (employee_id,)).fetchall()
    for row in rows:
        db.execute("UPDATE employee_change_items SET status='cancelled',reviewer=?,reason=?,decided_at=? WHERE id=?",
                   (actor(), 'Pegawai dinonaktifkan/dihapus.', lifecycle.timestamp(lifecycle.local_now()), row['id']))
        db.execute('DELETE FROM employee_identity_reservations WHERE item_id=?', (row['id'],))
        batch = db.execute('SELECT client_id FROM employee_change_batches WHERE id=?', (row['batch_id'],)).fetchone()
        audit(db, batch['client_id'], employee_id, 'cancelled', after={'request_item': row['id']})
    for batch_id in sorted({row['batch_id'] for row in rows}):
        batch = db.execute('SELECT client_id FROM employee_change_batches WHERE id=?', (batch_id,)).fetchone()
        notify(db, batch['client_id'], 'employee_result', 'Usulan pegawai dibatalkan',
               'Pegawai dinonaktifkan atau dihapus. Ajukan ulang jika masih diperlukan.', batch_id, 'management')
    return rows


def submit_update(db, client, employee_id, old, values, batch_id=None):
    old = snapshot(old)
    changes = {field: value for field, value in values.items() if old.get(field) != value}
    if not changes:
        return False, False
    if session.get('is_superadmin'):
        apply_update(db, employee_id, old, changes)
        if changes.get('status_aktif') == 0:
            cancel_employee_requests(db, employee_id)
        if batch_id:
            item_id = queue_item(db, batch_id, 'activity', changes, employee_id,
                                 {field: old[field] for field in changes})
            db.execute("UPDATE employee_change_items SET status='applied' WHERE id=?", (item_id,))
        return True, False
    protected = FINANCIAL_FIELDS | ({'status_aktif'} if values['status_aktif'] > old['status_aktif'] else set())
    pending = {field: value for field, value in changes.items() if field in protected}
    immediate = {field: value for field, value in changes.items() if field not in protected}
    if immediate:
        apply_update(db, employee_id, old, immediate)
        if immediate.get('status_aktif') == 0:
            cancel_employee_requests(db, employee_id)
    if pending:
        if batch_id is None:
            batch_id = new_batch(db, client['id'], 'edit')
        comparison = {field: old[field] for field in FINANCIAL_FIELDS | {'status_aktif', 'perusahaan_induk'}}
        comparison.update({field: value for field, value in immediate.items() if field in comparison})
        queue_item(db, batch_id, 'update', pending, employee_id, comparison)
        audit(db, client['id'], employee_id, 'requested', comparison, {**comparison, **pending})
    if batch_id is None:
        batch_id = new_batch(db, client['id'], 'activity')
    if immediate:
        queue_item(db, batch_id, 'activity', immediate, employee_id,
                   {field: old[field] for field in immediate})
        db.execute("UPDATE employee_change_items SET status='applied' WHERE batch_id=? AND operation='activity'",
                   (batch_id,))
    return bool(immediate), bool(pending)


def decide_items(db, batch, item_ids, decision, reason=''):
    if decision not in {'approve', 'reject', 'cancel'}:
        raise ValueError('Keputusan tidak valid.')
    if decision != 'cancel' and not session.get('is_superadmin'):
        raise ValueError('Hanya superadmin dapat memutuskan usulan.')
    if decision == 'cancel' and not session.get('is_superadmin') and batch['actor'] != actor():
        raise ValueError('Hanya pembuat usulan dapat membatalkan.')
    if decision == 'reject' and not reason.strip():
        raise ValueError('Alasan penolakan wajib diisi.')
    if len(reason) > 2000:
        raise ValueError('Catatan maksimal 2000 karakter.')
    if decision == 'approve' and batch['actor'].startswith('admin:'):
        submitter = db.execute('SELECT status_aktif,company,role FROM admins WHERE id=?',
                               (int(batch['actor'].split(':')[1]),)).fetchone()
        client = db.execute('SELECT name_key FROM clients WHERE id=?', (batch['client_id'],)).fetchone()
        if not submitter or submitter['status_aktif'] != 1 or (
                submitter['role'] != 'superadmin' and name_key(submitter['company']) != client['name_key']):
            raise ValueError('Akses pembuat usulan telah berubah. Tolak atau batalkan usulan ini.')
    if not item_ids or len(item_ids) != len(set(item_ids)):
        raise ValueError('Pilih item yang akan diputuskan.')
    items = []
    for item_id in sorted(item_ids):
        item = db.execute('SELECT * FROM employee_change_items WHERE id=? AND batch_id=?',
                          (item_id, batch['id'])).fetchone()
        if not item or item['status'] != 'pending':
            raise ValueError('Item sudah diputuskan atau tidak berada dalam batch ini.')
        items.append(dict(item))
    conflicts = []
    for item in items:
        if decision != 'approve':
            continue
        proposed = json.loads(item['proposed_json'])
        client = db.execute('SELECT * FROM clients WHERE id=?', (batch['client_id'],)).fetchone()
        if not client or name_key(proposed.get('perusahaan_induk', client['name'])) != client['name_key']:
            raise ValueError('Perusahaan usulan tidak valid.')
        if item['operation'] == 'create':
            values = validate_employee(db, proposed)
            assert_identity(db, values, reservation_item=item['id'])
        else:
            row = db.execute('SELECT * FROM pegawai WHERE id=?', (item['employee_id'],)).fetchone()
            before = json.loads(item['before_json'])
            old = snapshot(row) if row else {}
            if not row or name_key(old['perusahaan_induk']) != client['name_key'] or any(
                    old.get(field) != value for field, value in before.items()):
                conflicts.append(item)
                continue
            validate_employee(db, proposed, old)
    if conflicts:
        for item in conflicts:
            db.execute("UPDATE employee_change_items SET status='conflict',reason=?,reviewer=?,decided_at=? WHERE id=?",
                       ('Data efektif berubah; ajukan ulang.', actor(), lifecycle.timestamp(lifecycle.local_now()), item['id']))
            db.execute('DELETE FROM employee_identity_reservations WHERE item_id=?', (item['id'],))
        notify(db, batch['client_id'], 'employee_result', 'Usulan pegawai berkonflik',
               'Data berubah sejak pengajuan. Tidak ada perubahan terpilih yang diterapkan.', batch['id'], 'management')
        return 'conflict'
    status = {'approve': 'approved', 'reject': 'rejected', 'cancel': 'cancelled'}[decision]
    for item in items:
        proposed = json.loads(item['proposed_json'])
        employee_id = item['employee_id']
        if decision == 'approve':
            if item['operation'] == 'create':
                employee_id = create_employee(db, validate_employee(db, proposed), item['id'])
            else:
                old = snapshot(db.execute('SELECT * FROM pegawai WHERE id=?', (employee_id,)).fetchone())
                apply_update(db, employee_id, old, proposed)
        db.execute('''UPDATE employee_change_items SET status=?,employee_id=?,reviewer=?,reason=?,decided_at=?
            WHERE id=? AND status='pending' ''',
            (status, employee_id, actor(), reason.strip(), lifecycle.timestamp(lifecycle.local_now()), item['id']))
        db.execute('DELETE FROM employee_identity_reservations WHERE item_id=?', (item['id'],))
        audit(db, batch['client_id'], employee_id, status, after={'request_item': item['id'], 'reason': reason})
    notify(db, batch['client_id'], 'employee_result', 'Hasil persetujuan pegawai',
           f'{len(items)} item: {status}. Oleh {actor()}. ' + reason.strip(), batch['id'], 'management')
    return status
