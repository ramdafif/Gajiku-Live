import sqlite3
from unittest.mock import MagicMock

import pytest

from app.services.client_service import (
    ensure_clients_schema,
    get_reg_withdrawal_max,
    parse_reg_withdrawal_max,
)


@pytest.mark.parametrize('raw,expected', [
    ('1', 1), ('500000', 500000), ('1000000', 1000000),
    ('2000000', 2000000), ('2147483647', 2147483647), (' 500000 ', 500000),
])
def test_parse_regular_limit(raw, expected):
    assert parse_reg_withdrawal_max(raw) == expected


@pytest.mark.parametrize('raw', [
    None, '', ' ', '0', '-1', '+1', '1.5', '1000000.0', '1.000.000',
    '1,000,000', '1e6', 'abc', '2147483648', '9' * 100, '\u0661',
])
def test_parse_regular_limit_rejects_invalid_input(raw):
    with pytest.raises(ValueError):
        parse_reg_withdrawal_max(raw)


def test_legacy_sqlite_schema_upgrade_preserves_data_and_custom_limit():
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT, name_key TEXT, notes TEXT);
            CREATE TABLE pegawai (id INTEGER PRIMARY KEY, perusahaan_induk TEXT);
            CREATE TABLE app_settings (`key` TEXT PRIMARY KEY, value TEXT);
            INSERT INTO app_settings VALUES ('clients_catalog_v1', '1');
            INSERT INTO clients VALUES (1, 'Legacy PT', 'legacy pt', 'keep notes');
        ''')
        ensure_clients_schema(db, 'sqlite')
        assert dict(db.execute('SELECT * FROM clients WHERE id=1').fetchone()) == {
            'id': 1, 'name': 'Legacy PT', 'name_key': 'legacy pt',
            'notes': 'keep notes', 'reg_withdrawal_max': 1000000,
        }
        assert get_reg_withdrawal_max(db, ' LEGACY PT ') == 1000000
        db.execute('UPDATE clients SET reg_withdrawal_max=2000000 WHERE id=1')
        db.commit()
        ensure_clients_schema(db, 'sqlite')
        assert get_reg_withdrawal_max(db, 'legacy pt') == 2000000
        assert get_reg_withdrawal_max(db, '') == 1000000
        assert get_reg_withdrawal_max(db, 'missing pt') == 1000000
        db.execute("INSERT INTO clients (id,name,name_key) VALUES (2,'New PT','new pt')")
        assert get_reg_withdrawal_max(db, 'new pt') == 1000000


@pytest.mark.parametrize('column_exists', [False, True])
def test_mysql_schema_checks_limit_before_catalog_marker(column_exists):
    db = MagicMock()
    db.execute.return_value.fetchone.side_effect = [
        {'exists': 1}, {'exists': 1}, {'exists': 1} if column_exists else None,
        {'value': '1'},
    ]
    ensure_clients_schema(db, 'mysql')
    alters = [call.args[0] for call in db.execute.call_args_list
              if call.args[0].startswith('ALTER TABLE clients')]
    assert alters == ([] if column_exists else [
        'ALTER TABLE clients ADD COLUMN reg_withdrawal_max INTEGER NOT NULL DEFAULT 1000000',
    ])
