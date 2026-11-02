"""Tests for the September 2026 workflow & design review.

Covers:
  - Week labels: the repair of labels written by the old importer ("27 a 3 de
    Maio", "Marco"), and the importer now building weeks with the app's own
    helpers (cross-month labels, public holidays).
  - Conflitos: lists come out in triage order, and every row links to where
    the issue gets fixed.
  - Trocas: whoever has something to decide sees that bucket first.
  - Supervisor dashboard: honest "today" panel when no week covers today, no
    duplicate logout, "Copiar semana" defaults to previous week → this week.
  - Employee home: the week pill says which week it is.
  - The global [hidden] rule, and a guard against the informal register
    creeping back into the UI.
"""
import importlib.util
import json
import os
import re
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from test_basics import app, client, supervisor_client, employee_client, PROJECT_ROOT


def _make_week(app_mod, monday, label=None):
    sem = app_mod.SemanaConfig(codigo=app_mod.week_codigo(monday),
                               label=label or app_mod.week_label(monday),
                               dias_json=json.dumps(app_mod.week_from_monday(monday)),
                               ativa=False)
    app_mod.db.session.add(sem)
    app_mod.db.session.commit()
    return sem


# ───────────────────────────────────────────────────────────────────────────────
# Week labels
# ───────────────────────────────────────────────────────────────────────────────

def test_month_names_are_spelled_correctly(app):
    assert app.week_label(date(2027, 3, 1)) == '1 a 7 de Março'
    assert app.week_label(date(2026, 4, 27)) == '27 de Abril a 3 de Maio'


def test_repair_week_labels_fixes_only_machine_written_shapes(app):
    with app.app.app_context():
        cross = _make_week(app, date(2026, 4, 27), label='27 a 3 de Maio')
        march = _make_week(app, date(2027, 3, 1), label='1 a 7 de Marco')
        mine  = _make_week(app, date(2027, 3, 8), label='Semana de teste')
        ids = (cross.id, march.id, mine.id)

        assert app._repair_week_labels() == 2
        c, m, x = (app.db.session.get(app.SemanaConfig, i) for i in ids)
        assert c.label == '27 de Abril a 3 de Maio'
        assert m.label == '1 a 7 de Março'
        assert x.label == 'Semana de teste', 'a chosen label must never be rewritten'
        assert app._repair_week_labels() == 0, 'must be a no-op once fixed'


def test_importer_builds_weeks_with_the_app_helpers():
    """The importer hand-rolled its label (wrong across months) and wrote every
    day as feriado=False, so imported weeks never showed public holidays."""
    spec = importlib.util.spec_from_file_location(
        'import_history_under_test',
        os.path.join(PROJECT_ROOT, 'scripts', 'import_history.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    codigo, label, dias_json = mod.week_record(date(2026, 4, 27))
    assert codigo == '27-3mai'
    assert label == '27 de Abril a 3 de Maio'
    dias = json.loads(dias_json)
    maio_1 = next(d for d in dias if d['date'] == '2026-05-01')
    assert maio_1['feriado'] is True and maio_1['feriado_nome']


# ───────────────────────────────────────────────────────────────────────────────
# Conflitos
# ───────────────────────────────────────────────────────────────────────────────

def _conflitos_week_with_overtime(app):
    """Two contract workers over their hours; the SMALLER overrun is inserted
    first, so dict order alone would list it first."""
    with app.app.app_context():
        sem = _make_week(app, date(2027, 3, 1))
        dias = [d['label'] for d in json.loads(sem.dias_json)]
        for sig in ('ZZB', 'ZZA'):
            app.db.session.add(app.Funcionario(sigla=sig, nome=f'Pessoa {sig}',
                                               horas_contrato=10,
                                               tipo_contrato='contrato'))
        for d in dias[:2]:            # ZZB: 2 × 8h = 16h → +6h
            app.db.session.add(app.Turno(semana=sem.codigo, dia=d, loja='gaia',
                                         horario='08:00-16:30', funcionario='ZZB'))
        for d in dias[:3]:            # ZZA: 3 × 8h = 24h → +14h
            app.db.session.add(app.Turno(semana=sem.codigo, dia=d, loja='mbs',
                                         horario='10:00-18:30', funcionario='ZZA'))
        app.db.session.commit()
        return sem.codigo


def test_conflitos_lists_worst_overrun_first(app, supervisor_client):
    codigo = _conflitos_week_with_overtime(app)
    body = supervisor_client.get(f'/supervisor/conflitos?semana={codigo}').data.decode('utf-8')
    over = body[body.index('Excesso de horas'):]
    assert over.index('ZZA') < over.index('ZZB'), '+14h must be listed above +6h'


def test_conflitos_rows_link_to_where_they_get_fixed(app, supervisor_client):
    codigo = _conflitos_week_with_overtime(app)
    with app.app.app_context():
        # A trainee alone at Norte Shopping → an "abertura sozinho" issue.
        app.db.session.add(app.Funcionario(sigla='ZZC', nome='Pessoa ZZC',
                                           horas_contrato=0, tipo_contrato='turnos',
                                           rank='trainee'))
        dia0 = json.loads(app.SemanaConfig.query.filter_by(codigo=codigo)
                          .first().dias_json)[0]['label']
        app.db.session.add(app.Turno(semana=codigo, dia=dia0, loja='norte',
                                     horario='14:00-22:30', funcionario='ZZC'))
        app.db.session.commit()
    body = supervisor_client.get(f'/supervisor/conflitos?semana={codigo}').data.decode('utf-8')
    # Hours issues open the person's week; per-shift issues open the store.
    # (Literal "&" in a template is not escaped — browsers read it as-is.)
    assert f'href="/minha-escala?sigla=ZZA&semana={codigo}"' in body
    assert f'href="/supervisor/escala?semana={codigo}#store-norte"' in body


# ───────────────────────────────────────────────────────────────────────────────
# Trocas
# ───────────────────────────────────────────────────────────────────────────────

_CENTRAL_TITLE = 'tr-sec-title">Central<'


def test_trocas_supervisor_sees_approvals_before_the_central(supervisor_client):
    body = supervisor_client.get('/trocas').data.decode('utf-8')
    assert body.index('A aprovar') < body.index(_CENTRAL_TITLE)


def test_trocas_employee_with_nothing_to_decide_sees_the_central_first(employee_client):
    """With nothing to decide there is no empty "Por decidir" box at all — on a
    phone two empty sections filled the first screen — and the central leads."""
    body = employee_client.get('/trocas').data.decode('utf-8')
    assert 'Por decidir' not in body
    assert _CENTRAL_TITLE in body
    assert 'enviaste' not in body


# ───────────────────────────────────────────────────────────────────────────────
# Supervisor dashboard
# ───────────────────────────────────────────────────────────────────────────────

def _push_all_weeks_into_the_past(app):
    shift = timedelta(days=35)
    for sem in app.SemanaConfig.query.all():
        dias = json.loads(sem.dias_json or '[]')
        for d in dias:
            if d.get('date'):
                d['date'] = (date.fromisoformat(d['date']) - shift).isoformat()
        sem.dias_json = json.dumps(dias)
    app.db.session.commit()


def test_today_panel_without_a_week_for_today_is_not_live(app, supervisor_client):
    """No week covers today → no "em direto" pulse and no zeroed stats; the
    panel names today's date instead."""
    with app.app.app_context():
        _push_all_weeks_into_the_past(app)
        panel = app._build_supervisor_today_panel()
        assert panel['today_label'] is None
        assert panel['today_long'] == app.date_long(date.today())
    body = supervisor_client.get('/supervisor').data.decode('utf-8')
    assert 'Esta semana · em direto' not in body
    assert app.date_long(date.today()) in body


def test_dashboard_has_a_single_logout(supervisor_client):
    body = supervisor_client.get('/supervisor').data.decode('utf-8')
    assert '/supervisor/logout' not in body   # the nav's "Sair" is the one


def test_copiar_semana_defaults_to_previous_week(app, supervisor_client):
    with app.app.app_context():
        ativa = app.get_semana_ativa()
        monday = date.fromisoformat(ativa.dias_list[0]['date'])
        prev = _make_week(app, monday - timedelta(weeks=1)).codigo
    body = supervisor_client.get('/supervisor').data.decode('utf-8')
    src = re.search(r'<select id="cp-src">(.*?)</select>', body, re.S).group(1)
    selected = re.search(r'<option value="([^"]+)" selected', src)
    assert selected and selected.group(1) == prev


# ───────────────────────────────────────────────────────────────────────────────
# Supervisor escala editor
# ───────────────────────────────────────────────────────────────────────────────

def test_escala_state_is_declared_before_the_supervisor_init_uses_it(supervisor_client):
    """The supervisor init runs setFilter('todos') at the top level. With the
    `let activeFilter` below that call, the page threw a ReferenceError (TDZ)
    on every load and everything after it never ran: search, the type filter,
    the keyboard shortcuts, the store tabs. Present since the first commit —
    tests render HTML but don't run it, so this pins the order instead."""
    html = supervisor_client.get('/supervisor/escala').data.decode('utf-8')
    call = html.index("setFilter('todos');")
    for decl in ("let activeFilter", "let acIdx"):
        assert html.index(decl) < call, f'{decl} is declared after it is first used'


# ───────────────────────────────────────────────────────────────────────────────
# Employee home
# ───────────────────────────────────────────────────────────────────────────────

def test_home_week_pill_names_the_current_week(employee_client):
    # The seed's active week is the one containing today.
    body = employee_client.get('/').data.decode('utf-8')
    assert 'Semana atual ·' in body
    assert 'Boa madrugada' not in body
    assert '★' not in body


# ───────────────────────────────────────────────────────────────────────────────
# Global rules
# ───────────────────────────────────────────────────────────────────────────────

def test_hidden_attribute_beats_component_display_rules(client):
    """`.draft-banner { display: flex }` used to cancel the `hidden` attribute,
    so every employee saw a false "alterações recuperadas" banner."""
    css = client.get('/static/app.css').data.decode('utf-8')
    assert '[hidden] { display: none !important; }' in css


# Phrases from the informal "tu" register that were removed from the UI in this
# review. The owner requires impersonal, professional copy — see CLAUDE.md.
_INFORMAL = [
    'Preenche a', '-- Escolhe --', 'Para ti', 'que enviaste', 'Copia a mensagem',
    'Ativa uma semana', 'Seleciona as', 'Escreve um', 'Clica para',
    'extra adicionais usa', 'Não tens permissão', 'Tenta novamente',
    'a tua sigla', 'adiciona \'Troca', 'guarda as alterações', 'Boa madrugada',
    'alteracao(oes)', 'toca para atribuir', 'Guarda para confirmar',
    'precisas de', 'te candidataste',
    # Trocas review (September 2026): server messages and the old dialogs.
    'Podes negar', 'não é para ti', 'recusou-te', 'Tu ficas sem', 'Procuras quem',
]


def test_informal_register_does_not_come_back():
    files = [os.path.join(PROJECT_ROOT, 'app.py')] + [
        os.path.join(PROJECT_ROOT, 'templates', f)
        for f in os.listdir(os.path.join(PROJECT_ROOT, 'templates'))
        if f.endswith('.html')]
    hits = []
    for path in files:
        src = open(path, encoding='utf-8').read()
        hits += [f'{os.path.basename(path)}: {p!r}' for p in _INFORMAL if p in src]
    assert not hits, 'informal copy in the UI:\n' + '\n'.join(hits)


# ───────────────────────────────────────────────────────────────────────────────
# Layout regressions found in the trocas review
# ───────────────────────────────────────────────────────────────────────────────

def test_page_entrance_animation_does_not_keep_a_transform():
    """main.page fades in with a small translateY. With fill-mode `both` the
    last keyframe stayed applied — and `transform: none` interpolated against
    translateY(4px) is an identity MATRIX, not `none` — which made <main> the
    containing block of every position:fixed element inside it. Modals opened
    below the fold on long pages, and the escala's assignment panel and
    quick-add popover scrolled off-screen with the page."""
    css = open(os.path.join(PROJECT_ROOT, 'static', 'app.css'), encoding='utf-8').read()
    rule = re.search(r'main\.page\s*\{\s*animation:\s*([^;]+);', css)
    assert rule, 'main.page entrance animation not found'
    assert not re.search(r'\b(both|forwards)\b', rule.group(1)), rule.group(1)


def test_trocas_comes_before_equipa_in_the_nav(employee_client):
    """On a 375px phone only the first four nav links fit; the one carrying
    the pending-swaps badge must be among them."""
    body = employee_client.get('/minha-escala').data.decode('utf-8')
    nav = body[body.index('class="nav-links"'):body.index('class="nav-right"')]
    assert nav.index('href="/trocas"') < nav.index('href="/equipa"')


def test_supervisor_availability_page_counts_descarga_and_shows_evening(app, supervisor_client):
    """The page counted descarga as tipo == 'extra' — a tipo converted away at
    startup — so its "Descarga" total was always 0; an opt-in on a day marked
    available was never shown; and 'noite' was missing from the slot labels,
    so an evening preference silently disappeared."""
    monday = date.today() + timedelta(days=14 - date.today().weekday())
    with app.app.app_context():
        sem = _make_week(app, monday)
        dia = json.loads(sem.dias_json)[0]['label']
        app.db.session.add(app.Disponibilidade(
            semana=sem.codigo, funcionario='FO', dia=dia, tipo='disponivel',
            slots_pref='noite', descarga_opt=True))
        app.db.session.commit()
        codigo = sem.codigo
    body = supervisor_client.get(f'/supervisor/disponibilidades?semana={codigo}').data.decode('utf-8')
    stat = re.search(r'<div class="sp-val">(\d+)</div><div class="sp-lbl">Descarga</div>', body)
    assert stat and stat.group(1) == '1'
    assert '<span class="pref-chip is-slot">Noite</span>' in body
    assert '<span class="tipo-pill p-extra">Descarga</span>' in body


def test_dashboard_does_not_nag_about_a_week_that_has_ended(app, supervisor_client):
    """With the active week over, the dashboard kept counting its unfilled
    seats and missing availability answers as things to do (and badging the
    tabs). The one thing to do then is activate the next week."""
    with app.app.app_context():
        _push_all_weeks_into_the_past(app)
    body = supervisor_client.get('/supervisor').data.decode('utf-8')
    assert 'A semana ativa já terminou' in body
    assert 'sem resposta de disponibilidade' not in body
    assert 'lugares por preencher</strong>' not in body
    assert 'tab-badge alert' not in body
    assert 'Semana terminada:' in body


def test_availability_never_asks_for_a_week_that_has_ended(app, employee_client):
    """With the active week over, /disponibilidade asked everyone to fill in a
    week already in the past."""
    with app.app.app_context():
        _push_all_weeks_into_the_past(app)
    body = employee_client.get('/disponibilidade').data.decode('utf-8')
    assert 'Sem semana aberta para disponibilidade' in body
