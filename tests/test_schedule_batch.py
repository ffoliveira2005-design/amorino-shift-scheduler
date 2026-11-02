"""Atomic schedule imports operate on standalone scratch SQLite databases."""
import hashlib
import json
import sqlite3
from datetime import date, timedelta

import pytest

from scripts.import_schedule_batch import ScheduleImportError, import_batch


def _database(tmp_path):
    path = tmp_path / 'scratch.db'
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TABLE funcionarios (
                sigla TEXT PRIMARY KEY, nome TEXT, horas_contrato INTEGER,
                tipo_contrato TEXT, pin TEXT, rank TEXT, ical_token TEXT);
            CREATE TABLE semanas (
                id INTEGER PRIMARY KEY, codigo TEXT UNIQUE, label TEXT,
                dias_json TEXT, ativa INTEGER, descarga_dias TEXT,
                descarga_lojas TEXT, descarga_config TEXT, criada_em TEXT);
            CREATE TABLE turnos (
                id INTEGER PRIMARY KEY, loja TEXT, horario TEXT, dia TEXT,
                funcionario TEXT, nota TEXT, semana TEXT, updated_at TEXT);
            CREATE TABLE descargas (
                id INTEGER PRIMARY KEY, semana TEXT, dia TEXT, loja TEXT,
                funcionario TEXT, horario TEXT, criada_em TEXT,
                UNIQUE (semana,dia,loja,funcionario));
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, ip TEXT,
                action TEXT, target TEXT, detail TEXT);
            CREATE TABLE loja_headcount_targets (
                id INTEGER PRIMARY KEY, loja TEXT, dia_idx INTEGER, target INTEGER);
            INSERT INTO funcionarios VALUES
                ('EX','Nome existente',24,'contrato','hashed-pin','store_manager','private-token'),
                ('OTHER','Outro funcionário',16,'contrato','other-pin','staff','other-token');
            INSERT INTO semanas (codigo,label,dias_json,ativa,descarga_config)
                VALUES ('14-20set','Semana anterior','[]',1,'{}');
            INSERT INTO turnos (loja,horario,dia,funcionario,nota,semana,updated_at)
                VALUES ('gaia','09:00-17:00','Seg 14','EX','Manual','14-20set','original-time');
            INSERT INTO loja_headcount_targets (loja,dia_idx,target)
                VALUES ('gaia',0,6);
        ''')
    return path


def _week(monday, worker, *, source):
    days = [{'label': f'{["Seg","Ter","Qua","Qui","Sex","Sab","Dom"][i]} {(monday + timedelta(days=i)).day}',
             'date': (monday + timedelta(days=i)).isoformat(), 'feriado': False,
             'feriado_nome': ''} for i in range(7)]
    sunday = monday + timedelta(days=6)
    code = f'{monday.day}-{sunday.day}{["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"][sunday.month - 1]}'
    return {'source': source, 'week_start': monday.isoformat(),
            'week_codigo': code, 'week_label': source,
            'days': days, 'warnings': [],
            'shifts': [{'loja': 'carmelitas', 'dia': days[1]['label'],
                        'funcionario': worker, 'horario': '14:00-20:00'}],
            'descargas': [],
            'roster': [{'sigla': worker, 'nome': 'Nome ' + worker,
                        'horas_contrato': 0, 'tipo_contrato': 'turnos', 'rank': None}]}


def _batch():
    first = _week(date(2026, 9, 28), 'NEW', source='Primeiro PDF')
    first['shifts'].append({'loja': 'norte', 'dia': first['days'][0]['label'],
                            'funcionario': 'EX', 'horario': '08:00-16:30'})
    first['descargas'].append({'loja': 'carmelitas', 'dia': first['days'][1]['label'],
                               'funcionario': 'NEW', 'inicio': '08:00'})
    # The positive base row must take precedence over a duplicate on-call row.
    first['roster'].append({'sigla': 'NEW', 'nome': 'Nome NEW',
                            'horas_contrato': 32, 'tipo_contrato': 'contrato', 'rank': None})
    # Unscheduled roster additions must not create unnecessary staff accounts.
    first['roster'].append({'sigla': 'UNUSED', 'nome': 'Não escalado',
                            'horas_contrato': 0, 'tipo_contrato': 'turnos', 'rank': None})
    second = _week(date(2026, 10, 5), 'NEXT', source='Segundo PDF')
    return [first, second]


def _rows(path, table):
    with sqlite3.connect(path) as connection:
        return connection.execute(f'SELECT * FROM {table} ORDER BY 1').fetchall()


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_batch_plan_is_read_only_and_does_not_make_a_backup(tmp_path):
    path = _database(tmp_path)
    before = _digest(path)
    report = import_batch(path, _batch(), backup_dir=tmp_path / 'backups')
    assert _digest(path) == before
    assert not (tmp_path / 'backups').exists()
    assert report['applied'] is False and report['backup_path'] is None
    assert report['counts']['staff_added'] == 2
    assert report['counts']['weeks_added'] == 2
    assert report['counts']['shifts_added'] == 3
    assert report['counts']['deliveries_added'] == 1
    assert {person['sigla'] for person in report['new_staff']} == {'NEW', 'NEXT'}
    new = next(person for person in report['new_staff'] if person['sigla'] == 'NEW')
    assert new['horas_contrato'] == 32 and new['tipo_contrato'] == 'contrato'
    assert new['rank'] == 'trainee'


def test_explicit_contract_updates_preserve_identity_and_credentials(tmp_path):
    path = _database(tmp_path)
    changes = [{'sigla': 'EX', 'horas_contrato': 32, 'tipo_contrato': 'contrato'},
               {'sigla': 'OTHER', 'horas_contrato': 0, 'tipo_contrato': 'turnos'}]
    before = _digest(path)
    plan = import_batch(path, _batch(), contract_updates=changes)
    assert _digest(path) == before and plan['counts']['contracts_updated'] == 2
    result = import_batch(path, _batch(), apply=True, backup_dir=tmp_path / 'backups',
                          contract_updates=changes)
    assert result['counts']['contracts_updated'] == 2
    with sqlite3.connect(path) as connection:
        existing = connection.execute('SELECT * FROM funcionarios WHERE sigla="EX"').fetchone()
        assert existing == ('EX', 'Nome existente', 32, 'contrato', 'hashed-pin', 'store_manager', 'private-token')
        audit = json.loads(connection.execute('SELECT detail FROM audit_log').fetchone()[0])
        assert audit['contract_updates'][0]['previous_hours'] == 24
    assert import_batch(path, _batch(), contract_updates=changes)['counts']['contracts_updated'] == 0


def test_unknown_contract_update_does_not_write(tmp_path):
    path = _database(tmp_path)
    before = _digest(path)
    with pytest.raises(ScheduleImportError):
        import_batch(path, _batch(), apply=True, backup_dir=tmp_path / 'backups',
                     contract_updates=[{'sigla': 'UNKNOWN', 'horas_contrato': 24, 'tipo_contrato': 'contrato'}])
    assert _digest(path) == before
    assert not (tmp_path / 'backups').exists()


def test_bad_second_week_leaves_the_entire_batch_unapplied(tmp_path):
    path = _database(tmp_path)
    before = _digest(path)
    batch = _batch()
    batch[1]['roster'] = []  # NEXT has no confirmed identity.
    with pytest.raises(ScheduleImportError, match='NEXT'):
        import_batch(path, batch, apply=True, backup_dir=tmp_path / 'backups')
    assert _digest(path) == before
    assert not (tmp_path / 'backups').exists()


def test_write_failure_in_second_week_rolls_back_first_week_and_new_staff(tmp_path):
    path = _database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.executescript('''
            CREATE TRIGGER fail_second BEFORE INSERT ON turnos
            WHEN NEW.semana='5-11out'
            BEGIN SELECT RAISE(ABORT, 'second week rejected'); END;
        ''')
    before = {table: _rows(path, table) for table in
              ('funcionarios', 'semanas', 'turnos', 'descargas', 'audit_log')}
    with pytest.raises(sqlite3.IntegrityError, match='second week rejected'):
        import_batch(path, _batch(), apply=True, backup_dir=tmp_path / 'backups')
    assert {table: _rows(path, table) for table in before} == before
    backups = list((tmp_path / 'backups').rglob('*.db'))
    assert len(backups) == 1
    assert _rows(backups[0], 'funcionarios') == before['funcionarios']


def test_repeat_import_is_idempotent_and_preserves_existing_fields_and_manual_rows(tmp_path):
    path = _database(tmp_path)
    staff_before = _rows(path, 'funcionarios')
    manual_before = _rows(path, 'turnos')[0]
    targets_before = _rows(path, 'loja_headcount_targets')
    first = import_batch(path, _batch(), apply=True, backup_dir=tmp_path / 'backups')
    saved = {table: _rows(path, table) for table in ('funcionarios', 'semanas', 'turnos', 'descargas')}
    second = import_batch(path, _batch(), apply=True, backup_dir=tmp_path / 'backups')
    assert {table: _rows(path, table) for table in saved} == saved
    assert second['counts']['staff_added'] == second['counts']['weeks_added'] == 0
    assert second['counts']['shifts_added'] == second['counts']['deliveries_added'] == 0
    assert second['counts']['shifts_skipped'] == 3 and second['counts']['deliveries_skipped'] == 1
    assert first['backup_path'] != second['backup_path']
    assert all(row in _rows(path, 'funcionarios') for row in staff_before)
    assert manual_before in _rows(path, 'turnos')
    assert _rows(path, 'loja_headcount_targets') == targets_before
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT pin,rank,ical_token FROM funcionarios WHERE sigla="NEW"').fetchone() == ('', 'trainee', '')
        assert connection.execute('SELECT horario FROM descargas').fetchone()[0] == '08:00-12:00'
        assert connection.execute('SELECT codigo FROM semanas WHERE ativa=1').fetchone()[0] == '14-20set'
        config = json.loads(connection.execute('SELECT descarga_config FROM semanas WHERE codigo="28-4out"').fetchone()[0])
        assert config == {'Ter 29': {'carmelitas': '08:00-12:00'}}
        audit = json.loads(connection.execute('SELECT detail FROM audit_log ORDER BY id LIMIT 1').fetchone()[0])
        assert audit['sources'] == ['Primeiro PDF', 'Segundo PDF']
        assert audit['counts']['shifts_added'] == 3


def test_different_existing_assignment_fails_without_overwriting_manual_work(tmp_path):
    path = _database(tmp_path)
    first = _batch()[0]
    with sqlite3.connect(path) as connection:
        connection.execute('INSERT INTO turnos (loja,horario,dia,funcionario,nota,semana) VALUES (?,?,?,?,?,?)',
            ('gaia', '09:00-17:00', first['days'][0]['label'], 'EX', 'Manual', first['week_codigo']))
    before = _digest(path)
    with pytest.raises(ScheduleImportError, match='atribuição diferente'):
        import_batch(path, _batch(), apply=True, backup_dir=tmp_path / 'backups')
    assert _digest(path) == before


@pytest.mark.parametrize('mutation', [
    lambda batch: batch.reverse(),
    lambda batch: batch[1]['days'][0].update(label='Ter 5'),
    lambda batch: batch[1]['shifts'][0].update(loja='unknown'),
    lambda batch: batch[1]['shifts'][0].update(horario='25:00-26:00'),
    lambda batch: batch[1]['roster'][0].update(nome=''),
])
def test_invalid_batch_is_rejected_before_any_write(tmp_path, mutation):
    path = _database(tmp_path)
    batch = _batch()
    mutation(batch)
    before = _digest(path)
    with pytest.raises(ScheduleImportError):
        import_batch(path, batch, apply=True, backup_dir=tmp_path / 'backups')
    assert _digest(path) == before


def test_activation_requires_an_explicit_batch_week_and_preserves_other_settings(tmp_path):
    path = _database(tmp_path)
    with pytest.raises(ScheduleImportError, match='ativação'):
        import_batch(path, _batch(), activate_week='unknown')
    result = import_batch(path, _batch(), apply=True,
                          backup_dir=tmp_path / 'backups', activate_week='28-4out')
    assert result['activate_week'] == '28-4out'
    with sqlite3.connect(path) as connection:
        assert connection.execute('SELECT codigo FROM semanas WHERE ativa=1').fetchall() == [('28-4out',)]
    assert _rows(path, 'loja_headcount_targets') == [(1, 'gaia', 0, 6)]
