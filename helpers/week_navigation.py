"""Date-based navigation over a caller's already authorised schedule weeks."""
import json
from datetime import date

from config import MESES_LONGOS
from helpers.time_helpers import semana_start_date


def _start(week):
    try:
        return date.fromisoformat(semana_start_date(week))
    except (ValueError, TypeError):
        return None


def schedule_week_navigation(weeks, selected):
    """Never use IDs/codes for chronology, including duplicate/undated imports."""
    result = {'previous': None, 'next': None, 'label': 'Sem semanas disponíveis',
              'start': '', 'end': ''}
    if selected is None:
        return result
    start = _start(selected)
    if start is None:
        result['label'] = 'Datas da semana indisponíveis'
        return result
    try:
        days = json.loads(selected.dias_json or '[]')
        dates = [date.fromisoformat(day['date']) for day in days]
        end = max(dates) if dates else start
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        end = start
    if start.year != end.year:
        label = (f'{start.day} de {MESES_LONGOS[start.month - 1]} de {start.year}'
                 f' a {end.day} de {MESES_LONGOS[end.month - 1]} de {end.year}')
    elif start.month != end.month:
        label = (f'{start.day} de {MESES_LONGOS[start.month - 1]}'
                 f' a {end.day} de {MESES_LONGOS[end.month - 1]} de {end.year}')
    else:
        label = f'{start.day} a {end.day} de {MESES_LONGOS[end.month - 1]} de {end.year}'
    dated = [(week, _start(week)) for week in weeks]
    earlier = [(week, day) for week, day in dated if day and day < start]
    later = [(week, day) for week, day in dated if day and day > start]
    result.update(label=label, start=start.isoformat(), end=end.isoformat(),
                  previous=max(earlier, key=lambda pair: pair[1])[0] if earlier else None,
                  next=min(later, key=lambda pair: pair[1])[0] if later else None)
    return result
