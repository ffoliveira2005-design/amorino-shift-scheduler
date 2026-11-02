"""Concrete, bookable shift recommendations for the day planner.

No database writes: every score and explanation describes the very same time
window that the supervisor can assign. Availability slots still mean entry
times; coverage and delivery conflicts use the complete clock interval.
"""
import re

from helpers.scoring import _score_candidate, _historical_affinity
from helpers.time_helpers import horario_to_slot, shift_bounds, shift_hours, shifts_overlap

_MANAGED_STORE_BONUS = 500


def _availability_tier(info, loja):
    tipo = info.get('tipo')
    if tipo == 'preferencia':
        return 4 if loja in (info.get('lojas') or set()) else 3
    if tipo == 'disponivel':
        return 2
    if tipo == 'extra' or info.get('descarga'):
        return 1
    return 0


def _fmt_hours(value):
    return f'{value:g}'.replace('.', ',')


def _bookable_bounds(horario):
    clocks = re.fullmatch(
        r'(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})'
        r'(?:\s*\(\+\d+(?:[,.]\d+)?\))?', horario or '')
    if not clocks:
        return None, None
    h1, m1, h2, m2 = map(int, clocks.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or (h1, m1) == (h2, m2):
        return None, None
    return shift_bounds(horario)


def build_suggestions(*, loja, dia, weekday, horarios, current_shifts,
                      funcionarios, availability, weekly_hours, worked_days,
                      same_day_shifts, deliveries, prior, missing, day_labels,
                      day_date=None, dated_assignments=None, dated_work_days=None,
                      turnos_mean=0):
    """Rank actual person/band options, then fill gaps with distinct people.

    The first ``missing`` recommendations form a proposed team, so their
    coverage is evaluated together. Extra choices are independent alternatives
    against the saved schedule, never phantom assignments for nonexistent seats.
    """
    if not missing:
        return []
    bands = [(h, *_bookable_bounds(h)) for h in horarios]
    # Special labels such as Provas remain available in the manual picker; they
    # cannot provide a concrete, timed recommendation.
    bands = [(h, start, end) for h, start, end in bands if start is not None]
    if not bands:
        return []
    dated_assignments = dated_assignments or {}
    dated_work_days = dated_work_days or {}
    typical = {h: prior.get('band_typical', {}).get((loja, weekday, h), 0)
               for h, _, _ in bands}
    usual = [(start, end) for h, start, end in bands if typical[h] > 0]
    actual = [_bookable_bounds(h) for h, _ in current_shifts]
    operating = usual or [(start, end) for _, start, end in bands]
    operating += [(start, end) for start, end in actual if start is not None]
    opening = min(start for start, _ in operating)
    closing = max(end for _, end in operating)
    day_idx = day_labels.index(dia)

    def fatigue(sigla):
        days = worked_days.get(sigla, set())
        projected = days | {dia}
        if len(projected) >= 7:
            return None, len(projected), 7
        run = 1
        if day_date:
            ordinal = day_date.toordinal()
            # Untimed work (e.g. Provas Práticas) still uses a working day.
            # Keep it separate from the clock intervals needed for rest gaps.
            dated_days = set(dated_work_days.get(sigla, set()))
            dated_days.update(d for d, _, _ in dated_assignments.get(sigla, []))
            # Direct helper callers may only supply the current week labels.
            dated_days.update(ordinal + idx - day_idx
                              for idx, label in enumerate(day_labels) if label in days)
            for direction in (-1, 1):
                cursor = ordinal + direction
                while cursor in dated_days:
                    run += 1
                    cursor += direction
        else:
            for direction in (-1, 1):
                cursor = day_idx + direction
                while 0 <= cursor < len(day_labels) and day_labels[cursor] in days:
                    run += 1
                    cursor += direction
        penalty = -600 if run >= 6 else -250 if run == 5 else -80 if run == 4 else 0
        if len(projected) == 6:
            penalty -= 150
        return penalty, len(projected), run

    eligible = {}
    for sigla, worker in funcionarios.items():
        info = availability.get(sigla, {}).get(dia, {})
        if info.get('tipo') == 'indisponivel' or same_day_shifts.get((sigla, dia)):
            continue
        rank = getattr(worker, 'rank', 'staff') or 'staff'
        usual_store = rank == 'shift_supervisor' and loja in prior.get('usual_stores', {}).get(sigla, [])
        # Shift supervisors are automatically placed only in their established
        # usual stores. Missing history means no automatic placement; manual
        # assignment remains available rather than guessing from availability.
        if rank == 'shift_supervisor' and not usual_store:
            continue
        managed_store = rank == 'store_manager' and getattr(worker, 'loja_gerida', '') == loja
        fat, days_after, run = fatigue(sigla)
        if fat is None:
            continue
        named = info.get('slots') or set()
        options = []
        for h, start, end in bands:
            slot = horario_to_slot(h)
            if named and slot not in named:
                continue
            if any(shifts_overlap(h, dh) for dh in deliveries.get((sigla, dia), [])):
                continue
            rest_gaps = []
            if day_date:
                anchor = day_date.toordinal() * 1440
                for ordinal, prev_start, prev_end in dated_assignments.get(sigla, []):
                    if ordinal == day_date.toordinal():
                        continue  # same-day deliveries were checked exactly above
                    a, b = ordinal * 1440 + prev_start, ordinal * 1440 + prev_end
                    if ordinal < day_date.toordinal():
                        rest_gaps.append((anchor + start - b) / 60)
                    else:
                        rest_gaps.append((a - anchor - end) / 60)
                # A previous overnight shift can extend into today. Never offer
                # a window with an actual cross-date collision.
                if rest_gaps and min(rest_gaps) < 0:
                    continue
            min_rest = min(rest_gaps) if rest_gaps else None
            hours = shift_hours(h)
            before = weekly_hours.get(sigla, 0)
            after = round(before + hours, 2)
            contract = worker.horas_contrato or 0
            hist = _historical_affinity(sigla, loja, weekday, slot, prior)
            # Keep the compatibility scorer's availability/history policy;
            # workload here includes the duration of this specific proposal.
            score, reason = _score_candidate(loja, slot, info, before, 0, True, hist)
            score += fat
            # A manager's assigned store strongly influences otherwise similar
            # choices. Explicit availability tiers still come first, and all
            # interval conflicts above are hard exclusions even for managers.
            if managed_store:
                score += _MANAGED_STORE_BONUS
            warnings = []
            if not worker.is_turnos and contract > 0:
                remaining = max(0, contract - before)
                added_excess = max(0, after - contract) - max(0, before - contract)
                score += min(200, remaining * 20) + min(hours, remaining) * 10
                score -= min(500, added_excess * 50)
                if after > contract:
                    warnings.append(f'Ultrapassa o contrato em {_fmt_hours(after - contract)} h')
            elif worker.is_turnos:
                score += max(-150, min(150, (turnos_mean - after) * 30))
            if min_rest is not None and min_rest < 11:
                score -= min(450, 60 + (11 - min_rest) * 45)
                warnings.append(f'Intervalo entre turnos: {_fmt_hours(round(min_rest, 1))} h')
            if days_after == 6:
                warnings.append('Fica com apenas um dia sem trabalho nesta semana')
            if run >= 5:
                warnings.append(f'{run} dias de trabalho consecutivos')
            if info.get('tipo') not in ('disponivel', 'preferencia'):
                warnings.append('Sem disponibilidade confirmada para este dia')
            options.append({
                'sigla': sigla, 'reason': reason, 'horario': h,
                'weekly_hours': before, 'shift_hours': hours,
                'projected_hours': after, 'contract_hours': contract,
                'working_days': len(worked_days.get(sigla, set())),
                'projected_days': days_after, 'warnings': warnings,
                '_score': score, '_tier': _availability_tier(info, loja),
                '_start': start, '_end': end, '_hist': hist,
                '_usual_store': usual_store, '_managed_store': managed_store,
            })
        if options:
            eligible[sigla] = options

    def evaluate(option, coverage):
        result = dict(option)
        start, end = option['_start'], option['_end']
        timed = [(_bookable_bounds(h), sigla) for h, sigla in coverage]
        timed = [(bounds, sigla) for bounds, sigla in timed if bounds[0] is not None]
        band_count = len({s for h, s in coverage if h == option['horario']})
        # Count real overlapping people at half-hour samples, rather than only
        # the entry bucket of a long shift. Reward the less-covered intervals.
        counts = [len({s for (a, b), s in timed if a <= minute < b})
                  for minute in range(start, end, 30)]
        peak = max([len({s for (a, b), s in timed if a <= minute < b})
                    for minute in range(opening, closing, 30)] or [0])
        average = sum(counts) / len(counts) if counts else 0
        coverage_bonus = min(140, max(0, peak - average) * 70)
        deficit = max(0, typical[option['horario']] - band_count)
        pattern_bonus = min(100, deficit * 70)
        open_covered = any(a <= opening < b and funcionarios.get(s) and
                           funcionarios[s].can_open_alone for (a, b), s in timed)
        close_covered = any(a < closing <= b and funcionarios.get(s) and
                            funcionarios[s].can_close_alone for (a, b), s in timed)
        worker = funcionarios[option['sigla']]
        responsible = []
        if not open_covered and start <= opening < end and worker.can_open_alone:
            responsible.append('Cobre a abertura com responsável')
        if not close_covered and start < closing <= end and worker.can_close_alone:
            responsible.append('Cobre o fecho com responsável')
        resp_bonus = 120 * len(responsible)
        if not coverage and start <= opening and not worker.can_open_alone:
            resp_bonus -= 60
        pair = max([prior.get('pair_affinity', {}).get(
            (*sorted((option['sigla'], s)), loja, weekday), 0)
            for (a, b), s in timed if a < end and start < b] or [0])
        result['_score'] += coverage_bonus + pattern_bonus + resp_bonus + pair * 75
        reason = result['reason']
        if responsible and reason in ('sem resposta', 'disponível', 'descarga', 'histórico'):
            if option['_hist'] * 250 < resp_bonus:
                reason = 'responsável'
        role_explanation = ('Gere esta loja' if option['_managed_store'] else
                            'Loja habitual' if option['_usual_store'] else '')
        if role_explanation and reason not in ('prefere', 'quer trabalhar'):
            reason = 'responsável' if option['_managed_store'] else 'histórico'
        result['reason'] = reason
        primary = {
            'prefere': 'Prefere esta loja',
            'quer trabalhar': 'Indicou preferência por trabalhar',
            'disponível': 'Disponibilidade confirmada',
            'descarga': 'Indicou disponibilidade para descarga',
            'histórico': 'Costuma trabalhar nesta loja e período',
            'responsável': responsible[0] if responsible else 'Responsável de turno',
            'sem resposta': 'Sem disponibilidade confirmada',
        }[reason]
        if role_explanation and reason not in ('prefere', 'quer trabalhar'):
            # Store habits may be known from one past week or a different day
            # and time. Never describe that as evidence of this specific period.
            primary = role_explanation
        explanations = [primary]
        if role_explanation and role_explanation not in explanations:
            explanations.append(role_explanation)
        for text in responsible:
            if text not in explanations:
                explanations.append(text)
        if coverage_bonus > 0:
            explanations.append('Reforça as horas com menor cobertura')
        if pattern_bonus > 0:
            explanations.append('Reforça um horário habitual desta loja')
        if option['contract_hours'] > 0 and not worker.is_turnos:
            if option['projected_hours'] <= option['contract_hours']:
                explanations.append('Cabe nas horas de contrato restantes')
        result['explanations'] = explanations
        # Independent alternatives use the same actual coverage. Stable final
        # ties favour less weekly work, then configured band order and initials.
        result['_key'] = (-result['_tier'], -result['_score'],
                          result['weekly_hours'], horarios.index(result['horario']),
                          result['sigla'])
        return result

    chosen, selected = [], set()
    proposed_coverage = list(current_shifts)
    for _ in range(min(missing, len(eligible))):
        candidates = [evaluate(option, proposed_coverage)
                      for sigla, options in eligible.items() if sigla not in selected
                      for option in options]
        if not candidates:
            break
        best = min(candidates, key=lambda option: option['_key'])
        chosen.append(best)
        selected.add(best['sigla'])
        proposed_coverage.append((best['horario'], best['sigla']))
    # Extra choices are alternatives to a saved gap, not additional team seats.
    alternatives = [min((evaluate(option, current_shifts) for option in options),
                        key=lambda option: option['_key'])
                    for sigla, options in eligible.items() if sigla not in selected]
    alternatives.sort(key=lambda option: option['_key'])
    chosen.extend(alternatives[:max(0, 3 - len(chosen))])
    return [{key: value for key, value in option.items() if not key.startswith('_')}
            for option in chosen]
