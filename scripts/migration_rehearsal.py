"""Record launch migration acceptance using a freshly created fictional legacy DB."""
import argparse
from datetime import date, timedelta, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

try:
    from .migrate_launch import run
    from .ops_common import ROOT, atomic_json, readonly
except ImportError:
    from migrate_launch import run
    from ops_common import ROOT, atomic_json, readonly

sys.path.insert(0, str(ROOT))
FICTIONAL_PIN = 'Fictional-migration-7483'


def create_legacy_fixture(database):
    """Six real legacy app tables, with no publication/invitation tables or fields."""
    from helpers.auth import hash_pin
    database = Path(database).absolute()
    if database.resolve() != database or database == ROOT / 'amorino.db' or database.exists():
        raise ValueError('Fictional fixture requires a new non-redirected scratch database.')
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript('''
        CREATE TABLE funcionarios (sigla TEXT PRIMARY KEY, nome TEXT NOT NULL,
          horas_contrato INTEGER, pin TEXT, tipo_contrato TEXT, rank TEXT,
          loja_gerida TEXT DEFAULT '', ical_token TEXT DEFAULT '');
        CREATE TABLE semanas (id INTEGER PRIMARY KEY, codigo TEXT UNIQUE NOT NULL,
          label TEXT NOT NULL, dias_json TEXT NOT NULL, ativa BOOLEAN,
          descarga_dias TEXT DEFAULT '', descarga_lojas TEXT DEFAULT '',
          descarga_config TEXT DEFAULT '', criada_em DATETIME);
        CREATE TABLE turnos (id INTEGER PRIMARY KEY, loja TEXT NOT NULL,
          horario TEXT NOT NULL, dia TEXT NOT NULL, funcionario TEXT NOT NULL,
          nota TEXT DEFAULT '', semana TEXT, updated_at DATETIME);
        CREATE TABLE descargas (id INTEGER PRIMARY KEY, semana TEXT NOT NULL,
          dia TEXT NOT NULL, loja TEXT NOT NULL, funcionario TEXT NOT NULL,
          horario TEXT NOT NULL, criada_em DATETIME);
        CREATE TABLE disponibilidades (id INTEGER PRIMARY KEY, semana TEXT NOT NULL,
          funcionario TEXT NOT NULL, dia TEXT NOT NULL, tipo TEXT NOT NULL,
          texto TEXT DEFAULT '', lojas_pref TEXT DEFAULT '', slots_pref TEXT DEFAULT '',
          descarga_opt BOOLEAN DEFAULT 0, submetido_em DATETIME, revisto BOOLEAN DEFAULT 0);
        CREATE TABLE trocas (id INTEGER PRIMARY KEY, criado_em DATETIME,
          atualizado_em DATETIME, de_sigla TEXT NOT NULL, para_sigla TEXT NOT NULL,
          turno_origem_id INTEGER NOT NULL, turno_destino_id INTEGER, motivo TEXT,
          estado TEXT NOT NULL, resposta_colega TEXT, resposta_supervisor TEXT,
          decisao_em DATETIME, tipo TEXT NOT NULL DEFAULT 'direto');
        ''')
        connection.executemany('INSERT INTO funcionarios VALUES (?, ?, ?, ?, ?, ?, ?, ?)', [
            ('MIGA', 'Pessoa fictícia com acesso', 24, hash_pin(FICTIONAL_PIN), 'contrato', 'staff', '', ''),
            ('MIGB', 'Pessoa fictícia por inscrever', 24, '', 'contrato', 'staff', '', '')])
        monday = date.today() - timedelta(days=date.today().weekday())
        for index, (code, offset) in enumerate((('LEGACY-PAST', -2), ('LEGACY-CURRENT', 0), ('LEGACY-FUTURE', 2)), 1):
            start = monday + timedelta(weeks=offset)
            days = [{'label': f'{abbr} {(start + timedelta(days=i)).day}',
                     'date': (start + timedelta(days=i)).isoformat(),
                     'feriado': False, 'feriado_nome': ''}
                    for i, abbr in enumerate(('Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab', 'Dom'))]
            delivery = {days[1]['label']: {'gaia': '08:00-10:00'}}
            connection.execute('INSERT INTO semanas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (index, code, f'Semana fictícia {index}', json.dumps(days), index == 3,
                 days[1]['label'], 'gaia', json.dumps(delivery), '2026-01-01 00:00:00'))
            connection.execute('INSERT INTO turnos VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (index, 'gaia', '16:00-00:30 (+1)', days[0]['label'], 'MIGA',
                 'Nota fictícia preservada', code, '2026-01-01 00:00:00'))
            connection.execute('INSERT INTO descargas VALUES (?, ?, ?, ?, ?, ?, ?)',
                (index, code, days[1]['label'], 'gaia', 'MIGA', '08:00-10:00', '2026-01-01 00:00:00'))
            for sigla in ('MIGA', 'MIGB'):
                for day in days:
                    connection.execute('INSERT INTO disponibilidades (semana,funcionario,dia,tipo,texto,lojas_pref,slots_pref,descarga_opt,submetido_em,revisto) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                        (code, sigla, day['label'], 'disponivel', 'Preferência fictícia',
                         'gaia', 'noite', day == days[1], '2026-01-01 00:00:00', True))
        connection.commit()
    finally:
        connection.close()


def rehearse(work_directory=None, report_path=None):
    from helpers.auth import verify_pin
    work = Path(work_directory or tempfile.mkdtemp(prefix='amorino-legacy-rehearsal-')).absolute()
    if work.resolve() != work or work == ROOT:
        raise ValueError('Use a dedicated non-redirected scratch directory.')
    if work.exists() and any(work.iterdir()):
        raise ValueError('Fictional migration work directory must be empty.')
    output = Path(report_path or work / 'fictional-migration-result.json').absolute()
    if output.resolve() != output or output.suffix.lower() != '.json':
        raise ValueError('Use a non-redirected .json path for the rehearsal report.')
    work.mkdir(parents=True, exist_ok=True)
    source = work / 'fictional-legacy.db'
    create_legacy_fixture(source)
    os.environ['AMORINO_DB_PATH'] = str(source)
    result = run(source, work_directory=work / 'migration')
    target = Path(result['migration_database'])
    with readonly(source) as conn:
        assert 'access_version' not in {row[1] for row in conn.execute('PRAGMA table_info(funcionarios)')}
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='schedule_releases'").fetchone()[0] == 0
    with readonly(target) as conn:
        assert verify_pin(conn.execute("SELECT pin FROM funcionarios WHERE sigla='MIGA'").fetchone()[0], FICTIONAL_PIN)
        assert conn.execute("SELECT pin,enrollment_hash,access_version FROM funcionarios WHERE sigla='MIGB'").fetchone() == ('', '', 0)
        assert conn.execute("SELECT COUNT(*) FROM published_turnos").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM published_descargas").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM published_semanas WHERE codigo='LEGACY-FUTURE'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM disponibilidades").fetchone()[0] == 42
        assert conn.execute("SELECT codigo FROM semanas WHERE ativa=1").fetchone()[0] == 'LEGACY-FUTURE'
    result.update(scope='fictional local legacy migration rehearsal; production untouched',
                  original_fictional_source_unmodified=True,
                  existing_secure_pin_verified=True, unclaimed_account_stays_locked=True,
                  published_shift_count=2, published_delivery_count=2,
                  future_remains_draft=True, production_deployment_verified=False,
                  recorded_utc=datetime.now(timezone.utc).isoformat())
    atomic_json(output, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir')
    parser.add_argument('--report')
    args = parser.parse_args()
    result = rehearse(args.work_dir, args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
