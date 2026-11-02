"""Plan or atomically import verified schedule JSON files without loading Flask.

The default is read-only. ``--apply`` takes an online SQLite backup (retained
without pruning), rechecks the whole batch under a write lock, then adds only
missing staff, weeks, shifts and four-hour deliveries in one transaction.
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from config import LOJAS, RANK_LEVELS, DAY_ABBR, MESES_CURTOS
from scripts.backup_db import backup


class ScheduleImportError(ValueError):
    """A batch cannot be applied safely to the current database."""


def _text(value, field, maximum=500):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ScheduleImportError(f'{field}: texto vazio ou inválido')
    return value.strip()


def _sigla(value):
    value = _text(value, 'sigla', 10).upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9]{0,9}', value):
        raise ScheduleImportError(f'Sigla inválida: {value}')
    return value


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
        raise ScheduleImportError(f'Hora inválida: {value!r}')
    hour, minute = map(int, value.split(':'))
    if hour > 23 or minute > 59:
        raise ScheduleImportError(f'Hora inválida: {value}')
    return hour * 60 + minute


def _bounds(value):
    value = _text(value, 'horario', 60)
    match = re.fullmatch(r'(\d{2}:\d{2})-(\d{2}:\d{2})(?: \(\+(\d+(?:[,.]\d+)?)\))?', value)
    if not match:
        raise ScheduleImportError(f'Janela de turno inválida: {value}')
    start, end = _clock(match[1]), _clock(match[2])
    if start == end or (match[3] and float(match[3].replace(',', '.')) > 24):
        raise ScheduleImportError(f'Janela de turno inválida: {value}')
    return start, end + (1440 if end < start else 0)


def _delivery_window(start):
    minutes = _clock(start)
    end = (minutes + 240) % 1440
    return f'{start}-{end // 60:02d}:{end % 60:02d}'


def _overlap(first, second):
    a, b = _bounds(first)
    c, d = _bounds(second)
    return a < d and c < b


def _list(payload, key):
    value = payload.get(key)
    if not isinstance(value, list):
        raise ScheduleImportError(f'{key}: é necessária uma lista')
    if any(not isinstance(row, dict) for row in value):
        raise ScheduleImportError(f'{key}: linha inválida')
    return value


def validate_batch(payloads):
    """Validate all source data before opening any database for writing."""
    if not isinstance(payloads, (list, tuple)) or not payloads:
        raise ScheduleImportError('É necessário pelo menos um ficheiro de escala')
    valid_stores = {store['id'] for store in LOJAS}
    weeks, roster, previous, codes = [], {}, None, set()
    for payload in payloads:
        if not isinstance(payload, dict):
            raise ScheduleImportError('Documento de escala inválido')
        source = _text(payload.get('source'), 'source', 2000)
        try:
            monday = date.fromisoformat(payload.get('week_start', ''))
        except (TypeError, ValueError):
            raise ScheduleImportError(f'{source}: week_start inválido') from None
        if monday.weekday() != 0 or (previous and monday < previous + timedelta(days=7)):
            raise ScheduleImportError('As semanas devem começar numa segunda-feira e estar em ordem cronológica')
        sunday = monday + timedelta(days=6)
        code = _text(payload.get('week_codigo'), 'week_codigo', 20)
        if code != f'{monday.day}-{sunday.day}{MESES_CURTOS[sunday.month - 1]}' or code in codes:
            raise ScheduleImportError(f'Código de semana inválido ou repetido: {code}')
        label = _text(payload.get('week_label'), 'week_label', 60)
        days = _list(payload, 'days')
        if len(days) != 7:
            raise ScheduleImportError(f'{code}: são necessários sete dias')
        clean_days = []
        for index, day in enumerate(days):
            expected = monday + timedelta(days=index)
            if day.get('date') != expected.isoformat() or day.get('label') != f'{DAY_ABBR[index]} {expected.day}':
                raise ScheduleImportError(f'{code}: data ou dia da semana incorreto')
            if not isinstance(day.get('feriado', False), bool) or not isinstance(day.get('feriado_nome', ''), str):
                raise ScheduleImportError(f'{code}: dados de feriado inválidos')
            clean_days.append({'label': day['label'], 'date': day['date'],
                               'feriado': day.get('feriado', False), 'feriado_nome': day.get('feriado_nome', '')})
        warnings = payload.get('warnings', [])
        if not isinstance(warnings, list) or warnings:
            raise ScheduleImportError(f'{code}: resolver os avisos do documento antes de importar')
        for person in _list(payload, 'roster'):
            sigla = _sigla(person.get('sigla'))
            name = _text(person.get('nome'), f'{sigla}: nome', 80)
            hours = person.get('horas_contrato')
            kind = person.get('tipo_contrato')
            if isinstance(hours, bool) or not isinstance(hours, int) or not 0 <= hours <= 168 or kind not in ('contrato', 'turnos'):
                raise ScheduleImportError(f'{sigla}: dados de contrato inválidos')
            rank = person.get('rank')
            roster.setdefault(sigla, []).append({'sigla': sigla, 'nome': name,
                'horas_contrato': hours, 'tipo_contrato': kind,
                'rank': rank if isinstance(rank, str) and rank in RANK_LEVELS else 'trainee'})
        shifts, deliveries, regular_seen, delivery_seen = [], [], {}, {}
        day_labels = {day['label'] for day in clean_days}
        for kind, rows in (('shift', _list(payload, 'shifts')), ('delivery', _list(payload, 'descargas'))):
            for row in rows:
                store, day, sigla = row.get('loja'), row.get('dia'), _sigla(row.get('funcionario'))
                if store not in valid_stores or day not in day_labels:
                    raise ScheduleImportError(f'{code}/{sigla}: loja ou dia inválido')
                horario = _text(row.get('horario'), 'horario', 60) if kind == 'shift' else _delivery_window(row.get('inicio'))
                _bounds(horario)
                assignment = {'semana': code, 'loja': store, 'dia': day, 'funcionario': sigla, 'horario': horario}
                seen = regular_seen if kind == 'shift' else delivery_seen
                key = (sigla, day)
                if key in seen and seen[key] != assignment:
                    raise ScheduleImportError(f'{code}/{day}/{sigla}: duas atribuições diferentes do mesmo tipo')
                if key not in seen:
                    seen[key] = assignment
                    (shifts if kind == 'shift' else deliveries).append(assignment)
        for key in regular_seen.keys() & delivery_seen.keys():
            if _overlap(regular_seen[key]['horario'], delivery_seen[key]['horario']):
                raise ScheduleImportError(f'{code}/{key[1]}/{key[0]}: turno sobreposto à descarga')
        delivery_config = {}
        for row in deliveries:
            cell = delivery_config.setdefault(row['dia'], {})
            if row['loja'] in cell and cell[row['loja']] != row['horario']:
                raise ScheduleImportError(f'{code}/{row["dia"]}/{row["loja"]}: janelas de descarga diferentes')
            cell[row['loja']] = row['horario']
        weeks.append({'source': source, 'week_start': monday.isoformat(), 'week_codigo': code,
                      'week_label': label, 'days': clean_days, 'shifts': shifts,
                      'descargas': deliveries, 'descarga_config': delivery_config})
        previous, codes = monday, codes | {code}
    return weeks, roster


def _connection(db_path, writable=False):
    path = Path(db_path).resolve(strict=True)
    connection = sqlite3.connect(path.as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def _plan(connection, weeks, roster, activate_week, contract_updates):
    required = {row['funcionario'] for week in weeks for kind in ('shifts', 'descargas') for row in week[kind]}
    existing_staff = {row['sigla'] for row in connection.execute('SELECT sigla FROM funcionarios')}
    new_staff = []
    for sigla in sorted(required - existing_staff):
        options = roster.get(sigla, [])
        if not options:
            raise ScheduleImportError(f'Sigla sem nome confirmado no quadro de pessoal: {sigla}')
        preferred = [row for row in options if row['tipo_contrato'] == 'contrato' and row['horas_contrato'] > 0] or options
        # A repeated on-call row never erases a positive contract/base row.
        if len({(row['nome'], row['horas_contrato'], row['tipo_contrato']) for row in preferred}) != 1:
            raise ScheduleImportError(f'{sigla}: dados de pessoal contraditórios entre os documentos')
        new_staff.append(preferred[0])
    if activate_week is not None and activate_week not in {week['week_codigo'] for week in weeks}:
        raise ScheduleImportError('A ativação explícita tem de identificar uma semana deste lote')
    counts = {'staff_added': len(new_staff), 'weeks_added': 0, 'shifts_added': 0,
              'shifts_skipped': 0, 'deliveries_added': 0, 'deliveries_skipped': 0,
              'configs_updated': 0, 'contracts_updated': 0}
    contracts = []
    for update in contract_updates:
        existing = connection.execute('SELECT horas_contrato,tipo_contrato FROM funcionarios WHERE sigla=?',
                                      (update['sigla'],)).fetchone()
        if not existing:
            raise ScheduleImportError(f'{update["sigla"]}: contrato sem colaborador existente')
        if (existing['horas_contrato'], existing['tipo_contrato']) != (update['horas_contrato'], update['tipo_contrato']):
            contracts.append({**update, 'previous_hours': existing['horas_contrato'],
                              'previous_type': existing['tipo_contrato']})
    counts['contracts_updated'] = len(contracts)
    actions = []
    for week in weeks:
        code = week['week_codigo']
        existing = connection.execute('SELECT * FROM semanas WHERE codigo=?', (code,)).fetchone()
        config = {}
        if existing:
            try:
                old_days = json.loads(existing['dias_json'])
                config = json.loads(existing['descarga_config'] or '{}')
            except (TypeError, ValueError):
                raise ScheduleImportError(f'{code}: configuração existente inválida') from None
            if [(day.get('date'), day.get('label')) for day in old_days] != [(day['date'], day['label']) for day in week['days']]:
                raise ScheduleImportError(f'{code}: o código existente corresponde a outras datas')
            if not isinstance(config, dict) or any(not isinstance(cell, dict) for cell in config.values()):
                raise ScheduleImportError(f'{code}: configuração de descargas existente inválida')
        else:
            counts['weeks_added'] += 1
        merged = {day: dict(cell) for day, cell in config.items()}
        for day, cell in week['descarga_config'].items():
            for store, horario in cell.items():
                previous = merged.setdefault(day, {}).get(store)
                if previous and previous != horario:
                    raise ScheduleImportError(f'{code}/{day}/{store}: a janela de descarga existente é diferente')
                merged[day][store] = horario
        if existing and merged != config:
            counts['configs_updated'] += 1
        regular = list(connection.execute('SELECT * FROM turnos WHERE semana=?', (code,)))
        deliveries = list(connection.execute('SELECT * FROM descargas WHERE semana=?', (code,)))
        additions = {'shifts': [], 'descargas': []}
        for kind, saved, incoming in (('shifts', regular, week['shifts']), ('descargas', deliveries, week['descargas'])):
            added_key, skipped_key = ('shifts_added', 'shifts_skipped') if kind == 'shifts' else ('deliveries_added', 'deliveries_skipped')
            for row in incoming:
                same_day = [old for old in saved if old['funcionario'] == row['funcionario'] and old['dia'] == row['dia']]
                different = [old for old in same_day if old['loja'] != row['loja'] or old['horario'] != row['horario']]
                if different:
                    raise ScheduleImportError(f'{code}/{row["dia"]}/{row["funcionario"]}: já existe uma atribuição diferente; rever manualmente')
                if same_day:
                    counts[skipped_key] += 1
                else:
                    additions[kind].append(row)
                    counts[added_key] += 1
                other = deliveries + week['descargas'] if kind == 'shifts' else regular + week['shifts']
                for old in other:
                    if old['funcionario'] == row['funcionario'] and old['dia'] == row['dia'] and _overlap(old['horario'], row['horario']):
                        raise ScheduleImportError(f'{code}/{row["dia"]}/{row["funcionario"]}: turno sobreposto à descarga existente ou importada')
        actions.append({'week': week, 'existing': bool(existing), 'config': merged,
                        'config_changed': merged != config, **additions})
    report = {'applied': False, 'sources': [week['source'] for week in weeks],
              'weeks': [{'codigo': week['week_codigo'], 'start': week['week_start'],
                         'shifts': len(week['shifts']), 'deliveries': len(week['descargas'])} for week in weeks],
              'new_staff': new_staff, 'counts': counts, 'activate_week': activate_week,
              'backup_path': None, 'contract_updates': contracts}
    return report, actions


def import_batch(db_path, payloads, *, apply=False, backup_dir=None, activate_week=None,
                 contract_updates=None):
    """Read-only plan by default; backup and apply the complete batch explicitly."""
    weeks, roster = validate_batch(payloads)
    clean_updates = []
    seen = set()
    for update in contract_updates or []:
        if not isinstance(update, dict):
            raise ScheduleImportError('Atualização de contrato inválida')
        sigla = _sigla(update.get('sigla'))
        hours, kind = update.get('horas_contrato'), update.get('tipo_contrato')
        if (sigla in seen or isinstance(hours, bool) or not isinstance(hours, int)
                or not 0 <= hours <= 80 or kind not in ('contrato', 'turnos')):
            raise ScheduleImportError(f'{sigla}: atualização de contrato inválida ou repetida')
        clean_updates.append({'sigla': sigla, 'horas_contrato': hours, 'tipo_contrato': kind})
        seen.add(sigla)
    connection = _connection(db_path)
    try:
        connection.execute('BEGIN')
        report, _ = _plan(connection, weeks, roster, activate_week, clean_updates)
    finally:
        connection.close()
    if not apply:
        return report
    # A unique directory also prevents the backup helper's second-resolution
    # filenames from overwriting a previous import snapshot.
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out_dir = Path(backup_dir or ROOT / 'backups') / ('schedule-import-' + stamp)
    saved_backup = backup(str(Path(db_path).resolve(strict=True)), str(out_dir), keep=0)
    connection = _connection(db_path, writable=True)
    try:
        connection.execute('BEGIN IMMEDIATE')
        # Recheck after backup while holding the write lock: concurrent manual
        # edits must never turn a previously valid preview into an overwrite.
        report, actions = _plan(connection, weeks, roster, activate_week, clean_updates)
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(' ')
        for person in report['new_staff']:
            connection.execute('INSERT INTO funcionarios (sigla,nome,horas_contrato,tipo_contrato,pin,rank,ical_token) VALUES (?,?,?,?,?,?,?)',
                (person['sigla'], person['nome'], person['horas_contrato'], person['tipo_contrato'], '', person['rank'], ''))
        for update in report['contract_updates']:
            connection.execute('UPDATE funcionarios SET horas_contrato=?,tipo_contrato=? WHERE sigla=?',
                (update['horas_contrato'], update['tipo_contrato'], update['sigla']))
        for action in actions:
            week, code = action['week'], action['week']['week_codigo']
            config_json = json.dumps(action['config'], ensure_ascii=False)
            if not action['existing']:
                connection.execute('INSERT INTO semanas (codigo,label,dias_json,ativa,descarga_dias,descarga_lojas,descarga_config,criada_em) VALUES (?,?,?,?,?,?,?,?)',
                    (code, week['week_label'], json.dumps(week['days'], ensure_ascii=False), 0,
                     ','.join(day['label'] for day in week['days'] if day['label'] in action['config']), '', config_json, now))
            elif action['config_changed']:
                connection.execute('UPDATE semanas SET descarga_config=? WHERE codigo=?', (config_json, code))
            for row in action['shifts']:
                connection.execute('INSERT INTO turnos (semana,loja,dia,funcionario,horario,nota,updated_at) VALUES (?,?,?,?,?,?,?)',
                    (code, row['loja'], row['dia'], row['funcionario'], row['horario'], '', now))
            for row in action['descargas']:
                connection.execute('INSERT INTO descargas (semana,loja,dia,funcionario,horario,criada_em) VALUES (?,?,?,?,?,?)',
                    (code, row['loja'], row['dia'], row['funcionario'], row['horario'], now))
        if activate_week is not None:
            connection.execute('UPDATE semanas SET ativa=CASE WHEN codigo=? THEN 1 ELSE 0 END', (activate_week,))
        audit_detail = json.dumps({'sources': report['sources'], 'counts': report['counts'],
                                   'activate_week': activate_week,
                                   'contract_updates': report['contract_updates']}, ensure_ascii=False)
        connection.execute('INSERT INTO audit_log (ts,actor,ip,action,target,detail) VALUES (?,?,?,?,?,?)',
            (now, 'ADMIN', 'local-import', 'schedule_batch_import', ','.join(week['week_codigo'] for week in weeks), audit_detail))
        connection.commit()
        report.update(applied=True, backup_path=saved_backup)
        return report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('json_files', nargs='+', help='verified JSON documents, oldest first')
    parser.add_argument('--db', default=os.environ.get('AMORINO_DB_PATH') or str(ROOT / 'amorino.db'))
    parser.add_argument('--apply', action='store_true', help='back up and apply the whole batch')
    parser.add_argument('--backup-dir', default=str(ROOT / 'backups'))
    parser.add_argument('--activate-week', help='explicitly make this imported week active')
    args = parser.parse_args()
    try:
        payloads = [json.loads(Path(path).read_text(encoding='utf-8')) for path in args.json_files]
        report = import_batch(args.db, payloads, apply=args.apply,
                              backup_dir=args.backup_dir, activate_week=args.activate_week)
    except (ScheduleImportError, OSError, sqlite3.Error, json.JSONDecodeError) as error:
        parser.exit(1, f'Importação não aplicada: {error}\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
