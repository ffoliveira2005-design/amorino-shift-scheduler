"""Audit logging helper.

Appends rows to the AuditLog table. Catches all exceptions internally so a
failure to write audit data never breaks the request that triggered it.
"""

import logging

from flask import request, session

from extensions import db
from models import AuditLog

log = logging.getLogger('amorino')


def audit(action: str, target: str = '', detail: str = '') -> None:
    """Append an audit entry. Never raises — failure here mustn't break the request."""
    try:
        actor = session.get('user_sigla') or 'anonymous'
        ip = (request.headers.get('X-Forwarded-For') or request.remote_addr or '?').split(',')[0].strip()
        db.session.add(AuditLog(
            actor=actor[:20], ip=ip[:64], action=action[:60],
            target=str(target or '')[:120], detail=str(detail or '')[:500],
        ))
        db.session.commit()
    except Exception as e:
        log.error('audit log write failed: %s', e)
        try:
            db.session.rollback()
        except Exception:
            pass
