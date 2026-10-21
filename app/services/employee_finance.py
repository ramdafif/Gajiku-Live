import calendar
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

import click

MAX_AMOUNT = 2_147_483_647
FINANCIAL_FIELDS = {'gaji', 'plafon', 'plafon_mode', 'plafon_persen', 'siklus_gaji'}
EMPLOYEE_FIELDS = ('id_pegawai', 'nama', 'email', 'jabatan', 'no_telp', 'no_rekening',
                   'no_rekening_lain', 'rekening_ewallet', 'perusahaan_induk', 'perusahaan',
                   'gaji', 'plafon', 'plafon_mode', 'plafon_persen', 'siklus_gaji',
                   'status_aktif', 'admin_fee_flat')


def columns(db, table):
    if isinstance(db, sqlite3.Connection):
        return {row[1] for row in db.execute(f'PRAGMA table_info({table})')}
    return {row['Field'] for row in db.execute(f'SHOW COLUMNS FROM {table}').fetchall()}


def ensure_employee_finance_schema(db, db_type):
    additions = {
        'pegawai': {'plafon': 'INTEGER DEFAULT NULL', 'plafon_mode': "VARCHAR(16) DEFAULT 'nominal'",
                    'plafon_persen': 'DECIMAL(7,4) DEFAULT NULL'},
        'users': {'pegawai_id': 'INTEGER DEFAULT NULL'},
        'clients': {'default_admin_fee': 'INTEGER NOT NULL DEFAULT 15000',
                    'plafon_start_day': 'INTEGER DEFAULT NULL', 'plafon_confirmed_at': 'VARCHAR(32)',
                    'pending_start_day': 'INTEGER DEFAULT NULL', 'period_change_on': 'VARCHAR(10)',
                    'pending_cutoff_day': 'INTEGER DEFAULT NULL', 'cutoff_change_on': 'VARCHAR(10)'},
        'client_bills': {'service_start': 'VARCHAR(10)', 'service_end': 'VARCHAR(10)'},
    }
    additions['clients'].update(period_history='TEXT', cutoff_history='TEXT')
    for table, fields in additions.items():
        existing = columns(db, table)
        for name, definition in fields.items():
            if name not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    if db_type != 'sqlite':
        for table, names in (('pegawai', ('gaji', 'admin_fee_flat')), ('users', ('gaji',))):
            metadata = {row['Field']: row for row in db.execute(f'SHOW COLUMNS FROM {table}').fetchall()}
            for name in names:
                if metadata[name]['Null'] != 'YES':
                    definition = metadata[name]['Type']
                    if not re.fullmatch(r'(?:bigint|int|integer|smallint)(?:\(\d+\))?(?: unsigned)?', definition):
                        raise RuntimeError(f'Tipe legacy {table}.{name} perlu diperiksa: {definition}')
                    db.execute(f'ALTER TABLE {table} MODIFY COLUMN {name} {definition} NULL DEFAULT NULL')
    identity = 'INTEGER PRIMARY KEY AUTOINCREMENT' if db_type == 'sqlite' else 'INT AUTO_INCREMENT PRIMARY KEY'
    suffix = '' if db_type == 'sqlite' else ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4'
    definitions = [
        f'''employee_change_batches (id {identity}, client_id INTEGER NOT NULL, actor VARCHAR(191) NOT NULL,
            source VARCHAR(32) NOT NULL, created_at VARCHAR(32) NOT NULL)''',
        f'''employee_change_items (id {identity}, batch_id INTEGER NOT NULL, employee_id INTEGER,
            operation VARCHAR(16) NOT NULL, before_json TEXT NOT NULL, proposed_json TEXT NOT NULL,
            status VARCHAR(16) NOT NULL DEFAULT 'pending', reviewer VARCHAR(191), reason TEXT,
            decided_at VARCHAR(32))''',
        '''employee_identity_reservations (identity_key VARCHAR(255) PRIMARY KEY, item_id INTEGER NOT NULL)''',
        '''employee_bulk_previews (token VARCHAR(64) PRIMARY KEY, actor VARCHAR(191) NOT NULL,
            client_id INTEGER NOT NULL, payload TEXT NOT NULL, created_at INTEGER NOT NULL,
            consumed INTEGER NOT NULL DEFAULT 0)''',
        f'''employee_audit (id {identity}, actor VARCHAR(191) NOT NULL, client_id INTEGER NOT NULL,
            employee_id INTEGER, action VARCHAR(64) NOT NULL, changes_json TEXT NOT NULL,
            created_at VARCHAR(32) NOT NULL)''',
    ]
    for definition in definitions:
        db.execute('CREATE TABLE IF NOT EXISTS ' + definition + suffix)
    verb = 'INSERT OR IGNORE' if db_type == 'sqlite' else 'INSERT IGNORE'
    db.execute(verb + " INTO app_settings (`key`,value) VALUES ('employee_write_lock','1')")
    if db_type != 'sqlite':
        preview_column = db.execute("SHOW COLUMNS FROM employee_bulk_previews LIKE 'payload'").fetchone()
        if preview_column['Type'].lower() != 'longtext':
            db.execute('ALTER TABLE employee_bulk_previews MODIFY COLUMN payload LONGTEXT NOT NULL')
        period_column = db.execute("SHOW COLUMNS FROM client_bills LIKE 'period'").fetchone()
        if period_column['Type'].lower() == 'varchar(7)':
            db.execute('ALTER TABLE client_bills MODIFY COLUMN period VARCHAR(32) NOT NULL')
    for table, name, fields in (
        ('employee_change_items', 'ix_employee_changes_status', 'status,batch_id,employee_id'),
        ('users', 'ix_users_pegawai', 'pegawai_id'),
        ('transactions', 'ix_transaction_user_date', 'user_id,tanggal,status'),
    ):
        if db_type == 'sqlite':
            db.execute(f'CREATE INDEX IF NOT EXISTS {name} ON {table}({fields})')
        elif not db.execute(f'SHOW INDEX FROM {table} WHERE Key_name=?', (name,)).fetchone():
            db.execute(f'CREATE INDEX {name} ON {table}({fields})')
    db.commit()


def money(raw, nullable=False, maximum=MAX_AMOUNT):
    if raw is None or str(raw).strip() == '':
        if nullable:
            return None
        raise ValueError('Nominal wajib diisi.')
    text = str(raw).strip()
    if not re.fullmatch(r'\d+', text):
        raise ValueError('Nominal harus angka bulat tanpa pemisah ribuan.')
    value = int(text)
    if value > maximum:
        raise ValueError(f'Nominal maksimal {maximum}.')
    return value


def financial_values(values, existing=None):
    merged = dict(existing or {})
    merged.update(values)
    salary = money(merged.get('gaji'), nullable=True)
    mode = merged.get('plafon_mode') or 'nominal'
    if mode == 'persentase':
        try:
            percent = Decimal(str(merged.get('plafon_persen', '')))
        except InvalidOperation:
            raise ValueError('Persentase plafon tidak valid.') from None
        if not percent.is_finite() or not 0 <= percent <= 100 or percent.as_tuple().exponent < -4:
            raise ValueError('Persentase harus 0-100, maksimal 4 angka desimal.')
        if salary is None:
            raise ValueError('Gaji pokok wajib diisi untuk plafon persentase.')
        return {'gaji': salary, 'plafon_mode': mode, 'plafon_persen': str(percent.normalize()),
                'plafon': int(Decimal(salary) * percent / 100)}
    if mode != 'nominal':
        raise ValueError('Mode plafon tidak valid.')
    return {'gaji': salary, 'plafon_mode': mode, 'plafon_persen': None,
            'plafon': money(merged.get('plafon'))}


def monthly_start(at_date, day):
    candidate = date(at_date.year, at_date.month, min(day, calendar.monthrange(at_date.year, at_date.month)[1]))
    if candidate <= at_date:
        return candidate
    previous = at_date.replace(day=1) - timedelta(days=1)
    return previous.replace(day=min(day, previous.day))


def next_start(start, day):
    following = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
    return following.replace(day=min(day, calendar.monthrange(following.year, following.month)[1]))


def period_bounds(client, at_date):
    history = json.loads(client.get('period_history') or '[]')
    if history:
        applicable = [entry for entry in history if date.fromisoformat(entry['from']) <= at_date]
        entry = applicable[-1] if applicable else history[0]
        day = int(entry['day'])
        start = max(monthly_start(at_date, day), date.fromisoformat(entry['from']))
        end = next_start(start, day)
        following = [date.fromisoformat(item['from']) for item in history if date.fromisoformat(item['from']) > at_date]
        if following:
            end = min(end, min(following))
        return start, end
    day = int(client.get('plafon_start_day') or 1)
    change = client.get('period_change_on')
    if change and at_date >= date.fromisoformat(str(change)):
        day = int(client['pending_start_day'])
    start = monthly_start(at_date, day)
    end = next_start(start, day)
    if change:
        boundary = date.fromisoformat(str(change))
        if at_date >= boundary and start < boundary:
            start = boundary
        if start < boundary < end:
            end = boundary
    return start, end


def configure_employee_policy(db, client, start_day, fee):
    from app.services.company_lifecycle import local_now, timestamp
    from app.services.employee_changes import audit
    start_day = money(start_day, maximum=31)
    if start_day < 1:
        raise ValueError('Tanggal awal periode harus 1-31.')
    fee = money(fee, maximum=1_000_000)
    today = local_now().date()
    history = json.loads(client.get('period_history') or '[]')
    if not history:
        start = monthly_start(today, start_day)
        history.append({'from': start.isoformat(), 'day': start_day})
    else:
        active = [entry for entry in history if date.fromisoformat(entry['from']) <= today][-1]
        if int(active['day']) != start_day:
            if any(date.fromisoformat(entry['from']) > today for entry in history):
                raise ValueError('Perubahan periode sudah terjadwal. Tunggu sampai berlaku.')
            boundary = period_bounds(client, today)[1]
            history.append({'from': boundary.isoformat(), 'day': start_day})
    db.execute('''UPDATE clients SET default_admin_fee=?,plafon_start_day=?,plafon_confirmed_at=?,period_history=?
        WHERE id=?''', (fee, int(history[0]['day']), client.get('plafon_confirmed_at') or timestamp(local_now()),
                       json.dumps(history), client['id']))
    audit(db, client['id'], None, 'company.policy',
          {'default_admin_fee': client['default_admin_fee'], 'period_history': client.get('period_history')},
          {'default_admin_fee': fee, 'period_history': json.dumps(history)})


def weekly_config(db, company, at_date):
    from app.services.client_service import client_by_name, name_key
    client = client_by_name(db, company) or {}
    history = json.loads(client.get('cutoff_history') or '[]')
    applicable = [entry for entry in history if date.fromisoformat(entry['from']) <= at_date]
    if applicable:
        entry = applicable[-1]
        return {'cutoff_hari': int(entry['day']), 'effective_from': entry['from']} if entry['enabled'] else None
    row = db.execute('''SELECT cutoff_mingguan_aktif,cutoff_hari FROM admins
        WHERE LOWER(TRIM(company))=? ORDER BY id DESC LIMIT 1''', (name_key(company),)).fetchone()
    if not row or not row['cutoff_mingguan_aktif']:
        return None
    return {'cutoff_hari': int(row['cutoff_hari'] if row['cutoff_hari'] is not None else 2)}


def schedule_cutoff(db, client, enabled, day):
    from app.services.company_lifecycle import local_now
    today = local_now().date()
    current = weekly_config(db, client['name'], today)
    current_enabled = int(bool(current))
    current_day = current['cutoff_hari'] if current else 2
    if (enabled, day) == (current_enabled, current_day):
        return current_enabled, current_day
    history = json.loads(client.get('cutoff_history') or '[]')
    if any(date.fromisoformat(entry['from']) > today for entry in history):
        raise ValueError('Perubahan cutoff sudah terjadwal. Tunggu sampai berlaku.')
    if not history:
        history.append({'from': '1900-01-01', 'enabled': current_enabled, 'day': current_day})
    if current:
        start = today - timedelta(days=(today.weekday() - ((current_day + 1) % 7)) % 7)
        boundary = start + timedelta(days=7)
    else:
        boundary = period_bounds(client, today)[1]
    history.append({'from': boundary.isoformat(), 'enabled': enabled, 'day': day})
    db.execute('UPDATE clients SET cutoff_history=? WHERE id=?', (json.dumps(history), client['id']))
    return current_enabled, current_day


def period_billing_state(db, client, setting, today):
    from app.services.company_lifecycle import as_date, due_date, following_due
    bills = [dict(row) for row in db.execute('SELECT * FROM client_bills WHERE client_id=? ORDER BY due_on,id',
                                            (client['id'],)).fetchall()]
    initial = json.loads(client.get('period_history') or '[]')
    first = date.fromisoformat(initial[0]['from']) if initial else monthly_start(
        as_date(str(client['plafon_confirmed_at'])[:10]), int(client['plafon_start_day']))
    anomalies = []
    cursor = first
    while cursor <= today:
        start, end = period_bounds(client, cursor)
        matches = [bill for bill in bills if bill.get('service_start') == start.isoformat()
                   and bill.get('service_end') == end.isoformat()]
        legacy = [bill for bill in bills if not bill.get('service_start') or not bill.get('service_end')]
        if legacy:
            anomalies = [bill['id'] for bill in legacy]
            break
        overlaps = [bill for bill in bills if as_date(bill['service_start']) < end
                    and as_date(bill['service_end']) > start and bill not in matches]
        if overlaps:
            anomalies = [bill['id'] for bill in overlaps]
            break
        if not matches:
            due = due_date(end.year, end.month, int(setting['due_day']))
            if due < end:
                due = following_due(due, int(setting['due_day']))
            bills.append({'id': None, 'client_id': client['id'], 'period': 'P:' + start.isoformat(),
                          'due_on': due, 'paid_at': None, 'paid_by': None,
                          'service_start': start.isoformat(), 'service_end': end.isoformat()})
        cursor = end
    overdue = [bill for bill in bills if not bill['paid_at'] and (
        (bill.get('service_end') and as_date(bill['service_end']) <= today)
        or (not bill.get('service_end') and as_date(bill['due_on']) < today))]
    return {'configured': True, 'blocked': bool(setting['blocked_at'] or overdue or anomalies),
            'bills': bills, 'overdue': overdue, 'setting': setting,
            'configuration_required': bool(anomalies), 'legacy_bill_ids': anomalies}


def migrate_finance(db, apply=False):
    if apply:
        db.commit()
        if isinstance(db, sqlite3.Connection):
            db.execute('BEGIN IMMEDIATE')
        else:
            db.execute("SELECT value FROM app_settings WHERE `key`='employee_write_lock' FOR UPDATE").fetchone()
    marker = db.execute("SELECT value FROM app_settings WHERE `key`='employee_finance_v1'").fetchone()
    if marker:
        if apply:
            db.commit()
        return {'already_migrated': True, 'employees': 0, 'unlinked_users': []}
    employees = [dict(row) for row in db.execute('SELECT id,email,gaji,plafon,admin_fee_flat FROM pegawai').fetchall()]
    unlinked, links = [], []
    for user in db.execute('SELECT id,email,pegawai_id FROM users').fetchall():
        matches = [employee for employee in employees if employee['email'].strip().lower() == user['email'].strip().lower()]
        if len(matches) != 1 or (user['pegawai_id'] and user['pegawai_id'] != matches[0]['id']):
            unlinked.append(user['id'])
        elif any(employee_id == matches[0]['id'] for employee_id, user_id in links):
            unlinked.append(user['id'])
        else:
            links.append((matches[0]['id'], user['id']))
    report = {'already_migrated': False, 'employees': len(employees), 'unlinked_users': unlinked,
              'legacy_salary_total': sum(int(row['gaji'] or 0) for row in employees if row['plafon'] is None),
              'converted_plafon_total': sum(max(int(row['gaji'] or 0), 0) // 2 for row in employees if row['plafon'] is None),
              'legacy_bills_to_map': [row['id'] for row in db.execute('SELECT id FROM client_bills WHERE service_start IS NULL').fetchall()]}
    if apply:
        if unlinked:
            raise ValueError('Migrasi dibatalkan: relasi users-pegawai ambigu/tidak ditemukan: ' + str(unlinked))
        for employee in employees:
            if employee['plafon'] is None:
                db.execute("UPDATE pegawai SET plafon=?,plafon_mode='nominal',plafon_persen=NULL,gaji=NULL WHERE id=?",
                           (max(int(employee['gaji'] or 0), 0) // 2, employee['id']))
            if employee['admin_fee_flat'] == 15000:
                db.execute('UPDATE pegawai SET admin_fee_flat=NULL WHERE id=?', (employee['id'],))
        db.executemany('UPDATE users SET pegawai_id=?,gaji=NULL WHERE id=?', links)
        db.execute("INSERT INTO app_settings (`key`,value) VALUES ('employee_finance_v1',?)",
                   (datetime.now().isoformat(),))
        db.commit()
    return report


def register_finance_commands(app):
    @app.cli.command('migrate-employee-finance')
    @click.option('--apply', is_flag=True, help='Terapkan setelah backup dan cutover; default dry-run.')
    def migrate_command(apply):
        from app.db import get_db
        try:
            click.echo(json.dumps(migrate_finance(get_db(), apply), indent=2))
        except ValueError as error:
            get_db().rollback()
            raise click.ClickException(str(error)) from error

    @app.cli.command('map-legacy-bill')
    @click.option('--bill-id', type=int, required=True)
    @click.option('--service-start', required=True, help='Tanggal awal inklusif YYYY-MM-DD.')
    @click.option('--service-end', required=True, help='Tanggal akhir eksklusif YYYY-MM-DD.')
    @click.option('--apply', is_flag=True)
    def map_bill(bill_id, service_start, service_end, apply):
        from app.db import get_db
        db = get_db()
        start, end = date.fromisoformat(service_start), date.fromisoformat(service_end)
        row = db.execute('SELECT * FROM client_bills WHERE id=?', (bill_id,)).fetchone()
        if not row or start >= end:
            raise click.ClickException('Tagihan/rentang tidak valid.')
        overlaps = db.execute('''SELECT id FROM client_bills WHERE client_id=? AND id<>?
            AND service_start<? AND service_end>?''', (row['client_id'], bill_id, end.isoformat(), start.isoformat())).fetchall()
        if overlaps:
            raise click.ClickException('Rentang bertumpang tindih dengan tagihan lain.')
        click.echo(json.dumps({'bill_id': bill_id, 'service_start': service_start, 'service_end': service_end,
                               'apply': apply}))
        if apply:
            db.execute('UPDATE client_bills SET service_start=?,service_end=? WHERE id=?',
                       (service_start, service_end, bill_id))
            db.commit()


def compute_employee_limits(at_date, user_id):
    from app.db import get_db
    from app.routes.web import (get_weekly_cutoff_config, normalize_siklus, weekly_period_start,
                                weekly_day_in_period, period_key_by_cycle, day_in_cycle, SIKLUS_START_DAY)
    from app.services.client_service import client_by_name
    db = get_db()
    row = db.execute('SELECT p.* FROM users u JOIN pegawai p ON p.id=u.pegawai_id WHERE u.id=?',
                     (user_id,)).fetchone()
    employee = dict(row) if row else {}
    client = client_by_name(db, employee.get('perusahaan_induk')) or {}
    weekly = weekly_config(db, employee.get('perusahaan_induk'), at_date)
    cycle = normalize_siklus(employee.get('siklus_gaji') or 'A')
    ready = bool(row and client and employee.get('plafon') is not None)
    if weekly:
        ready = ready and bool(client.get('plafon_confirmed_at'))
        monthly_from, monthly_to = period_bounds(client, at_date)
        cutoff_day = weekly['cutoff_hari']
        change = client.get('cutoff_change_on')
        if change and at_date >= date.fromisoformat(str(change)):
            cutoff_day = int(client['pending_cutoff_day'])
        week_from = weekly_period_start(at_date, cutoff_day)
        if weekly.get('effective_from'):
            week_from = max(week_from, date.fromisoformat(weekly['effective_from']))
        if change and at_date >= date.fromisoformat(str(change)):
            week_from = max(week_from, date.fromisoformat(str(change)))
        period_from, period_to = week_from, weekly_period_start(at_date, cutoff_day) + timedelta(days=7)
        day = (at_date - week_from).days + 1
        period_key = 'W:' + week_from.isoformat()
    else:
        monthly_from = monthly_start(at_date, SIKLUS_START_DAY[cycle])
        monthly_to = next_start(monthly_from, SIKLUS_START_DAY[cycle])
        period_from, period_to = monthly_from, monthly_to
        day = day_in_cycle(at_date, cycle)
        period_key = period_key_by_cycle(at_date, cycle)
    monthly_cap = int(employee.get('plafon') or 0) if ready else 0
    cap = monthly_cap // 4 if weekly else monthly_cap
    days = 7 if weekly else 30
    daily = cap // days

    def used(start, end):
        legacy_key = period_key if weekly else period_key_by_cycle(at_date, cycle)
        result = db.execute('''SELECT COALESCE(SUM(t.nominal),0) AS total FROM transactions t
            JOIN users u ON u.id=t.user_id WHERE u.pegawai_id=? AND t.tanggal>=?
            AND (t.tanggal<? OR t.periode=?)
            AND t.status IN ('sukses','on-proses')''',
            (employee.get('id', 0), start.isoformat(), end.isoformat(), legacy_key)).fetchone()
        return int(result['total'] or 0)

    monthly_used = used(monthly_from, monthly_to)
    period_used = used(period_from, period_to)
    monthly_left = max(monthly_cap - monthly_used, 0)
    period_left = max(cap - period_used, 0)
    balance = max(min(daily * day, cap) - period_used, 0)
    return {'plafon': cap, 'plafon_bulanan': monthly_cap, 'limit_harian': daily,
            'limit_period_days': days, 'hari_ke': day, 'total_sukses': period_used,
            'total_bulanan': monthly_used, 'saldo': min(balance, monthly_left),
            'periode_key': period_key, 'siklus': cycle, 'sisa_plafon': min(period_left, monthly_left),
            'sisa_bulanan': monthly_left, 'cutoff_mingguan': bool(weekly),
            'cutoff_hari': cutoff_day if weekly else None, 'configuration_required': not ready,
            'monthly_start': monthly_from.isoformat(), 'monthly_end': (monthly_to - timedelta(days=1)).isoformat(),
            'gaji_pokok': employee.get('gaji')}
