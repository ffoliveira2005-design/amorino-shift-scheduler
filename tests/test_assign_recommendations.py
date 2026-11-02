"""Tests for the one-click 'recommended time' on supervisor assign suggestions.

api_empties now returns a `horario` on each suggestion: the time band the
supervisor would assign with a single click. It must respect the person's slot
preference and never collide with a band they're already on.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client


def _week(app):
    with app.app.app_context():
        a = app.get_semana_ativa()
        return a.codigo, json.loads(a.dias_json or '[]')


def _set_target(app, loja, dia_idx, n):
    with app.app.app_context():
        app.db.session.query(app.LojaHeadcountTarget).filter_by(
            loja=loja, dia_idx=dia_idx).delete()
        app.db.session.add(app.LojaHeadcountTarget(loja=loja, dia_idx=dia_idx, target=n))
        app.db.session.commit()


def test_scoring_return_slot_is_backward_compatible():
    """_score_best_slot_for_loja returns a 2-tuple by default, 3-tuple on demand."""
    from helpers.scoring import _score_best_slot_for_loja
    two = _score_best_slot_for_loja('carmelitas', {'tipo': 'disponivel'}, 0, 0, False)
    assert len(two) == 2
    three = _score_best_slot_for_loja('carmelitas', {'tipo': 'disponivel'}, 0, 0, False,
                                      return_slot=True)
    assert len(three) == 3
    assert three[0] == two[0] and three[1] == two[1]   # score+reason unchanged


def test_score_candidate_handles_every_availability_shape():
    """No availability shape may silently drop a candidate except 'indisponivel'."""
    from helpers.scoring import _score_candidate
    call = lambda info: _score_candidate('carmelitas', 'manha', info, 0, 0, False)

    assert call(None)                    == (0, 'sem resposta')
    assert call({})                      == (0, 'sem resposta')
    assert call({'tipo': ''})            == (0, 'sem resposta')
    assert call({'tipo': 'disponivel'})  == (300, 'disponível')
    # Descarga opt-in, old (tipo) and current (flag on an empty-tipo row) shapes
    assert call({'tipo': 'extra'})                     == (150, 'descarga')
    assert call({'tipo': '', 'descarga': True})        == (150, 'descarga')
    # A real availability answer still wins over the descarga flag
    assert call({'tipo': 'disponivel', 'descarga': True}) == (300, 'disponível')
    # 'indisponivel' stays a hard filter
    assert call({'tipo': 'indisponivel'}) == (None, None)


def test_suggestions_carry_a_recommended_horario(app, supervisor_client):
    codigo, dias = _week(app)
    day = dias[0]['label']
    from helpers.time_helpers import dia_label_to_idx
    _set_target(app, 'carmelitas', dia_label_to_idx(day), 3)

    j = supervisor_client.get(f'/api/empties/{codigo}/{day}').get_json()
    carm = next(L for L in j['lojas'] if L['loja'] == 'carmelitas')
    assert carm['missing'] >= 1
    assert carm['suggestions'], 'expected suggestions for an understaffed loja'
    # Every suggestion has the key, and the recommended band is a real horario
    from config import HORARIOS_BASE
    valid = set(HORARIOS_BASE['carmelitas'])
    for s in carm['suggestions']:
        assert 'horario' in s
        if s['horario'] is not None:
            assert s['horario'] in valid, f"{s['horario']} not a carmelitas band"


def test_recommended_horario_matches_slot_preference(app, supervisor_client):
    """Someone who prefers the 'tarde' slot should be recommended a tarde band
    (a shift starting 12:00–14:59)."""
    codigo, dias = _week(app)
    day = dias[0]['label']
    from helpers.time_helpers import dia_label_to_idx, horario_to_slot
    _set_target(app, 'carmelitas', dia_label_to_idx(day), 2)
    with app.app.app_context():
        app.db.session.add(app.Disponibilidade(
            semana=codigo, funcionario='CV', dia=day, tipo='preferencia',
            lojas_pref='carmelitas', slots_pref='tarde'))
        app.db.session.commit()

    j = supervisor_client.get(f'/api/empties/{codigo}/{day}').get_json()
    carm = next(L for L in j['lojas'] if L['loja'] == 'carmelitas')
    cv = next((s for s in carm['suggestions'] if s['sigla'] == 'CV'), None)
    assert cv is not None, 'CV should be suggested (she prefers this loja)'
    assert cv['horario'] is not None
    assert horario_to_slot(cv['horario']) == 'tarde', \
        f"expected a tarde band, got {cv['horario']}"


def test_clicking_recommended_time_assigns_without_conflict(app, supervisor_client):
    """End-to-end: the recommended band is directly assignable (no 409)."""
    codigo, dias = _week(app)
    day = dias[0]['label']
    from helpers.time_helpers import dia_label_to_idx
    _set_target(app, 'gaia', dia_label_to_idx(day), 2)

    j = supervisor_client.get(f'/api/empties/{codigo}/{day}').get_json()
    gaia = next(L for L in j['lojas'] if L['loja'] == 'gaia')
    sug = next((s for s in gaia['suggestions'] if s['horario']), None)
    assert sug, 'expected at least one suggestion with a recommended band'

    # Simulate the one-click assign the chip performs.
    r = supervisor_client.post('/api/turno/add', json={
        'loja': 'gaia', 'horario': sug['horario'], 'dia': day,
        'funcionario': sug['sigla'], 'nota': '', 'semana': codigo,
    })
    assert r.status_code == 200, f"recommended band should assign cleanly: {r.get_json()}"
    assert r.get_json()['ok'] is True


def test_recommended_horario_avoids_band_already_used(app, supervisor_client):
    """If the top candidate is already on their best band, the recommendation
    must move to a different free band (so the one-click assign won't 409)."""
    codigo, dias = _week(app)
    day = dias[0]['label']
    from helpers.time_helpers import dia_label_to_idx
    from config import HORARIOS_BASE
    _set_target(app, 'carmelitas', dia_label_to_idx(day), 4)

    # Put CV on the first carmelitas band already.
    first_band = HORARIOS_BASE['carmelitas'][0]
    with app.app.app_context():
        app.db.session.add(app.Turno(semana=codigo, dia=day, loja='carmelitas',
                                     horario=first_band, funcionario='CV'))
        app.db.session.commit()

    j = supervisor_client.get(f'/api/empties/{codigo}/{day}').get_json()
    carm = next(L for L in j['lojas'] if L['loja'] == 'carmelitas')
    # CV is already on the loja so she won't be re-suggested; but for ANY
    # suggestion, the recommended band must not be one they already hold.
    # (Stronger invariant: the recommendation never collides.)
    for s in carm['suggestions']:
        if s['horario'] is None:
            continue
        # Re-assign attempt must succeed → proves no collision baked in
        r = supervisor_client.post('/api/turno/add', json={
            'loja': 'carmelitas', 'horario': s['horario'], 'dia': day,
            'funcionario': s['sigla'], 'nota': '', 'semana': codigo,
        })
        assert r.status_code == 200, \
            f"recommended band for {s['sigla']} collided: {r.get_json()}"
