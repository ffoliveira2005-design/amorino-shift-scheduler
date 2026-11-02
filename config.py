"""Pure data constants for the Amorino app.

Anything in here is a Python literal that doesn't depend on Flask, the DB, or
runtime state. Everything else (env-var-driven config, app secret, etc.) stays
in app.py because it's tightly bound to Flask initialisation.

Modules that need constants import them from here. The names are kept identical
to their original positions in app.py so existing code patterns are preserved.
"""

# ── Rate limiting policy ──────────────────────────────────────────────────────
# Per-IP login attempts: max LOGIN_MAX in LOGIN_WINDOW seconds.
LOGIN_WINDOW   = 60 * 5               # 5 minutes
LOGIN_MAX_DEFAULT = 8
LOGIN_MAX_ADMIN   = 5                 # supervisor account gets stricter limit
# Per-process global: max LOGIN_GLOBAL_MAX login attempts per LOGIN_GLOBAL_WINDOW.
LOGIN_GLOBAL_WINDOW   = 60 * 60       # 1 hour
LOGIN_GLOBAL_MAX      = 30            # 30 across all IPs per hour

# ── Session lifetime ──────────────────────────────────────────────────────────
SESSION_MAX_AGE_SECONDS = 12 * 60 * 60   # 12 hours

# ── PIN policy ────────────────────────────────────────────────────────────────
PIN_MIN_LENGTH = 4
PIN_MAX_LENGTH = 32

# Trivial PINs rejected on FIRST login only (existing PINs are grandfathered so
# nobody gets locked out). The check in helpers.auth.is_weak_pin also rejects
# all-same-digit (0000), simple sequences (1234/4321), and the user's own sigla.
WEAK_PINS = {
    '1234', '12345', '123456', '0000', '1111', '2222', '3333', '4444', '5555',
    '6666', '7777', '8888', '9999', '4321', '6789', '0123', '1212', '1313',
    '2580', '1004', '2000', '1010', 'password', 'pass', 'admin', 'amorino',
    'pin', 'qwerty', 'abcd', '1379', '7410',
}

# ── Admin identity ────────────────────────────────────────────────────────────
ADMIN_SIGLA = 'ADMIN'

# ── Lojas (stores) ────────────────────────────────────────────────────────────
LOJAS = [
    {'id': 'carmelitas', 'nome': 'Carmelitas',         'short': 'CAR'},
    {'id': 'clerigos',   'nome': 'Clérigos',            'short': 'CL'},
    {'id': 'norte',      'nome': 'Norte Shopping',      'short': 'NS'},
    {'id': 'gaia',       'nome': 'Gaia',                'short': 'GAIA'},
    {'id': 'mbs',        'nome': 'Mercado Bom Sucesso', 'short': 'MBS'},
]

# ── Time slots (availability buckets, by shift ENTRY time) ───────────────────
# manha  = entrada < 12h
# tarde  = entrada 12h–15h
# noite  = entrada >= 15h
# Canonical order + labels used across availability UI and suggestion display.
SLOTS = ['manha', 'tarde', 'noite']
SLOT_LABELS = {'manha': 'Manhã', 'tarde': 'Tarde', 'noite': 'Noite'}

# ── Descarga (delivery unloading) ────────────────────────────────────────────
# A descarga assignment occupies the worker for a fixed morning window. Default
# is 08:00–12:00 at every store except Mercado Bom Sucesso, which opens later
# and runs 10:00–14:00. Whoever is assigned to a descarga cannot also hold a
# regular shift overlapping these hours that day (enforced at assignment time).
DESCARGA_HORARIO_DEFAULT = '08:00-12:00'
DESCARGA_HORARIO_BY_LOJA = {'mbs': '10:00-14:00'}

# A descarga is credited as AT LEAST 4 hours: turning up for the delivery is
# worth a 4-hour block even if it's over sooner. A longer window is paid for
# what it actually is, so a supervisor who widens a cell (see
# /api/descarga/horario) doesn't shortchange anyone.
#   3h window → 4h   ·   4h window → 4h   ·   6h window → 6h
# helpers.time_helpers.descarga_hours() applies this; every hours total in the
# app (employee pages, workload, conflitos, CSV export) goes through it so they
# can never disagree.
DESCARGA_HORAS_MIN = 4.0

# ── Base time-bands per loja (display order preserved) ────────────────────────
HORARIOS_BASE = {
    'carmelitas': ['08:00-16:30','08:00-17:00 (+0,5)','Provas Práticas','10:00-18:30',
                   '12:30-21:00','13:00-19:00','13:00-22:00 (+0,5)','14:00-22:30',
                   '15:00-00:30 (+1)','16:00-00:30','16:00-01:30 (+1)','17:00-23:00'],
    'clerigos':   ['08:00-16:30','09:00-17:30','11:30-20:00','13:00-22:00 (+0,5)',
                   '14:00-22:30','15:00-00:30 (+1)','16:00-00:30','19:00-00:30'],
    'norte':      ['09:00-15:00','09:00-17:30','13:00-22:00 (+0,5)','14:00-20:00',
                   '14:00-22:30','15:30-00:00','19:00-00:00'],
    'gaia':       ['08:00-16:30','10:30-19:00','12:30-22:00 (+1)','14:00-20:00',
                   '14:00-22:30','15:00-00:30 (+1)','16:00-00:30'],
    'mbs':        ['10:00-14:30','10:00-18:30','14:00-19:30','14:00-22:30',
                   '19:00-23:30','19:00-00:30'],
}

# ── Contract threshold ────────────────────────────────────────────────────────
# At or above this many hours = 'contrato' (contracted worker).
# Below = 'turnos' (shift worker without a fixed contract).
AVENCA_THRESHOLD = 16

# ── Employee ranks (used for "can open/close alone" rules) ────────────────────
RANK_LEVELS = {
    'trainee':          0,   # cannot open OR close alone
    'staff':            1,   # can open alone but not close alone
    'shift_supervisor': 2,   # can open AND close alone
    'store_manager':    3,   # can open AND close, full responsibility
}
RANK_LABELS = {
    'trainee':          'Formação',
    'staff':            'Colaborador',
    'shift_supervisor': 'Resp. de turno',
    'store_manager':    'Resp. de loja',
}
RANK_ORDER = ['trainee', 'staff', 'shift_supervisor', 'store_manager']

# ── Planned-hour convention: deduct 30 min for windows >=8h ──────────────────
# This operational calculation is not a statement of Portuguese labour law.
# Applicable breaks and collective agreements need a separate compliance review.
LUNCH_BREAK_THRESHOLD = 8.0   # in hours
LUNCH_BREAK_MINUTES   = 30

# ── Troca (shift swap) state machine ─────────────────────────────────────────
TROCA_STATES_OPEN     = {'pendente_colega', 'pendente_supervisor', 'aberto'}
TROCA_STATES_CLOSED   = {'recusado_colega', 'cancelado', 'aprovado', 'negado',
                         'expirado'}
TROCA_STATE_LABELS = {
    'aberto':              'Aberto na central',
    'pendente_colega':     'À espera do/a colega',
    'pendente_supervisor': 'À espera da supervisão',
    'recusado_colega':     'Recusado pelo/a colega',
    'cancelado':           'Cancelado',
    'aprovado':            'Aprovado',
    'negado':              'Recusado pela supervisão',
    'expirado':            'Expirou',
}
TROCA_TIPO_LABELS = {
    'direto':         'direto',
    'aberto_doacao':  'doação aberta',
    'aberto_troca':   'troca aberta',
}

# ── Calendar helpers ──────────────────────────────────────────────────────────
DAY_ABBR  = {0:'Seg',1:'Ter',2:'Qua',3:'Qui',4:'Sex',5:'Sab',6:'Dom'}
MESES_CURTOS = ['jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez']
MESES_LONGOS = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
DIAS_LONGOS  = ['Segunda-feira','Terça-feira','Quarta-feira','Quinta-feira',
                'Sexta-feira','Sábado','Domingo']

# ── Historical-affinity scoring (Tier-1 ML) ──────────────────────────────────
HISTORICAL_LOOKBACK_WEEKS = 12
HISTORICAL_MIN_WEEKS      = 2
HISTORICAL_MAX_BONUS      = 250
