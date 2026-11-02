"""Database models.

All db.Model classes for the Amorino app, in one place. The `db` instance is
imported from extensions.py to avoid circular imports.

If you add a new column, you also need to update the migration block in app.py
(which runs ALTER TABLE on startup) — until we switch to Alembic/Flask-Migrate.
"""

from datetime import datetime, timezone
from extensions import db
from config import RANK_LEVELS, RANK_LABELS, RANK_ORDER

# ── Models ─────────────────────────────────────────────────────────────────────

class Turno(db.Model):
    __tablename__ = 'turnos'
    id          = db.Column(db.Integer, primary_key=True)
    loja        = db.Column(db.String(30), nullable=False)
    horario     = db.Column(db.String(60), nullable=False)
    dia         = db.Column(db.String(20), nullable=False)
    funcionario = db.Column(db.String(10), nullable=False)
    nota        = db.Column(db.String(100), default='')
    semana      = db.Column(db.String(20), default='11-17mai')
    # FEATURE 25: when was this row last touched? Helps the supervisor see how
    # fresh the schedule is. Set on insert by api_add_turno.
    updated_at  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Funcionario(db.Model):
    __tablename__ = 'funcionarios'
    sigla          = db.Column(db.String(10), primary_key=True)
    nome           = db.Column(db.String(80), nullable=False)
    horas_contrato = db.Column(db.Integer, default=0)
    # FIX BUG 13: increased width — now stores pbkdf2 hashes (~90 chars). Legacy
    # plaintext PINs continue to work and are upgraded on the next successful login.
    pin            = db.Column(db.String(200), default='')
    # 'contrato' = fixed hours contract, 'turnos' = shift/on-call worker
    tipo_contrato  = db.Column(db.String(10), default='contrato')
    # Hierarchy/rank: trainee < staff < shift_supervisor < store_manager
    # Lower ranks cannot open/close stores alone.
    rank           = db.Column(db.String(20), default='staff')
    # Scheduling responsibility only; this does not grant app permissions.
    loja_gerida    = db.Column(db.String(30), nullable=False, default='')
    # FEATURE: iCal subscription token. Secret per-funcionario value that lets
    # the calendar feed be accessed without a session (because phone calendar
    # apps don't carry our login cookie). Treat like a password — bearer auth.
    ical_token     = db.Column(db.String(64), default='')
    # Incremented on access reset/provisioning to revoke signed browser sessions.
    access_version = db.Column(db.Integer, nullable=False, default=0)
    # A high-entropy invitation is shown once; only its digest is retained.
    enrollment_hash = db.Column(db.String(64), nullable=False, default='')
    enrollment_expires_at = db.Column(db.DateTime, nullable=True)
    enrollment_kind = db.Column(db.String(20), nullable=False, default='')

    @property
    def is_turnos(self):
        return self.tipo_contrato == 'turnos'

    @property
    def is_contrato(self):
        return self.tipo_contrato == 'contrato'

    @property
    def rank_level(self):
        return RANK_LEVELS.get(self.rank, 1)

    @property
    def can_open_alone(self):
        # Only staff and above can open alone (trainees cannot)
        return self.rank_level >= 1

    @property
    def can_close_alone(self):
        # Only shift supervisors and above can close alone
        return self.rank_level >= 2

class Disponibilidade(db.Model):
    __tablename__ = 'disponibilidades'
    id           = db.Column(db.Integer, primary_key=True)
    semana       = db.Column(db.String(20), nullable=False)
    funcionario  = db.Column(db.String(10), nullable=False)
    dia          = db.Column(db.String(20), nullable=False)
    tipo         = db.Column(db.String(20), nullable=False)
    texto        = db.Column(db.String(200), default='')
    # Preferred stores (CSV of loja IDs) and preferred time slots (CSV of slot codes:
    # manha/tarde/noite/fecho). Both empty by default for backwards compatibility.
    lojas_pref   = db.Column(db.String(120), default='')
    slots_pref   = db.Column(db.String(120), default='')
    # Independent descarga opt-in (a worker can be available AND do descarga).
    descarga_opt = db.Column(db.Boolean, default=False)
    submetido_em = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    revisto      = db.Column(db.Boolean, default=False)

class SemanaConfig(db.Model):
    __tablename__ = 'semanas'
    id         = db.Column(db.Integer, primary_key=True)
    codigo     = db.Column(db.String(20), unique=True, nullable=False)
    label      = db.Column(db.String(60), nullable=False)
    # JSON: list of {label, date, feriado, feriado_nome}
    dias_json  = db.Column(db.String(500), nullable=False)
    ativa      = db.Column(db.Boolean, default=True)
    # CSV of day labels (e.g. "Ter 19,Qua 20") on which "Descarga" is available
    # for the week. Empty = no descarga that week.
    descarga_dias = db.Column(db.String(120), default='')
    # CSV of store ids that have descarga this week. Empty = all stores
    # (default); the sentinel '__none__' means explicitly no stores.
    # LEGACY (week-level) — superseded by descarga_config below, kept for the
    # one-time migration only.
    descarga_lojas = db.Column(db.String(120), default='')
    # Per-day descarga config (source of truth): JSON {dia_label: {loja_id: 'HH:MM-HH:MM'}}.
    # A day is a descarga day iff it has >=1 store. Hours default per store but
    # can be overridden per cell.
    descarga_config = db.Column(db.Text, default='')
    # FIX BUG 1: was datetime.now(timezone.utc) (evaluated once at startup)
    criada_em  = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

class Descarga(db.Model):
    """A descarga (delivery-unloading) assignment for one worker, at one store,
    on one day of a given week. Modelled separately from Turno so the regular
    schedule grid and its headcount maths stay untouched, while hours and
    time-conflict checks integrate explicitly where it matters.

    horario is fixed by the store (see config.DESCARGA_HORARIO_*). A worker can
    do descarga at several stores in a week, but not twice at the same store on
    the same day (enforced by the unique constraint).
    """
    __tablename__ = 'descargas'
    id          = db.Column(db.Integer, primary_key=True)
    semana      = db.Column(db.String(20), nullable=False)
    dia         = db.Column(db.String(20), nullable=False)   # day label, e.g. 'Ter 19'
    loja        = db.Column(db.String(30), nullable=False)   # store id
    funcionario = db.Column(db.String(10), nullable=False)   # sigla
    horario     = db.Column(db.String(60), nullable=False)   # '08:00-12:00' / '10:00-14:00'
    criada_em   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (
        db.UniqueConstraint('semana', 'dia', 'loja', 'funcionario',
                            name='uq_descarga_assignment'),
    )

class HorarioBloco(db.Model):
    """Custom shift blocks added by supervisor — persisted in DB."""
    __tablename__ = 'horario_blocos'
    id      = db.Column(db.Integer, primary_key=True)
    loja    = db.Column(db.String(30), nullable=False)
    horario = db.Column(db.String(60), nullable=False)
    ordem   = db.Column(db.Integer, default=999)
    db.UniqueConstraint('loja', 'horario')

class HorarioSupressed(db.Model):
    """Base horarios (from HORARIOS_BASE) that the supervisor has chosen to hide.
    FIX BUG 4: previously deletions were in-memory only and reverted on restart."""
    __tablename__ = 'horario_supressed'
    id      = db.Column(db.Integer, primary_key=True)
    loja    = db.Column(db.String(30), nullable=False)
    horario = db.Column(db.String(60), nullable=False)
    db.UniqueConstraint('loja', 'horario')

class AuditLog(db.Model):
    """Append-only record of sensitive supervisor actions.

    Read it directly with sqlite3 / a SQL client. There's no UI on purpose —
    if the UI is compromised, the attacker shouldn't be able to read or alter
    their own trail.
    """
    __tablename__ = 'audit_log'
    id        = db.Column(db.Integer, primary_key=True)
    ts        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    actor     = db.Column(db.String(20))   # sigla or 'ADMIN'
    ip        = db.Column(db.String(64))
    action    = db.Column(db.String(60))   # e.g. 'pin_reset', 'funcionario_delete'
    target    = db.Column(db.String(120))  # e.g. the affected sigla, semana, etc
    detail    = db.Column(db.String(500))  # free-form, json or short text

# NOTE: the audit() *function* lives in audit.py (imported as helpers.audit),
# which correctly imports flask.request/session and a logger. A duplicate used
# to live here but referenced those names without importing them — dead code
# that would raise NameError if ever called. Removed to avoid the trap.


class TrocaPedido(db.Model):
    """Shift-swap request flow:

        criar  →  pendente_colega
                       |
            (B aceita) | (B recusa)        (A cancela em qualquer momento antes da aprovação)
                       ↓                              |
           pendente_supervisor                        ↓
                       |                          cancelado
            (sup. aprova) | (sup. nega)
                       ↓                ↓
                   aprovado          negado

    On 'aprovado', the underlying Turno rows actually swap funcionário (or, in
    the case of a 'doação' request where destino is NULL, the origin turno
    is reassigned to para_sigla).

    FEATURE: Central (open marketplace) variants exist too. When the request
    is published openly (no specific recipient), `tipo` is 'aberto_doacao' or
    'aberto_troca', `para_sigla` is empty (''), and the initial state is
    'aberto'. Colleagues create TrocaCandidatura rows to apply; once the
    author picks one for a genuine exchange. Donations move immediately to
    pendente_supervisor on the first candidacy; para_sigla remains empty until
    administration approves one active candidate. Both donation open states
    allow more applicants and cancellation/withdrawal before final approval.
    """
    __tablename__ = 'trocas'
    id               = db.Column(db.Integer, primary_key=True)
    criado_em        = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    atualizado_em    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                                 onupdate=lambda: datetime.now(timezone.utc))
    de_sigla         = db.Column(db.String(10), nullable=False, index=True)
    # Donations keep this empty until the administration approves a candidate.
    para_sigla       = db.Column(db.String(10), nullable=False, default='', index=True)
    turno_origem_id  = db.Column(db.Integer,    nullable=False)
    # NULL = doação (one-way: para_sigla picks up the shift; de_sigla is freed)
    turno_destino_id = db.Column(db.Integer,    nullable=True)
    motivo               = db.Column(db.String(300), default='')
    estado               = db.Column(db.String(30), nullable=False,
                                     default='pendente_colega', index=True)
    resposta_colega      = db.Column(db.String(300), default='')
    resposta_supervisor  = db.Column(db.String(300), default='')
    decisao_em           = db.Column(db.DateTime, nullable=True)
    # FEATURE: Central. Type discriminator. Defaults to 'direto' so existing
    # rows from before this migration are unaffected.
    tipo                 = db.Column(db.String(20), nullable=False,
                                     default='direto', index=True)
    # Published identity at request time, including the actual calendar date.
    origem_snapshot_json = db.Column(db.Text, nullable=False, default='')

class TrocaCandidatura(db.Model):
    """A colleague's offer to take on (or swap with) an open TrocaPedido.

    Only used for 'aberto_*' tipo requests. For exchanges the author chooses;
    for donations the administration chooses as part of final approval.
    The winning candidatura is marked 'aceite', others 'rejeitada'.
    """
    __tablename__ = 'trocas_candidaturas'
    id           = db.Column(db.Integer, primary_key=True)
    troca_id     = db.Column(db.Integer, nullable=False, index=True)
    sigla        = db.Column(db.String(10), nullable=False, index=True)
    comentario   = db.Column(db.String(300), default='')
    # For 'aberto_troca': the candidato offers one of THEIR turnos in exchange.
    # NULL when the request is a pure doação.
    turno_oferecido_id = db.Column(db.Integer, nullable=True)
    criado_em    = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    # 'pendente' (default) → 'aceite' (chosen) | 'rejeitada' (another was chosen)
    #                     → 'retirada' (candidate withdrew)
    status       = db.Column(db.String(20), default='pendente', index=True)


class LojaHeadcountTarget(db.Model):
    """Per-loja, per-weekday target headcount for the day-first planner.

    The empties view counts how many people each store needs on each day-of-week
    and surfaces missing headcount as the primary unit of "what's still to do".
    This is separate from the schedule template (HORARIOS_BASE) which tracks
    *time bands*, not *headcount*.

    dia_idx follows Python's weekday(): 0=Monday, 6=Sunday.
    """
    __tablename__ = 'loja_headcount_targets'
    id      = db.Column(db.Integer, primary_key=True)
    loja    = db.Column(db.String(40), nullable=False, index=True)
    dia_idx = db.Column(db.Integer, nullable=False)    # 0=Seg .. 6=Dom
    target  = db.Column(db.Integer, nullable=False, default=0)
    __table_args__ = (
        db.UniqueConstraint('loja', 'dia_idx', name='uq_loja_dia'),
    )


class PublishedTurno(db.Model):
    """Current authorised shift projection. Source ids remain stable for swaps/ICS."""
    __tablename__ = 'published_turnos'
    id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    loja = db.Column(db.String(30), nullable=False)
    horario = db.Column(db.String(60), nullable=False)
    dia = db.Column(db.String(20), nullable=False)
    funcionario = db.Column(db.String(10), nullable=False)
    nota = db.Column(db.String(100), default='')
    semana = db.Column(db.String(20), nullable=False, index=True)
    updated_at = db.Column(db.DateTime)
    version = db.Column(db.Integer, nullable=False)


class PublishedDescarga(db.Model):
    __tablename__ = 'published_descargas'
    id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    semana = db.Column(db.String(20), nullable=False, index=True)
    dia = db.Column(db.String(20), nullable=False)
    loja = db.Column(db.String(30), nullable=False)
    funcionario = db.Column(db.String(10), nullable=False)
    horario = db.Column(db.String(60), nullable=False)
    criada_em = db.Column(db.DateTime)
    version = db.Column(db.Integer, nullable=False)


class PublishedSemana(db.Model):
    __tablename__ = 'published_semanas'
    codigo = db.Column(db.String(20), primary_key=True)
    id = db.Column(db.Integer)
    label = db.Column(db.String(60), nullable=False)
    dias_json = db.Column(db.Text, nullable=False)
    ativa = db.Column(db.Boolean, default=False)
    descarga_config = db.Column(db.Text, default='')
    descarga_dias = db.Column(db.String(120), default='')
    descarga_lojas = db.Column(db.String(120), default='')
    criada_em = db.Column(db.DateTime)
    version = db.Column(db.Integer, nullable=False)


class ScheduleRelease(db.Model):
    """Immutable authorised versions, including the full before/after change list."""
    __tablename__ = 'schedule_releases'
    id = db.Column(db.Integer, primary_key=True)
    semana = db.Column(db.String(20), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    published_at = db.Column(db.DateTime, nullable=False)
    actor = db.Column(db.String(20), nullable=False)
    reason = db.Column(db.String(300), nullable=False)
    snapshot_json = db.Column(db.Text, nullable=False)
    changes_json = db.Column(db.Text, nullable=False)
    __table_args__ = (db.UniqueConstraint('semana', 'version'),)


class ScheduleChange(db.Model):
    __tablename__ = 'schedule_changes'
    id = db.Column(db.Integer, primary_key=True)
    changed_at = db.Column(db.DateTime, nullable=False)
    actor = db.Column(db.String(20), nullable=False)
    semana = db.Column(db.String(20), nullable=False, index=True)
    changes_json = db.Column(db.Text, nullable=False)


class SchemaMigration(db.Model):
    __tablename__ = 'schema_migrations'
    name = db.Column(db.String(80), primary_key=True)
    applied_at = db.Column(db.DateTime, nullable=False)


class ScheduleIdentity(db.Model):
    __tablename__ = 'schedule_identities'
    kind = db.Column(db.String(20), primary_key=True)
    high_water = db.Column(db.Integer, nullable=False)
