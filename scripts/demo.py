"""Start a repeatable CEO demonstration using only fictional, disposable data.

The live database is never opened, copied, or modified. The database location
is deliberately fixed; AMORINO_DB_PATH supplied by the caller is ignored.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta, timezone
import importlib
import json
import os
from pathlib import Path
import secrets
import socket
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "tmp" / "ceo-demo"
DEMO_DB = DEMO_DIR / "amorino-demo.db"
HOST = "127.0.0.1"
PORT = 5001

# Public, disposable credentials for this localhost-only demonstration.
# They must never be reused for real users or a production deployment.
SUPERVISOR_PIN = "Demo-CEO-8V4p7R2x"
EMPLOYEE_PIN = "Demo-Staff-6N3w9K5q"
EMPLOYEE_SIGLA = "DCA04"
DONATION_APPLICANTS = ("DCA03", "DCA05")
DONATION_DAY_INDEX = 4  # Next Friday; independent of the prepared Monday swap.

FICTIONAL_TEAMS = {
    "carmelitas": ("DCA", ["Inês Monteiro", "Beatriz Almeida", "Tiago Reis",
        "Clara Matos", "Gonçalo Faria", "Leonor Barros", "Daniel Azevedo",
        "Mafalda Costa", "Tomás Ribeiro"]),
    "clerigos": ("DCL", ["Catarina Moura", "André Cunha", "Mariana Pires",
        "Rafael Seabra", "Sofia Neves", "João Tavares", "Diana Lopes",
        "Pedro Rocha", "Francisca Melo"]),
    "norte": ("DNS", ["Miguel Loureiro", "Carolina Duarte", "Diogo Pinto",
        "Rita Andrade", "Henrique Maia", "Marta Freitas", "Afonso Campos",
        "Matilde Correia", "Rodrigo Cardoso"]),
    "gaia": ("DGA", ["Filipa Coelho", "Nuno Moreira", "Sara Nogueira",
        "Luís Miranda", "Teresa Marques", "Bruno Valente", "Ana Esteves",
        "Vasco Sousa", "Lara Gomes"]),
    "mbs": ("DMB", ["Mónica Carvalho", "Hugo Fernandes", "Patrícia Martins",
        "Ricardo Lemos", "Isabel Viana", "David Oliveira", "Adriana Silva",
        "Salvador Alves", "Benedita Ramos"]),
}

# Each block is eight planned hours under the app's existing lunch-break rules.
# Opening and closing always have an appropriately qualified employee.
DEMO_HORARIOS = {
    "carmelitas": ["08:00-16:30", "10:00-18:30", "12:30-21:00",
                   "14:00-22:30", "16:00-00:30"],
    "clerigos": ["08:00-16:30", "09:00-17:30", "11:30-20:00",
                 "14:00-22:30", "16:00-00:30"],
    "norte": ["09:00-17:30", "10:30-19:00", "13:00-21:30",
              "14:00-22:30", "15:30-00:00"],
    "gaia": ["08:00-16:30", "10:30-19:00", "11:00-19:30",
             "14:00-22:30", "16:00-00:30"],
    "mbs": ["10:00-18:30", "10:00-18:30", "11:00-19:30",
            "14:00-22:30", "16:00-00:30"],
}

# Employee indices for the three middle shifts, Monday to Sunday.
# Result: manager 5 days / 40h; six senior/staff members 4 days / 32h;
# two trainees 3 days / 24h. Nobody works more than five days per week.
MIDDLE_ROTATION = [
    (2, 3, 6), (4, 5, 8), (3, 6, 7), (4, 5, 8),
    (3, 6, 7), (4, 5, 8), (5, 6, 7),
]
NEXT_WEEK_GAPS = {("carmelitas", 2, 2), ("clerigos", 4, 2), ("norte", 5, 2)}


def lisbon_today() -> date:
    """Use Lisbon's calendar date even on a Windows host without tzdata."""
    now = datetime.now(timezone.utc)
    try:
        return now.astimezone(ZoneInfo("Europe/Lisbon")).date()
    except ZoneInfoNotFoundError:
        # Portugal's current EU clock rule: last Sunday of March/October,
        # transition at 01:00 UTC. No extra dependency is needed on Windows.
        def transition(month: int) -> datetime:
            last = date(now.year, month + 1, 1) - timedelta(days=1)
            sunday = last - timedelta(days=(last.weekday() + 1) % 7)
            return datetime.combine(sunday, datetime.min.time(), timezone.utc).replace(hour=1)

        summer = transition(3) <= now < transition(10)
        return (now + timedelta(hours=int(summer))).date()


def _validated_demo_path() -> Path:
    """Reject symlinks/junctions before creating or resetting demo files."""
    expected = ROOT / "tmp" / "ceo-demo" / "amorino-demo.db"
    for path in (ROOT / "tmp", DEMO_DIR, DEMO_DB):
        if path.exists() and path.resolve() != path:
            raise RuntimeError(f"Demo path redirects outside its dedicated location: {path}")
    if DEMO_DB != expected or DEMO_DB.resolve() != expected:
        raise RuntimeError("Refusing to reset a database outside tmp/ceo-demo/amorino-demo.db.")
    if DEMO_DB == ROOT / "amorino.db":
        raise RuntimeError("The demonstration must never use the live database.")
    return DEMO_DB


@contextmanager
def _demo_lock():
    """An OS lock prevents resetting a database used by another demo process."""
    _validated_demo_path()
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = DEMO_DIR / "demo.lock"
    if lock_path.resolve() != lock_path:
        raise RuntimeError("Refusing to open a redirected demo lock file.")
    with lock_path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("A demo is already running. Stop it with Ctrl+C before restarting.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _check_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, PORT))
        except OSError as exc:
            raise RuntimeError("Port 5001 is occupied. Stop the existing local demo before starting another.") from exc


def _reset_disposable_database() -> Path:
    path = _validated_demo_path()
    # Delete only these exact demo files, never directories or glob matches.
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(str(path) + suffix)
        if candidate.exists() and candidate.resolve() != candidate:
            raise RuntimeError(f"Refusing to remove a redirected demo file: {candidate}")
        candidate.unlink(missing_ok=True)
    return path


def _load_application(path: Path):
    # app.py creates/seeds the database at import time. These must be set first.
    os.environ.update({
        "AMORINO_DB_PATH": str(path),
        "AMORINO_SECRET_KEY": secrets.token_hex(32),
        "AMORINO_SUPERVISOR_PIN": SUPERVISOR_PIN,
        "AMORINO_SEED": "0",  # The presentation never needs the source roster.
        "AMORINO_HTTPS": "0", "AMORINO_TRUST_PROXY": "0", "AMORINO_DEBUG": "0",
    })
    sys.path.insert(0, str(ROOT))
    if "app" in sys.modules:
        raise RuntimeError("Run the demo launcher in a fresh Python process, before importing app.py.")
    module = importlib.import_module("app")
    module.app.config.update(
        AMORINO_DEMO=True,
        TEMPLATES_AUTO_RELOAD=True,
        # A demo on port 5001 must not overwrite a live local app's cookie.
        SESSION_COOKIE_NAME="amorino_demo_session",
    )
    module.app.jinja_env.auto_reload = True
    with module.app.app_context():
        actual = Path(module.db.engine.url.database).resolve()
        if actual != path.resolve():
            raise RuntimeError("The app did not connect to the dedicated demo database.")
    return module


def _populate(module) -> dict:
    from config import HORARIOS_BASE, LOJAS
    from helpers.publication import release_week
    from helpers.time_helpers import week_codigo, week_from_monday, week_label
    from models import SchemaMigration

    db = module.db
    today = lisbon_today()
    monday = today - timedelta(days=today.weekday())
    weeks = []
    stamp = datetime.now(timezone.utc) - timedelta(hours=3)
    with module.app.app_context():
        if Path(db.engine.url.database).resolve() != _validated_demo_path():
            raise RuntimeError("Refusing to replace seed rows in a non-demo database.")
        # The normal app startup has initialized its schema and historical seeds.
        # Replace only the contents of our newly created disposable database.
        for table in reversed(db.metadata.sorted_tables):
            db.session.execute(table.delete())
        # The disposable reset also removed the one-time migration marker.
        # Recreate it so later imports do not mistake fictional drafts for
        # legacy authorised schedules and implicitly publish them.
        for name in ('schedule_publication_v1', 'donation_admin_review_v1'):
            db.session.add(SchemaMigration(name=name, applied_at=datetime.now(timezone.utc)))
        db.session.commit()

        employee_hash = module.hash_pin(EMPLOYEE_PIN)
        for loja, (prefix, names) in FICTIONAL_TEAMS.items():
            for i, name in enumerate(names):
                rank = ("store_manager" if i == 0 else "shift_supervisor" if i < 3
                        else "trainee" if i >= 7 else "staff")
                db.session.add(module.Funcionario(
                    sigla=f"{prefix}{i + 1:02}", nome=name, pin=employee_hash,
                    horas_contrato=40 if i == 0 or f"{prefix}{i + 1:02}" in DONATION_APPLICANTS
                    else 24 if i >= 7 else 32,
                    tipo_contrato="contrato", rank=rank,
                    loja_gerida=loja if i == 0 else "",
                ))
            for ordem, horario in enumerate(dict.fromkeys(DEMO_HORARIOS[loja])):
                if horario not in HORARIOS_BASE[loja]:
                    db.session.add(module.HorarioBloco(loja=loja, horario=horario, ordem=ordem))
        db.session.add(module.Funcionario(
            sigla="DFX01", nome="Duarte Teixeira", pin=employee_hash,
            horas_contrato=0, tipo_contrato="turnos", rank="staff",
        ))
        for loja in LOJAS:
            for day_idx in range(7):
                db.session.add(module.LojaHeadcountTarget(loja=loja["id"], dia_idx=day_idx, target=5))

        for week_idx in range(2):
            start = monday + timedelta(weeks=week_idx)
            days = week_from_monday(start)
            code = week_codigo(start)
            delivery_config = {
                days[1]["label"]: {"carmelitas": "08:00-12:00"},
                days[2]["label"]: {"clerigos": "08:00-12:00"},
                days[3]["label"]: {"mbs": "10:00-14:00"},
            }
            week = module.SemanaConfig(
                codigo=code, label=week_label(start), dias_json=json.dumps(days),
                ativa=bool(week_idx), descarga_dias=",".join(delivery_config),
                descarga_config=json.dumps(delivery_config),
            )
            db.session.add(week)
            weeks.append({"codigo": code, "label": week.label, "start": start.isoformat()})
            for loja, (prefix, _) in FICTIONAL_TEAMS.items():
                for day_idx, day in enumerate(days):
                    opening = 0 if day_idx < 5 else 3 if day_idx == 5 else 4
                    closing = 1 if day_idx % 2 == 0 else 2
                    employees = (opening, *MIDDLE_ROTATION[day_idx], closing)
                    for position, employee_idx in enumerate(employees):
                        if week_idx and (loja, day_idx, position) in NEXT_WEEK_GAPS:
                            continue
                        db.session.add(module.Turno(
                            loja=loja, horario=DEMO_HORARIOS[loja][position], dia=day["label"],
                            funcionario=f"{prefix}{employee_idx + 1:02}", semana=code,
                            nota="", updated_at=stamp,
                        ))
            for day, stores in delivery_config.items():
                for loja, horario in stores.items():
                    db.session.add(module.Descarga(
                        semana=code, dia=day, loja=loja, funcionario="DFX01", horario=horario,
                    ))
            db.session.flush()
            scheduled = {}
            for shift in module.Turno.query.filter_by(semana=code).all():
                scheduled.setdefault(shift.funcionario, set()).add(shift.dia)
            if week_idx:
                # Keep the original, suitably qualified colleague available
                # for each deliberate gap, with eight hours of contract room.
                # This gives the presentation a practical suggestion to accept.
                for loja, day_idx, position in NEXT_WEEK_GAPS:
                    employee_idx = MIDDLE_ROTATION[day_idx][position - 1]
                    prefix = FICTIONAL_TEAMS[loja][0]
                    scheduled.setdefault(f"{prefix}{employee_idx + 1:02}", set()).add(days[day_idx]["label"])
            for person in module.Funcionario.query.all():
                if week_idx and person.sigla.endswith("09"):
                    continue  # Five intentionally outstanding responses in the draft.
                preferred_store = next((loja for loja, (prefix, _) in FICTIONAL_TEAMS.items()
                                        if person.sigla.startswith(prefix)), "")
                worked = scheduled.get(person.sigla, set())
                if person.sigla == "DFX01":
                    worked = set(delivery_config)
                extra_day = next((d["label"] for d in days if d["label"] not in worked), None)
                for day in days:
                    available = day["label"] in worked or day["label"] == extra_day
                    if week_idx and person.sigla in DONATION_APPLICANTS and day == days[DONATION_DAY_INDEX]:
                        available = True  # Both fictional applicants can accept Friday's donation.
                    db.session.add(module.Disponibilidade(
                        semana=code, funcionario=person.sigla, dia=day["label"],
                        tipo="disponivel" if available else "indisponivel",
                        texto="Preferência pela loja habitual." if available and preferred_store else "",
                        lojas_pref=preferred_store if available else "", slots_pref="",
                        descarga_opt=person.sigla == "DFX01" and day["label"] in delivery_config,
                        revisto=not bool(week_idx), submetido_em=stamp,
                    ))

        db.session.flush()
        # Both fictional weeks start from an explicit authorised baseline.
        # The next week retains its three intentional coverage gaps; filling
        # them in the presentation stages amendments until explicitly released.
        for week in weeks:
            release_week(week['codigo'], 'DEMO',
                         'Publicação inicial fictícia para ensaio local; sem aprovação da empresa.')
        # A future same-day swap between equally qualified staff. Each keeps one
        # eight-hour shift, so approval introduces no overlap or hours overrun.
        next_code = weeks[1]["codigo"]
        next_monday_label = week_from_monday(monday + timedelta(weeks=1))[0]["label"]
        source = module.Turno.query.filter_by(
            semana=next_code, dia=next_monday_label, funcionario=EMPLOYEE_SIGLA).one()
        destination = module.Turno.query.filter_by(
            semana=next_code, dia=next_monday_label, funcionario="DCA07").one()
        source_id, destination_id = source.id, destination.id
        donation_day = week_from_monday(monday + timedelta(weeks=1))[DONATION_DAY_INDEX]
        donation_shift = module.Turno.query.filter_by(
            semana=next_code, dia=donation_day['label'], funcionario=EMPLOYEE_SIGLA).one()
        donation = {'donor': EMPLOYEE_SIGLA, 'applicants': list(DONATION_APPLICANTS),
                    'shift_id': donation_shift.id, 'day': donation_day['label'],
                    'date': donation_day['date'], 'store': donation_shift.loja,
                    'time': donation_shift.horario}
        db.session.commit()

    # Prepare the pending exchange through the same permissions, validation,
    # CSRF and persistence used by the interface, rather than forcing a state.
    with _demo_client(module, EMPLOYEE_SIGLA) as donor:
        created = _api_request(donor, '/api/trocas', {
            'turno_origem_id': source_id, 'turno_destino_id': destination_id,
            'para_sigla': 'DCA07', 'motivo': 'Consulta marcada. Troca de horário acordada com o colega.'})
        swap_id = created.get_json()['troca']['id']
    with _demo_client(module, 'DCA07') as colleague:
        _api_request(colleague, f'/api/trocas/{swap_id}/responder', {
            'aceitar': True, 'nota': 'Disponível para assegurar este horário.'})

    return {"date_lisbon": today.isoformat(), "current_week": weeks[0], "next_week": weeks[1],
            "staff": 46, "current_shifts": 175, "draft_shifts": 172,
            "draft_missing_places": 3, "draft_responses": 41, "pending_swap_id": swap_id,
            "current_published_version": 1, "next_published_version": 1,
            "employee_sigla": EMPLOYEE_SIGLA, "donation": donation, "database": str(DEMO_DB)}


@contextmanager
def _demo_client(module, sigla):
    """Independent signed-in sessions, with the app's normal login mechanism."""
    # Do not preserve request contexts: several independent clients are used
    # together, and a preserved context from one would leak into the next.
    client = module.app.test_client()
    pin = SUPERVISOR_PIN if sigla == 'ADMIN' else EMPLOYEE_PIN
    response = client.post('/login', data={'sigla': sigla, 'pin': pin})
    assert response.status_code == 302, f'Demo login failed for {sigla}'
    yield client


def _api_request(client, path, data, status=200):
    with client.session_transaction() as session:
        token = session['_csrf']
    response = client.post(path, json=data, headers={'X-CSRF-Token': token})
    assert response.status_code == status, f'Demo action failed: {path}: {response.status_code} {response.get_json()}'
    return response


def _verify_donation_workflow(module, summary):
    """Rehearse real donation writes before restoring the fictional baseline.

    These are startup checks, not a substitute for browser rehearsal. The app
    commits each action normally; _populate restores the scenario afterwards.
    """
    from models import AuditLog, PublishedTurno, ScheduleRelease
    scenario = summary['donation']
    code = summary['next_week']['codigo']
    checks = 0
    with ExitStack() as stack:
        clients = {sigla: stack.enter_context(_demo_client(module, sigla))
                   for sigla in ('ADMIN', EMPLOYEE_SIGLA, *DONATION_APPLICANTS)}
        opened = _api_request(clients[EMPLOYEE_SIGLA], '/api/trocas/abrir', {
            'turno_origem_id': scenario['shift_id'], 'tipo': 'aberto_doacao',
            'motivo': 'Compromisso pessoal. Candidaturas sujeitas à decisão da administração.'})
        tid = opened.get_json()['troca']['id']
        assert opened.get_json()['troca']['para_sigla'] == ''
        checks += 1
        candidate_ids = []
        for sigla in DONATION_APPLICANTS:
            applied = _api_request(clients[sigla], f'/api/trocas/{tid}/candidatar', {
                'comentario': 'Disponível para assegurar o turno.'})
            candidate_ids.append(applied.get_json()['candidatura']['id'])
            queued = clients['ADMIN'].get('/api/central').get_json()['pedidos']
            donation = next(p for p in queued if p['id'] == tid)
            assert donation['estado'] == 'pendente_supervisor' and donation['para_sigla'] == ''
            assert len(donation['candidaturas']) == len(candidate_ids)
            assert all(not c['fatal'] and not c['conflitos'] and not c['avisos']
                       for c in donation['candidaturas'])
            checks += 1
        _api_request(clients[DONATION_APPLICANTS[0]], f'/api/trocas/{tid}/candidatar', {}, status=409)
        _api_request(clients[EMPLOYEE_SIGLA], f'/api/trocas/{tid}/escolher',
                     {'candidatura_id': candidate_ids[0]}, status=403)
        _api_request(clients[EMPLOYEE_SIGLA], f'/api/trocas/{tid}/decidir',
                     {'aprovar': True, 'candidatura_id': candidate_ids[0]}, status=403)
        checks += 3
        with module.app.app_context():
            days = json.loads(module.SemanaConfig.query.filter_by(codigo=code).one().dias_json)
        # The prepared Wednesday gap is a recognisable unrelated draft change.
        added = _api_request(clients['ADMIN'], '/api/turno/add', {
            'semana': code, 'loja': 'carmelitas', 'dia': days[2]['label'],
            'horario': DEMO_HORARIOS['carmelitas'][2], 'funcionario': 'DCA07',
            'nota': 'Ensaio de alteração por publicar.'})
        draft_id = added.get_json()['id']
        approved = _api_request(clients['ADMIN'], f'/api/trocas/{tid}/decidir', {
            'aprovar': True, 'candidatura_id': candidate_ids[1],
            'nota': 'Segunda candidatura escolhida pela administração no ensaio local.'})
        assert approved.get_json()['troca']['para_sigla'] == DONATION_APPLICANTS[1]
        _api_request(clients['ADMIN'], f'/api/trocas/{tid}/decidir', {
            'aprovar': True, 'candidatura_id': candidate_ids[0]}, status=409)
        checks += 3
        with module.app.app_context():
            assert module.db.session.get(module.Turno, scenario['shift_id']).funcionario == DONATION_APPLICANTS[1]
            assert module.db.session.get(PublishedTurno, scenario['shift_id']).funcionario == DONATION_APPLICANTS[1]
            assert module.db.session.get(module.Turno, draft_id)
            assert module.db.session.get(PublishedTurno, draft_id) is None
            candidates = module.TrocaCandidatura.query.filter_by(troca_id=tid).all()
            assert {c.sigla: c.status for c in candidates} == {
                DONATION_APPLICANTS[0]: 'rejeitada', DONATION_APPLICANTS[1]: 'aceite'}
            assert ScheduleRelease.query.filter_by(semana=code).count() == 2
            assert AuditLog.query.filter_by(action='troca_aprovar', target=str(tid)).count() == 1
            latest = module.latest_release(code)
            assert latest.version == 2 and len(json.loads(latest.changes_json)) == 1
        checks += 1
        employee_page = clients[DONATION_APPLICANTS[1]].get('/escala', query_string={'semana': code})
        assert employee_page.status_code == 200 and b'Ensaio de altera' not in employee_page.data
        assert clients['ADMIN'].get('/api/trocas/contagem').get_json()['pendentes'] == 1  # The original exchange only.
        checks += 1
    return checks


def _validate_data(module, summary: dict) -> None:
    from models import PublishedTurno, PublishedDescarga, PublishedSemana, ScheduleRelease, SchemaMigration
    with module.app.app_context():
        assert module.Funcionario.query.count() == 46
        assert module.SemanaConfig.query.count() == 2
        assert PublishedSemana.query.count() == ScheduleRelease.query.count() == 2
        assert module.db.session.get(SchemaMigration, 'schedule_publication_v1')
        assert module.db.session.get(SchemaMigration, 'donation_admin_review_v1')
        assert module.SemanaConfig.query.filter_by(ativa=True).one().codigo == summary["next_week"]["codigo"]
        for index, name in enumerate(("current_week", "next_week")):
            code = summary[name]["codigo"]
            shifts = module.Turno.query.filter_by(semana=code).all()
            assert len(shifts) == (175 if index == 0 else 172)
            release = ScheduleRelease.query.filter_by(semana=code).one()
            assert release.version == 1 and release.actor == 'DEMO'
            assert PublishedTurno.query.filter_by(semana=code).count() == len(shifts)
            assert PublishedDescarga.query.filter_by(semana=code).count() == 3
            seen = set()
            daily_hours = {}
            for shift in shifts:
                key = (shift.funcionario, shift.dia)
                assert key not in seen, f"Duplicate employee/day: {key}"
                seen.add(key)
                response = module.Disponibilidade.query.filter_by(
                    semana=code, funcionario=shift.funcionario, dia=shift.dia).first()
                assert response is None or response.tipo != "indisponivel"
                daily_hours.setdefault(shift.funcionario, []).append(module._shift_hours(shift.horario))
            for person, hours in daily_hours.items():
                employee = module.db.session.get(module.Funcionario, person)
                assert len(hours) <= 5
                assert sum(hours) <= employee.horas_contrato
            responses = {r.funcionario for r in module.Disponibilidade.query.filter_by(semana=code).all()}
            assert len(responses) == (46 if index == 0 else 41)
            assert module.Descarga.query.filter_by(semana=code).count() == 3
        for loja, (prefix, _) in FICTIONAL_TEAMS.items():
            assert module.Funcionario.query.filter_by(sigla=f"{prefix}01").one().loja_gerida == loja


def _smoke_routes(module, summary: dict) -> int:
    """Exercise real login, protected pages, exports and the CSRF gate."""
    code = summary["next_week"]["codigo"]
    checks = 0
    with module.app.test_client() as client:
        for path in ("/", "/supervisor", "/supervisor/visao-geral"):
            response = client.get(path)
            assert response.status_code == 302, f"Anonymous access was not rejected: {path}"
            checks += 1
        response = client.post("/login", data={"sigla": "ADMIN", "pin": SUPERVISOR_PIN})
        assert response.status_code == 302
        for path in (
            "/healthz", "/supervisor", "/supervisor/visao-geral", "/equipa", "/escala", "/trocas",
            "/supervisor/escala", "/supervisor/disponibilidades", "/supervisor/conflitos",
            "/escala/descargas", "/supervisor/exportar-pdf", "/supervisor/exportar-horas-csv",
        ):
            response = client.get(path, query_string={"semana": code})
            assert response.status_code == 200, f"Demo route failed: {path} ({response.status_code})"
            checks += 1
        assert client.post("/api/headcount-targets", json={}).status_code == 403
        checks += 1
    with module.app.test_client() as client:
        response = client.post("/login", data={"sigla": EMPLOYEE_SIGLA, "pin": EMPLOYEE_PIN})
        assert response.status_code == 302
        for path in ("/", "/minha-escala", "/disponibilidade", "/trocas", "/escala"):
            response = client.get(path)
            assert response.status_code == 200, f"Employee demonstration failed: {path}"
            checks += 1
        response = client.get("/supervisor/visao-geral")
        assert response.status_code in (302, 403)
        checks += 1
    return checks


def _dispose_application(module) -> None:
    # Release SQLite files before another process can acquire the demo lock.
    with module.app.app_context():
        module.db.session.remove()
        module.db.engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true",
                        help="Reset and verify the fictional demo database without starting a server.")
    args = parser.parse_args(argv)
    module = None
    try:
        with ExitStack() as stack:
            stack.enter_context(_demo_lock())
            if not args.prepare_only:
                _check_port()
            path = _reset_disposable_database()
            module = _load_application(path)
            stack.callback(_dispose_application, module)
            summary = _populate(module)
            _validate_data(module, summary)
            donation_checks = _verify_donation_workflow(module, summary)
            summary = _populate(module)  # Restore fresh versions and unopened donation for the meeting.
            _validate_data(module, summary)
            summary['donation_checks'] = donation_checks
            summary["route_checks"] = _smoke_routes(module, summary)
            summary_path = DEMO_DIR / "summary.json"
            if summary_path.resolve() != summary_path:
                raise RuntimeError("Refusing to overwrite a redirected demo summary file.")
            summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            print("\nCEO demonstration ready. All names, availability and schedules are fictional.")
            print(f"Verified {summary['route_checks']} route/access checks; live data was never opened.")
            print(f"Verified {summary['donation_checks']} donation workflow checks; restored the clean fictional scenario.")
            print(f"Current week: {summary['current_week']['label']} ({summary['current_shifts']} shifts)")
            print(f"Next week:    {summary['next_week']['label']} (published demo v1; {summary['draft_shifts']} shifts; 3 places to fill and publish)")
            print(f"Supervisor: ADMIN / {SUPERVISOR_PIN}")
            print(f"Employee:   {EMPLOYEE_SIGLA} / {EMPLOYEE_PIN}")
            print(f"Applicants: {', '.join(DONATION_APPLICANTS)} / {EMPLOYEE_PIN} (use separate browser sessions)")
            print(f"Donation:   {summary['donation']['date']} / Carmelitas / {summary['donation']['time']}")
            print(f"Demo database: {path}")
            if args.prepare_only:
                print("Preparation complete. No server was started.")
            else:
                print(f"Open http://{HOST}:{PORT}/supervisor/visao-geral")
                print("Stop with Ctrl+C. Run this launcher again to restore the original demonstration.\n")
                module.app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
        return 0
    except KeyboardInterrupt:
        print("\nDemonstration stopped. Its disposable data can be reset on the next launch.")
        return 0
    except (RuntimeError, OSError, AssertionError) as exc:
        print(f"Demo could not start: {exc}", file=sys.stderr)
        return 1
if __name__ == "__main__":
    raise SystemExit(main())
