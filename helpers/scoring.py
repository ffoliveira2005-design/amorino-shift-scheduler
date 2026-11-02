"""Scoring engine for the headcount-driven empties view.

Two layers:
  - _load_prior_weeks(current_semana_codigo): one-shot, queries the DB to
    collect cell/pair/headcount affinities from the last ~12 weeks
  - _score_candidate(...) / _score_best_slot_for_loja(...): pure functions
    that take the prior data + a candidate and produce a (score, reason) tuple

Imported by api_empties and api_headcount_targets_suggest. Tests in
tests/test_basics.py cover the scoring rules and the prior loader.
"""

from datetime import date

from config import HISTORICAL_LOOKBACK_WEEKS, HISTORICAL_MIN_WEEKS, HISTORICAL_MAX_BONUS
from models import SemanaConfig, Turno
from helpers.time_helpers import (
    horario_to_slot, dia_label_to_idx, semana_start_date, shift_bounds,
)

# Back-compat alias — old code used the underscore-prefixed name.
_semana_start_date = semana_start_date


def _load_prior_weeks(current_semana_codigo: str):
    """Returns a structure describing past weeks relevant to historical scoring.

    Output shape:
      {
        'n_weeks': int,                                    # how many past weeks counted
        'affinity': { (sigla, loja, weekday_idx, slot): float },  # 0..1 per cell
        'pair_affinity': { (sigla_a, sigla_b, loja, weekday_idx): float },
        'loja_day_counts': { (loja, weekday_idx): [c1,c2,...] },  # for auto-targets
        'band_typical': { (loja, weekday_idx, horario): float },
        # Present when past store staffing exists, including a single week:
        'store_day_weights': { (sigla, loja): int },
        'usual_stores': { sigla: [loja, ...] },               # tied highest weights
      }
    The 'affinity' and 'pair_affinity' values are already normalised; callers
    add no further math beyond multiplying by their scaling constant.

    Past weeks are ordered by their actual start date (from dias_json), not by
    DB id, so chronology stays correct even when rows are inserted out-of-order
    (e.g. tests, manual data entry).
    """
    out = {'n_weeks': 0, 'affinity': {}, 'pair_affinity': {},
           'loja_day_counts': {}, 'band_typical': {}}

    def start_date(sw):
        try:
            return date.fromisoformat(_semana_start_date(sw))
        except (ValueError, TypeError):
            return None

    current = SemanaConfig.query.filter_by(codigo=current_semana_codigo).first()
    current_date = start_date(current) if current else None
    # Without a dated anchor there is no safe way to distinguish history from
    # the current or a future schedule. Do not learn from either accidentally.
    if current_date is None:
        return out

    # All weeks (we'll filter to "past" in Python — fewer than 200 weeks ever,
    # no point doing a fancy date-string query in SQL).
    all_weeks = SemanaConfig.query.all()
    past = []
    for sw in all_weeks:
        d = start_date(sw)
        if d is not None and d < current_date:
            past.append((d, sw))
    # Empty configured weeks are drafts, not observations of zero staffing.
    # Load once before taking the lookback so drafts cannot displace real weeks.
    past.sort(key=lambda x: (x[0], x[1].codigo), reverse=True)
    past_codes = [sw.codigo for _d, sw in past]
    rows_by_week = {}
    if past_codes:
        for t in Turno.query.filter(Turno.semana.in_(past_codes)).all():
            if dia_label_to_idx(t.dia) is not None:
                rows_by_week.setdefault(t.semana, []).append(t)
    past = [sw for _d, sw in past if rows_by_week.get(sw.codigo)]
    past = past[:HISTORICAL_LOOKBACK_WEEKS]
    out['n_weeks'] = len(past)

    if not past:
        return out

    # weight by recency rank (0 = newest)
    def _weight(rank):
        if rank < 4:  return 3
        if rank < 8:  return 2
        return 1
    rank_by_code = {w.codigo: i for i, w in enumerate(past)}
    total_weight_per_week = sum(_weight(i) for i in range(len(past)))

    rows = [t for sw in past for t in rows_by_week[sw.codigo]]

    # Store placement for shift supervisors is independent of weekday/entry
    # time. A split shift or duplicate imported row is still just one day at
    # that store. Unlike generic affinity, one dated past week is sufficient:
    # the strict usual-store rule must not guess a different placement.
    store_day_weights = {}
    store_day_seen = set()
    stores_by_worker = {}
    for t in rows:
        observation = (t.semana, t.dia, t.funcionario, t.loja)
        if observation in store_day_seen:
            continue
        store_day_seen.add(observation)
        key = (t.funcionario, t.loja)
        store_day_weights[key] = store_day_weights.get(key, 0) + _weight(rank_by_code[t.semana])
    for (sigla, loja), weight in store_day_weights.items():
        stores_by_worker.setdefault(sigla, {})[loja] = weight
    out['store_day_weights'] = store_day_weights
    out['usual_stores'] = {
        sigla: sorted(loja for loja, weight in weights.items() if weight == max(weights.values()))
        for sigla, weights in stores_by_worker.items()
    }

    if len(past) < HISTORICAL_MIN_WEEKS:
        return out

    # ── Per-cell accumulator: (sigla, loja, weekday_idx, slot) → total weight ─
    cell_weight = {}
    cell_seen = set()
    # ── Loja-day count accumulator: for auto-targets ──────────────────────────
    headcount_per_week = {}
    # Coworkers only form a pair when their real time windows overlap at the
    # same store. Merely working opposite ends of the day is not team evidence.
    same_day_groups = {}   # (week_code, dia_label, loja) -> {sigla: [bounds]}
    band_people = {}       # (week_code, loja, weekday_idx, horario) -> set

    for t in rows:
        wkd = dia_label_to_idx(t.dia)
        if wkd is None:
            continue
        slot = horario_to_slot(t.horario)
        # Cell affinity
        key = (t.funcionario, t.loja, wkd, slot)
        observation = (t.semana,) + key
        if observation not in cell_seen:
            cell_seen.add(observation)
            cell_weight[key] = cell_weight.get(key, 0) + _weight(rank_by_code[t.semana])
        # Headcount (de-duplicated per week)
        hkey = (t.loja, wkd, t.semana)
        headcount_per_week.setdefault(hkey, set()).add(t.funcionario)
        # Same-day-loja groups for pair affinity
        gkey = (t.semana, t.dia, t.loja)
        bounds = shift_bounds(t.horario)
        if bounds[0] is not None:
            same_day_groups.setdefault(gkey, {}).setdefault(t.funcionario, []).append(bounds)
            # Count distinct workers on a real band, once per week. Duplicate
            # imported rows must not inflate the band's typical staffing.
            bkey = (t.semana, t.loja, wkd, t.horario)
            band_people.setdefault(bkey, set()).add(t.funcionario)

    # Normalise cell affinity to 0..1: divide by total possible weight
    out['affinity'] = {
        k: min(1.0, v / total_weight_per_week)
        for k, v in cell_weight.items()
    }

    # ── Headcount-per-(loja, weekday) for auto-targets ────────────────────────
    counts = {}
    for loja, wkd, week_code in sorted(
            headcount_per_week,
            key=lambda k: (k[0], k[1], rank_by_code[k[2]])):
        siglas = headcount_per_week[(loja, wkd, week_code)]
        counts.setdefault((loja, wkd), []).append(len(siglas))
    out['loja_day_counts'] = counts

    # ── Pair affinity ─────────────────────────────────────────────────────────
    pair_w = {}
    pair_seen = set()
    for (week_code, dia, loja), windows in same_day_groups.items():
        wkd = dia_label_to_idx(dia)
        if wkd is None:
            continue
        w = _weight(rank_by_code[week_code])
        siglas_sorted = sorted(windows)
        for i in range(len(siglas_sorted)):
            for j in range(i + 1, len(siglas_sorted)):
                a, b = siglas_sorted[i], siglas_sorted[j]
                if not any(sa < eb and sb < ea
                           for sa, ea in windows[a] for sb, eb in windows[b]):
                    continue
                pkey = (a, b, loja, wkd)
                observation = (week_code,) + pkey
                if observation not in pair_seen:
                    pair_seen.add(observation)
                    pair_w[pkey] = pair_w.get(pkey, 0) + w
    out['pair_affinity'] = {
        k: min(1.0, v / total_weight_per_week)
        for k, v in pair_w.items()
    }

    # ── Typical people-per-week on each (loja, weekday, exact band) ───────────
    # Lets the empties panel recommend time bands that match how the store is
    # actually run (e.g. one 08:00–16:30 opener + one 14:00 closer) instead of
    # arbitrary near-duplicate shifts. Apply the same recency weights as worker
    # affinity, so obsolete bands cannot outweigh the recent operating pattern.
    band_weight = {}
    for (week_code, loja, wkd, horario), siglas in band_people.items():
        key = (loja, wkd, horario)
        band_weight[key] = band_weight.get(key, 0) + len(siglas) * _weight(rank_by_code[week_code])
    out['band_typical'] = {k: v / total_weight_per_week for k, v in band_weight.items()}

    return out


def _historical_affinity(sigla: str, loja: str, weekday_idx, slot: str,
                          prior: dict) -> float:
    """Returns the recency-weighted historical affinity (0..1) for this candidate
    on this exact (loja, weekday, slot) cell. Returns 0 if too little data."""
    if not prior or prior.get('n_weeks', 0) < HISTORICAL_MIN_WEEKS:
        return 0.0
    if weekday_idx is None:
        return 0.0
    return prior['affinity'].get((sigla, loja, weekday_idx, slot), 0.0)


def p75_ceiling(values) -> int:
    """Return the 75th percentile of `values`, rounded UP to an integer.

    Used for proposing headcount targets from historical observations. Median
    matches the typical case, which under-staffs for busy days; P75-ceiling
    biases toward the busy case (the operational cost of being short-staffed
    on a busy Saturday is much higher than slightly over-staffing on a slow
    Tuesday). Less skewed by outliers than using MAX directly.

    Examples:
      [1, 3, 3, 3]    → 3   (median 3, P75 3)
      [2, 2, 3, 5]    → 4   (median 2.5, P75 3.5, ceil 4)
      [1, 2, 2, 2]    → 2   (median 2, P75 2)
      [5, 5, 5, 8]    → 6   (median 5, P75 5.75, ceil 6)
    """
    import math
    if not values:
        return 0
    s = sorted(values)
    n = len(s)
    # Linear-interpolated percentile (matches numpy's 'linear' method)
    idx = (n - 1) * 0.75
    lo = int(idx)
    hi = min(lo + 1, n - 1)
    frac = idx - lo
    p75 = s[lo] * (1 - frac) + s[hi] * frac
    return math.ceil(p75)


def _score_candidate(loja: str, slot: str, d_info: dict,
                     weekly_hours: float, contrato_hours: int,
                     is_turnos: bool,
                     historical_affinity: float = 0.0) -> tuple:
    """Score a candidate for an empty slot. Higher = better fit.

    Returns (score, reason_word). Reason_word is a single short Portuguese token
    that captures the dominant factor — used in the "Sugestão" pin to explain
    the pick. Returns (None, None) if this candidate fails a hard filter that
    the caller didn't pre-apply (currently caller pre-applies all hard filters,
    so this is defensive only).

    Ranking philosophy:
      1. Preference matches dominate everything else
      2. Then plain availability
      3. Historical pattern (where they usually worked) — capped so it can't
         override explicit preference
      4. Workload balance — under-hours people get a small bonus, over-hours
         people get a small penalty
    """
    if not d_info:
        # "Sem resposta" — neutral starting point.
        score = 0
        reason = 'sem resposta'
    elif d_info.get('tipo') == 'preferencia':
        # Slots are a hard availability filter applied by the caller, so by the
        # time we score, `slot` is already one the worker can do. Preference here
        # is purely about the STORE.
        lojas = d_info.get('lojas') or set()
        if lojas:
            score, reason = (1000, 'prefere') if loja in lojas else (500, 'quer trabalhar')
        else:
            score, reason = 500, 'quer trabalhar'
    elif d_info.get('tipo') == 'disponivel':
        score, reason = 300, 'disponível'
    elif d_info.get('tipo') == 'indisponivel':
        # Hard filter. The caller excludes these before scoring; kept here as a
        # guard so a direct caller can never suggest someone who said no.
        return (None, None)
    elif d_info.get('tipo') == 'extra' or d_info.get('descarga'):
        # Descarga opt-in. Used to be its own tipo ('extra'); since the descarga
        # rework it's an independent flag on a row whose tipo is empty.
        score, reason = 150, 'descarga'
    else:
        # A row with no regular tipo — e.g. the worker submitted the form but
        # left this day blank. Same standing as not answering at all: dropping
        # them here would make submitting worse than staying silent.
        score, reason = 0, 'sem resposta'

    # Historical bonus (Tier-1 ML), capped at +250.
    hist_bonus = int(historical_affinity * HISTORICAL_MAX_BONUS)
    score += hist_bonus
    if hist_bonus >= 100 and reason in ('sem resposta', 'disponível', 'descarga'):
        reason = 'histórico'

    # Workload balance (only for contracted workers).
    if not is_turnos and contrato_hours > 0:
        gap = contrato_hours - weekly_hours
        adj = max(-200, min(200, int(gap * 50)))
        score += adj

    return (score, reason)


def rest_day_penalty(days_worked: int) -> int:
    """Score penalty for stacking a 6th (or 7th) working day onto someone.

    Sub-dominant by design: it can flip ties and near-ties between otherwise
    equal candidates, but never overrides an explicit store preference
    (1000) or plain availability gaps (300+). Protects the weekly rest day
    without hard-blocking — the supervisor can still assign manually.
    """
    return -150 if days_worked >= 5 else 0


def _score_best_slot_for_loja(loja, d_info, weekly_hrs, contrato_hrs, is_turnos,
                                sigla='', weekday_idx=None, prior=None,
                                return_slot=False, avail_slots=None,
                                penal_slots=None, penal_amount=120):
    """Score a candidate for working a given loja on a given day, taking the
    max across all slot options. Used by api_empties when we don't yet know
    which time-band the supervisor will pick.

    By default returns (score, reason) for backward compatibility. Pass
    return_slot=True to also get the winning slot code, i.e.
    (score, reason, slot) — used to recommend a concrete time band so the
    supervisor can assign in one click instead of opening the slot picker.

    `penal_slots` applies a per-slot penalty (default −120) — used for
    "clopening" protection: someone who closed late yesterday gets their
    manhã option penalised today, steering their best slot toward tarde/noite
    without excluding them outright.
    """
    # HARD availability filter: if the worker named the slots they can do, only
    # those slots are eligible. No slots named = available any time (legacy).
    avail = avail_slots or set()
    best = (None, None, '')
    for slot in ('manha', 'tarde', 'noite', ''):
        if avail and (not slot or slot not in avail):
            continue   # worker is not available in this time band
        hist = _historical_affinity(sigla, loja, weekday_idx, slot, prior or {}) if sigla else 0.0
        s, r = _score_candidate(loja, slot, d_info, weekly_hrs, contrato_hrs, is_turnos,
                                historical_affinity=hist)
        if s is None:
            continue
        if penal_slots and slot in penal_slots:
            s -= penal_amount
        if best[0] is None or s > best[0]:
            best = (s, r, slot)
    if return_slot:
        return best
    return (best[0], best[1])
