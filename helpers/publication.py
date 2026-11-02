"""Drafts and authorised schedule snapshots; no employee read uses draft rows."""
import hashlib
import json
import threading
from datetime import date, datetime, timezone

from flask import current_app, g, has_request_context, request, session
from sqlalchemy import event
from sqlalchemy.orm import Session

from extensions import db
from helpers.time_helpers import local_str
from models import (Turno, Descarga, SemanaConfig, PublishedTurno,
                    PublishedDescarga, PublishedSemana, ScheduleRelease,
                    ScheduleChange, SchemaMigration, ScheduleIdentity)

schedule_lock = threading.RLock()
SHIFT_FIELDS = ('id', 'semana', 'dia', 'loja', 'horario', 'funcionario', 'nota')
DELIVERY_FIELDS = ('id', 'semana', 'dia', 'loja', 'horario', 'funcionario')
WEEK_FIELDS = ('id', 'codigo', 'label', 'dias_json', 'descarga_config',
               'descarga_dias', 'descarga_lojas')
EXPORTS = {'exportar_pdf', 'exportar_pdf_loja', 'exportar_pdf_individual', 'exportar_horas_csv'}


def published_read():
    if not has_request_context():
        return False
    return (request.endpoint == 'ical_feed' or not session.get('supervisor')
            or (request.endpoint in EXPORTS and request.args.get('rascunho') != '1'))


def schedule_turno():
    return PublishedTurno if published_read() else Turno


def schedule_descarga():
    return PublishedDescarga if published_read() else Descarga


def schedule_semana():
    return PublishedSemana if published_read() else SemanaConfig


def snapshot(week):
    sem = SemanaConfig.query.filter_by(codigo=week).first()
    if not sem:
        return None
    return {
        'week': {k: getattr(sem, k) or ('' if k != 'id' else 0) for k in WEEK_FIELDS},
        'shifts': [{k: getattr(t, k) or ('' if k != 'id' else 0) for k in SHIFT_FIELDS}
                   for t in Turno.query.filter_by(semana=week).order_by(Turno.id).all()],
        'deliveries': [{k: getattr(t, k) for k in DELIVERY_FIELDS}
                       for t in Descarga.query.filter_by(semana=week).order_by(Descarga.id).all()],
    }


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(value):
    return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def differences(before, after):
    before, after = before or {}, after or {}
    changes = []
    for kind in ('shifts', 'deliveries'):
        old = {r['id']: r for r in before.get(kind, [])}
        new = {r['id']: r for r in after.get(kind, [])}
        for row_id in sorted(set(old) | set(new)):
            a, b = old.get(row_id), new.get(row_id)
            if a != b:
                changes.append({'kind': kind, 'operation': 'add' if a is None else 'remove' if b is None else 'move',
                                'before': a, 'after': b})
    if before.get('week') != after.get('week'):
        changes.append({'kind': 'week', 'operation': 'configure',
                        'before': before.get('week'), 'after': after.get('week')})
    return changes


def latest_release(week):
    return ScheduleRelease.query.filter_by(semana=week).order_by(ScheduleRelease.version.desc()).first()


def release_week(week, actor, reason, supplied_snapshot=None):
    """Caller commits. Version, immutable history and projections change atomically."""
    value = supplied_snapshot or snapshot(week)
    if not value:
        raise ValueError('Semana não encontrada')
    previous = latest_release(week)
    old = json.loads(previous.snapshot_json) if previous else None
    changes = differences(old, value)
    if previous and not changes:
        return previous
    version = previous.version + 1 if previous else 1
    now = datetime.now(timezone.utc)
    row = ScheduleRelease(semana=week, version=version, published_at=now, actor=actor,
                          reason=reason, snapshot_json=encoded(value), changes_json=encoded(changes))
    db.session.add(row)
    PublishedTurno.query.filter_by(semana=week).delete(synchronize_session=False)
    PublishedDescarga.query.filter_by(semana=week).delete(synchronize_session=False)
    PublishedSemana.query.filter_by(codigo=week).delete(synchronize_session=False)
    db.session.flush()
    for t in value['shifts']:
        db.session.add(PublishedTurno(**t, updated_at=now, version=version))
    for t in value['deliveries']:
        db.session.add(PublishedDescarga(**t, criada_em=now, version=version))
    db.session.add(PublishedSemana(**value['week'], ativa=False, criada_em=now, version=version))
    return row


def migration_legacy_publication(today=None):
    """One time: retain past/current authorised legacy data; future weeks stay draft."""
    name = 'schedule_publication_v1'
    if db.session.get(SchemaMigration, name):
        return []
    today = today or date.today()
    migrated = []
    for week in SemanaConfig.query.order_by(SemanaConfig.id).all():
        try:
            days = json.loads(week.dias_json or '[]')
            dates = [date.fromisoformat(d['date']).isoformat() for d in days]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue  # malformed legacy dates require operator review, never publication
        if dates and min(dates) <= today.isoformat():
            release_week(week.codigo, 'MIGRATION',
                         'Preservação da escala anterior à publicação explícita; data original desconhecida.')
            migrated.append(week.codigo)
    db.session.add(SchemaMigration(name=name, applied_at=datetime.now(timezone.utc)))
    from sqlalchemy import func
    for kind, model in (('shift', Turno), ('delivery', Descarga)):
        maximum = db.session.query(func.max(model.id)).scalar() or 0
        identity = db.session.get(ScheduleIdentity, kind)
        if identity:
            identity.high_water = max(identity.high_water, maximum)
        else:
            db.session.add(ScheduleIdentity(kind=kind, high_water=maximum))
    db.session.commit()
    return migrated


def publication_info(week, include_draft=False):
    releases = ScheduleRelease.query.filter_by(semana=week).order_by(ScheduleRelease.version).all()
    last = releases[-1] if releases else None
    result = {'semana': week, 'versao': last.version if last else 0, 'publicada': bool(last),
              'publicado_em': last.published_at.isoformat() + 'Z' if last else None,
              'publicado_em_texto': local_str(last.published_at, '%d/%m/%Y %H:%M') if last else '',
              'responsavel': last.actor if last else None,
              'historico': [{'versao': r.version, 'publicado_em': r.published_at.isoformat() + 'Z',
                            'publicado_em_texto': local_str(r.published_at, '%d/%m/%Y %H:%M'),
                            'responsavel': r.actor, 'motivo': r.reason,
                            'alteracoes': json.loads(r.changes_json)} for r in releases]}
    if include_draft:
        value = snapshot(week)
        old = json.loads(last.snapshot_json) if last else None
        result.update(rascunho_hash=fingerprint(value), alteracoes_pendentes=differences(old, value))
    return result


def install_schedule_audit(app):
    @app.before_request
    def capture_schedule_before():
        mutation = request.method in ('POST', 'PUT', 'PATCH', 'DELETE') and session.get('supervisor')
        # One process is an explicit architecture constraint. Readers hold this
        # lock across all SELECTs so a page cannot mix two authorised versions.
        if mutation or (published_read() and (session.get('user_sigla') or request.endpoint == 'ical_feed')):
            schedule_lock.acquire()
            g.schedule_locked = True
        if mutation:
            g.schedule_before = {s.codigo: snapshot(s.codigo) for s in SemanaConfig.query.all()}

    @app.teardown_request
    def release_schedule_lock(error):
        if getattr(g, 'schedule_locked', False):
            g.schedule_locked = False
            schedule_lock.release()


@event.listens_for(Session, 'before_commit')
def record_schedule_changes(sa_session):
    if not has_request_context() or not hasattr(g, 'schedule_before'):
        return
    sa_session.flush()
    after = {s.codigo: snapshot(s.codigo) for s in SemanaConfig.query.all()}
    for week in set(g.schedule_before) | set(after):
        changes = differences(g.schedule_before.get(week), after.get(week))
        if changes:
            sa_session.add(ScheduleChange(changed_at=datetime.now(timezone.utc),
                                         actor=session.get('user_sigla', 'unknown'), semana=week,
                                         changes_json=encoded(changes)))
    g.schedule_before = after


@event.listens_for(Turno, 'before_insert')
@event.listens_for(Descarga, 'before_insert')
def prevent_reused_published_ids(mapper, connection, target):
    # Legacy SQLite tables lack AUTOINCREMENT. A removed draft id may still be
    # published. Never recycle it for a different assignment (stable ICS/swaps).
    if target.id is None:
        published = PublishedTurno if isinstance(target, Turno) else PublishedDescarga
        draft = Turno if isinstance(target, Turno) else Descarga
        from sqlalchemy import select, func
        a = connection.execute(select(func.max(draft.id))).scalar() or 0
        b = connection.execute(select(func.max(published.id))).scalar() or 0
        # Persist the high-water mark even after a published deletion so an old
        # swap/history/calendar id can never refer to a new assignment.
        from sqlalchemy import text
        kind = 'shift' if isinstance(target, Turno) else 'delivery'
        high = connection.execute(text('SELECT high_water FROM schedule_identities WHERE kind=:kind'), {'kind': kind}).scalar() or 0
        # Reserve ids across all objects pending in this flush, too.
        from sqlalchemy.orm import object_session
        pending = [r for r in object_session(target).new if isinstance(r, draft)]
        assigned = [r.id for r in pending if r.id is not None]
        target.id = max([a, b, high, *assigned]) + 1
        connection.execute(text('INSERT INTO schedule_identities (kind, high_water) VALUES (:kind, :high) ON CONFLICT(kind) DO UPDATE SET high_water=:high'), {'kind': kind, 'high': target.id})
