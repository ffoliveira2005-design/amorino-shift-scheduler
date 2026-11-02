"""SQLite transaction boundary and migration for administration-reviewed donations."""
import json
from datetime import date, datetime, timedelta, timezone
from functools import wraps
import re
from zoneinfo import ZoneInfo

from flask import g, request, session, has_request_context
from sqlalchemy import text

from extensions import db
from helpers.time_helpers import shift_bounds
from models import (AuditLog, PublishedSemana, PublishedTurno, SchemaMigration,
                    TrocaCandidatura, TrocaPedido)

ACTIVE_CANDIDATES = ('pendente', 'aceite')
DONATION_STATES = ('aberto', 'pendente_supervisor')


def is_donation(p):
    return p.tipo == 'aberto_doacao' or (p.tipo != 'aberto_troca' and p.turno_destino_id is None)


def shift_identity(shift):
    if not shift:
        return None
    week = db.session.get(PublishedSemana, shift.semana)
    try:
        days = json.loads(week.dias_json) if week else []
        if not isinstance(days, list):
            days = []
        raw_date = next((d.get('date') for d in days if isinstance(d, dict) and d.get('label') == shift.dia), '')
        actual_date = date.fromisoformat(raw_date).isoformat()
    except (ValueError, TypeError, KeyError):
        actual_date = ''
    return {**{k: getattr(shift, k) or '' for k in
               ('id', 'semana', 'dia', 'loja', 'horario', 'funcionario', 'nota')},
            'date': actual_date}


def shift_started(shift, now=None):
    identity = shift_identity(shift)
    if not identity or not identity['date']:
        return True  # An unresolved published date cannot be authorised safely.
    start, _ = shift_bounds(shift.horario)
    clock = re.match(r'^(\d{1,2}):(\d{2})', shift.horario or '')
    if start is None or not clock or int(clock[1]) > 23 or int(clock[2]) > 59:
        return True
    try:
        begins = datetime.combine(date.fromisoformat(identity['date']), datetime.min.time(),
                                  tzinfo=ZoneInfo('Europe/Lisbon'))
    except (ValueError, TypeError):
        return True
    begins += timedelta(minutes=start)
    return (now or datetime.now(timezone.utc)) >= begins


def transaction_audit(action, target, detail, actor=None):
    """Do not commit or suppress errors: evidence is part of the transfer."""
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False, separators=(',', ':'))
    db.session.add(AuditLog(actor=actor or (session.get('user_sigla', 'anonymous') if has_request_context() else 'SYSTEM'),
                           ip=(request.remote_addr or '') if has_request_context() else '', action=action,
                           target=str(target), detail=detail))


def begin_write():
    # Hooks/authentication may have read already. Discard those read snapshots
    # and acquire SQLite's writer reservation BEFORE authoritative reads.
    db.session.rollback()
    db.session.execute(text('BEGIN IMMEDIATE'))
    g._dias_da_semana = {}


def troca_transaction(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        begin_write()
        g.troca_transaction = True
        # Refresh the schedule audit baseline under the database reservation.
        if hasattr(g, 'schedule_before'):
            from helpers.publication import snapshot
            from models import SemanaConfig
            g.schedule_before = {s.codigo: snapshot(s.codigo) for s in SemanaConfig.query.all()}
        try:
            response = fn(*args, **kwargs)
            db.session.commit()
            return response
        except BaseException:
            db.session.rollback()
            raise
        finally:
            g.troca_transaction = False
    return wrapped


def migration_donation_review():
    """Idempotent; completed history remains untouched, nominations are not approvals."""
    db.session.rollback()
    db.session.execute(text('BEGIN IMMEDIATE'))
    name = 'donation_admin_review_v1'
    if db.session.get(SchemaMigration, name):
        db.session.commit()
        return 0
    count = 0
    for p in TrocaPedido.query.filter(TrocaPedido.estado.in_(
            ('aberto', 'pendente_colega', 'pendente_supervisor'))).all():
        if not is_donation(p):
            continue
        before = {'tipo': p.tipo, 'estado': p.estado, 'para': p.para_sigla}
        candidates = TrocaCandidatura.query.filter_by(troca_id=p.id).all()
        # Previously accepted/picked people expressed interest; a mere direct
        # nomination that never replied does not constitute a candidacy.
        if p.estado == 'pendente_supervisor':
            if p.para_sigla and not any(c.sigla == p.para_sigla for c in candidates):
                c = TrocaCandidatura(troca_id=p.id, sigla=p.para_sigla,
                                     comentario=p.resposta_colega or '', status='pendente',
                                     criado_em=p.decisao_em or p.atualizado_em or p.criado_em)
                db.session.add(c)
                candidates.append(c)
            for c in candidates:
                if c.status in ('aceite', 'rejeitada'):
                    c.status = 'pendente'
        p.tipo, p.para_sigla, p.turno_destino_id = 'aberto_doacao', '', None
        p.estado = 'pendente_supervisor' if any(c.status == 'pendente' for c in candidates) else 'aberto'
        p.decisao_em = None
        p.origem_snapshot_json = json.dumps(shift_identity(db.session.get(PublishedTurno, p.turno_origem_id)),
                                            ensure_ascii=False)
        db.session.add(AuditLog(actor='MIGRATION', ip='', action='doacao_migrar', target=str(p.id),
                               detail=json.dumps({'antes': before, 'depois': {'estado': p.estado, 'para': ''}},
                                                 ensure_ascii=False)))
        count += 1
    db.session.add(SchemaMigration(name=name, applied_at=datetime.now(timezone.utc)))
    db.session.commit()
    return count
