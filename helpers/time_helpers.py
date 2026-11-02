"""Time and date helpers — pure functions, no Flask, no DB.

Two groups of helpers:
  - Week-level: take a Monday date, return a 7-day structure with feriado info
  - Shift-level: parse horario strings like '08:00-16:30 (+0,5)' and compute
    bounds, overlap, and paid hours

If you change anything in here, also re-check helpers/scoring.py — it depends
on these for slot computation and weekday mapping.
"""

import re
from datetime import date, datetime, timedelta, timezone

import holidays

from config import (
    DAY_ABBR, MESES_CURTOS, MESES_LONGOS, DIAS_LONGOS,
    LUNCH_BREAK_THRESHOLD, LUNCH_BREAK_MINUTES,
)


# ── Week helpers ──────────────────────────────────────────────────────────────

def week_from_monday(monday: date) -> list:
    """Returns a list of 7 dicts (one per day Mon→Sun) with label, ISO date,
    and Portuguese holiday info."""
    sunday = monday + timedelta(days=6)
    pt = holidays.Portugal(years=[monday.year, sunday.year])
    dias = []
    for i in range(7):
        d = monday + timedelta(days=i)
        feriado_nome = pt.get(d)
        dias.append({
            'label':        f"{DAY_ABBR[d.weekday()]} {d.day}",
            'date':         d.isoformat(),
            'feriado':      bool(feriado_nome),
            'feriado_nome': feriado_nome or '',
        })
    return dias


def next_mondays(n: int = 8, include_today: bool = False) -> list:
    """Returns the next N Mondays as date objects, optionally including today
    if today is a Monday."""
    today = date.today()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0 and not include_today:
        days_ahead = 7
    first = today + timedelta(days=days_ahead)
    return [first + timedelta(weeks=i) for i in range(n)]


def mondays_from_current_week(n: int = 8) -> list:
    """The Monday of the CURRENT week plus the following n-1 Mondays.

    Unlike next_mondays(), the week we're living right now is included — so
    mid-week the supervisor can still create/activate the week that covers
    today (next_mondays only offers weeks starting AFTER today).
    """
    today = date.today()
    first = today - timedelta(days=today.weekday())
    return [first + timedelta(weeks=i) for i in range(n)]


def week_codigo(monday: date) -> str:
    """Short identifier like '11-17mai' used as DB key for SemanaConfig."""
    end = monday + timedelta(days=6)
    return f"{monday.day}-{end.day}{MESES_CURTOS[end.month-1]}"


def resolve_week_codigo(monday: date, weeks) -> str:
    """Preserve legacy codes while keeping different years' schedules separate.

    The short code repeats across years. Reuse a week with the same actual
    Monday; otherwise add the year only when the short code is already taken.
    Existing schedules and their links keep their original identifiers.
    """
    weeks = list(weeks)
    for week in weeks:
        if semana_start_date(week) == monday.isoformat():
            return week.codigo
    used = {week.codigo for week in weeks}
    base = week_codigo(monday)
    if base not in used:
        return base
    candidate = f'{base}-{monday.year}'
    if candidate not in used:
        return candidate
    # A malformed imported row must never be overwritten either.
    return monday.isoformat()


def week_label(monday: date) -> str:
    """Human-readable label like '11 a 17 de Maio' (or with month/year spans
    when the week crosses month/year boundaries)."""
    end = monday + timedelta(days=6)
    if monday.month == end.month:
        return f"{monday.day} a {end.day} de {MESES_LONGOS[end.month-1]}"
    if monday.year == end.year:
        return (f"{monday.day} de {MESES_LONGOS[monday.month-1]}"
                f" a {end.day} de {MESES_LONGOS[end.month-1]}")
    return (f"{monday.day} de {MESES_LONGOS[monday.month-1]} de {monday.year}"
            f" a {end.day} de {MESES_LONGOS[end.month-1]} de {end.year}")


def date_long(d: date) -> str:
    """'Sexta-feira, 25 de Setembro' — a single day, spelled out."""
    return f"{DIAS_LONGOS[d.weekday()]}, {d.day} de {MESES_LONGOS[d.month-1]}"


# ── Stored timestamps ─────────────────────────────────────────────────────────
# Every DateTime column is written as UTC, but SQLite hands them back naive.
# A naive ISO string ('2026-09-28T23:28:47') is read by the browser as LOCAL
# time, so in Portuguese summer time every "há X min" came out an hour late,
# and strftime() on the raw value printed UTC hours as if they were local.

def as_utc(dt: datetime):
    """The stored timestamp as an aware UTC datetime (None stays None)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso_utc(dt: datetime):
    """ISO string with an explicit offset, safe to hand to `new Date()`."""
    dt = as_utc(dt)
    return dt.isoformat() if dt else None


def local_str(dt: datetime, fmt: str = '%d/%m %H:%M') -> str:
    """A stored timestamp in the server's local time (TZ=Europe/Lisbon in
    production, see docs/DEPLOYMENT.md), formatted for display."""
    dt = as_utc(dt)
    return dt.astimezone().strftime(fmt) if dt else ''


# ── Slot / weekday mapping (used by scoring) ──────────────────────────────────

def horario_to_slot(horario: str) -> str:
    """Map a horario label to a slot bucket: 'manha' / 'tarde' / 'noite' / ''.
    Robust against non-time labels like 'Provas Práticas' (returns '')."""
    m = re.match(r'(\d{1,2}):(\d{2})', horario or '')
    if not m:
        return ''
    h = int(m.group(1))
    if h < 12: return 'manha'
    if h < 15: return 'tarde'
    return 'noite'


_DIA_LABEL_TO_IDX = {'Seg': 0, 'Ter': 1, 'Qua': 2, 'Qui': 3, 'Sex': 4, 'Sab': 5, 'Dom': 6}

def dia_label_to_idx(dia_label: str):
    """Map 'Seg 25' / 'Dom 17' to weekday 0..6. None if unrecognised."""
    return _DIA_LABEL_TO_IDX.get((dia_label or '')[:3])


# ── Week chronology (used by week selector + historical scoring) ──────────────

def semana_start_date(sw):
    """Extract the week's start date (Monday) from a SemanaConfig row's dias_json.
    Returns an ISO string like '2026-05-18' or '' if unparseable.

    This is the canonical way to sort weeks chronologically — sorting by DB id
    is wrong because imported historical weeks get higher ids despite being
    earlier dates.
    """
    import json
    try:
        dias = json.loads(sw.dias_json or '[]')
        if dias and isinstance(dias, list):
            return dias[0].get('date', '') or ''
    except (json.JSONDecodeError, AttributeError, KeyError, IndexError):
        pass
    return ''


def semanas_chronological(SemanaConfig, descending=True):
    """Returns all SemanaConfig rows sorted by actual week start date.
    Pass descending=False for chronological order (oldest first).

    SemanaConfig is passed in to avoid a circular import — models.py imports
    nothing from helpers, so helpers can't import models at module load time.
    """
    rows = SemanaConfig.query.all()
    rows.sort(key=lambda s: semana_start_date(s) or '', reverse=descending)
    return rows


# ── Shift parsing ─────────────────────────────────────────────────────────────

def shift_bounds(horario_str):
    """Returns (start_minutes, end_minutes) for a shift label, handling midnight
    cross-over (end always >= start). Returns (None, None) if unparseable.
    Note: ignores the (+X) extra-hours marker — those are paid extras at the END
    of a shift, but the *core* clock window for overlap purposes ends at the
    second time. (+1) is typically a paid stretch after the door closes.
    """
    m = re.match(r'(\d{1,2}):(\d{2})\D+(\d{1,2}):(\d{2})', horario_str or '')
    if not m:
        return None, None
    h1, m1, h2, m2 = map(int, m.groups())
    s = h1 * 60 + m1
    e = h2 * 60 + m2
    if e <= s:
        e += 24 * 60
    return s, e


def shifts_overlap(h1, h2):
    """True if two horario labels overlap in time (same-day comparison)."""
    s1, e1 = shift_bounds(h1)
    s2, e2 = shift_bounds(h2)
    if s1 is None or s2 is None:
        return False
    return not (e1 <= s2 or e2 <= s1)


def shift_hours(horario_str):
    """Calculate paid hours from a horario string.
    - '08:00-16:30' = 8.5h gross, minus 0.5h lunch = 8.0h paid
    - '16:00-00:30 (+1)' = 8.5h gross + 1h extra - 0.5h lunch = 9.0h paid
    - '13:00-19:00' = 6h gross, no lunch deduction = 6h paid

    Shifts of 8.0h or more (gross) get the 30-min lunch/dinner deducted.
    """
    m = re.match(r'(\d{1,2}):(\d{2})\D+(\d{1,2}):(\d{2})', horario_str)
    if not m: return 0
    h1, m1, h2, m2 = map(int, m.groups())
    start = h1 * 60 + m1
    end   = h2 * 60 + m2
    if end < start:
        end += 24 * 60   # crosses midnight
    minutes = end - start
    # Account for "(+X)" or "(+0,5)" extras
    extra = re.search(r'\(\+(\d+(?:[,.]\d+)?)\)', horario_str)
    if extra:
        extra_h = float(extra.group(1).replace(',', '.'))
        minutes += int(extra_h * 60)
    # Deduct lunch break for shifts of 8h or more
    gross_hours = minutes / 60.0
    if gross_hours >= LUNCH_BREAK_THRESHOLD:
        minutes -= LUNCH_BREAK_MINUTES
    return round(minutes / 60.0, 2)


def descarga_hours(horario_str) -> float:
    """Hours credited for one descarga, given the window it ran in.

    At least DESCARGA_HORAS_MIN (4h) — showing up for the delivery is worth a
    4-hour block even if the window is shorter — but a longer window counts for
    what it actually is. Unparseable windows fall back to the minimum rather
    than to zero, so a malformed row can never silently erase someone's hours.
    """
    from config import DESCARGA_HORAS_MIN
    return max(DESCARGA_HORAS_MIN, shift_hours(horario_str or ''))


def descarga_horario_for(loja_id: str) -> str:
    """Return the fixed descarga window for a store: 10:00-14:00 for MBS,
    08:00-12:00 everywhere else."""
    from config import DESCARGA_HORARIO_DEFAULT, DESCARGA_HORARIO_BY_LOJA
    return DESCARGA_HORARIO_BY_LOJA.get(loja_id, DESCARGA_HORARIO_DEFAULT)


def slots_blocked_by(horario_str) -> set:
    """Which slot buckets a given time window occupies (for hiding a descarga
    worker from overlapping regular slots in suggestions). A window starting
    before 12h always blocks 'manha'; if it runs past 12h it also blocks
    'tarde'; past 15h, 'noite'."""
    s, e = shift_bounds(horario_str)
    if s is None:
        return set()
    blocked = set()
    if s < 12 * 60:               blocked.add('manha')
    if s < 15 * 60 and e > 12 * 60: blocked.add('tarde')
    if e > 15 * 60:               blocked.add('noite')
    return blocked


# ── Backwards-compat aliases ──────────────────────────────────────────────────
# Original code used underscore-prefixed names. Keep aliases so anything that
# already says `from helpers.time_helpers import _shift_hours` still works.
_horario_to_slot   = horario_to_slot
_dia_label_to_idx  = dia_label_to_idx
_shift_bounds      = shift_bounds
_shifts_overlap    = shifts_overlap
_shift_hours       = shift_hours
