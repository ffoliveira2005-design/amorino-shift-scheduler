#!/usr/bin/env python3
"""
import_history.py — Import past Amorino schedule PDFs into the database.

Usage:
    python import_history.py <pdf1> [<pdf2> ...] [--db PATH] [--dry-run]

Each PDF is parsed for the 5 loja tables on page 1. For every (loja, horario,
day, sigla) tuple we create or skip a Turno row in the database. The week's
SemanaConfig is created if it doesn't already exist.

The script is idempotent: running it twice on the same PDF will not duplicate
data (it checks (funcionario, loja, horario, dia, semana) uniqueness).

It is conservative: turnos for siglas not in the current Funcionario table are
skipped and reported, never inserted with a dangling reference.

Run with --dry-run to preview without writing anything.
"""

import argparse
import json
import re
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

# We must import the app module to reuse models + db connection.
# This script lives in scripts/, so app.py is one directory up (the repo root).
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


# Lojas in the order they appear on page 1 of every PDF
LOJA_HEADER_MAP = {
    'Amorino_Carmelitas':           'carmelitas',
    'Amorino_Clérigos':             'clerigos',
    'Amorino_Norte Shopping':       'norte',
    'Amorino_Gaia':                 'gaia',
    'Amorino_Mercado Bom Sucesso':  'mbs',
}

# Day labels in Portuguese (short form used in the system)
DAY_ABBR = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sab', 'Dom']
MESES_PT = {
    'janeiro': 1, 'fevereiro': 2, 'março': 3, 'marco': 3, 'abril': 4,
    'maio': 5, 'junho': 6, 'julho': 7, 'agosto': 8, 'setembro': 9,
    'outubro': 10, 'novembro': 11, 'dezembro': 12,
}


def parse_filename_year(filename: str) -> int:
    """Extract year from filename like 'Amorino__Horários_2026_-...pdf'.

    Some upload pipelines prepend a timestamp like '1779478957005_' to the
    filename. That looks like a 4-digit number to a naive regex (1779), so we
    only accept years in the plausible range 2000..2099 and prefer the first
    such match. Falls back to current year if none found.
    """
    for m in re.finditer(r'(\d{4})', filename):
        y = int(m.group(1))
        if 2000 <= y < 2100:
            return y
    return date.today().year


def normalise_horario(label: str) -> str:
    """Convert PDF clock bands, including underscore/slash separators, into the
    canonical app format '08:00-16:30' or '08:00-17:00 (+0,5)'.
    Returns '' for non-time labels like 'Provas Praticas'."""
    if not label:
        return ''
    label = label.strip()
    # Skip non-time rows
    if 'Provas' in label or 'Hórarios' in label or label.startswith('Amorino_'):
        return ''
    # Multi-line: time first, modifier (+X) second
    lines = [l.strip() for l in label.split('\n') if l.strip()]
    if not lines:
        return ''
    head = lines[0]
    m = re.match(r'(\d{1,2}):(\d{2})\s*(?:_|/|-)\s*(\d{1,2}):(\d{2})', head)
    if not m:
        return ''
    h1, m1, h2, m2 = m.groups()
    canonical = f'{int(h1):02d}:{m1}-{int(h2):02d}:{m2}'
    # Append the (+X) modifier if present on later lines
    for extra in lines[1:]:
        if extra.startswith('('):
            canonical += ' ' + extra
            break
    return canonical


def _clock_range(label: str) -> str:
    """A cell's explicit clock range, including short forms such as 15-17."""
    m = re.fullmatch(r'(\d{1,2})(?::(\d{2}))?\s*(?:-|_|/)\s*'
                     r'(\d{1,2})(?::(\d{2}))?', label)
    if not m:
        return ''
    h1, m1, h2, m2 = (int(v or 0) for v in m.groups())
    if not (0 <= h1 <= 23 and 0 <= h2 <= 23 and 0 <= m1 <= 59 and 0 <= m2 <= 59):
        return ''
    return f'{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}'


def _cell_assignments(cell: str, inherited_horario: str):
    """Bind a written time override to its employee, rather than the full band."""
    tokens = re.split(r'\s+', cell.strip())
    assignments, unusual = [], []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if re.fullmatch(r'[A-Z]{1,4}', token):
            horario = inherited_horario
            consumed = 0
            for size in (1, 3):
                if i + size < len(tokens):
                    override = _clock_range(' '.join(tokens[i + 1:i + 1 + size]))
                    if override:
                        horario = override
                        consumed = size
                        break
            assignments.append((token, horario))
            i += 1 + consumed
        else:
            if token and not re.fullmatch(r'[\*\d:,]+', token):
                unusual.append(token)
            i += 1
    return assignments, unusual


def _plain(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text)
                   if not unicodedata.combining(c)).casefold()


def parse_descargas(text: str, day_dates: list):
    """Read delivery footnotes, retaining stated starts without inventing ends."""
    weekday_names = ['segunda-feira', 'terca-feira', 'quarta-feira', 'quinta-feira',
                     'sexta-feira', 'sabado', 'domingo']
    stores = {'carmelitas': 'carmelitas', 'clerigos': 'clerigos',
              'norteshopping': 'norte', 'gaia': 'gaia',
              'mercadobomsucesso': 'mbs'}
    records, warnings = [], []
    for line in text.splitlines():
        match = re.search(r'\bTSM\s*-\s*(.*?)\s*-\s*(\d{1,2}):(\d{2})\s*-\s*'
                          r'(.*?)\s*-\s*(.*?)\s*-\s*'
                          r'([A-Z]{1,4}(?:\s*,\s*[A-Z]{1,4})*)\s*$', line)
        if not match:
            if re.search(r'\bTSM\s*-', line):
                warnings.append(f'  unparsed descarga footnote: {line.strip()}')
            continue
        weekday, hour, minute, store, description, siglas = match.groups()
        day_index = next((i for i, name in enumerate(weekday_names)
                          if _plain(weekday).strip() == name), None)
        loja = stores.get(re.sub(r'\s+', '', _plain(store)))
        if (day_index is None or not loja or len(day_dates) <= day_index
                or not day_dates[day_index][0] or int(hour) > 23 or int(minute) > 59):
            warnings.append(f'  invalid descarga footnote: {line.strip()}')
            continue
        for sigla in re.split(r'\s*,\s*', siglas):
            records.append({'loja': loja, 'dia': day_dates[day_index][0],
                            'funcionario': sigla, 'inicio': f'{int(hour):02d}:{int(minute):02d}',
                            'descricao': description.strip()})
    return records, warnings


def parse_pdf(pdf_path: Path, year_hint: int = None):
    """Parse one PDF; returns dict with week_start (date), turnos (list of
    (loja, horario, dia_label, sigla) tuples), descargas (delivery workers and
    stated start times; no inferred end), and warnings (list of strings).
    """
    import pdfplumber

    year = year_hint or parse_filename_year(pdf_path.name)
    out_turnos = []
    warnings = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        tables = page.extract_tables()
        page_text = page.extract_text() or ''
        if not tables:
            return {'week_start': None, 'turnos': [], 'descargas': [], 'day_dates': [],
                    'warnings': ['No tables on page 1']}
        table = tables[0]

    # ── State machine: walk rows, track which loja we're in ─────────────────
    current_loja = None
    current_horario = ''   # Inherited horario for multi-row bands (e.g. 16:00_00:30
                            # is rendered as 2 rows: first row has the band label,
                            # second row has more siglas under the same band).
    day_dates = []   # list of (DAY_ABBR_label, isoformat) extracted from header row

    for row in table:
        first = (row[0] or '').strip() if row[0] else ''

        # Loja header row?
        loja_headers = [line.strip() for line in first.splitlines()
                        if line.strip() in LOJA_HEADER_MAP]
        if loja_headers:
            current_loja = LOJA_HEADER_MAP[loja_headers[-1]]
            current_horario = ''   # reset on loja change
            continue
        if 'Amorino_' in first:
            current_loja = None
            current_horario = ''
            warnings.append(f'  unknown loja header "{first}"')
            continue

        # Day header row? It starts with 'Hórarios /Dias' and the 7 columns
        # have day names like 'Segunda-feira\n04/05'.
        if first.startswith('Hórarios'):
            current_horario = ''
            day_dates = []
            for i in range(1, 8):
                cell = (row[i] or '').strip() if i < len(row) else ''
                # Extract DD/MM pattern
                m = re.search(r'(\d{1,2})/(\d{1,2})', cell)
                if m:
                    dd, mm = int(m.group(1)), int(m.group(2))
                    try:
                        the_date = date(year, mm, dd)
                    except ValueError:
                        the_date = None
                    abbr = DAY_ABBR[i - 1]
                    day_dates.append((f'{abbr} {dd}', the_date.isoformat() if the_date else ''))
                else:
                    day_dates.append(('', ''))
            continue

        # Legend / footer rows ('* Troca de Loja', 'Legenda:', etc.) — reset horario
        if first.startswith('*') or first.startswith('Legenda') or first.startswith('Descargas'):
            current_horario = ''
            continue

        # Regular shift row — need a current_loja and known day_dates
        if not current_loja or not day_dates or len(row) < 8:
            continue

        # Determine horario: either this row defines it, or inherit from previous row
        if first:
            horario = normalise_horario(first)
            if not horario:
                # Non-time row like 'Provas Praticas' or unparseable — reset and skip
                current_horario = ''
                continue
            current_horario = horario
        else:
            # Empty first column → this row's siglas belong to the previous band
            horario = current_horario
            if not horario:
                continue   # no band context — skip

        for i in range(7):
            cell = (row[i + 1] or '').strip() if (i + 1) < len(row) else ''
            if not cell:
                continue
            # Split on whitespace — a single cell may contain 2+ siglas
            # (e.g. '16:00_00:30' rows commonly have 2 names per day).
            dia_label = day_dates[i][0]
            if not dia_label:
                continue
            assignments, unusual = _cell_assignments(cell, horario)
            for token in unusual:
                warnings.append(f'  unusual token "{token}" in {current_loja} {horario} {dia_label}')
            for sigla, actual_horario in assignments:
                out_turnos.append((current_loja, actual_horario, dia_label, sigla))

    # Compute week_start (Monday of this week) from any non-empty day date
    week_start = None
    for _label, dt_iso in day_dates:
        if dt_iso:
            d = date.fromisoformat(dt_iso)
            week_start = d - timedelta(days=d.weekday())
            break

    descargas, descarga_warnings = parse_descargas(page_text, day_dates)
    return {'week_start': week_start, 'turnos': out_turnos, 'descargas': descargas,
            'day_dates': day_dates, 'warnings': warnings + descarga_warnings}


def week_record(week_start: date):
    """(codigo, label, dias_json) for the week starting on this Monday.

    Built with the SAME helpers the app uses when a supervisor creates a week.
    Hand-rolling them here produced "27 a 3 de Maio" for a week spanning two
    months, and marked every day feriado=False — so an imported week never
    showed its public holidays.
    """
    from helpers.time_helpers import week_codigo, week_label, week_from_monday
    return (week_codigo(week_start), week_label(week_start),
            json.dumps(week_from_monday(week_start)))


def import_to_db(parsed, db_path: str, dry_run: bool = False, replace: bool = False):
    """Insert parsed PDF into the database. Returns dict with counts:
    {created, skipped_dup, skipped_unknown_sigla, deleted, unknown_siglas: set}

    If replace=True, all existing Turno rows for the week's codigo are deleted
    first, then turnos from the PDF are inserted fresh. Use this when the live
    DB has stale/wrong data from a previous bad seed and you want to overwrite
    it with the PDF as source of truth. DESTRUCTIVE — no undo.
    """
    import os
    os.environ.setdefault('AMORINO_DB_PATH', db_path)
    import app as amorino_app

    week_start = parsed['week_start']
    turnos = parsed['turnos']
    if not week_start:
        return {'error': 'Could not determine week start date'}

    codigo, label, dias_json = week_record(week_start)

    counts = {'created': 0, 'skipped_dup': 0, 'skipped_unknown_sigla': 0,
              'deleted': 0,
              'unknown_siglas': set(), 'week_codigo': codigo,
              'week_label': label, 'week_start': week_start.isoformat()}

    with amorino_app.app.app_context():
        # A short week code repeats across years. Resolve the actual calendar
        # week before replace mode can delete any assignments.
        codigo = amorino_app.resolve_week_codigo(week_start, amorino_app.SemanaConfig.query.all())
        counts['week_codigo'] = codigo
        # ── Replace mode: wipe existing turnos for this week first ────────────
        if replace:
            existing = amorino_app.Turno.query.filter_by(semana=codigo).all()
            counts['deleted'] = len(existing)
            if not dry_run:
                amorino_app.Turno.query.filter_by(semana=codigo).delete()
                amorino_app.db.session.commit()

        # Make sure SemanaConfig exists
        sw = amorino_app.SemanaConfig.query.filter_by(codigo=codigo).first()
        if not sw:
            if not dry_run:
                sw = amorino_app.SemanaConfig(codigo=codigo, label=label,
                                                dias_json=dias_json, ativa=False)
                amorino_app.db.session.add(sw)
                amorino_app.db.session.commit()
            counts['week_created'] = True
        else:
            counts['week_created'] = False

        # Pre-fetch known siglas
        known_siglas = {f.sigla for f in amorino_app.Funcionario.query.all()}

        # Insert turnos
        for (loja, horario, dia_label, sigla) in turnos:
            if sigla not in known_siglas:
                counts['skipped_unknown_sigla'] += 1
                counts['unknown_siglas'].add(sigla)
                continue
            # Idempotency check
            existing = amorino_app.Turno.query.filter_by(
                funcionario=sigla, loja=loja, horario=horario,
                dia=dia_label, semana=codigo
            ).first()
            if existing:
                counts['skipped_dup'] += 1
                continue
            if not dry_run:
                amorino_app.db.session.add(amorino_app.Turno(
                    funcionario=sigla, loja=loja, horario=horario,
                    dia=dia_label, semana=codigo
                ))
                counts['created'] += 1
            else:
                counts['created'] += 1   # would-be created
        if not dry_run:
            amorino_app.db.session.commit()

    return counts


def main():
    parser = argparse.ArgumentParser(description='Import Amorino schedule PDFs')
    parser.add_argument('pdfs', nargs='+', help='PDF files to import')
    parser.add_argument('--db', default='amorino.db', help='SQLite DB path')
    parser.add_argument('--dry-run', action='store_true',
                        help='Parse and report but do not write to DB')
    parser.add_argument('--year', type=int,
                        help='Year hint for date inference (default: from filename)')
    parser.add_argument('--replace', action='store_true',
                        help='DESTRUCTIVE: delete all existing Turno rows for each '
                             'imported week before re-inserting. Use this to fix a DB '
                             'with stale/wrong data from an old seed. Cannot be undone.')
    parser.add_argument('--reset-targets', action='store_true',
                        help='After import, recompute LojaHeadcountTarget for every '
                             '(loja, weekday) from the peak headcount observed in '
                             'the current Turno table. Use together with --replace '
                             'to get fully consistent state.')
    args = parser.parse_args()

    banner = ''
    if args.dry_run:    banner += 'DRY RUN — '
    if args.replace:    banner += '⚠ REPLACE MODE — existing turnos for each imported week will be DELETED before re-import. '
    print(f"{banner}Importing {len(args.pdfs)} PDF(s) into {args.db}")
    print('=' * 70)

    grand_totals = {'created': 0, 'skipped_dup': 0, 'skipped_unknown_sigla': 0,
                    'deleted': 0, 'unknown_siglas': set()}

    for pdf_path in args.pdfs:
        p = Path(pdf_path)
        if not p.is_file():
            print(f'\n✗ {p.name}: file not found, skipping')
            continue
        print(f'\n→ {p.name}')
        parsed = parse_pdf(p, year_hint=args.year)
        if not parsed['week_start']:
            print(f'  ✗ Could not determine week — no dates found in header')
            continue
        print(f'  Week: {parsed["week_start"]} ({len(parsed["turnos"])} turnos parsed)')
        for w in parsed['warnings'][:5]:
            print(f'  ⚠ {w}')
        if len(parsed['warnings']) > 5:
            print(f'  ... and {len(parsed["warnings"]) - 5} more warnings')

        counts = import_to_db(parsed, args.db, dry_run=args.dry_run, replace=args.replace)
        if 'error' in counts:
            print(f'  ✗ {counts["error"]}')
            continue
        verb = 'would create' if args.dry_run else 'created'
        print(f'  Codigo: {counts["week_codigo"]} ({"new" if counts["week_created"] else "existing"})')
        if args.replace and counts.get('deleted', 0):
            verb_del = 'would delete' if args.dry_run else 'deleted'
            print(f'  {verb_del}: {counts["deleted"]} existing turnos for this week')
        print(f'  {verb}: {counts["created"]} turnos, '
              f'skipped {counts["skipped_dup"]} duplicates, '
              f'{counts["skipped_unknown_sigla"]} unknown sigla')
        if counts['unknown_siglas']:
            print(f'  Unknown siglas: {", ".join(sorted(counts["unknown_siglas"]))}')

        grand_totals['created'] += counts['created']
        grand_totals['skipped_dup'] += counts['skipped_dup']
        grand_totals['skipped_unknown_sigla'] += counts['skipped_unknown_sigla']
        grand_totals['deleted'] += counts.get('deleted', 0)
        grand_totals['unknown_siglas'] |= counts['unknown_siglas']

    # ── Optional: recompute LojaHeadcountTarget from current data ─────────────
    if args.reset_targets and not args.dry_run:
        print('\n' + '─' * 70)
        print('Resetting LojaHeadcountTarget from current Turno data...')
        import os
        os.environ.setdefault('AMORINO_DB_PATH', args.db)
        import app as amorino_app
        from collections import defaultdict
        # weekday mapping using the app's helper
        with amorino_app.app.app_context():
            # Gather peak headcount per (loja, weekday_idx) across ALL weeks
            peak = defaultdict(int)
            # Walk each week, count distinct siglas per (loja, dia)
            for sw in amorino_app.SemanaConfig.query.all():
                # We need weekday idx from dia label. Build a label→idx map
                # from this week's dias_json so day-of-month doesn't matter.
                import json as _j
                try:
                    dias = _j.loads(sw.dias_json or '[]')
                except _j.JSONDecodeError:
                    continue
                # Order in dias_json is Mon..Sun by construction
                label_to_idx = {d['label']: i for i, d in enumerate(dias)}
                ts = amorino_app.Turno.query.filter_by(semana=sw.codigo).all()
                by_ld = defaultdict(set)
                for t in ts:
                    idx = label_to_idx.get(t.dia)
                    if idx is None:
                        continue
                    by_ld[(t.loja, idx)].add(t.funcionario)
                for (loja, idx), siglas in by_ld.items():
                    if len(siglas) > peak[(loja, idx)]:
                        peak[(loja, idx)] = len(siglas)
            # Apply to LojaHeadcountTarget
            updated = 0
            for (loja, idx), n in peak.items():
                row = amorino_app.LojaHeadcountTarget.query.filter_by(
                    loja=loja, dia_idx=idx).first()
                if row:
                    if row.target != n:
                        row.target = n
                        updated += 1
                else:
                    amorino_app.db.session.add(amorino_app.LojaHeadcountTarget(
                        loja=loja, dia_idx=idx, target=n))
                    updated += 1
            amorino_app.db.session.commit()
            print(f'  Updated {updated} (loja, weekday) targets to match observed peak headcount.')
    elif args.reset_targets and args.dry_run:
        print('\n  --reset-targets requested but skipped (--dry-run).')

    print('\n' + '=' * 70)
    print('SUMMARY')
    if grand_totals.get('deleted', 0):
        verb_del = 'would be deleted' if args.dry_run else 'deleted'
        print(f'  Existing turnos {verb_del}:        {grand_totals["deleted"]}')
    print(f'  Turnos {("would be " if args.dry_run else "")}created: {grand_totals["created"]}')
    print(f'  Duplicates skipped:                {grand_totals["skipped_dup"]}')
    print(f'  Turnos with unknown sigla skipped: {grand_totals["skipped_unknown_sigla"]}')
    if grand_totals['unknown_siglas']:
        print(f'  All unknown siglas: {", ".join(sorted(grand_totals["unknown_siglas"]))}')
        print(f'  ↑ If any of these are real, add them to Funcionario before re-running.')

    if not args.dry_run and grand_totals['created'] > 0:
        print()
        print('  Next step: open the supervisor schedule page, click "Atribuir Turnos",')
        print('  and look for the "Ver sugestões" banner. The current headcount targets')
        print('  were seeded from the very first week — now that you have more history,')
        print('  the system can propose better targets based on what you actually staff.')


if __name__ == '__main__':
    main()
