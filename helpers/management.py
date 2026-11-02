"""Read-only, week-scoped figures for the management overview."""
import json

from config import LOJAS
from models import (
    Funcionario, Turno, Descarga, Disponibilidade, LojaHeadcountTarget,
    TrocaPedido,
)
from helpers.time_helpers import dia_label_to_idx, _shift_hours, descarga_hours


def build_management_overview(semana):
    staff = Funcionario.query.all()
    roster = {f.sigla for f in staff}
    days = json.loads(semana.dias_json or '[]') if semana else []
    labels = {d['label'] for d in days}
    shifts = Turno.query.filter_by(semana=semana.codigo).all() if semana else []
    deliveries = Descarga.query.filter_by(semana=semana.codigo).all() if semana else []
    targets = {(t.loja, t.dia_idx): max(0, t.target)
               for t in LojaHeadcountTarget.query.all()}
    assigned = {}
    hours = {}
    people = {}
    workdays = {}
    regular_hours = delivery_hours = 0.0
    for shift in shifts:
        if shift.dia not in labels:
            continue
        assigned.setdefault((shift.loja, shift.dia), set()).add(shift.funcionario)
        h = _shift_hours(shift.horario)
        regular_hours += h
        hours[shift.loja] = hours.get(shift.loja, 0.0) + h
        people.setdefault(shift.loja, set()).add(shift.funcionario)
        workdays.setdefault(shift.funcionario, set()).add(shift.dia)
    for delivery in deliveries:
        if delivery.dia not in labels:
            continue
        h = descarga_hours(delivery.horario)
        delivery_hours += h
        hours[delivery.loja] = hours.get(delivery.loja, 0.0) + h
        people.setdefault(delivery.loja, set()).add(delivery.funcionario)
        workdays.setdefault(delivery.funcionario, set()).add(delivery.dia)

    stores = []
    covered = required = missing = configured = 0
    for store in LOJAS:
        cells = []
        store_missing = store_required = store_covered = 0
        for day in days:
            target = targets.get((store['id'], dia_label_to_idx(day['label'])), 0)
            count = len(assigned.get((store['id'], day['label']), set()))
            gap = max(0, target - count)
            cells.append({'assigned': count, 'target': target, 'missing': gap,
                          'status': 'unset' if not target else ('gap' if gap else 'met')})
            store_required += target
            store_covered += min(count, target)
            store_missing += gap
            configured += int(target > 0)
        stores.append({**store, 'cells': cells,
                       'hours': round(hours.get(store['id'], 0.0), 1),
                       'people': len(people.get(store['id'], set())),
                       'missing': store_missing, 'required': store_required,
                       'coverage': round(100 * store_covered / store_required)
                                   if store_required else None})
        covered += store_covered
        required += store_required
        missing += store_missing

    responses = set()
    if semana:
        responses = {r.funcionario for r in
                     Disponibilidade.query.filter_by(semana=semana.codigo).all()
                     if r.dia in labels and r.funcionario in roster}
        # Join to the origin shift so other weeks' requests never enter the KPI.
        swaps = (TrocaPedido.query.join(Turno, TrocaPedido.turno_origem_id == Turno.id)
                 .filter(Turno.semana == semana.codigo,
                         TrocaPedido.estado == 'pendente_supervisor').count())
    else:
        swaps = 0

    max_hours = max((s['hours'] for s in stores), default=0)
    for store in stores:
        store['hours_pct'] = round(store['hours'] / max_hours * 100) if max_hours else 0
    names = {f.sigla: f.nome for f in staff}
    rest_people = sorted(
        ({'sigla': sigla, 'nome': names.get(sigla, sigla), 'days': len(ds)}
         for sigla, ds in workdays.items() if len(ds) >= 6),
        key=lambda person: (-person['days'], person['nome']))
    return {
        'days': days, 'stores': stores, 'staff_count': len(staff),
        'shift_count': sum(s.dia in labels for s in shifts),
        'scheduled_people': len(workdays),
        'regular_hours': round(regular_hours, 1),
        'delivery_hours': round(delivery_hours, 1),
        'total_hours': round(regular_hours + delivery_hours, 1),
        'responses': len(responses), 'response_missing': len(staff) - len(responses),
        'response_pct': round(len(responses) / len(staff) * 100) if staff else 0,
        'swaps': swaps, 'missing': missing, 'required': required, 'covered': covered,
        'coverage_pct': round(covered / required * 100) if required else None,
        'configured_cells': configured, 'total_cells': len(days) * len(LOJAS),
        'rest_review': len(rest_people), 'rest_people': rest_people,
    }
