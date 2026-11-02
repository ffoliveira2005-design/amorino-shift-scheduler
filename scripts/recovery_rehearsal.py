"""End-to-end local recovery drill with freshly generated fictional data only.

Run in a fresh process. No production database is copied, opened or imported.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

try:
    from .backup_db import backup
    from .ops_common import ROOT, atomic_json, readonly, validate_database
    from .restore_db import restore
    from .verify_backup import verify
except ImportError:
    from backup_db import backup
    from ops_common import ROOT, atomic_json, readonly, validate_database
    from restore_db import restore
    from verify_backup import verify

SUPERVISOR_PIN = 'recovery-supervisor-5827'
EMPLOYEE_PIN = 'recovery-employee-5827'


def _environment(database):
    env = os.environ.copy()
    env.update(AMORINO_DB_PATH=str(database), AMORINO_SECRET_KEY=secrets.token_hex(32),
               AMORINO_SUPERVISOR_PIN=SUPERVISOR_PIN, AMORINO_HTTPS='0',
               AMORINO_TRUST_PROXY='0', AMORINO_DEBUG='0', AMORINO_DEMO='0',
               AMORINO_SEED='0',
               TZ='Europe/Lisbon', AMORINO_PROCESSES='1', AMORINO_STORAGE='local')
    return env


def _app(database):
    if Path(os.environ.get('AMORINO_DB_PATH', '')).resolve() != database.resolve():
        raise ValueError('Explicit scratch AMORINO_DB_PATH must match the phase database.')
    if database.resolve() == ROOT / 'amorino.db':
        raise ValueError('Recovery rehearsal refuses the repository production database.')
    sys.path.insert(0, str(ROOT))
    module = importlib.import_module('app')
    module.app.config.update(TESTING=True, AMORINO_DEMO=False)
    with module.app.app_context():
        if Path(module.db.engine.url.database).resolve() != database.resolve():
            raise ValueError('Application opened a different database.')
    return module


def _login(module, sigla, pin):
    client = module.app.test_client()
    response = client.post('/login', data={'sigla': sigla, 'pin': pin})
    if response.status_code != 302:
        raise AssertionError(f'Login failed for fictional account {sigla}')
    client.get('/')  # creates the per-session CSRF value through template context
    with client.session_transaction() as session:
        if session.get('user_sigla') != sigla:
            raise AssertionError('Login did not authenticate the expected fictional account.')
    return client


def _post(client, path, data):
    with client.session_transaction() as session:
        token = session.get('_csrf', '')
    response = client.post(path, json={**data, 'csrf_token': token})
    body = response.get_json(silent=True)
    if response.status_code != 200 or not body or not body.get('ok'):
        raise AssertionError(f'Workflow failed at {path}: {response.status_code} {body}')
    return body


def _pdf_fingerprint(content):
    if not content.startswith(b'%PDF-') or b'%%EOF' not in content[-64:] or b'/Type /Page' not in content:
        raise AssertionError('PDF export is incomplete.')
    # ReportLab adds volatile creation timestamps and trailer IDs. Preserve
    # every other byte, including the actual compressed page content.
    stable = re.sub(rb'/(?:CreationDate|ModDate)\s*\([^)]*\)', b'/Date()', content)
    stable = re.sub(rb'/ID\s*\[\s*<[^>]*>\s*<[^>]*>\s*\]', b'/ID[]', stable)
    return hashlib.sha256(stable).hexdigest()


def _exports(module, code):
    supervisor = _login(module, 'ADMIN', SUPERVISOR_PIN)
    employee = _login(module, 'RHA', EMPLOYEE_PIN)
    calendar = employee.get('/api/cal-link').get_json()
    assert calendar and calendar.get('url')
    from urllib.parse import urlsplit
    feed = employee.get(urlsplit(calendar['url']).path)
    assert feed.status_code == 200 and b'BEGIN:VCALENDAR' in feed.data
    for client, paths in ((supervisor, ('/equipa', '/supervisor', '/escala', '/trocas')),
                          (employee, ('/minha-escala', '/escala', '/trocas', '/disponibilidade'))):
        for path in paths:
            response = client.get(path, query_string={'semana': code})
            if response.status_code != 200:
                raise AssertionError(f'Restored application page failed: {path}')
    health = module.app.test_client().get('/healthz')
    assert health.status_code == 200 and health.get_json() == {'ok': True}
    pdf = supervisor.get('/supervisor/exportar-pdf', query_string={'semana': code})
    personal = employee.get('/supervisor/exportar-pdf-individual/RHA', query_string={'semana': code})
    hours = supervisor.get('/supervisor/exportar-horas-csv', query_string={'semana': code})
    assert pdf.status_code == personal.status_code == hours.status_code == 200
    rows = list(csv.DictReader(io.StringIO(hours.data.decode('utf-8-sig')), delimiter=';'))
    staff_rows = {row['Sigla']: row for row in rows if row['Sigla']}
    assert set(staff_rows) == {'RHA', 'RHB', 'RHC'}
    assert staff_rows['RHC']['Horas descargas'] == '4,00'
    assert staff_rows['RHC']['Descargas'] == '1'
    assert len(rows) == 4 and rows[-1]['Nome'] == 'TOTAL'
    for sigla, row in staff_rows.items():
        actual = float(row['Total horas'].replace(',', '.'))
        assert actual == float(row['Horas turnos'].replace(',', '.')) + float(row['Horas descargas'].replace(',', '.'))
    return {'csv_sha256': hashlib.sha256(hours.data).hexdigest(),
            'pdf_content_sha256': _pdf_fingerprint(pdf.data),
            'personal_pdf_content_sha256': _pdf_fingerprint(personal.data),
            'csv_rows_including_total': len(rows), 'delivery_credit_hours': 4,
            'pages_checked': 8, 'healthz': True, 'recreated_calendar_feed_checked': True}


def _phase_prepare(database, state_path):
    module = _app(database)
    from helpers.time_helpers import week_codigo, week_from_monday, week_label
    today = datetime.now(timezone.utc).date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
    days = week_from_monday(monday)
    code = week_codigo(monday)
    with module.app.app_context():
        # Only the just-created scratch database is replaced, never a copied DB.
        for table in reversed(module.db.metadata.sorted_tables):
            module.db.session.execute(table.delete())
        module.db.session.commit()
        pin_hash = module.hash_pin(EMPLOYEE_PIN)
        for sigla in ('RHA', 'RHB', 'RHC'):
            module.db.session.add(module.Funcionario(sigla=sigla, nome=f'Pessoa fictícia {sigla}',
                pin=pin_hash, horas_contrato=40, tipo_contrato='contrato', rank='shift_supervisor',
                ical_token=secrets.token_urlsafe(32)))
            for day in days:
                module.db.session.add(module.Disponibilidade(semana=code, funcionario=sigla,
                    dia=day['label'], tipo='disponivel', revisto=True))
        module.db.session.add(module.SemanaConfig(codigo=code, label=week_label(monday),
            dias_json=json.dumps(days), ativa=True, descarga_config=json.dumps({days[3]['label']: {'gaia': '08:00-10:00'}})))
        shifts = [module.Turno(semana=code, loja='carmelitas', dia=days[0]['label'],
                              horario='08:00-16:30', funcionario='RHA'),
                  module.Turno(semana=code, loja='gaia', dia=days[0]['label'],
                              horario='16:00-00:30', funcionario='RHB'),
                  module.Turno(semana=code, loja='gaia', dia=days[2]['label'],
                              horario='16:00-01:30 (+1)', funcionario='RHC')]
        module.db.session.add_all(shifts)
        module.db.session.add(module.Descarga(semana=code, loja='gaia', dia=days[3]['label'],
                                            horario='08:00-10:00', funcionario='RHC'))
        module.db.session.commit()
        shift_ids = [shift.id for shift in shifts]
    supervisor = _login(module, 'ADMIN', SUPERVISOR_PIN)
    review = supervisor.get(f'/api/publicacao/{code}').get_json()
    _post(supervisor, f'/api/publicacao/{code}', {'confirmar': True, 'versao_esperada': 0,
          'rascunho_hash': review['rascunho_hash'], 'motivo': 'Ensaio fictício: publicação inicial'})
    employee_a, employee_b = _login(module, 'RHA', EMPLOYEE_PIN), _login(module, 'RHB', EMPLOYEE_PIN)
    swap = _post(employee_a, '/api/trocas', {'turno_origem_id': shift_ids[0],
        'turno_destino_id': shift_ids[1], 'para_sigla': 'RHB', 'motivo': 'Ensaio fictício'})['troca']
    _post(employee_b, f'/api/trocas/{swap["id"]}/responder', {'aceitar': True})
    _post(supervisor, f'/api/trocas/{swap["id"]}/decidir', {'aprovar': True, 'nota': 'Ensaio fictício'})
    with module.app.app_context():
        assert module.db.session.get(module.TrocaPedido, swap['id']).estado == 'aprovado'
        assert module.db.session.get(module.Turno, shift_ids[0]).funcionario == 'RHB'
        assert module.db.session.get(module.Turno, shift_ids[1]).funcionario == 'RHA'
        assert module.Funcionario.query.count() == 3
        assert module.Disponibilidade.query.count() == 21
        expected_version = module.ScheduleRelease.query.filter_by(semana=code).count()
        assert expected_version == 2, 'Approved published swap must create a recorded amendment.'
    state = {'week': code, 'shift_ids': shift_ids, 'swap_id': swap['id'],
             'exports': _exports(module, code), 'expected_release_count': expected_version}
    atomic_json(state_path, state)
    with module.app.app_context():
        module.db.session.remove()
        module.db.engine.dispose()


def _phase_verify(database, state_path):
    state = json.loads(state_path.read_text(encoding='utf-8'))
    module = _app(database)
    with module.app.app_context():
        assert module.Funcionario.query.count() == 3
        assert all(person.nome == f'Pessoa fictícia {person.sigla}' for person in module.Funcionario.query.all())
        assert module.Turno.query.count() == 3
        assert module.Disponibilidade.query.count() == 21
        assert module.Descarga.query.count() == 1
        assert module.db.session.get(module.TrocaPedido, state['swap_id']).estado == 'aprovado'
        assert module.db.session.get(module.Turno, state['shift_ids'][0]).funcionario == 'RHB'
        assert module.db.session.get(module.Turno, state['shift_ids'][1]).funcionario == 'RHA'
        assert module.ScheduleRelease.query.filter_by(semana=state['week']).count() == state['expected_release_count']
        assert all(not person.enrollment_hash and not person.ical_token for person in module.Funcionario.query.all())
    actual = _exports(module, state['week'])
    if actual != state['exports']:
        raise AssertionError('Restored exports differ from the verified pre-backup outputs.')
    atomic_json(state_path.with_name('verification.json'), {'ok': True, 'checks': actual})
    with module.app.app_context():
        module.db.session.remove()
        module.db.engine.dispose()


def run(work_directory=None, *, report_path=None):
    work = Path(work_directory or tempfile.mkdtemp(prefix='amorino-recovery-')).absolute()
    if work.resolve() != work or work.resolve() == ROOT or work.resolve() == ROOT / 'amorino.db':
        raise ValueError('Rehearsal requires a dedicated non-redirected scratch directory.')
    if work.exists() and any(work.iterdir()):
        raise ValueError('Rehearsal work directory must be empty; choose a new scratch directory.')
    work.mkdir(parents=True, exist_ok=True)
    source, state = work / 'fictional.db', work / 'state.json'
    start = time.perf_counter()
    env = _environment(source)
    os.environ['AMORINO_DB_PATH'] = str(source)
    command = [sys.executable, str(Path(__file__).resolve())]
    def phase(name):
        result = subprocess.run(command + ['--phase', name, '--db', str(source), '--state', str(state)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f'Rehearsal {name} failed:\n{result.stdout}\n{result.stderr}')
    phase('prepare')
    snapshot = Path(backup(str(source), str(work / 'backups'), keep=14))
    # A second local directory demonstrates transfer verification only; it is
    # deliberately NOT recorded as an off-server backup.
    receiver = work / 'simulated-receiver'
    receiver.mkdir()
    shutil.copy2(snapshot, receiver / snapshot.name)
    shutil.copy2(Path(str(snapshot) + '.json'), receiver / (snapshot.name + '.json'))
    verify(receiver / snapshot.name)
    connection = sqlite3.connect(source)
    try:
        connection.execute("UPDATE funcionarios SET nome=? WHERE sigla=?", ('Alteração fictícia posterior à cópia', 'RHA'))
        connection.commit()
    finally:
        connection.close()
    recovery_start = time.perf_counter()
    # Simulate damage after writers have exited. Exact sidecar files are kept
    # by the restore, even when their contents cannot be read as SQLite.
    source.write_bytes(b'Fictional damaged database for recovery drill')
    Path(str(source) + '-wal').write_bytes(b'fictional damaged WAL')
    Path(str(source) + '-shm').write_bytes(b'fictional damaged SHM')
    recovered = restore(receiver / snapshot.name, source, service_stopped=True,
                        confirmed_target=str(source), allow_repository_target=True)
    phase('verify')
    recovery_seconds = round(time.perf_counter() - recovery_start, 3)
    result = {'ok': True, 'recorded_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'isolated fictional local rehearsal; no production or off-server deployment verified',
        'work_directory': str(work), 'total_rehearsal_seconds': round(time.perf_counter() - start, 3),
        'restore_and_application_validation_seconds': recovery_seconds,
        'snapshot_started_utc': json.loads(Path(str(snapshot) + '.json').read_text())['snapshot_started_utc'],
        'post_snapshot_fictional_changes_lost': 1,
        'daily_schedule_possible_missing_window_hours': 25,
        'missing_window_note': 'Daily 03:00 Lisbon backups: up to 25 hours around autumn DST, plus job duration; older after failures. Actual RPO awaits approved monitoring/frequency.',
        'damaged_files_preserved': sorted(path.name for path in Path(recovered['preserved']).iterdir()),
        'counts': validate_database(source),
        'checks': json.loads((work / 'verification.json').read_text())['checks'],
        'production_https_checked': False, 'production_restart_checked': False,
        'restored_invitations_and_calendar_secrets_revoked': True,
        'off_server_copy_verified': False}
    atomic_json(report_path or work / 'rehearsal-result.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir')
    parser.add_argument('--report')
    parser.add_argument('--phase', choices=('prepare', 'verify'), help=argparse.SUPPRESS)
    parser.add_argument('--db', help=argparse.SUPPRESS)
    parser.add_argument('--state', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.phase:
        function = _phase_prepare if args.phase == 'prepare' else _phase_verify
        function(Path(args.db), Path(args.state))
        return 0
    result = run(args.work_dir, report_path=args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
