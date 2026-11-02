from flask import Flask, render_template, request, redirect, url_for, jsonify, session, abort, make_response, g
from datetime import datetime, date, timedelta, timezone
from sqlalchemy import text, func, event as sa_event
from sqlalchemy.engine import Engine
from functools import wraps
import os, re, json, hmac, time, secrets, logging, sqlite3, gzip

# ── Internal modules (refactored out of this file, May 2026) ──────────────────
# Only names this file actually uses are imported. A few are re-exported purely
# so tests can reach them as `app.X` — those are marked below; don't drop them
# just because nothing in this file reads them.
from extensions import db
from config import (
    LOGIN_MAX_DEFAULT, LOGIN_MAX_ADMIN,          # re-export: tests
    SESSION_MAX_AGE_SECONDS, PIN_MIN_LENGTH, PIN_MAX_LENGTH,
    ADMIN_SIGLA, LOJAS, HORARIOS_BASE, AVENCA_THRESHOLD, MESES_LONGOS, MESES_CURTOS,
    RANK_LEVELS, RANK_LABELS,
    TROCA_STATES_OPEN, TROCA_STATES_CLOSED, TROCA_STATE_LABELS, TROCA_TIPO_LABELS,
    HISTORICAL_MIN_WEEKS,
)
from models import (
    Turno, Funcionario, Disponibilidade, SemanaConfig,
    HorarioBloco, HorarioSupressed, AuditLog,   # AuditLog re-export: tests
    TrocaPedido, TrocaCandidatura, LojaHeadcountTarget, Descarga,
)
from seed_data import FUNCIONARIOS_SEED, TURNOS_SEED
from helpers.auth import (
    hash_pin, verify_pin, _looks_hashed, is_weak_pin, invitation_digest,
    login_rate_limited, login_record_failure, login_clear_attempts,
    LOGIN_ATTEMPTS, LOGIN_MAX,                   # re-export: tests
    get_csrf_token, _csrf_token_from_request,
)
from helpers.time_helpers import (
    week_from_monday, mondays_from_current_week,
    week_codigo, resolve_week_codigo, week_label, date_long, iso_utc, local_str,
    horario_to_slot, dia_label_to_idx,
    shift_bounds, semana_start_date, semanas_chronological,
    descarga_horario_for, descarga_hours, slots_blocked_by,
    _dia_label_to_idx, _shift_bounds, _shifts_overlap, _shift_hours,
)
from helpers.scoring import (
    _load_prior_weeks, _score_candidate, _score_best_slot_for_loja,
    p75_ceiling,
)
from helpers.suggestions import build_suggestions
from helpers.week_navigation import schedule_week_navigation
from helpers.audit import audit
from helpers.donations import (
    is_donation, shift_identity, shift_started, transaction_audit, begin_write,
    troca_transaction, migration_donation_review, ACTIVE_CANDIDATES, DONATION_STATES,
)
from helpers.publication import (
    schedule_turno, schedule_descarga, schedule_semana, published_read,
    migration_legacy_publication, install_schedule_audit, publication_info,
    latest_release, snapshot, fingerprint, differences, release_week,
)
from models import PublishedTurno, PublishedDescarga, PublishedSemana, ScheduleRelease

# ═══════════════════════════════════════════════════════════════════════════════
# APP / CONFIG / SECURITY POSTURE
# ═══════════════════════════════════════════════════════════════════════════════
# This whole header is the security boundary. Read it before touching anything.
#
# Threat model:
#   - Multi-tenant browser sessions from untrusted phones/desktops
#   - The supervisor account is the high-value target (full read+write)
#   - Funcionarios are semi-trusted: they shouldn't be able to read other
#     funcionarios' personal data or write to the schedule
#   - Network is assumed hostile (cafe wifi etc) — we set Secure cookies when
#     served over HTTPS, and Strict-Transport-Security to keep it that way
# ═══════════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
base_dir = os.path.abspath(os.path.dirname(__file__))

# Logger — written to stderr by default; in production point this somewhere
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('amorino')

# ── DB path ────────────────────────────────────────────────────────────────────
_db_path = os.environ.get('AMORINO_DB_PATH') or os.path.join(base_dir, 'amorino.db')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + _db_path
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
# A real WSGI server serves requests on many threads at once, so more than one
# connection is live at a time. SQLite's default rollback journal takes a lock
# that excludes readers for the whole write, so a supervisor saving the escala
# stalls every employee reading it — and past `timeout` they get
# "database is locked" as a 500. Raised to 30s so a slow write waits instead of
# failing. The PRAGMAs themselves are per-connection, so they go on `connect`.
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {'connect_args': {'timeout': 30}}

# ── Secret key (signs session cookies, CSRF tokens) ────────────────────────────
# CRITICAL: in production, set AMORINO_SECRET_KEY to a long random string.
# If we detect the default value, log a loud warning at startup.
_DEFAULT_SECRET = 'amorino-gelato-2026'
_env_secret = os.environ.get('AMORINO_SECRET_KEY', '')
app.secret_key = _env_secret or _DEFAULT_SECRET
if app.secret_key == _DEFAULT_SECRET:
    log.warning('═' * 70)
    log.warning('AMORINO_SECRET_KEY is set to the DEFAULT value!')
    log.warning('This is OK for local dev but UNSAFE in production.')
    log.warning('Set the env var to a long random string. To generate one:')
    log.warning("  python -c \"import secrets; print(secrets.token_hex(32))\"")
    log.warning('═' * 70)

# ── Cookie / session hardening ─────────────────────────────────────────────────
# - HttpOnly: JS can't read the cookie (mitigates XSS cookie theft)
# - SameSite=Lax: cookie not sent on cross-site POSTs (mitigates basic CSRF)
# - Secure: only sent over HTTPS — automatically enabled when AMORINO_HTTPS=1
# - Session expires after 12 hours of inactivity (was: forever)
_force_https = os.environ.get('AMORINO_HTTPS', '').lower() in ('1', 'true', 'yes')
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=_force_https,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    SESSION_REFRESH_EACH_REQUEST=True,
    # Block forms larger than 256 KB — guard against accidental/malicious huge submits
    MAX_CONTENT_LENGTH=256 * 1024,
)

# ── Reverse proxy ─────────────────────────────────────────────────────────────
# A proxy terminating TLS forwards plain HTTP to us, so WSGI reports the scheme
# as 'http' and the host as whatever we're bound to. Everything derived from the
# request URL is then wrong in exactly the way that is invisible in dev:
# url_for(..., _external=True) builds the iCal subscription link an employee
# copies into their phone, and it would hand out http:// — the calendar client
# doesn't honour HSTS, so the feed token would go over the wire in clear on
# every poll. ProxyFix reads X-Forwarded-Proto/Host/For and puts the real values
# back into the WSGI environ, which also gives request.remote_addr the caller's
# IP instead of the proxy's, so the login rate limit counts the right thing.
#
# SECURITY: those headers are forgeable by anyone who can reach the app
# directly, so this is opt-in on the same flag helpers.auth._client_ip() uses.
# Set AMORINO_TRUST_PROXY=1 only when a proxy really does rewrite them, and only
# with the app bound to loopback so nothing else can reach it.
_trust_proxy = os.environ.get('AMORINO_TRUST_PROXY', '').lower() in ('1', 'true', 'yes')
if _trust_proxy:
    from werkzeug.middleware.proxy_fix import ProxyFix
    # x_*=1 — trust exactly one hop, our own proxy. A larger number would let a
    # client prepend entries and choose what we read back.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

db.init_app(app)


# ── SQLite per-connection PRAGMAs ─────────────────────────────────────────────
# Applied on every new connection, because PRAGMAs are connection-scoped and the
# pool opens one per thread.
#   journal_mode=WAL — readers no longer block the writer and vice versa. This
#     is the setting that decides whether concurrent use works at all: without
#     it every write locks the whole file. WAL is persistent (stored in the DB
#     header), so setting it once would be enough, but a fresh file (tests,
#     a restored backup) needs it too, hence every connect.
#   synchronous=NORMAL — the safe pairing for WAL: durable across a process
#     crash, and only at risk of losing the last commits if the machine itself
#     loses power. FULL costs an fsync per commit for a guarantee a schedule
#     app doesn't need.
#   foreign_keys=ON — SQLite ignores FK constraints unless asked, per connection.
#
# WAL needs a real filesystem (it memory-maps an index alongside the DB), so it
# does not work over SMB/NFS. If amorino.db is ever moved onto a network share
# this must come back out — that is a deployment constraint, not a preference.
@sa_event.listens_for(Engine, 'connect')
def _sqlite_pragmas(dbapi_conn, _rec):
    if not isinstance(dbapi_conn, sqlite3.Connection):
        return
    cur = dbapi_conn.cursor()
    try:
        cur.execute('PRAGMA journal_mode=WAL')
        cur.execute('PRAGMA synchronous=NORMAL')
        cur.execute('PRAGMA foreign_keys=ON')
    finally:
        cur.close()


# ── Supervisor credentials ─────────────────────────────────────────────────────
# The supervisor account is the highest-value target in the whole app: full
# read+write over every employee's data. It gets held to AT LEAST the standard
# we already impose on employees at first login (helpers.auth.is_weak_pin) —
# it made no sense to refuse '1234' from a funcionário while shipping it as the
# supervisor default.
#
# Weak here means blocklisted, all-identical, a digit run, or equal to 'ADMIN'.
# In a production posture (AMORINO_HTTPS=1) that is FATAL: the app refuses to
# start rather than come up guessable. Without it we assume local dev and only
# warn, so the default keeps working for tests and local runs.
SUPERVISOR_PIN = os.environ.get('AMORINO_SUPERVISOR_PIN', '1234')
if is_weak_pin(SUPERVISOR_PIN, ADMIN_SIGLA):
    _how = ('is the default "1234"' if SUPERVISOR_PIN == '1234'
            else 'is trivially guessable')
    if _force_https:
        raise RuntimeError(
            f'AMORINO_SUPERVISOR_PIN {_how}. Refusing to start with '
            f'AMORINO_HTTPS=1 (production posture). Set a strong value: '
            f'python -c "import secrets; print(secrets.token_hex(8))"'
        )
    log.warning('═' * 70)
    log.warning('AMORINO_SUPERVISOR_PIN %s — the same PIN the app REFUSES', _how)
    log.warning('from employees at first login. Fine for local dev; with')
    log.warning('AMORINO_HTTPS=1 the app will refuse to start.')
    log.warning('═' * 70)

# ── PIN policy (constants imported from config; nothing to declare here) ─────

# ── PIN hashing (FIX BUG 13) ──────────────────────────────────────────────────
# Old DBs may still have plaintext PINs. verify_pin handles both: if the stored
# value doesn't look hashed (no algorithm prefix like 'pbkdf2:sha256$...'), we
# fall back to constant-time plaintext compare. On any successful login the
# caller should re-hash and persist via hash_pin().
# Auth helpers (hash_pin, verify_pin, login_*, CSRF tokens) moved to helpers/auth.py.

def _enforce_session_lifetime():
    """SECURITY: hard-cap session age regardless of cookie tampering.
    Checks login_at vs now; if expired, drops the session before any handler
    sees a stale authenticated user.
    """
    if 'login_at' not in session:
        return
    try:
        login_at = datetime.fromisoformat(session['login_at'])
    except (ValueError, TypeError):
        # Malformed — kick the session
        session.clear()
        return
    age = (datetime.now(timezone.utc) - login_at).total_seconds()
    if age > SESSION_MAX_AGE_SECONDS:
        sigla = session.get('user_sigla', '')
        session.clear()
        log.info('Session expired for %s (age=%.0fs)', sigla, age)


# FIX: this used to be defined but never registered. Sessions could outlive
# the 12h budget if a cookie was kept fresh client-side. Now enforced
# server-side on every request, before any handler runs.
@app.before_request
def _session_lifetime_hook():
    _enforce_session_lifetime()
    sigla = session.get('user_sigla')
    if sigla and not session.get('supervisor'):
        employee = db.session.get(Funcionario, sigla)
        # Old signed sessions predate access_version and therefore represent
        # version 0. A reset increments it; every route then rejects replay.
        if (not employee or not employee.pin or
                session.get('access_version', 0) != employee.access_version):
            session.clear()


# Endpoints that are exempt from CSRF entirely (no session yet, or by design)
_CSRF_EXEMPT_ENDPOINTS = {'login', 'static'}


@app.before_request
def _csrf_protect():
    # Only state-changing methods need protection
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return
    # Exempt endpoints (e.g. login, before any session exists)
    if request.endpoint in _CSRF_EXEMPT_ENDPOINTS:
        return
    # Skip CSRF if no session is established (the endpoint will reject the
    # unauthenticated request itself via @login_required)
    if 'user_sigla' not in session and request.endpoint != 'enroll_access':
        return
    supplied = _csrf_token_from_request()
    expected = session.get('_csrf', '')
    if not expected or not supplied or not hmac.compare_digest(str(expected), str(supplied)):
        log.warning('CSRF rejected: endpoint=%s ip=%s sigla=%s',
                    request.endpoint, request.remote_addr, session.get('user_sigla'))
        # Don't leak details — generic 403
        abort(403)

# Make get_csrf_token() callable from Jinja templates as {{ csrf_token() }}
@app.context_processor
def _inject_csrf():
    return {'csrf_token': get_csrf_token}


# ── Static asset caching ──────────────────────────────────────────────────────
# Flask's default is Cache-Control: no-cache, which makes the browser revalidate
# every static file on every navigation. That is 7 round-trips and 140 KB of CSS
# and fonts per page — paid again on each click, on a phone, in a shop. These
# files change only on deploy, so they get a year instead.
#
# A year-long cache is only safe if a deploy can invalidate it, which is what
# asset() below does. Anything NOT routed through asset() (the fonts, the
# favicons, the PWA icons) keeps that year with no way to bust it, which is the
# right trade for those specific files: their content is fixed and a new version
# would arrive under a new filename. If one is ever edited in place, rename it.
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = timedelta(days=365)


def asset(filename: str) -> str:
    """URL for a file in static/, carrying a version taken from its mtime+size.

    Edit static/app.css and the URL changes, so the browser fetches the new file
    instead of serving the year-old copy it was told to keep. stat() per call is
    far cheaper than the revalidation round-trip it replaces, and reading it live
    rather than at import means an edit lands without restarting the server.
    """
    path = os.path.join(app.static_folder, *filename.split('/'))
    try:
        st = os.stat(path)
        version = f'{int(st.st_mtime):x}-{st.st_size:x}'
    except OSError:
        # Missing file: emit the plain URL and let the 404 be the visible signal
        # rather than inventing a version for something that isn't there.
        return f'/static/{filename}'
    return f'/static/{filename}?v={version}'


@app.context_processor
def _inject_asset():
    return {'asset': asset}


# Stored timestamps are UTC; people read local time. `{{ x|hora_local }}`.
app.add_template_filter(local_str, 'hora_local')

# ── Security headers (applied to every response) ───────────────────────────────
@app.after_request
def _set_security_headers(resp):
    # Defence in depth: layered headers. Each line is a known-good attack mitigation.
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    resp.headers.setdefault(
        'Permissions-Policy',
        'geolocation=(), microphone=(), camera=(), payment=(), usb=()'
    )
    # CSP: this app uses inline styles and inline scripts (legacy). We keep
    # 'unsafe-inline' for both for now, but disallow inline event handlers in
    # *new* code where possible. Future improvement: nonce-based CSP.
    # The font-src + style-src external entries cover Google Fonts.
    resp.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        # Fonts are now self-hosted (see static/app-fonts.css) — no Google CDN,
        # so style-src/font-src no longer need to allow fonts.googleapis.com /
        # fonts.gstatic.com. Tighter is better.
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "manifest-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )
    # HSTS only meaningful over HTTPS — only set it if we're behind one
    if _force_https:
        resp.headers.setdefault(
            'Strict-Transport-Security',
            'max-age=31536000; includeSubDomains'
        )
    # Sensitive HTML pages: prevent caching to the disk so a shared computer
    # doesn't expose the schedule via the back button after logout.
    # /login is in scope too: a cached copy of it would outlive any change to
    # the page, and it used to carry the whole staff roster.
    if resp.mimetype == 'text/html' and request.endpoint not in (None, 'static'):
        resp.headers.setdefault('Cache-Control', 'no-store, private, max-age=0')
        resp.headers.setdefault('Pragma', 'no-cache')
    return _maybe_gzip(resp)


# ── Compression ───────────────────────────────────────────────────────────────
# The rendered pages are large: /escala is ~197 KB of HTML, /equipa ~131 KB, and
# the schedule is read on phones over store wifi and mobile data. gzip takes
# those to roughly a tenth. A reverse proxy would normally do this, but the app
# must not depend on one being configured — an uncompressed 197 KB page is the
# difference between "instant" and "slow" on a bad connection, and it should not
# hinge on a line in someone's nginx config. If a proxy does compress, it sees
# Content-Encoding already set and passes the body through untouched.
_GZIP_MIN_BYTES = 1024          # below this, framing costs more than it saves
_GZIP_MIMETYPES = frozenset({
    'text/html', 'text/css', 'text/plain', 'text/calendar', 'text/javascript',
    'application/javascript', 'application/json', 'image/svg+xml',
    'application/manifest+json',
})


def _maybe_gzip(resp):
    # send_file() responses stream from a file handle; get_data() would defeat
    # that, and static files are the one thing a proxy reliably compresses.
    if resp.direct_passthrough:
        return resp
    if not (200 <= resp.status_code < 300) or resp.status_code == 204:
        return resp
    if resp.mimetype not in _GZIP_MIMETYPES or 'Content-Encoding' in resp.headers:
        return resp
    # Announce the negotiation even when we don't compress: a cache that saw an
    # uncompressed response must not serve it to a client that asked for gzip.
    resp.vary.add('Accept-Encoding')
    if 'gzip' not in (request.headers.get('Accept-Encoding') or '').lower():
        return resp
    data = resp.get_data()
    if len(data) < _GZIP_MIN_BYTES:
        return resp
    resp.set_data(gzip.compress(data, 6))
    resp.headers['Content-Encoding'] = 'gzip'
    return resp


# ── Health probe ──────────────────────────────────────────────────────────────
@app.route('/healthz')
def healthz():
    """Liveness check for the reverse proxy, systemd, or an uptime monitor.

    Unauthenticated by necessity — a monitor holds no session — so it discloses
    nothing: no version, no hostname, no row counts. It reports the one failure a
    TCP check cannot see, which is the process still accepting connections while
    every page 500s because the database went away (disk full, file moved, a
    restore left the WAL behind).
    """
    try:
        db.session.execute(text('SELECT 1'))
        body, code = jsonify({'ok': True}), 200
    except Exception as e:
        db.session.rollback()
        log.error('healthz: database unreachable: %s', e)
        body, code = jsonify({'ok': False}), 503
    # A cached 200 would keep reporting healthy after the app stopped being.
    body.headers['Cache-Control'] = 'no-store'
    return body, code


# ── Error handlers ──────────────────────────────────────────────────────────────
# Render the branded erro.html instead of Werkzeug's default white page. API
# routes (/api/*) get JSON so a fetch() never receives an HTML body it can't parse.

def _wants_json() -> bool:
    return (request.path.startswith('/api/')
            or request.is_json
            or 'application/json' in (request.headers.get('Accept') or ''))


def _error_response(code: int, msg: str):
    if _wants_json():
        return jsonify({'ok': False, 'erro': msg}), code
    try:
        return render_template('erro.html', msg=msg), code
    except Exception:
        # Last-resort fallback if template rendering itself fails.
        return msg, code


@app.errorhandler(403)
def _err_403(e):
    return _error_response(403, 'Sem permissão para aceder a esta página.')


@app.errorhandler(404)
def _err_404(e):
    return _error_response(404, 'Página não encontrada.')


@app.errorhandler(413)
def _err_413(e):
    return _error_response(413, 'O pedido é demasiado grande.')


@app.errorhandler(500)
def _err_500(e):
    # A 500 may have left the DB session in a broken state — roll it back so the
    # error page (and the next request) start clean.
    try:
        db.session.rollback()
    except Exception:
        pass
    log.error('unhandled 500: %s', e)
    return _error_response(500, 'Ocorreu um erro inesperado. Tentar novamente dentro de momentos.')


# ── Audit log ──────────────────────────────────────────────────────────────────
# Records sensitive supervisor actions so anomalies can be investigated.
# Kept simple — append-only table, no UI yet (read with sqlite3 directly).

# Models moved to models.py — see imports at top.

# Valid state transitions — the only path the API will permit.
# Open requests (tipo=aberto_*) sit at 'aberto' until the author picks a winner.
# Constants + seed data moved to config.py / seed_data.py.

# Week helpers moved to helpers/time_helpers.py.

# ── DB init & seed ─────────────────────────────────────────────────────────────

def seed_db():
    if os.environ.get('AMORINO_SEED', '1') == '0':
        return
    if Funcionario.query.count() == 0:
        for row in FUNCIONARIOS_SEED:
            sigla, nome, horas, tipo = row
            db.session.add(Funcionario(sigla=sigla, nome=nome,
                                       horas_contrato=horas, tipo_contrato=tipo))
        db.session.commit()

    if schedule_turno().query.count() == 0:
        for loja, horario, dia, func in TURNOS_SEED:
            db.session.add(Turno(loja=loja, horario=horario, dia=dia,
                                 funcionario=func, semana='11-17mai'))
        db.session.commit()

    if SemanaConfig.query.count() == 0:
        # FIX BUG 12: previously the seed always created a fixed week (11-17 May 2026).
        # Now we keep that as a historical/demo week (matching the seeded TURNOS_SEED)
        # AND create a "current" active week so the app is usable out of the box at any
        # time. The historical week stays as `ativa=False`; the new week is `ativa=True`.
        dias_seed = [
            {'label':'Seg 11','date':'2026-05-11','feriado':False,'feriado_nome':''},
            {'label':'Ter 12','date':'2026-05-12','feriado':False,'feriado_nome':''},
            {'label':'Qua 13','date':'2026-05-13','feriado':False,'feriado_nome':''},
            {'label':'Qui 14','date':'2026-05-14','feriado':False,'feriado_nome':''},
            {'label':'Sex 15','date':'2026-05-15','feriado':False,'feriado_nome':''},
            {'label':'Sab 16','date':'2026-05-16','feriado':False,'feriado_nome':''},
            {'label':'Dom 17','date':'2026-05-17','feriado':False,'feriado_nome':''},
        ]
        db.session.add(SemanaConfig(
            codigo='11-17mai', label='11 a 17 de Maio',
            dias_json=json.dumps(dias_seed), ativa=False))
        # The active week = monday-of-this-week (or today if it's Monday)
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        codigo = week_codigo(monday)
        # Avoid colliding with the historical 11-17mai seed code in the (unlikely)
        # case it lands on the same week.
        if codigo != '11-17mai':
            db.session.add(SemanaConfig(
                codigo=codigo, label=week_label(monday),
                dias_json=json.dumps(week_from_monday(monday)), ativa=True))
        else:
            # Mark the seeded historical week active instead of creating a duplicate
            SemanaConfig.query.filter_by(codigo='11-17mai').update({'ativa': True})
        db.session.commit()

    # ── Seed LojaHeadcountTarget defaults from observed peak headcount ──────
    # We compute "how many people typically work loja X on weekday Y" by looking
    # at TURNOS_SEED. If no targets exist yet, set them from that data; otherwise
    # leave the supervisor's edits alone.
    if LojaHeadcountTarget.query.count() == 0:
        # Map "Seg 11" → 0 (Monday), "Ter 12" → 1, etc.
        _wd_map = {'Seg': 0, 'Ter': 1, 'Qua': 2, 'Qui': 3, 'Sex': 4, 'Sab': 5, 'Dom': 6}
        counts = {}  # (loja, dia_idx) -> headcount
        for loja, _horario, dia_label, _func in TURNOS_SEED:
            wd = _wd_map.get((dia_label or '')[:3])
            if wd is None:
                continue
            counts[(loja, wd)] = counts.get((loja, wd), 0) + 1
        # If a (loja, weekday) had zero seed turnos, fall back to 3 (a reasonable
        # default — supervisor can adjust later).
        for loja_dict in LOJAS:
            for wd in range(7):
                tgt = counts.get((loja_dict['id'], wd), 3)
                db.session.add(LojaHeadcountTarget(
                    loja=loja_dict['id'], dia_idx=wd, target=tgt
                ))
        db.session.commit()


def _repair_week_labels() -> int:
    """Fix week labels written by older code; returns how many were changed.

    scripts/import_history.py used to build its own label and ignored month
    boundaries — the week of 27 Apr–3 May read "27 a 3 de Maio" — and the month
    list spelled March "Marco". Re-derive the label from the week's own Monday,
    but only where the stored text is exactly one of those machine-written
    shapes, never a label someone could have chosen. Labels are display-only
    (weeks are keyed by `codigo`), and this is a no-op once they are right, so
    it runs on every boot.
    """
    fixed = 0
    for sem in SemanaConfig.query.all():
        try:
            mon = date.fromisoformat(semana_start_date(sem))
        except ValueError:
            continue
        good = week_label(mon)
        if sem.label == good:
            continue
        end = mon + timedelta(days=6)
        naive = f"{mon.day} a {end.day} de {MESES_LONGOS[end.month - 1]}"
        legacy = {naive, naive.replace('Março', 'Marco'), good.replace('Março', 'Marco')}
        if sem.label in legacy:
            sem.label = good
            fixed += 1
    if fixed:
        db.session.commit()
    return fixed


with app.app_context():
    db.create_all()
    # Migrations for existing DBs
    with db.engine.connect() as conn:
        cols_f = [r[1] for r in conn.execute(text("PRAGMA table_info(funcionarios)")).fetchall()]
        if 'pin' not in cols_f:
            # FIX BUG 13: store hashed PINs going forward; column wide enough for pbkdf2.
            conn.execute(text("ALTER TABLE funcionarios ADD COLUMN pin TEXT DEFAULT ''"))
        if 'tipo_contrato' not in cols_f:
            conn.execute(text("ALTER TABLE funcionarios ADD COLUMN tipo_contrato TEXT DEFAULT 'contrato'"))
            conn.execute(text(
                f"UPDATE funcionarios SET tipo_contrato='turnos' WHERE horas_contrato < {AVENCA_THRESHOLD}"
            ))
        if 'rank' not in cols_f:
            conn.execute(text("ALTER TABLE funcionarios ADD COLUMN rank TEXT DEFAULT 'staff'"))
        if 'loja_gerida' not in cols_f:
            conn.execute(text("ALTER TABLE funcionarios ADD COLUMN loja_gerida TEXT NOT NULL DEFAULT ''"))
        if 'ical_token' not in cols_f:
            # FEATURE: per-funcionario iCal subscription token; populated lazily
            # on first /api/cal-link request.
            conn.execute(text("ALTER TABLE funcionarios ADD COLUMN ical_token TEXT DEFAULT ''"))
        for column, declaration in (
                ('access_version', 'INTEGER NOT NULL DEFAULT 0'),
                ('enrollment_hash', "TEXT NOT NULL DEFAULT ''"),
                ('enrollment_expires_at', 'DATETIME'),
                ('enrollment_kind', "TEXT NOT NULL DEFAULT ''")):
            if column not in cols_f:
                conn.execute(text(f"ALTER TABLE funcionarios ADD COLUMN {column} {declaration}"))
        # FEATURE: Central — add 'tipo' column to existing trocas table (if it exists).
        # Only do this if the trocas table exists already (from a previous deploy).
        trocas_exists = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='trocas'")
        ).fetchone()
        if trocas_exists:
            cols_tr = [r[1] for r in conn.execute(text("PRAGMA table_info(trocas)")).fetchall()]
            if 'tipo' not in cols_tr:
                conn.execute(text("ALTER TABLE trocas ADD COLUMN tipo TEXT DEFAULT 'direto'"))
            if 'origem_snapshot_json' not in cols_tr:
                conn.execute(text("ALTER TABLE trocas ADD COLUMN origem_snapshot_json TEXT NOT NULL DEFAULT ''"))
        cols_t = [r[1] for r in conn.execute(text("PRAGMA table_info(turnos)")).fetchall()]
        if 'semana' not in cols_t:
            conn.execute(text("ALTER TABLE turnos ADD COLUMN semana TEXT DEFAULT '11-17mai'"))
        if 'updated_at' not in cols_t:
            # FEATURE 25: backfill with NULL — existing rows simply report "—" in the UI.
            conn.execute(text("ALTER TABLE turnos ADD COLUMN updated_at DATETIME"))
        cols_s = [r[1] for r in conn.execute(text("PRAGMA table_info(semanas)")).fetchall()]
        if cols_s and 'dias_json' not in cols_s and 'dias' in cols_s:
            conn.execute(text("ALTER TABLE semanas ADD COLUMN dias_json TEXT DEFAULT '[]'"))
        if cols_s and 'descarga_dias' not in cols_s:
            conn.execute(text("ALTER TABLE semanas ADD COLUMN descarga_dias TEXT DEFAULT ''"))
        if cols_s and 'descarga_lojas' not in cols_s:
            conn.execute(text("ALTER TABLE semanas ADD COLUMN descarga_lojas TEXT DEFAULT ''"))
        if cols_s and 'descarga_config' not in cols_s:
            conn.execute(text("ALTER TABLE semanas ADD COLUMN descarga_config TEXT DEFAULT ''"))
        cols_d = [r[1] for r in conn.execute(text("PRAGMA table_info(disponibilidades)")).fetchall()]
        if cols_d and 'lojas_pref' not in cols_d:
            conn.execute(text("ALTER TABLE disponibilidades ADD COLUMN lojas_pref TEXT DEFAULT ''"))
        if cols_d and 'slots_pref' not in cols_d:
            conn.execute(text("ALTER TABLE disponibilidades ADD COLUMN slots_pref TEXT DEFAULT ''"))
        if cols_d and 'descarga_opt' not in cols_d:
            conn.execute(text("ALTER TABLE disponibilidades ADD COLUMN descarga_opt BOOLEAN DEFAULT 0"))

        # One-time rename of the legacy 'fecho' slot code to 'noite'. Safe to run
        # every boot — touches only rows that still contain the old token.
        try:
            conn.execute(text(
                "UPDATE disponibilidades SET slots_pref = "
                "REPLACE(slots_pref, 'fecho', 'noite') "
                "WHERE slots_pref LIKE '%fecho%'"))
        except Exception:
            pass
        conn.commit()
    # ── One-time data migrations for the descarga rework ──────────────────────
    # (a) 'extra' availability becomes an independent descarga opt-in flag.
    _ex = Disponibilidade.query.filter_by(tipo='extra').all()
    for _r in _ex:
        _r.descarga_opt = True; _r.tipo = ''
    # (b) Build per-day descarga config from the legacy week-level fields, once.
    _all_ids = [l['id'] for l in LOJAS]
    for _sem in SemanaConfig.query.all():
        if (_sem.descarga_config or '').strip():
            continue
        _days = [d.strip() for d in (_sem.descarga_dias or '').split(',') if d.strip()]
        if not _days:
            continue
        _raw = (_sem.descarga_lojas or '').strip()
        if _raw == '__none__':
            _stores = []
        else:
            _sel = [x.strip() for x in _raw.split(',') if x.strip() in _all_ids]
            _stores = _sel if _sel else _all_ids
        _sem.descarga_config = json.dumps(
            {d: {lid: descarga_horario_for(lid) for lid in _stores} for d in _days})
    db.session.commit()
    _repair_week_labels()
    seed_db()
    migration_legacy_publication()
    migration_donation_review()

# ── Helpers ────────────────────────────────────────────────────────────────────

def _descarga_config(s):
    """Per-day descarga config for a week: {dia_label: {loja_id: 'HH:MM-HH:MM'}}.
    Source of truth is SemanaConfig.descarga_config (JSON). A day is a descarga
    day iff it maps to >=1 store."""
    raw = (getattr(s, 'descarga_config', '') or '').strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    all_ids = {l['id'] for l in LOJAS}
    out = {}
    for dia, stores in (data.items() if isinstance(data, dict) else []):
        if not isinstance(stores, dict):
            continue
        cell = {lid: hor for lid, hor in stores.items()
                if lid in all_ids and isinstance(hor, str) and hor.strip()}
        if cell:
            out[dia] = cell
    return out


def get_semana_ativa():
    s = SemanaConfig.query.filter_by(ativa=True).order_by(SemanaConfig.id.desc()).first()
    if s:
        try:
            s.dias_list = json.loads(s.dias_json or '[]')
        except json.JSONDecodeError:
            s.dias_list = []
        s.day_labels = [d['label'] for d in s.dias_list]
        s.feriados   = {d['label']: d['feriado_nome'] for d in s.dias_list if d['feriado']}
        s.descarga_map = _descarga_config(s)
        s.descarga_set = {d for d, st in s.descarga_map.items() if st}
        s.descarga_lojas_set = set().union(*s.descarga_map.values()) if s.descarga_map else set()
    return s

def _hydrate_semana(s):
    """Attach dias_list / feriados / descarga_set to a SemanaConfig row.
    Same as what get_semana_ativa does — extracted so other helpers can reuse it."""
    if not s:
        return s
    if not hasattr(s, 'dias_list'):
        try:
            s.dias_list = json.loads(s.dias_json or '[]')
        except json.JSONDecodeError:
            s.dias_list = []
    if not hasattr(s, 'feriados'):
        s.feriados   = {d['label']: d['feriado_nome'] for d in s.dias_list if d.get('feriado')}
    if not hasattr(s, 'descarga_map'):
        s.descarga_map = _descarga_config(s)
    if not hasattr(s, 'descarga_set'):
        s.descarga_set = {d for d, st in s.descarga_map.items() if st}
    if not hasattr(s, 'descarga_lojas_set'):
        s.descarga_lojas_set = set().union(*s.descarga_map.values()) if s.descarga_map else set()
    if not hasattr(s, 'day_labels'):
        s.day_labels = [d['label'] for d in s.dias_list]
    return s

def _semana_terminou(semana) -> bool:
    """True once every day of the week is behind us."""
    if not semana:
        return False
    try:
        dias = json.loads(semana.dias_json or '[]')
    except json.JSONDecodeError:
        return False
    ultimo = (dias[-1].get('date') if dias else '') or ''
    return bool(ultimo) and ultimo < date.today().isoformat()


def _semana_contains(semana, day_iso: str) -> bool:
    """True iff the given ISO date (YYYY-MM-DD) falls inside this semana's day list."""
    try:
        dias = json.loads(semana.dias_json or '[]')
    except json.JSONDecodeError:
        return False
    return any(d.get('date') == day_iso for d in dias)

def get_semana_for_employee():
    """The semana that the employee dashboard should display.

    Rule (decided with the owner): prefer the active week if it contains today;
    otherwise return the OLDEST semana that contains today (i.e. the week the
    funcionario is actually living right now, even if the supervisor has
    already started drafting the next one). If nothing matches today, fall back
    to the active week (so the page is never empty).

    Why "oldest containing today" for ties: when two weeks both contain today,
    one is the week-being-worked and the other is the not-yet-finalized next
    one. The funcionario cares about the one they're actually in.
    """
    today_iso = date.today().isoformat()
    ativa_draft = get_semana_ativa()
    ativa = (schedule_semana().query.filter_by(codigo=ativa_draft.codigo).first()
             if ativa_draft else None)
    if ativa and _semana_contains(ativa, today_iso):
        return _hydrate_semana(ativa)
    # Find any week that contains today; pick the oldest (lowest id)
    candidates = schedule_semana().query.order_by(schedule_semana().id.asc()).all()
    for s in candidates:
        if _semana_contains(s, today_iso):
            return _hydrate_semana(s)
    # No match — fall back to the active week (could be None)
    return _hydrate_semana(ativa or (candidates[-1] if candidates else None))

def current_user_sigla():
    """Sigla currently logged in, or None. ADMIN_SIGLA indicates the supervisor."""
    return session.get('user_sigla')

def current_user_func():
    """The Funcionario row for the logged-in user, or None.
    Returns None for both 'not logged in' and 'logged in as ADMIN'."""
    sigla = current_user_sigla()
    if not sigla or sigla == ADMIN_SIGLA:
        return None
    return db.session.get(Funcionario, sigla)

def is_supervisor() -> bool:
    return bool(session.get('supervisor'))

def safe_referrer(default_endpoint: str) -> str:
    """Return request.referrer only if it's a same-site relative path; else
    fall back to url_for(default_endpoint). FIX BUG 6."""
    ref = request.referrer or ''
    # Accept only paths that start with a single '/' (not '//' protocol-relative)
    # and contain no scheme — a quick guard against open-redirect via Referer.
    # Backslashes are rejected too: browsers normalise '/\evil.com' to
    # '//evil.com', so a path that merely *starts* with a single '/' is not by
    # itself same-site. _is_safe_next() has always covered this; the Referer
    # path had not, despite SECURITY.md describing both the same way.
    if (ref.startswith('/') and not ref.startswith('//')
            and '://' not in ref and '\\' not in ref):
        return ref
    return url_for(default_endpoint)

@app.context_processor
def inject_user():
    """Make auth state available to every template."""
    sigla = current_user_sigla()
    func  = current_user_func()
    return {
        'cu_sigla':      sigla,
        'cu_func':       func,
        'cu_is_admin':   sigla == ADMIN_SIGLA,
        'cu_is_supervisor': is_supervisor(),
        'cu_display':    (ADMIN_SIGLA if sigla == ADMIN_SIGLA
                          else (func.nome if func else (sigla or ''))),
    }

def login_required(f):
    """Any authenticated user (employee or supervisor).

    SECURITY: also verifies the user-in-session still exists in the DB.
    If a funcionario was deleted while their session was alive, that session
    becomes invalid immediately — no zombie sessions accessing data they
    shouldn't see.

    /api/* gets 401 JSON instead of a redirect. A redirect is answered with the
    login PAGE, and fetch() follows it transparently, so every call site's
    `await res.json()` threw on the HTML — the button did nothing at all and the
    user got no clue their 12-hour session had run out. `supervisor_required`
    already made this distinction; this decorator did not.
    """
    def _reject(next_path: str = ''):
        if request.path.startswith('/api/'):
            return jsonify({'ok': False,
                            'erro': 'A sessão expirou. Iniciar sessão novamente.',
                            'sessao_expirada': True}), 401
        return redirect(url_for('login', next=next_path) if next_path
                        else url_for('login'))

    @wraps(f)
    def decorated(*args, **kwargs):
        sigla = current_user_sigla()
        if not sigla:
            return _reject(request.path)
        # Supervisor session: sigla must be ADMIN; otherwise the session is
        # malformed (could be a session-replay attack or session-fixation
        # remnant — either way, drop it).
        if is_supervisor():
            if sigla != ADMIN_SIGLA:
                log.warning('Malformed supervisor session: sigla=%r, dropping', sigla)
                session.clear()
                return _reject(request.path)
        else:
            # Regular user — verify the funcionario row exists
            if not db.session.get(Funcionario, sigla):
                log.info('Session for deleted funcionario %s — clearing', sigla)
                session.clear()
                return _reject()
        return f(*args, **kwargs)
    return decorated

def supervisor_required(f):
    """Supervisor only.

    Calls made by fetch() get JSON, never the login page: every /api/* route,
    plus the /supervisor/* endpoints the dashboard POSTs JSON to (create or
    activate a week, the descarga grid). Within that:
      * no session at all → 401 `sessao_expirada`, the same answer
        login_required gives, so base.html's fetch wrapper says the session
        expired and sends the supervisor to sign in again. This used to be a
        403 "Não autenticado" — shown as an error, with no way back — and the
        non-/api endpoints got the login page, which `res.json()` choked on.
      * an employee's session → 403: signed in, but not allowed.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_supervisor():
            if request.path.startswith('/api/') or request.is_json:
                if not current_user_sigla():
                    return jsonify({'ok': False,
                                    'erro': 'A sessão expirou. Iniciar sessão novamente.',
                                    'sessao_expirada': True}), 401
                return jsonify({'ok': False, 'erro': 'Sem permissão.'}), 403
            return redirect(url_for('login', next=request.path))
        # And verify the supervisor session is correctly formed
        if session.get('user_sigla') != ADMIN_SIGLA:
            log.warning('Malformed supervisor session: dropping')
            session.clear()
            return redirect(url_for('login', next=request.path))
        return f(*args, **kwargs)
    return decorated

def build_grid(loja_id: str, semana_str: str, dias: list) -> tuple:
    turnos = schedule_turno().query.filter_by(loja=loja_id, semana=semana_str).all()
    base   = list(HORARIOS_BASE.get(loja_id, []))
    # FIX BUG 4: hide base blocks that the supervisor has suppressed (persisted)
    suppressed = ({r.horario for r in HorarioSupressed.query.filter_by(loja=loja_id).all()}
                  if not published_read() else set())
    if suppressed:
        base = [h for h in base if h not in suppressed]
    # Add persisted custom blocks for this loja (from DB)
    custom = (HorarioBloco.query.filter_by(loja=loja_id).order_by(HorarioBloco.ordem, HorarioBloco.id).all()
              if not published_read() else [])
    seen   = set(base)
    for cb in custom:
        if cb.horario not in seen:
            base.append(cb.horario)
            seen.add(cb.horario)
    # Also pick up any horario used in turnos that isn't in base or custom
    # (defensive: a turno was assigned to a custom shift that has since been deleted
    #  or to a base block that's now suppressed — we still need to render it so the
    #  user can see/fix it).
    extras = []
    for t in turnos:
        if t.horario not in seen:
            seen.add(t.horario)
            extras.append(t.horario)
    horarios = base + extras

    # Sort rows by shift start so custom/extra blocks land where the eye
    # expects (a new '13:00-19:00' between 12:30 and 14:00, not after 17:00).
    # Label-only rows ('Provas Práticas') carry the start of the previous
    # parseable row, so the stable sort keeps them in their configured spot.
    keyed, prev_start = [], 0
    for i, h in enumerate(horarios):
        s, _e = shift_bounds(h)
        if s is None:
            s = prev_start
        else:
            prev_start = s
        keyed.append((s, i, h))
    keyed.sort()
    horarios = [h for (_s, _i, h) in keyed]

    grid = {h: {d: [] for d in dias} for h in horarios}
    for t in turnos:
        if t.horario in grid and t.dia in grid[t.horario]:
            grid[t.horario][t.dia].append({
                'id':    t.id,
                'sigla': t.funcionario,
                'nota':  t.nota or '',
            })
    return grid, horarios

def func_map_with_tipo() -> dict:
    """Returns {sigla: {nome, tipo_contrato, horas_contrato, is_turnos, rank, rank_level}}"""
    return {
        f.sigla: {
            'nome':           f.nome,
            'tipo_contrato':  f.tipo_contrato,
            'horas_contrato': f.horas_contrato,
            'is_turnos':      f.is_turnos,
            'rank':           f.rank or 'staff',
            'loja_gerida':     f.loja_gerida or '',
            'rank_level':     f.rank_level,
            'rank_label':     RANK_LABELS.get(f.rank or 'staff', 'Colaborador'),
        }
        for f in Funcionario.query.all()
    }

# ════════════════════════════════════════════════════════════════════════════════
# PUBLIC ROUTES
# ════════════════════════════════════════════════════════════════════════════════

@app.route('/')
@login_required
def index():
    # The supervisor's index has nothing useful to offer — send them straight
    # to the dashboard where they actually work.
    if is_supervisor():
        return redirect(url_for('supervisor_dashboard'))
    # Use the working-week-for-the-employee, not the supervisor's drafting week.
    # See get_semana_for_employee() for the rule.
    semana = get_semana_for_employee()
    user_data = None
    if semana:
        func = current_user_func()
        if func:
            user_data = _build_user_home(func, semana)
    # That rule picks the week containing today when there is one, and only
    # falls back to the active (drafting) week otherwise — the page labels the
    # week accordingly instead of always calling it "ativa".
    semana_is_current = bool(semana and _semana_contains(semana, date.today().isoformat()))
    return render_template('index.html', semana=semana, user_data=user_data,
                           semana_is_current=semana_is_current)


def _build_user_home(func, semana):
    """Compute the per-user homepage panel: next shift, week hours, disponibilidade status.

    `semana` is the WORKING week (what they're actually living right now). The
    shift data, next-shift, and hours all relate to this week.

    The DISPONIBILIDADE status, however, refers to the DRAFTING (active) week —
    because that's the one they still need to submit availability for. If both
    are the same week, that's fine; if they differ, we show the drafting-week
    status here so the prompt is meaningful.
    """
    semana = _hydrate_semana(semana)

    # Pull all this user's shifts for the WORKING week
    turnos = schedule_turno().query.filter_by(semana=semana.codigo, funcionario=func.sigla).all()

    # Map day label → index for ordering
    day_order = {d['label']: i for i, d in enumerate(semana.dias_list)}
    # And day label → ISO date (for "is today" check)
    day_date  = {d['label']: d.get('date', '') for d in semana.dias_list}
    today_iso = date.today().isoformat()
    # FIX 11: map day → feriado_nome (empty string if not a feriado)
    day_feriado = {d['label']: d.get('feriado_nome', '') for d in semana.dias_list if d.get('feriado')}

    # Loja name lookup
    loja_name = {l['id']: l['nome'] for l in LOJAS}

    # Decorate and sort: (day index, start time, ...)
    def shift_start_minutes(horario):
        m = re.match(r'(\d{1,2}):(\d{2})', horario or '')
        return int(m.group(1)) * 60 + int(m.group(2)) if m else 0

    decorated = []
    total_h = 0.0
    hours_done = 0.0       # hours already worked (shifts whose day is BEFORE today)
    for t in turnos:
        idx = day_order.get(t.dia, 99)
        h   = _shift_hours(t.horario)
        total_h += h
        d_iso = day_date.get(t.dia, '')
        if d_iso and d_iso < today_iso:
            hours_done += h
        decorated.append({
            'id':        t.id,
            'dia':       t.dia,
            'date':      d_iso,
            'horario':   t.horario,
            'loja_id':   t.loja,
            'loja_nome': loja_name.get(t.loja, t.loja),
            'nota':      t.nota or '',
            'horas':     h,
            'feriado':   day_feriado.get(t.dia, ''),
            'is_past':   bool(d_iso and d_iso < today_iso),
            'is_today':  d_iso == today_iso,
            'order':     (idx, shift_start_minutes(t.horario)),
        })
    decorated.sort(key=lambda x: x['order'])

    # Descargas count toward the week's hours too (4h minimum each). They aren't
    # shifts, so they don't join `decorated` / next_shift — only the totals.
    for dc in schedule_descarga().query.filter_by(semana=semana.codigo,
                                       funcionario=func.sigla).all():
        dh = descarga_hours(dc.horario)
        total_h += dh
        d_iso = day_date.get(dc.dia, '')
        if d_iso and d_iso < today_iso:
            hours_done += dh

    # Find next upcoming shift — today or later
    next_shift = None
    for s in decorated:
        if s['date'] and s['date'] >= today_iso:
            next_shift = s
            break

    # Disponibilidade status: check the DRAFTING (active) week, not the
    # working week. If the supervisor is mid-draft of next week and this user
    # hasn't submitted yet, we want to nudge them here.
    drafting = get_semana_ativa()
    drafting_codigo = drafting.codigo if drafting else semana.codigo
    drafting_label  = drafting.label  if drafting else semana.label
    drafting_is_different = (drafting_codigo != semana.codigo)

    has_disp = Disponibilidade.query.filter_by(
        semana=drafting_codigo, funcionario=func.sigla).first() is not None

    # Friendly week-state message when there's no next shift
    week_ended_for_me = bool(decorated) and next_shift is None
    no_shifts_at_all  = not decorated

    # Hours remaining (just informative; can be negative for over-contract)
    hours_remaining = round(total_h - hours_done, 1)

    # ═══════════════════════════════════════════════════════════════════════
    # NEW: "Quem está contigo hoje" — shift-mates today
    # ═══════════════════════════════════════════════════════════════════════
    # For each shift the user has TODAY, find everyone else at the same store
    # whose shift overlaps. Returns the user's day grouped, by store, with
    # teammate sigla+nome+horario. Empty list if no shift today.
    today_team = []
    today_shifts = [s for s in decorated if s['is_today']]
    if today_shifts:
        # Pull all other shifts in those (loja, dia) pairs in one query
        sigla_to_nome = {f.sigla: f.nome for f in Funcionario.query.all()}
        for my_shift in today_shifts:
            others = schedule_turno().query.filter_by(
                semana=semana.codigo,
                dia=my_shift['dia'],
                loja=my_shift['loja_id'],
            ).filter(schedule_turno().funcionario != func.sigla).all()
            mates = []
            for o in others:
                if not _shifts_overlap(my_shift['horario'], o.horario):
                    continue
                mates.append({
                    'sigla':   o.funcionario,
                    'nome':    sigla_to_nome.get(o.funcionario, o.funcionario),
                    'horario': o.horario,
                })
            # Sort by shift start time
            mates.sort(key=lambda m: shift_start_minutes(m['horario']))
            today_team.append({
                'my_horario': my_shift['horario'],
                'my_horas':   my_shift['horas'],
                'loja_nome':  my_shift['loja_nome'],
                'mates':      mates,
            })

    # ═══════════════════════════════════════════════════════════════════════
    # NEW: Week strip — 7-day overview, one entry per weekday with shift info
    # ═══════════════════════════════════════════════════════════════════════
    # Build a Mon→Sun list so the template doesn't need any logic. Each entry
    # has: label (Seg/Ter/...), date_short ("12"), is_today, is_past, shifts.
    # `shifts` is a list of {horario, loja_id, loja_nome, start_pct, width_pct}
    # where the pcts position the shift block over a 24h day (0%=midnight,
    # 100%=next midnight). Cross-midnight shifts wrap visually as a partial bar.
    shifts_by_day = {}
    for s in decorated:
        shifts_by_day.setdefault(s['dia'], []).append(s)
    week_strip = []
    for d in semana.dias_list:
        label = d['label']
        d_iso = d.get('date', '')
        shifts_here = []
        for s in shifts_by_day.get(label, []):
            sm, em = shift_bounds(s['horario'])
            if sm is None:
                continue
            # Clamp display range: 6h..30h (so 06:00→06:00 next day is the
            # visible canvas). Start at 6am to skip the dead hours of night.
            DISPLAY_START = 6 * 60
            DISPLAY_END   = 30 * 60   # 06:00 next day
            sm_d = max(sm, DISPLAY_START)
            em_d = min(em, DISPLAY_END)
            if em_d <= sm_d:
                continue
            span = DISPLAY_END - DISPLAY_START
            shifts_here.append({
                'horario':    s['horario'],
                'loja_id':    s['loja_id'],
                'loja_nome':  s['loja_nome'],
                'start_pct':  round((sm_d - DISPLAY_START) / span * 100, 2),
                'width_pct':  round((em_d - sm_d) / span * 100, 2),
            })
        # Day label (short date number for the column header, e.g. "12")
        date_short = ''
        if d_iso:
            try:
                date_short = d_iso.split('-')[-1].lstrip('0')
            except Exception:
                pass
        week_strip.append({
            'label':      label,
            'date_short': date_short,
            'date':       d_iso,
            'is_today':   d_iso == today_iso,
            'is_past':    bool(d_iso and d_iso < today_iso),
            'is_feriado': label in day_feriado,
            'feriado_nome': day_feriado.get(label, ''),
            'shifts':     shifts_here,
        })

    # ═══════════════════════════════════════════════════════════════════════
    # NEW: Time-of-day-aware greeting
    # ═══════════════════════════════════════════════════════════════════════
    now_h = datetime.now().hour
    # Before 05h it's still "Boa noite" — a "madrugada" greeting isn't European
    # Portuguese usage and reads as playful.
    if   now_h < 5:  greeting = 'Boa noite'
    elif now_h < 12: greeting = 'Bom dia'
    elif now_h < 19: greeting = 'Boa tarde'
    else:            greeting = 'Boa noite'

    return {
        'next_shift':        next_shift,
        'all_shifts':        decorated,
        'total_turnos':      len(decorated),
        'total_hours':       round(total_h, 1),
        'hours_done':        round(hours_done, 1),
        'hours_remaining':   hours_remaining,
        'contract_hours':    func.horas_contrato or 0,
        'is_turnos':         func.is_turnos,
        'has_disp':          has_disp,
        'drafting_codigo':   drafting_codigo,
        'drafting_label':    drafting_label,
        'drafting_is_different': drafting_is_different,
        'week_ended_for_me': week_ended_for_me,
        'no_shifts_at_all':  no_shifts_at_all,
        # NEW additions:
        'today_team':        today_team,
        'week_strip':        week_strip,
        'greeting':          greeting,
    }

@app.route('/disponibilidade', methods=['GET', 'POST'])
@login_required
def disponibilidade():
    ativa = get_semana_ativa()
    if not ativa:
        return render_template('erro.html', msg='Nenhuma semana ativa.')

    # ── Which weeks can be answered? ────────────────────────────────────────
    # The active (drafting) week is the default, but any already-created week
    # that starts AFTER it is also open — so someone who knows they'll be away
    # in three weeks can record it now instead of remembering later.
    # A week that has ended is never offered: with the active week over (as
    # happens when the next one hasn't been opened yet), every employee was
    # asked for availability for a week already in the past.
    ativa_start = semana_start_date(ativa) or ''
    week_choices = [] if _semana_terminou(ativa) else [ativa]
    for s in semanas_chronological(SemanaConfig, descending=False):
        if s.codigo == ativa.codigo:
            continue
        st = semana_start_date(s) or ''
        if st and ativa_start and st > ativa_start and not _semana_terminou(s):
            week_choices.append(_hydrate_semana(s))
    if not week_choices:
        return render_template('erro.html',
            msg=f'Sem semana aberta para disponibilidade: a semana ativa '
                f'({ativa.label}) já terminou e a seguinte ainda não foi criada.')
    sel = ((request.form.get('semana') if request.method == 'POST'
            else request.args.get('semana')) or '').strip()
    if request.method == 'POST' and sel and not any(s.codigo == sel for s in week_choices):
        return render_template('erro.html',
            msg='A semana selecionada já não está aberta para disponibilidade. '
                'Abrir novamente o formulário e selecionar uma semana disponível.'), 400
    semana = next((s for s in week_choices if s.codigo == sel), week_choices[0])

    error = None; success = False; sigla_ok = None

    # Whitelists — defend against injected form values
    VALID_LOJAS = {l['id'] for l in LOJAS}
    VALID_SLOTS = {'manha', 'tarde', 'noite'}
    VALID_TIPOS = {'disponivel', 'preferencia', 'indisponivel'}

    def clean_csv(raw: str, allowed: set) -> str:
        if not raw:
            return ''
        items = [s.strip() for s in raw.split(',') if s.strip() in allowed]
        # de-dup while preserving order
        seen, out = set(), []
        for s in items:
            if s not in seen:
                seen.add(s); out.append(s)
        return ','.join(out)

    # The admin shouldn't submit availability for themselves.
    if session.get('user_sigla') == ADMIN_SIGLA:
        return render_template('erro.html',
            msg='Apenas funcionários podem submeter disponibilidade.')

    func = current_user_func()
    if not func:
        return render_template('erro.html', msg='Sessão inválida. Iniciar sessão novamente.')

    if request.method == 'POST':
        dias_processados = 0
        dias_a_limpar = []
        for dia_obj in semana.dias_list:
            dia   = dia_obj['label']
            tipo  = request.form.get(f'tipo_{dia}', '').strip()
            # Cap to the column width. SQLite does not enforce String(200), so
            # without this an employee could store a note the size of the whole
            # body cap (256 KB) — reloaded into /api/empties and re-rendered in
            # the supervisor's panel on every day switch. Every other free-text
            # field is capped at its column width; this one was missed.
            texto = request.form.get(f'texto_{dia}', '').strip()[:200]
            lojas = clean_csv(request.form.get(f'lojas_{dia}', ''), VALID_LOJAS)
            slots = clean_csv(request.form.get(f'slots_{dia}', ''), VALID_SLOTS)
            # Descarga opt-in is independent of the regular availability tipo: a
            # worker can be Disponível AND do descarga (a shift can follow it).
            descarga_opt = request.form.get(f'descarga_{dia}', '') in ('1', 'true', 'on')
            descarga_opt = descarga_opt and (dia in semana.descarga_set)
            # Whitelist tipos — defend against form tampering
            if tipo and tipo not in VALID_TIPOS:
                continue
            # A day counts only with a regular state or a descarga opt-in.
            if not tipo and not descarga_opt:
                # An explicitly empty field is the form's "clear this day"
                # action. Omitted fields preserve earlier answers, so a partial
                # submission cannot accidentally erase the rest of the week.
                if f'tipo_{dia}' in request.form:
                    dias_a_limpar.append(dia)
                continue
            # Indisponivel doesn't make sense to have preferences for
            if tipo == 'indisponivel':
                lojas = ''; slots = ''
            dias_processados += 1
            existing = Disponibilidade.query.filter_by(
                semana=semana.codigo, funcionario=func.sigla, dia=dia).first()
            if existing:
                existing.tipo = tipo; existing.texto = texto
                existing.lojas_pref = lojas
                existing.slots_pref = slots
                existing.descarga_opt = descarga_opt
                existing.submetido_em = datetime.now(timezone.utc); existing.revisto = False
            else:
                db.session.add(Disponibilidade(
                    semana=semana.codigo, funcionario=func.sigla,
                    dia=dia, tipo=tipo, texto=texto,
                    lojas_pref=lojas, slots_pref=slots, descarga_opt=descarga_opt))
        if dias_processados == 0:
            error = 'É necessário marcar pelo menos um dia.'
        else:
            if dias_a_limpar:
                Disponibilidade.query.filter_by(
                    semana=semana.codigo, funcionario=func.sigla
                ).filter(Disponibilidade.dia.in_(dias_a_limpar)).delete(synchronize_session=False)
            db.session.commit()
            success = True; sigla_ok = func.sigla

    # FIX BUG 1: pre-load existing answers so the user can edit, not start over.
    # We compute per-day prefs and a best-guess "global" set (the intersection of
    # day-prefs across "disponivel" days — i.e. the prefs that look like they
    # were set as a default).
    existing_by_day = {}   # {dia: {tipo, texto, lojas:[...], slots:[...]}}
    rows = Disponibilidade.query.filter_by(
        semana=semana.codigo, funcionario=func.sigla).all()
    for r in rows:
        existing_by_day[r.dia] = {
            'tipo':  r.tipo or '',
            'texto': r.texto or '',
            'lojas': [s for s in (r.lojas_pref or '').split(',') if s],
            'slots': [s for s in (r.slots_pref or '').split(',') if s],
            'descarga': bool(r.descarga_opt),
        }
    # Best-effort "globals": intersection of lojas/slots across all "disponivel" days
    disp_days = [v for v in existing_by_day.values() if v['tipo'] == 'disponivel']
    if disp_days:
        global_lojas = set(disp_days[0]['lojas'])
        global_slots = set(disp_days[0]['slots'])
        for d in disp_days[1:]:
            global_lojas &= set(d['lojas'])
            global_slots &= set(d['slots'])
        global_lojas = sorted(global_lojas, key=lambda x: disp_days[0]['lojas'].index(x) if x in disp_days[0]['lojas'] else 99)
        global_slots = sorted(global_slots, key=lambda x: ['manha','tarde','noite'].index(x) if x in ['manha','tarde','noite'] else 99)
    else:
        global_lojas, global_slots = [], []

    # ── Offer "copy from last week" when this week is still blank ──────────────
    # Lots of staff keep a stable availability week to week. Rather than re-enter
    # it from scratch, pull the most recent prior week where THIS person actually
    # submitted, and map it onto this week's day labels (by weekday). Only offered
    # when nothing is submitted yet, so it can never clobber real answers.
    prev_prefill = {}
    prev_week_label = ''
    if not rows:
        cur_wd_to_label = {}
        for d in semana.dias_list:
            wd = dia_label_to_idx(d['label'])
            if wd is not None:
                cur_wd_to_label[wd] = d['label']
        this_start = semana_start_date(semana)
        for pw in semanas_chronological(SemanaConfig):   # newest first
            pw_start = semana_start_date(pw)
            if pw.codigo == semana.codigo:
                continue
            if this_start and pw_start and pw_start >= this_start:
                continue   # not a prior week
            prows = Disponibilidade.query.filter_by(
                semana=pw.codigo, funcionario=func.sigla).all()
            if not prows:
                continue
            for pr in prows:
                lbl = cur_wd_to_label.get(dia_label_to_idx(pr.dia))
                if not lbl:
                    continue
                prev_prefill[lbl] = {
                    'tipo':  pr.tipo or '',
                    'texto': pr.texto or '',
                    'lojas': [s for s in (pr.lojas_pref or '').split(',') if s],
                    'slots': [s for s in (pr.slots_pref or '').split(',') if s],
                    # Only carry the descarga opt-in if THIS week has descarga
                    # on that day, otherwise the chip wouldn't exist to toggle.
                    'descarga': bool(pr.descarga_opt) and (lbl in semana.descarga_set),
                }
            prev_week_label = pw.label
            break

    # When the answers were last touched — shown on the "já submetida" banner
    # so people trust that what they see is what the supervisor sees.
    # Timestamps are written as UTC but SQLite hands them back naive, so
    # re-attach UTC or the browser would read them as local time.
    last_sub = max((r.submetido_em for r in rows if r.submetido_em), default=None)
    if last_sub and last_sub.tzinfo is None:
        last_sub = last_sub.replace(tzinfo=timezone.utc)
    last_submitted_iso = last_sub.isoformat() if last_sub else ''

    return render_template('disponibilidade.html', semana=semana,
                           error=error, success=success, sigla_ok=sigla_ok, lojas=LOJAS,
                           primeiro_nome=(func.nome.split()[0] if func.nome else func.sigla),
                           descarga_set=semana.descarga_set if semana else set(),
                           descarga_lojas_nomes=([l['nome'] for l in LOJAS if l['id'] in (semana.descarga_lojas_set if semana else set())]),
                           existing_by_day=existing_by_day,
                           initial_global_lojas=global_lojas,
                           initial_global_slots=global_slots,
                           prev_prefill=prev_prefill,
                           prev_week_label=prev_week_label,
                           week_choices=[{'codigo': s.codigo, 'label': s.label,
                                          'is_ativa': s.codigo == ativa.codigo}
                                         for s in week_choices],
                           last_submitted_iso=last_submitted_iso,
                           has_existing=bool(rows))

def _schedule_week_selection(week_model, default_week):
    """Resolve a deep link and its neighbours exclusively inside the visible list."""
    weeks = semanas_chronological(week_model)
    requested = request.args.get('semana', '').strip()
    selected = next((week for week in weeks if week.codigo == requested), None)
    if requested and selected is None:
        abort(404)
    if not requested:
        selected = next((week for week in weeks
                         if default_week and week.codigo == default_week.codigo),
                        weeks[0] if weeks else None)
    navigation = schedule_week_navigation(weeks, selected)
    for direction in ('previous', 'next'):
        neighbour = navigation.pop(direction)
        params = request.args.to_dict(flat=False)
        if neighbour:
            params['semana'] = neighbour.codigo
        navigation[direction + '_url'] = url_for(request.endpoint, **params) if neighbour else None
    return weeks, selected, navigation


@app.route('/escala')
@login_required
def escala():
    semanas, semana_obj, week_navigation = _schedule_week_selection(
        schedule_semana(), get_semana_for_employee())
    semana_str = semana_obj.codigo if semana_obj else ''
    if semana_obj:
        try: dias_full = json.loads(semana_obj.dias_json or '[]')
        except json.JSONDecodeError: dias_full = []
        dias     = [d['label'] for d in dias_full]
        feriados = {d['label']: d['feriado_nome'] for d in dias_full if d['feriado']}
    else:
        dias = []
        feriados = {}; dias_full = []
    lojas_data = []
    for loja in LOJAS:
        g, h = build_grid(loja['id'], semana_str, dias)
        lojas_data.append({'loja': loja, 'grid': g, 'horarios': h})
    _today_iso = date.today().isoformat()
    today_label = next((d['label'] for d in dias_full
                        if d.get('date') == _today_iso), '')
    return render_template('escala.html', lojas=LOJAS, lojas_data=lojas_data,
        dias=dias, dias_full=dias_full, feriados=feriados,
        semanas=semanas, semana_atual=semana_str, week_navigation=week_navigation, is_supervisor=False,
        today_label=today_label,
        func_info=func_map_with_tipo())

@app.route('/equipa')
@login_required
def equipa():
    semana_str = request.args.get('semana', '').strip()
    if not semana_str:
        # The supervisor balances the week being built (the active one); an
        # employee reads hours for the week they are working in — the active
        # week is often next week's unfinished draft.
        base = get_semana_ativa() if is_supervisor() else get_semana_for_employee()
        semana_str = base.codigo if base else ''
    semanas    = semanas_chronological(schedule_semana())
    today_iso  = date.today().isoformat()
    hoje       = next((s for s in semanas if _semana_contains(s, today_iso)), None)
    semana_obj = schedule_semana().query.filter_by(codigo=semana_str).first()
    turnos_sem = schedule_turno().query.filter_by(semana=semana_str).all() if semana_str else []
    stats = {}
    for t in turnos_sem:
        s = t.funcionario
        if s not in stats:
            stats[s] = {'turnos': 0, 'horas': 0.0, 'lojas': set()}
        stats[s]['turnos'] += 1
        stats[s]['horas']  += _shift_hours(t.horario)
        stats[s]['lojas'].add(t.loja)
    # Descargas add hours without being shifts — someone whose only work that
    # week is a descarga still needs a row here.
    for s, h in (descarga_horas_semana(semana_str) if semana_str else {}).items():
        stats.setdefault(s, {'turnos': 0, 'horas': 0.0, 'lojas': set()})
        stats[s]['horas'] += h
    for s in stats:
        stats[s]['horas'] = round(stats[s]['horas'], 1)
        stats[s]['lojas'] = list(stats[s]['lojas'])
    funcionarios = sorted(Funcionario.query.all(), key=lambda f: f.nome)
    return render_template('equipa.html',
        funcionarios=funcionarios, stats=stats, lojas=LOJAS,
        semanas=semanas, semana_atual=semana_str, semana_obj=semana_obj,
        hoje_codigo=hoje.codigo if hoje else '',
        is_supervisor=session.get('supervisor', False))

# ════════════════════════════════════════════════════════════════════════════════
# SUPERVISOR ROUTES
# ════════════════════════════════════════════════════════════════════════════════

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Unified login: sigla + PIN.

    Security posture:
      * One generic error message — never reveal which of (sigla, PIN) is wrong.
        Otherwise attackers can enumerate valid siglas.
      * Rate limiting per (sigla, IP) AND globally per sigla (see login_rate_limited).
      * Session regenerated on success to prevent session fixation.
      * An empty PIN never authenticates or provisions an account. A privately
        delivered supervisor invitation is required to establish access.
      * Existing PINs are grandfathered to avoid locking anyone out.
      * Audit log captures every login (success and failure).
    """
    error = None
    # GENERIC_FAIL is what we show users — same message regardless of which field
    # was wrong. Internal logging records the actual cause.
    GENERIC_FAIL = 'Sigla ou PIN incorreto.'

    if request.method == 'POST':
        sigla = (request.form.get('sigla') or '').strip().upper()
        pin   = (request.form.get('pin')   or '')

        # Length limits — reject obviously-junk input before any DB work
        if len(sigla) > 10 or len(pin) > PIN_MAX_LENGTH:
            error = GENERIC_FAIL
        elif sigla and login_rate_limited(sigla):
            error = 'Demasiadas tentativas. Tentar novamente dentro de alguns minutos.'
            log.warning('rate-limit hit: sigla=%s ip=%s', sigla, request.remote_addr)
            audit('login_rate_limited', target=sigla)
        elif not sigla or not pin:
            error = 'Sigla e PIN são obrigatórios.'
        elif sigla == ADMIN_SIGLA:
            if verify_pin(SUPERVISOR_PIN, pin.strip()):
                login_clear_attempts(sigla)
                _do_login(sigla, supervisor=True)
                audit('login_ok', target=ADMIN_SIGLA, detail='supervisor')
                return _redirect_after_login()
            error = GENERIC_FAIL
            login_record_failure(sigla)
            audit('login_fail', target=ADMIN_SIGLA, detail='bad PIN')
            log.info('login failed: sigla=%s ip=%s reason=bad-admin-pin',
                     sigla, request.remote_addr)
        else:
            func = db.session.get(Funcionario, sigla)
            if not func:
                error = GENERIC_FAIL
                login_record_failure(sigla)
                audit('login_fail', target=sigla, detail='no such sigla')
                log.info('login failed: sigla=%s ip=%s reason=no-such-sigla',
                         sigla, request.remote_addr)
                # Constant-time fake compare so timing doesn't reveal existence
                hash_pin('x')  # warm CPU equivalently
            elif not func.pin or not verify_pin(func.pin, pin.strip()):
                error = GENERIC_FAIL
                login_record_failure(sigla)
                audit('login_fail', target=sigla, detail='bad PIN')
                log.info('login failed: sigla=%s ip=%s reason=bad-pin',
                         sigla, request.remote_addr)
                if not func.pin:
                    hash_pin('x')  # unclaimed and unknown siglas cost a hash
            else:
                # Preserve all existing valid credentials, including legacy
                # short/weak values; upgrade their storage after verification.
                if not _looks_hashed(func.pin):
                    func.pin = hash_pin(pin.strip())
                    db.session.commit()
                    audit('pin_set', target=sigla, detail='legacy-upgrade')
                login_clear_attempts(sigla)
                _do_login(func.sigla, supervisor=False)
                audit('login_ok', target=func.sigla)
                return _redirect_after_login()

    # SECURITY: the roster is NOT sent to this page. It used to render a picker
    # listing every funcionário's sigla and real name, unauthenticated — which
    # both leaked staff PII to anyone who loaded /login and made the generic
    # "Sigla ou PIN incorreto" message pointless, since the valid siglas were
    # handed out for free. Sigla is typed now.
    return render_template('login.html', error=error)


@app.route('/acesso', methods=['GET', 'POST'])
def enroll_access():
    """Redeem a privately issued invitation without putting it in a URL.

    The anonymous form has its own session CSRF token. Consume with a
    conditional UPDATE so simultaneous redemptions cannot both succeed.
    """
    error = None
    get_csrf_token()
    if request.method == 'POST':
        sigla = (request.form.get('sigla') or '').strip().upper()
        token = (request.form.get('convite') or '').strip()
        pin = (request.form.get('pin') or '').strip()
        generic = 'Convite inválido ou expirado. Contactar a supervisão.'
        employee = None
        if len(sigla) > 10 or len(token) > 100 or not sigla or not token:
            error = generic
        elif login_rate_limited(sigla):
            error = 'Demasiadas tentativas. Tentar novamente dentro de alguns minutos.'
            audit('enrollment_rate_limited', target=sigla)
        else:
            employee = db.session.get(Funcionario, sigla)
            digest = invitation_digest(token)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if (not employee or not employee.enrollment_hash or
                    not employee.enrollment_expires_at or
                    employee.enrollment_expires_at <= now or
                    not hmac.compare_digest(employee.enrollment_hash, digest)):
                error = generic
                login_record_failure(sigla)
                audit('enrollment_fail', target=sigla, detail='invalid-or-expired')
            elif len(pin) < PIN_MIN_LENGTH or len(pin) > PIN_MAX_LENGTH or is_weak_pin(pin, sigla):
                error = (f'Escolher um PIN entre {PIN_MIN_LENGTH} e {PIN_MAX_LENGTH} caracteres. '
                         'Evitar sequências, caracteres repetidos ou a própria sigla.')
            elif pin != (request.form.get('pin_confirm') or '').strip():
                error = 'Os PINs introduzidos não coincidem.'
            else:
                kind = employee.enrollment_kind
                version = employee.access_version
                new_hash = hash_pin(pin)
                consume_at = datetime.now(timezone.utc).replace(tzinfo=None)
                changed = Funcionario.query.filter(
                    Funcionario.sigla == sigla,
                    Funcionario.enrollment_hash == digest,
                    Funcionario.enrollment_expires_at > consume_at,
                    Funcionario.access_version == version,
                ).update({
                    'pin': new_hash, 'enrollment_hash': '',
                    'enrollment_expires_at': None, 'enrollment_kind': '',
                }, synchronize_session=False)
                if changed != 1:
                    db.session.rollback()
                    error = generic
                else:
                    # Audit metadata is committed with the credential change.
                    db.session.add(AuditLog(actor=sigla, ip=(request.remote_addr or '?')[:64],
                                            action='enrollment_complete', target=sigla,
                                            detail=kind))
                    db.session.commit()
                    db.session.expire_all()
                    login_clear_attempts(sigla)
                    _do_login(sigla, supervisor=False)
                    return _redirect_after_login()
    response = make_response(render_template('enroll_access.html', error=error))
    response.headers['Cache-Control'] = 'no-store, private, max-age=0'
    return response


def _do_login(sigla: str, *, supervisor: bool) -> None:
    """Establish a fresh authenticated session.

    Security: clearing and rewriting the session dict makes Flask re-emit a new
    session cookie with a new signature, preventing session fixation (attacker
    plants a cookie value, victim logs in, attacker uses the same cookie).
    """
    session.clear()
    session.permanent = True
    session['user_sigla'] = sigla
    session['supervisor'] = bool(supervisor)
    session['login_at']   = datetime.now(timezone.utc).isoformat()
    if not supervisor:
        employee = db.session.get(Funcionario, sigla)
        session['access_version'] = employee.access_version
    # Fresh CSRF token per login session
    session['_csrf']      = secrets.token_urlsafe(32)


def _is_safe_next(next_page: str) -> bool:
    """Return True if `next_page` is a safe redirect target for the user who
    just logged in.

    Safe means ALL of:
      * Same-site relative path (starts with single '/', no scheme, no '//',
        no '\\' which IE used to treat as scheme separator).
      * Not pointing back at /login or /logout (would loop or look like the
        login failed).
      * For non-supervisors: not a supervisor-only path. Otherwise the user
        bounces from /supervisor* back to /login and it *looks* like their
        PIN was wrong, when in fact they're logged in but lack permission.
    """
    if not next_page or not next_page.startswith('/'):
        return False
    if next_page.startswith('//') or '://' in next_page or '\\' in next_page:
        return False
    # Strip query/fragment for the path comparison
    path_only = next_page.split('?', 1)[0].split('#', 1)[0].rstrip('/')
    # Bouncing back to /login/logout would loop or look like a failed login
    if path_only in ('/login', '/logout', '/supervisor/login', '/supervisor/logout'):
        return False
    # Employees must not be redirected to supervisor pages — they would bounce
    # back to /login and the user would think the PIN didn't work.
    if not is_supervisor():
        if path_only == '/supervisor' or path_only.startswith('/supervisor/'):
            return False
        # /api/ endpoints don't render HTML — landing on one after login is
        # confusing (raw JSON in the browser). Drop them too.
        if path_only.startswith('/api/'):
            return False
    return True


def _redirect_after_login():
    """Send users to ?next= if it's a safe relative path, else to the index.

    FIX: previously any same-site relative path was honoured. That caused a
    bug where an employee who hit a supervisor URL (e.g. via a stale link)
    would log in correctly, get redirected to /supervisor, get bounced back
    to /login, and from the user's perspective it looked as if the PIN was
    wrong. We now also reject /login itself (loop), /api/ (renders JSON),
    and supervisor pages for non-supervisors.
    """
    next_page = request.args.get('next') or request.form.get('next') or ''
    if _is_safe_next(next_page):
        return redirect(next_page)
    # Supervisors should land on their dashboard, not the employee index.
    if is_supervisor():
        return redirect(url_for('supervisor_dashboard'))
    return redirect(url_for('index'))


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    sigla = session.get('user_sigla', '')
    session.clear()
    if sigla:
        audit('logout', target=sigla)
    return redirect(url_for('login'))


# Back-compat: any old links to /supervisor/login or /supervisor/logout still work.
@app.route('/supervisor/login', methods=['GET', 'POST'])
def supervisor_login():
    return redirect(url_for('login'))

@app.route('/supervisor/logout')
def supervisor_logout():
    return redirect(url_for('logout'))

@app.route('/supervisor')
@supervisor_required
def supervisor_dashboard():
    _expire_stale_trocas()
    # Allow viewing any week, not just active
    semana_str = request.args.get('semana', '').strip()
    if semana_str:
        semana = SemanaConfig.query.filter_by(codigo=semana_str).first()
    else:
        semana = get_semana_ativa()
    # Hydrate dias_list / descarga_set / descarga_map for the dashboard controls.
    if semana:
        _hydrate_semana(semana)
    funcionarios = Funcionario.query.order_by(Funcionario.nome).all()
    submetidos = set()
    if semana:
        rows = db.session.query(Disponibilidade.funcionario)\
                 .filter_by(semana=semana.codigo).distinct().all()
        submetidos = {r[0] for r in rows}
    pendentes   = [f for f in funcionarios if f.sigla not in submetidos]
    responderam = [f for f in funcionarios if f.sigla in submetidos]

    # ── Availability vs need (Respostas tab) ────────────────────────────────
    # The input-side mirror of the coverage matrix: for the week being built,
    # how many people are actually available each day vs how many seats that day
    # needs. Surfaces a staffing shortfall BEFORE the schedule is built, so the
    # supervisor can chase availability for thin days instead of discovering the
    # gap mid-build.
    avail_overview = None
    if semana:
        nome_by_sigla = {f.sigla: f.nome for f in funcionarios}
        by_day = {}      # dia -> {disp, pref, indisp, descarga} sets of siglas
        pref_loja = {}   # loja_id -> set of siglas that prefer it
        for r in Disponibilidade.query.filter_by(semana=semana.codigo).all():
            sl = by_day.setdefault(r.dia, {'disp': set(), 'pref': set(),
                                           'indisp': set(), 'descarga': set()})
            if   r.tipo == 'disponivel':   sl['disp'].add(r.funcionario)
            elif r.tipo == 'preferencia':  sl['pref'].add(r.funcionario)
            elif r.tipo == 'indisponivel': sl['indisp'].add(r.funcionario)
            if r.descarga_opt:             sl['descarga'].add(r.funcionario)
            if r.tipo in ('disponivel', 'preferencia'):
                for lj in (r.lojas_pref or '').split(','):
                    if lj:
                        pref_loja.setdefault(lj, set()).add(r.funcionario)
        need_by_wd = {}
        for tt in LojaHeadcountTarget.query.all():
            need_by_wd[tt.dia_idx] = need_by_wd.get(tt.dia_idx, 0) + tt.target
        _today_iso = date.today().isoformat()
        days_out = []
        for d in semana.dias_list:
            didx = _dia_label_to_idx(d['label'])
            sl = by_day.get(d['label'],
                            {'disp': set(), 'pref': set(), 'indisp': set(), 'descarga': set()})
            avail = len(sl['disp'] | sl['pref'])
            need  = need_by_wd.get(didx, 0) if didx is not None else 0
            if   need <= 0:              status = 'none'
            elif avail >= need:          status = 'ok'
            elif avail >= need * 0.8:    status = 'tight'
            else:                        status = 'short'
            who = sorted(sl['disp'] | sl['pref'])
            bar_pct = min(100, int(round(avail / need * 100))) if need else 100
            days_out.append({
                'label':    d['label'], 'is_feriado': bool(d.get('feriado')),
                'is_today': d.get('date', '') == _today_iso,
                'disp': len(sl['disp']), 'pref': len(sl['pref']),
                'indisp': len(sl['indisp']), 'descarga': len(sl['descarga']),
                'avail': avail, 'need': need, 'status': status, 'bar_pct': bar_pct,
                'who': [{'sig': s, 'nome': nome_by_sigla.get(s, s),
                         'pref': s in sl['pref']} for s in who],
            })
        loja_demand = [{
            'id': l['id'], 'nome': l['nome'], 'short': l['short'],
            'n': len(pref_loja.get(l['id'], set())),
        } for l in LOJAS]
        avail_overview = {
            'days': days_out, 'loja_demand': loja_demand,
            'n_short': sum(1 for d in days_out if d['status'] == 'short'),
        }
    semanas     = semanas_chronological(SemanaConfig)
    existing_codigos = {s.codigo for s in semanas}
    # One grouped query so each upcoming-week cell can show how drafted it is.
    turno_counts = dict(db.session.query(schedule_turno().semana, func.count(schedule_turno().id))
                        .group_by(schedule_turno().semana).all())
    # Starts at the week we're IN (not next Monday): mid-week the supervisor
    # must still be able to create/activate the week that covers today.
    upcoming    = [{
        'monday': m.isoformat(),
        'codigo': code,
        'label':  week_label(m),
        'exists': code in existing_codigos,
        'n_turnos': turno_counts.get(code, 0),
        'is_current': m <= date.today() <= m + timedelta(days=6),
    } for m in mondays_from_current_week(8)
      for code in [resolve_week_codigo(m, semanas)]]

    # ── "Esta semana" panel (FEATURE: working-week view for supervisor) ──
    # The dashboard is normally focused on the DRAFTING week (active). But the
    # supervisor often wants a quick read on what's happening RIGHT NOW — who
    # is working today, how many turnos across the 5 stores, etc.
    # We compute this for the working week (which may or may not be the active
    # week) and render it as a top panel.
    today_panel = _build_supervisor_today_panel()

    # Trocas waiting on the supervisor's decision — surfaced as a dashboard tab.
    # Each one is annotated with its approval pre-flight (the same checks the
    # decidir endpoint enforces) so problems are visible BEFORE the click:
    #   fatal     → the request can only be refused (turno deleted / new owner)
    #   conflitos → approving now would 409 on an overlap
    trocas_a_decidir = []
    for t in (TrocaPedido.query.filter_by(estado='pendente_supervisor')
              .order_by(TrocaPedido.criado_em.asc()).all()):
        d = _serialize_troca(t)
        d['fatal'], d['conflitos'] = _troca_approval_problems(t)
        trocas_a_decidir.append(d)
    n_trocas_problema = sum(1 for d in trocas_a_decidir
                            if d['fatal'] or d['conflitos'])

    # ── Week coverage matrix (stores × days) ────────────────────────────────
    # An at-a-glance read on a whole week: per store / per day, how many distinct
    # people are assigned vs the configured headcount target, so the supervisor
    # sees where a week still needs staffing without opening each day in the
    # builder. Defaults to the week we're CURRENTLY in (the active/drafting week
    # is often still empty), but any week can be picked via ?cob=<codigo>.
    cob_codigo = request.args.get('cob', '').strip()
    cob_semana = SemanaConfig.query.filter_by(codigo=cob_codigo).first() if cob_codigo else None
    if not cob_semana:
        cob_semana = get_semana_for_employee() or semana
    cob_codigo = cob_semana.codigo if cob_semana else ''
    week_coverage = None
    if cob_semana:
        _today = date.today().isoformat()
        fmap = {f.sigla: f for f in funcionarios}
        targets = {}
        for tt in LojaHeadcountTarget.query.all():
            targets[(tt.loja, tt.dia_idx)] = tt.target
        assigned = {}        # (loja, day_label) -> set(sigla)
        cell_shifts = {}     # (loja, day_label) -> [(start, end, sigla, horario)]
        days_by_sigla = {}   # sigla -> set(day_label)  (for the rest-risk strip)
        for tr in schedule_turno().query.filter_by(semana=cob_semana.codigo).all():
            assigned.setdefault((tr.loja, tr.dia), set()).add(tr.funcionario)
            s, e = shift_bounds(tr.horario)
            cell_shifts.setdefault((tr.loja, tr.dia), []).append(
                (s if s is not None else 0, e if e is not None else 0,
                 tr.funcionario, tr.horario))
            days_by_sigla.setdefault(tr.funcionario, set()).add(tr.dia)
        days = json.loads(cob_semana.dias_json or '[]')
        day_heads = [{
            'label':      d['label'].split(' ')[0],
            'num':        (d.get('date', '').split('-')[-1].lstrip('0') if d.get('date') else ''),
            'is_today':   d.get('date', '') == _today,
            'is_feriado': bool(d.get('feriado')),
        } for d in days]
        col_assigned = [0] * len(days)
        col_target   = [0] * len(days)
        rows = []
        for l in LOJAS:
            cells = []
            r_assigned = r_target = 0
            for ci, d in enumerate(days):
                key  = (l['id'], d['label'])
                didx = _dia_label_to_idx(d['label'])
                tgt  = targets.get((l['id'], didx), 0) if didx is not None else 0
                cnt  = len(assigned.get(key, set()))
                if tgt <= 0:      status = 'none'
                elif cnt == 0:    status = 'empty'
                elif cnt < tgt:   status = 'short'
                else:             status = 'met'
                shifts = sorted(cell_shifts.get(key, []))
                who_list = []
                for (_s, _e, sig, hor) in shifts:
                    fo = fmap.get(sig)
                    who_list.append({
                        'sig': sig, 'hor': hor,
                        'rank':     RANK_LABELS.get(fo.rank, '') if fo else '',
                        'ok_open':  bool(fo and fo.can_open_alone),
                        'ok_close': bool(fo and fo.can_close_alone),
                    })
                cells.append({'assigned': cnt, 'target': tgt, 'status': status,
                              'is_today': d.get('date', '') == _today,
                              'day': d['label'], 'who_list': who_list})
                r_assigned += cnt; r_target += tgt
                col_assigned[ci] += cnt; col_target[ci] += tgt
            rows.append({'id': l['id'], 'nome': l['nome'], 'short': l['short'],
                         'cells': cells, 'assigned': r_assigned, 'target': r_target})
        # Weekly rest-risk: people scheduled 6+ days (one rest day, or none at 7).
        rest_risk = sorted(
            ({'sigla': s, 'nome': (fmap[s].nome if s in fmap else s),
              'dias': len(ds)} for s, ds in days_by_sigla.items() if len(ds) >= 6),
            key=lambda x: (-x['dias'], x['nome']))
        week_coverage = {
            'rows':           rows,
            'day_heads':      day_heads,
            'col_assigned':   col_assigned,
            'col_target':     col_target,
            'grand_assigned': sum(col_assigned),
            'grand_target':   sum(col_target),
            # n_gaps = how many (loja, dia) cells are still short/empty (max 35).
            'n_gaps':         sum(1 for r in rows for c in r['cells']
                                  if c['status'] in ('short', 'empty')),
            # n_missing = how many actual SEATS are still unfilled (sum of the
            # per-cell shortfalls). This is the number that reconciles with the
            # matrix grand total (assigned/target) — "lugares por preencher".
            'n_missing':      sum(max(0, c['target'] - c['assigned'])
                                  for r in rows for c in r['cells']),
            'rest_risk':      rest_risk,
        }

    # A week that has ended can't be staffed or answered any more: its gaps
    # and missing answers are history, not "what needs doing". When the
    # ACTIVE week has ended, the thing to do is to activate the next one.
    semana_terminou = _semana_terminou(semana)
    cob_terminou = _semana_terminou(cob_semana)

    return render_template('supervisor_dashboard.html', semana=semana, lojas=LOJAS,
        semana_terminou=semana_terminou, cob_terminou=cob_terminou,
        total_func=len(funcionarios), pendentes=pendentes, responderam=responderam,
        semanas=semanas, upcoming=upcoming,
        semana_atual=(semana.codigo if semana else ''),
        trocas_a_decidir=trocas_a_decidir,
        n_trocas_problema=n_trocas_problema,
        week_coverage=week_coverage,
        avail_overview=avail_overview,
        cob_semana=cob_semana, cob_codigo=cob_codigo,
        today_panel=today_panel)


def _build_supervisor_today_panel():
    """Gather a snapshot of today's activity for the supervisor dashboard.

    Returns:
        None if there's no working week or no day matching today.
        Otherwise: dict with semana info, today's date label, list of turnos
        grouped by loja, plus totals.
    """
    working = get_semana_for_employee()
    if not working:
        return None
    today_iso = date.today().isoformat()
    # Find today's day-label inside the working week
    today_label = None
    for d in working.dias_list:
        if d.get('date') == today_iso:
            today_label = d['label']
            break
    if not today_label:
        # Working week doesn't contain today (e.g. fallback). Still useful to
        # show the week label so the supervisor knows what's "current".
        # Must carry the same keys as the full dict below — the template reads
        # lojas_com_pessoal etc. unconditionally.
        return {
            'semana':       working,
            'today_label':  None,
            'today_iso':    today_iso,
            'today_long':   date_long(date.today()),
            'by_loja':      [],
            'total_turnos': 0,
            'total_hours':  0.0,
            'now_people':   0,
            'lojas_com_pessoal': 0,
            'is_active':    working.ativa,
            'drafting_is_different': working.codigo != ((get_semana_ativa() or working).codigo),
        }

    # Pull every turno for the working week
    turnos = schedule_turno().query.filter_by(semana=working.codigo, dia=today_label).all()
    loja_name = {l['id']: l['nome'] for l in LOJAS}
    by_loja = {l['id']: {'id': l['id'], 'nome': l['nome'], 'turnos': []} for l in LOJAS}

    # Helper to extract a sortable start-time for grouping
    def start_min(h):
        m = re.match(r'(\d{1,2}):(\d{2})', h or '')
        return int(m.group(1))*60 + int(m.group(2)) if m else 0

    # Live state of a shift relative to *now*, within today's own panel.
    # A cross-midnight shift (e.g. 18:00-00:30) belonging to today can only be
    # 'later' (before it starts) or 'now' (after) — by the time it ends, the
    # panel is already showing the next day.
    def shift_state(h, now):
        m = re.match(r'(\d{1,2}):(\d{2})\D+(\d{1,2}):(\d{2})', h or '')
        if not m:
            return 'later'
        s = int(m.group(1))*60 + int(m.group(2))
        e = int(m.group(3))*60 + int(m.group(4))
        if e <= s:                       # crosses midnight
            return 'now' if now >= s else 'later'
        if s <= now < e:
            return 'now'
        return 'done' if now >= e else 'later'

    _now = datetime.now()
    now_min = _now.hour * 60 + _now.minute

    total_hours = 0.0
    for t in turnos:
        if t.loja in by_loja:
            h = _shift_hours(t.horario)
            total_hours += h
            f = db.session.get(Funcionario, t.funcionario)
            by_loja[t.loja]['turnos'].append({
                'sigla':    t.funcionario,
                'nome':     f.nome if f else t.funcionario,
                'horario':  t.horario,
                'horas':    h,
                'nota':     t.nota or '',
                'tipo':     f.tipo_contrato if f else 'contrato',
                'start':    start_min(t.horario),
                'state':    shift_state(t.horario, now_min),
            })
    # Sort turnos within each loja by start time, then sigla for stability
    for l in by_loja.values():
        l['turnos'].sort(key=lambda x: (x['start'], x['sigla']))
        l['unique_people'] = len({t['sigla'] for t in l['turnos']})
        l['now_count']     = len({t['sigla'] for t in l['turnos'] if t['state'] == 'now'})
    now_people = len({t['sigla'] for l in by_loja.values()
                      for t in l['turnos'] if t['state'] == 'now'})

    # Drop the supervisor's "ADMIN" out of the count if it sneaks in
    return {
        'semana':           working,
        'today_label':      today_label,
        'today_iso':        today_iso,
        'today_long':       date_long(date.today()),
        'by_loja':          list(by_loja.values()),
        'total_turnos':     len(turnos),
        'total_hours':      round(total_hours, 1),
        'now_people':       now_people,
        'lojas_com_pessoal': sum(1 for l in by_loja.values() if l['turnos']),
        'is_active':        working.ativa,
        # Drafting week — show a hint when working week differs from active
        'drafting_is_different': working.codigo != ((get_semana_ativa() or working).codigo),
    }


@app.route('/supervisor/descarga', methods=['POST'])
@supervisor_required
def supervisor_set_descarga():
    """Set the descarga days for a given week."""
    data = request.get_json(silent=True) or {}
    codigo = (data.get('semana') or '').strip()[:30]
    semana = SemanaConfig.query.filter_by(codigo=codigo).first()
    if not semana:
        return jsonify(ok=False, erro='Semana não encontrada'), 404
    try:
        dias_list = json.loads(semana.dias_json or '[]')
    except json.JSONDecodeError:
        dias_list = []
    valid_labels = {d['label'] for d in dias_list}
    valid_ids = {l['id'] for l in LOJAS}
    cfg_raw = data.get('config')
    if not isinstance(cfg_raw, dict):
        return jsonify(ok=False, erro='Config inválida'), 400
    _hydrate_semana(semana)
    old_map = semana.descarga_map or {}
    new_cfg = {}
    for dia, stores in cfg_raw.items():
        if dia not in valid_labels or not isinstance(stores, list):
            continue
        cell = {}
        for lid in stores:
            if isinstance(lid, str) and lid in valid_ids:
                # keep a previously-set (possibly custom) window for this cell
                cell[lid] = old_map.get(dia, {}).get(lid) or descarga_horario_for(lid)
        if cell:
            new_cfg[dia] = cell
    semana.descarga_config = json.dumps(new_cfg)
    # Disabling a delivery cell removes its assignments as part of the same
    # change. Otherwise they disappear from the grid but still inflate hours.
    removed = 0
    for dc in schedule_descarga().query.filter_by(semana=codigo).all():
        if dc.loja not in new_cfg.get(dc.dia, {}):
            db.session.delete(dc)
            removed += 1
    db.session.commit()
    audit('descarga_set', target=codigo,
          detail=f'{len(new_cfg)} dia(s) com descarga; atribuicoes_removidas={removed}')
    return jsonify(ok=True, dias=sorted(new_cfg.keys()),
                   lojas=sorted(set().union(*new_cfg.values()) if new_cfg else set()),
                   removed=removed)


@app.route('/supervisor/ativar-semana/<codigo>', methods=['POST'])
@supervisor_required
def ativar_semana(codigo):
    """Set a specific week as the active one."""
    codigo = (codigo or '').strip()[:30]
    s = SemanaConfig.query.filter_by(codigo=codigo).first()
    if not s:
        return jsonify({'ok': False, 'erro': 'Semana não encontrada'}), 404
    SemanaConfig.query.update({'ativa': False})
    s.ativa = True
    db.session.commit()
    audit('semana_ativar', target=codigo, detail=s.label)
    return jsonify({'ok': True, 'codigo': codigo, 'label': s.label})

@app.route('/supervisor/disponibilidades')
@supervisor_required
def supervisor_disponibilidades():
    semana_str = request.args.get('semana', '').strip()
    if not semana_str:
        ativa = get_semana_ativa()
        semana_str = ativa.codigo if ativa else ''
    semana = SemanaConfig.query.filter_by(codigo=semana_str).first()
    semanas = semanas_chronological(SemanaConfig)
    if not semana:
        return render_template('supervisor_disponibilidades.html',
            semana=None, semanas=semanas, semana_atual=semana_str,
            por_func={}, func_info=func_map_with_tipo(), dias=[], lojas=LOJAS)
    # Hydrate dias_list manually if not active
    try: dias_list = json.loads(semana.dias_json or '[]')
    except json.JSONDecodeError: dias_list = []
    semana.dias_list = dias_list
    todos = Disponibilidade.query.filter_by(semana=semana.codigo)\
                .order_by(Disponibilidade.funcionario).all()
    por_func = {}
    for d in todos:
        por_func.setdefault(d.funcionario, []).append(d)
    return render_template('supervisor_disponibilidades.html',
        semana=semana, semanas=semanas, semana_atual=semana_str,
        por_func=por_func, func_info=func_map_with_tipo(), dias=dias_list, lojas=LOJAS)

@app.route('/supervisor/escala')
@supervisor_required
def supervisor_escala():
    semanas, semana_obj, week_navigation = _schedule_week_selection(
        SemanaConfig, get_semana_ativa())
    semana_str = semana_obj.codigo if semana_obj else ''
    if semana_obj:
        try: dias_full = json.loads(semana_obj.dias_json or '[]')
        except json.JSONDecodeError: dias_full = []
        dias     = [d['label'] for d in dias_full]
        feriados = {d['label']: d['feriado_nome'] for d in dias_full if d['feriado']}
    else:
        dias = []
        feriados = {}; dias_full = []
    lojas_data = []
    for loja in LOJAS:
        g, h = build_grid(loja['id'], semana_str, dias)
        lojas_data.append({'loja': loja, 'grid': g, 'horarios': h})
    disps = {}
    for d in Disponibilidade.query.filter_by(semana=semana_str).all():
        disps.setdefault(d.funcionario, {})[d.dia] = {
            'tipo': d.tipo, 'texto': d.texto,
            'lojas': d.lojas_pref or '', 'slots': d.slots_pref or ''
        }
    funcionarios = Funcionario.query.order_by(Funcionario.sigla).all()
    # FEATURE 25: latest updated_at across the week's turnos, in ISO format.
    last_t = schedule_turno().query.filter_by(semana=semana_str).order_by(schedule_turno().updated_at.desc()).first()
    last_updated_iso = last_t.updated_at.isoformat() if (last_t and last_t.updated_at) else ''
    _today_iso = date.today().isoformat()
    today_label = next((d['label'] for d in dias_full
                        if d.get('date') == _today_iso), '')
    return render_template('escala.html', lojas=LOJAS, lojas_data=lojas_data,
        dias=dias, dias_full=dias_full, feriados=feriados,
        semanas=semanas, semana_atual=semana_str, week_navigation=week_navigation, disps=disps,
        is_supervisor=True, funcionarios=funcionarios,
        func_info=func_map_with_tipo(), today_label=today_label,
        last_updated_iso=last_updated_iso)

@app.route('/supervisor/semana/nova', methods=['POST'])
@supervisor_required
def nova_semana():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({'ok': False, 'erro': 'Formato de pedido inválido'}), 400
    monday_str = data.get('monday')
    if not monday_str:
        return jsonify({'ok': False, 'erro': 'Data em falta'}), 400
    try:
        monday = date.fromisoformat(monday_str)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'erro': 'Formato de data inválido'}), 400
    if monday.weekday() != 0:
        return jsonify({'ok': False, 'erro': 'A semana deve começar numa segunda-feira.'}), 400
    codigo = resolve_week_codigo(monday, SemanaConfig.query.all())
    label  = week_label(monday)
    dias   = week_from_monday(monday)
    SemanaConfig.query.update({'ativa': False})
    existing = SemanaConfig.query.filter_by(codigo=codigo).first()
    if existing:
        existing.label = label; existing.dias_json = json.dumps(dias); existing.ativa = True
    else:
        db.session.add(SemanaConfig(codigo=codigo, label=label,
                                    dias_json=json.dumps(dias), ativa=True))
    db.session.commit()
    return jsonify({'ok': True, 'codigo': codigo, 'label': label})

@app.route('/supervisor/marcar-revisto/<int:disp_id>', methods=['POST'])
@supervisor_required
def marcar_revisto(disp_id):
    d = db.session.get(Disponibilidade, disp_id)
    if d:
        d.revisto = not d.revisto
        db.session.commit()
        if request.form.get('view_state') == '1':
            # Browsers send an absolute Referer, which safe_referrer deliberately
            # rejects. Preserve the review view through bounded form fields and
            # a fixed internal destination, never an arbitrary return URL.
            view = {'semana': d.semana}
            person = request.form.get('pessoa', '').strip()[:80]
            if person:
                view['pessoa'] = person
            mode = request.form.get('ver', 'all')
            if mode in {'pending', 'notes', 'delivery'}:
                view['ver'] = mode
            allowed = {r[0] for r in db.session.query(Disponibilidade.funcionario)
                       .filter_by(semana=d.semana).distinct().all()}
            opened = set(request.form.get('abrir', '').split(',')) & allowed
            view['abrir'] = ','.join(sorted(opened))
            return redirect(url_for('supervisor_disponibilidades', **view))
    return redirect(safe_referrer('supervisor_disponibilidades'))

# ── API ────────────────────────────────────────────────────────────────────────

# ── Descarga (delivery unloading) ───────────────────────────────────────────

def _descarga_overlap_msg(func, dia, semana, horario, model=None):
    """Return a Portuguese conflict message if `func` has a descarga on this
    (dia, semana) whose window overlaps `horario`; otherwise None."""
    rows = (model or schedule_descarga()).query.filter_by(funcionario=func, dia=dia, semana=semana).all()
    for dc in rows:
        if _shifts_overlap(dc.horario, horario):
            return (f"{func} está na descarga ({dc.horario}) neste dia — "
                    f"não pode ter um turno que se sobreponha.")
    return None


def descarga_horas_semana(semana: str) -> dict:
    """{sigla: hours} credited for descargas in `semana`.

    Each descarga is worth at least 4h, or its real duration when the window is
    longer (see helpers.time_helpers.descarga_hours). Every surface that shows
    or checks weekly hours goes through this so the employee page, the workload
    tracker, the conflitos thresholds and the CSV export always agree.
    """
    out = {}
    for dc in schedule_descarga().query.filter_by(semana=semana).all():
        out[dc.funcionario] = out.get(dc.funcionario, 0.0) + descarga_hours(dc.horario)
    return out


def _descarga_optin_siglas(semana, dia):
    """Siglas who ticked the descarga opt-in for this day — the pool the
    supervisor can assign from. (Pre-rework this was a tipo of 'extra'; it's
    now an independent flag, so a worker can be Disponível AND do a descarga.)"""
    rows = Disponibilidade.query.filter_by(
        semana=semana, dia=dia, descarga_opt=True).all()
    return {r.funcionario for r in rows}


@app.route('/escala/descargas')
@supervisor_required
def escala_descargas():
    """Descarga assignment page (part of the escala area). One section per
    descarga day, one row per store, assignable from that day's opt-in pool."""
    semana_str = request.args.get('semana')
    if not semana_str:
        ativa = get_semana_ativa()
        semana_str = ativa.codigo if ativa else ''
    semana_obj = schedule_semana().query.filter_by(codigo=semana_str).first()
    if semana_obj:
        _hydrate_semana(semana_obj)

    funcionarios = {f.sigla: f for f in Funcionario.query.all()}
    nome_de = lambda sg: funcionarios[sg].nome if sg in funcionarios else sg

    # Current assignments, indexed by (dia, loja)
    assigned = {}
    if semana_obj:
        for dc in schedule_descarga().query.filter_by(semana=semana_str).all():
            assigned.setdefault((dc.dia, dc.loja), []).append(
                {'id': dc.id, 'sigla': dc.funcionario, 'nome': nome_de(dc.funcionario),
                 'horario': dc.horario})

    # Build the per-day structure (only days flagged as descarga days)
    dias_out = []
    descarga_days = []
    if semana_obj:
        labels = semana_obj.day_labels
        descarga_days = sorted(semana_obj.descarga_set or set(),
                               key=lambda d: labels.index(d) if d in labels else 99)
    dmap = semana_obj.descarga_map if semana_obj else {}
    loja_nome = {l['id']: l['nome'] for l in LOJAS}
    loja_short = {l['id']: l['short'] for l in LOJAS}
    loja_order = {l['id']: i for i, l in enumerate(LOJAS)}
    n_assignments = sum(len(v) for v in assigned.values())
    all_stores = set()
    for dia in descarga_days:
        optin = _descarga_optin_siglas(semana_str, dia)
        pool = sorted(({'sigla': sg, 'nome': nome_de(sg)} for sg in optin),
                      key=lambda p: p['nome'])
        day_stores = dmap.get(dia, {})
        all_stores |= set(day_stores)
        lojas_rows = []
        for lid in sorted(day_stores, key=lambda x: loja_order.get(x, 99)):
            cur = assigned.get((dia, lid), [])
            for member in cur:
                member['eligible'] = member['sigla'] in optin
            cur_siglas = {c['sigla'] for c in cur}
            lojas_rows.append({
                'loja': lid, 'loja_nome': loja_nome.get(lid, lid),
                'loja_short': loja_short.get(lid, lid),
                'horario': day_stores[lid],
                'assigned': cur,
                'available': [p for p in pool if p['sigla'] not in cur_siglas],
            })
        dias_out.append({'dia': dia, 'pool_size': len(pool), 'lojas': lojas_rows})
    n_lojas = len(all_stores)

    semanas = semanas_chronological(SemanaConfig)
    return render_template('descargas.html',
        semana_obj=semana_obj, semana_str=semana_str, semanas=semanas,
        dias=dias_out, n_dias=len(descarga_days), n_lojas=n_lojas,
        n_assignments=n_assignments)


@app.route('/api/descarga/add', methods=['POST'])
@supervisor_required
def api_descarga_add():
    data   = request.get_json(silent=True) or {}
    semana = (data.get('semana') or '').strip()[:30]
    dia    = (data.get('dia') or '').strip()[:20]
    loja   = (data.get('loja') or '').strip()[:30]
    func   = (data.get('funcionario') or '').strip().upper()[:10]
    if not all([semana, dia, loja, func]):
        return jsonify({'ok': False, 'erro': 'Campos em falta'}), 400
    if loja not in {l['id'] for l in LOJAS}:
        return jsonify({'ok': False, 'erro': 'Loja inválida'}), 400
    sem = SemanaConfig.query.filter_by(codigo=semana).first()
    if not sem:
        return jsonify({'ok': False, 'erro': 'Semana inválida'}), 400
    _hydrate_semana(sem)
    day_stores = (sem.descarga_map or {}).get(dia, {})
    if not day_stores:
        return jsonify({'ok': False, 'erro': 'Este dia não tem descarga.'}), 400
    if loja not in day_stores:
        return jsonify({'ok': False, 'erro': 'Esta loja não tem descarga neste dia.'}), 400
    if not db.session.get(Funcionario, func):
        return jsonify({'ok': False, 'erro': 'Funcionário inválido'}), 400
    # Only those who opted in for descarga that day
    if func not in _descarga_optin_siglas(semana, dia):
        return jsonify({'ok': False, 'erro': f'{func} não se marcou disponível para descarga neste dia.'}), 400
    # No duplicate at the same store/day
    if schedule_descarga().query.filter_by(semana=semana, dia=dia, loja=loja, funcionario=func).first():
        return jsonify({'ok': False, 'erro': f'{func} já está na descarga desta loja neste dia.'}), 409

    horario = day_stores[loja]
    # A worker cannot unload deliveries at two stores at the same time.
    for ex in schedule_descarga().query.filter_by(funcionario=func, dia=dia, semana=semana).all():
        if _shifts_overlap(ex.horario, horario):
            return jsonify(ok=False, erro=(
                f'{func} já tem descarga em {_LOJA_NOME.get(ex.loja, ex.loja)} '
                f'({ex.horario}) neste horário.')), 409
    # Reciprocal conflict: a regular shift overlapping the descarga window
    clash = schedule_turno().query.filter_by(funcionario=func, dia=dia, semana=semana).all()
    for ex in clash:
        if _shifts_overlap(ex.horario, horario):
            return jsonify({'ok': False, 'erro': (
                f'{func} tem um turno ({ex.horario} em {ex.loja}) que se '
                f'sobrepõe à descarga ({horario}).')}), 409

    dc = Descarga(semana=semana, dia=dia, loja=loja, funcionario=func, horario=horario)
    db.session.add(dc); db.session.commit()
    audit('descarga_add', target=f'{semana}/{dia}/{loja}', detail=func)
    f_obj = db.session.get(Funcionario, func)
    return jsonify({'ok': True, 'id': dc.id, 'horario': horario,
                    'nome': f_obj.nome if f_obj else func})


@app.route('/api/descarga/remove/<int:did>', methods=['DELETE'])
@supervisor_required
def api_descarga_remove(did):
    dc = db.session.get(schedule_descarga(), did)
    if not dc:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    audit('descarga_remove', target=f'{dc.semana}/{dc.dia}/{dc.loja}', detail=dc.funcionario)
    db.session.delete(dc); db.session.commit()
    return jsonify({'ok': True})


@app.route('/api/descarga/horario', methods=['POST'])
@supervisor_required
def api_descarga_horario():
    """Change the descarga window for one (dia, loja) cell of a week. Updates the
    week config and any existing assignments. Refuses if the new window would
    overlap a regular shift of someone already assigned to this descarga."""
    data    = request.get_json(silent=True) or {}
    semana  = (data.get('semana') or '').strip()[:30]
    dia     = (data.get('dia') or '').strip()[:20]
    loja    = (data.get('loja') or '').strip()[:30]
    horario = (data.get('horario') or '').strip()[:60]
    if not all([semana, dia, loja, horario]):
        return jsonify(ok=False, erro='Campos em falta'), 400
    if not re.match(r'^\d{1,2}:\d{2}-\d{1,2}:\d{2}$', horario):
        return jsonify(ok=False, erro='Formato inválido. Usar HH:MM-HH:MM.'), 400
    start, end = horario.split('-')
    clocks = [tuple(map(int, value.split(':'))) for value in (start, end)]
    if any(hour > 23 or minute > 59 for hour, minute in clocks) or clocks[0] == clocks[1]:
        return jsonify(ok=False, erro='Janela inválida.'), 400
    s1, e1 = _shift_bounds(horario)
    if s1 is None or e1 <= s1:
        return jsonify(ok=False, erro='Janela inválida.'), 400
    sem = SemanaConfig.query.filter_by(codigo=semana).first()
    if not sem:
        return jsonify(ok=False, erro='Semana inválida'), 400
    _hydrate_semana(sem)
    if loja not in (sem.descarga_map or {}).get(dia, {}):
        return jsonify(ok=False, erro='Esta loja não tem descarga neste dia.'), 400
    assigned = schedule_descarga().query.filter_by(semana=semana, dia=dia, loja=loja).all()
    for dc in assigned:
        others = schedule_descarga().query.filter(
            schedule_descarga().funcionario == dc.funcionario,
            schedule_descarga().semana == semana, schedule_descarga().dia == dia,
            schedule_descarga().id != dc.id,
        ).all()
        for ex in others:
            if _shifts_overlap(ex.horario, horario):
                return jsonify(ok=False, erro=(
                    f'{dc.funcionario} já tem descarga em '
                    f'{_LOJA_NOME.get(ex.loja, ex.loja)} ({ex.horario}). '
                    'O novo horário criaria uma sobreposição.')), 409
        for ex in schedule_turno().query.filter_by(funcionario=dc.funcionario, dia=dia, semana=semana).all():
            if _shifts_overlap(ex.horario, horario):
                return jsonify(ok=False, erro=(
                    f'{dc.funcionario} tem um turno ({ex.horario}) que se sobreporia '
                    f'a {horario}. Ajusta o turno primeiro.')), 409
    cfg = {d: dict(st) for d, st in sem.descarga_map.items()}
    cfg.setdefault(dia, {})[loja] = horario
    sem.descarga_config = json.dumps(cfg)
    for dc in assigned:
        dc.horario = horario
    db.session.commit()
    audit('descarga_horario', target=f'{semana}/{dia}/{loja}', detail=horario)
    return jsonify(ok=True, horario=horario, updated=len(assigned))


def _assignment_warnings(func_obj, dia, semana):
    """Soft, non-blocking warnings computed right after a shift is assigned, so
    the supervisor catches likely mistakes at the point of action instead of
    later on the conflitos page. Counts include the shift just added.

    Mirrors the conflitos thresholds: indisponível that day, over-contract by
    >2h, and 6+ distinct working days in the week (weekly rest at risk).
    """
    if not func_obj:
        return []
    sig = func_obj.sigla
    avisos = []
    disp = Disponibilidade.query.filter_by(
        semana=semana, funcionario=sig, dia=dia).first()
    if disp and disp.tipo == 'indisponivel':
        avisos.append(f'{sig} marcou-se indisponível neste dia.')
    week_turnos = schedule_turno().query.filter_by(semana=semana, funcionario=sig).all()
    if func_obj.tipo_contrato == 'contrato' and func_obj.horas_contrato:
        total = (sum(_shift_hours(t.horario) for t in week_turnos)
                 + descarga_horas_semana(semana).get(sig, 0.0))
        if total - func_obj.horas_contrato > 2:
            avisos.append(f'{sig} fica com {total:.1f}h '
                          f'(contrato {func_obj.horas_contrato}h).')
    n_dias = len({t.dia for t in week_turnos})
    if n_dias >= 6:
        avisos.append(f'{sig} fica com {n_dias} dias de trabalho nesta semana.')
    return avisos


# SECURITY: a horario is rendered inside inline event handlers in escala.html —
# onclick="openQuickAdd('carmelitas','<horario>','Seg 14',this)". Jinja turns a
# quote into &#39;, but the HTML parser decodes it back BEFORE the JavaScript
# runs, so one apostrophe in a horario breaks every button on that row, and a
# crafted one runs script in the supervisor's session. Shift labels only ever
# need times, "(+0,5)" extras and plain words ("Provas Práticas"), so allow
# exactly that and nothing that can close a string or open a tag.
_HORARIO_CHARS = re.compile(r"^[0-9A-Za-zÀ-ÖØ-öø-ÿ :+\-,.()]+$")
_HORARIO_ERRO  = 'Horário com caracteres inválidos. Usar o formato HH:MM-HH:MM.'

def _horario_ok(h: str) -> bool:
    return bool(h) and bool(_HORARIO_CHARS.match(h))

# Error messages name the store as people know it, not by its internal id.
_LOJA_NOME = {l['id']: l['nome'] for l in LOJAS}

def _other_store_msg(sigla: str, ex) -> str:
    return (f"{sigla} já está escalado para {_LOJA_NOME.get(ex.loja, ex.loja)} "
            f"({ex.horario}) neste dia. Se for troca de loja, indicar "
            f"'Troca de loja' na nota.")


@app.route('/api/turno/add', methods=['POST'])
@supervisor_required
def api_add_turno():
    data    = request.get_json(silent=True) or {}
    # Tight input validation — clamp lengths and validate referenced entities.
    loja    = (data.get('loja') or '').strip()[:30]
    horario = (data.get('horario') or '').strip()[:60]
    dia     = (data.get('dia') or '').strip()[:20]
    func    = (data.get('funcionario') or '').strip().upper()[:10]
    nota    = (data.get('nota') or '').strip()[:100]
    semana  = (data.get('semana') or '').strip()[:30]
    if not all([loja, horario, dia, func, semana]):
        return jsonify({'ok': False, 'erro': 'Campos em falta (semana obrigatória)'}), 400

    # Validate loja is one of ours; semana exists; funcionario exists
    if loja not in {l['id'] for l in LOJAS}:
        return jsonify({'ok': False, 'erro': 'Loja inválida'}), 400
    if not _horario_ok(horario):
        return jsonify({'ok': False, 'erro': _HORARIO_ERRO}), 400
    if not SemanaConfig.query.filter_by(codigo=semana).first():
        return jsonify({'ok': False, 'erro': 'Semana inválida'}), 400
    if dia not in _dias_da_semana(semana):
        return jsonify({'ok': False, 'erro': 'O dia não pertence a esta semana.'}), 400
    if not db.session.get(Funcionario, func):
        return jsonify({'ok': False, 'erro': 'Funcionário inválido'}), 400

    # FIX BUG 3: smarter same-day conflict.
    same_day_turnos = schedule_turno().query.filter_by(
        dia=dia, funcionario=func, semana=semana).all()
    for ex in same_day_turnos:
        if ex.loja == loja and ex.horario == horario:
            return jsonify({
                'ok': False,
                'erro': f"{func} já está escalado para este turno exato."
            }), 409
        if ex.loja != loja:
            if _shifts_overlap(ex.horario, horario):
                return jsonify({'ok': False,
                    'erro': (f'{func} já tem {ex.horario} em '
                             f'{_LOJA_NOME.get(ex.loja, ex.loja)} e os turnos sobrepõem-se.')}), 409
            is_troca = 'troca' in (nota or '').lower()
            if not is_troca:
                return jsonify({'ok': False, 'erro': _other_store_msg(func, ex)}), 409
        elif _shifts_overlap(ex.horario, horario):
            return jsonify({
                'ok': False,
                'erro': (f"{func} já tem {ex.horario} nesta loja e esse turno "
                         f"sobrepõe-se ao novo.")
            }), 409

    # Descarga occupies the worker for its window; a regular shift overlapping
    # those hours the same day is a conflict (see Descarga model).
    dmsg = _descarga_overlap_msg(func, dia, semana, horario)
    if dmsg:
        return jsonify({'ok': False, 'erro': dmsg}), 409

    t = Turno(loja=loja, horario=horario, dia=dia, funcionario=func,
              nota=nota, semana=semana, updated_at=datetime.now(timezone.utc))
    db.session.add(t); db.session.commit()
    f_obj = db.session.get(Funcionario, func)
    return jsonify({'ok': True, 'id': t.id,
                    'tipo_contrato': f_obj.tipo_contrato if f_obj else 'contrato',
                    'aviso': ' '.join(_assignment_warnings(f_obj, dia, semana))})

@app.route('/api/turno/remove/<int:tid>', methods=['DELETE'])
@supervisor_required
def api_remove_turno(tid):
    t = db.session.get(schedule_turno(), tid)
    if not t:
        return jsonify({'ok': False}), 404
    db.session.delete(t); db.session.commit()
    return jsonify({'ok': True})

@app.route('/api/turno/move/<int:tid>', methods=['POST'])
@supervisor_required
def api_move_turno(tid):
    """FIX BUG 2: atomically move a turno to a new (loja, horario, dia).
    Before this, the frontend did DELETE+ADD as two separate requests, so a
    failed add after a successful delete silently lost the row.

    Body: {to_loja, to_horario, to_dia, semana?, nota?}
    Returns: {ok, id, tipo_contrato} on success, or {ok:false, erro:...} on conflict.
    On conflict the original turno is preserved (we never delete first).
    """
    t = db.session.get(schedule_turno(), tid)
    if not t:
        return jsonify({'ok': False, 'erro': 'Turno não encontrado'}), 404

    data = request.get_json(silent=True) or {}
    # Same clamps and checks as api_add_turno: this writes the same columns.
    to_loja    = str(data.get('to_loja') or '').strip()[:30]
    to_horario = str(data.get('to_horario') or '').strip()[:60]
    to_dia     = str(data.get('to_dia') or '').strip()[:20]
    new_nota   = str(data.get('nota', t.nota or '') or '').strip()[:100]
    if not all([to_loja, to_horario, to_dia]):
        return jsonify({'ok': False, 'erro': 'Campos em falta'}), 400
    if to_loja not in {l['id'] for l in LOJAS}:
        return jsonify({'ok': False, 'erro': 'Loja inválida'}), 400
    if not _horario_ok(to_horario):
        return jsonify({'ok': False, 'erro': _HORARIO_ERRO}), 400
    if to_dia not in _dias_da_semana(t.semana):
        return jsonify({'ok': False, 'erro': 'O dia não pertence a esta semana.'}), 400

    # No-op move
    if (to_loja, to_horario, to_dia) == (t.loja, t.horario, t.dia):
        return jsonify({'ok': True, 'id': t.id, 'noop': True})

    # Same conflict checks as api_add_turno, but ignore the turno we're moving.
    same_day_turnos = schedule_turno().query.filter(
        schedule_turno().dia == to_dia,
        schedule_turno().funcionario == t.funcionario,
        schedule_turno().semana == t.semana,
        schedule_turno().id != t.id,
    ).all()
    for ex in same_day_turnos:
        if ex.loja == to_loja and ex.horario == to_horario:
            return jsonify({'ok': False, 'erro': f"{t.funcionario} já está escalado para este turno exato."}), 409
        if ex.loja != to_loja:
            if _shifts_overlap(ex.horario, to_horario):
                return jsonify({'ok': False,
                    'erro': (f'{t.funcionario} já tem {ex.horario} em '
                             f'{_LOJA_NOME.get(ex.loja, ex.loja)} e os turnos sobrepõem-se.')}), 409
            is_troca = 'troca' in (new_nota or '').lower()
            if not is_troca:
                return jsonify({'ok': False, 'erro': _other_store_msg(t.funcionario, ex)}), 409
        elif _shifts_overlap(ex.horario, to_horario):
            return jsonify({
                'ok': False,
                'erro': (f"{t.funcionario} já tem {ex.horario} nesta loja e o "
                         f"novo turno sobrepõe-se.")
            }), 409

    dmsg = _descarga_overlap_msg(t.funcionario, to_dia, t.semana, to_horario)
    if dmsg:
        return jsonify({'ok': False, 'erro': dmsg}), 409

    # Apply the move on the existing row — preserves id and is atomic.
    t.loja = to_loja
    t.horario = to_horario
    t.dia = to_dia
    t.nota = new_nota
    t.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    f_obj = db.session.get(Funcionario, t.funcionario)
    return jsonify({'ok': True, 'id': t.id,
                    'tipo_contrato': f_obj.tipo_contrato if f_obj else 'contrato'})

@app.route('/api/horario/add', methods=['POST'])
@supervisor_required
def api_add_horario():
    """Persist a new shift block in the DB for a given loja.
    Also: if this exact horario was suppressed (FIX BUG 4), un-suppress it
    instead of refusing as 'already exists in base'."""
    data = request.get_json(silent=True) or {}
    loja = (data.get('loja') or '').strip()[:30]
    h    = (data.get('horario') or '').strip()[:60]
    if not loja or not h:
        return jsonify({'ok': False, 'erro': 'Loja e horário obrigatórios'}), 400
    if loja not in {l['id'] for l in LOJAS}:
        return jsonify({'ok': False, 'erro': 'Loja inválida'}), 400
    if not _horario_ok(h):
        return jsonify({'ok': False, 'erro': _HORARIO_ERRO}), 400

    sup = HorarioSupressed.query.filter_by(loja=loja, horario=h).first()
    if sup:
        db.session.delete(sup)
        db.session.commit()
        audit('horario_restore', target=f'{loja}/{h}')
        return jsonify({'ok': True, 'tipo': 'restored'})

    if h in HORARIOS_BASE.get(loja, []):
        return jsonify({'ok': False, 'erro': 'Esse bloco já existe (base)'}), 409
    existing = HorarioBloco.query.filter_by(loja=loja, horario=h).first()
    if existing:
        return jsonify({'ok': False, 'erro': 'Esse bloco já foi adicionado'}), 409
    max_ord = db.session.query(db.func.max(HorarioBloco.ordem)).filter_by(loja=loja).scalar() or 999
    db.session.add(HorarioBloco(loja=loja, horario=h, ordem=max_ord + 1))
    db.session.commit()
    audit('horario_add', target=f'{loja}/{h}', detail='custom')
    return jsonify({'ok': True, 'tipo': 'created'})


@app.route('/api/horario/delete', methods=['POST'])
@supervisor_required
def api_delete_horario():
    """Delete (or, for base blocks, suppress) a shift block.
    Refuses if anyone is currently assigned to it."""
    data = request.get_json(silent=True) or {}
    loja = (data.get('loja') or '').strip()[:30]
    h    = (data.get('horario') or '').strip()[:60]
    if not loja or not h:
        return jsonify({'ok': False, 'erro': 'Campos em falta'}), 400
    if loja not in {l['id'] for l in LOJAS}:
        return jsonify({'ok': False, 'erro': 'Loja inválida'}), 400
    # Only count usage in weeks that haven't fully ended yet (current + future).
    # Past-week assignments are history — each Turno stores its hours as plain
    # text, so removing a band never alters past schedules, and old usage
    # shouldn't stop the supervisor from retiring a band going forward.
    today_iso = date.today().isoformat()
    relevant = set()
    for s in SemanaConfig.query.all():
        try:
            dias = json.loads(s.dias_json or '[]')
            last_date = dias[-1].get('date', '') if dias else ''
        except Exception:
            last_date = ''
        if (not last_date) or last_date >= today_iso:
            relevant.add(s.codigo)
    in_use = (schedule_turno().query.filter_by(loja=loja, horario=h)
              .filter(schedule_turno().semana.in_(relevant)).count()) if relevant else 0
    if in_use > 0:
        return jsonify({
            'ok': False,
            'erro': f'Bloco em uso ({in_use} turno{"s" if in_use!=1 else ""} em semanas atuais ou futuras). Remove esses turnos primeiro.'
        }), 409
    cb = HorarioBloco.query.filter_by(loja=loja, horario=h).first()
    if cb:
        db.session.delete(cb)
        db.session.commit()
        audit('horario_delete', target=f'{loja}/{h}', detail='custom')
        return jsonify({'ok': True, 'tipo': 'custom'})
    if h in HORARIOS_BASE.get(loja, []):
        already = HorarioSupressed.query.filter_by(loja=loja, horario=h).first()
        if not already:
            db.session.add(HorarioSupressed(loja=loja, horario=h))
            db.session.commit()
            audit('horario_suppress', target=f'{loja}/{h}', detail='base')
        return jsonify({'ok': True, 'tipo': 'base'})
    return jsonify({'ok': False, 'erro': 'Bloco não encontrado'}), 404


def _managed_store(data, rank, current=''):
    """Validate the optional scheduling responsibility before changing a row."""
    store = data.get('loja_gerida', current if rank == 'store_manager' else '')
    if not isinstance(store, str):
        raise ValueError('Loja gerida inválida')
    store = store.strip()
    if store and store not in {loja['id'] for loja in LOJAS}:
        raise ValueError('Loja gerida inválida')
    if store and rank != 'store_manager':
        raise ValueError('A loja gerida aplica-se apenas aos responsáveis de loja')
    return store


@app.route('/api/funcionario/update-rank', methods=['PATCH'])
@supervisor_required
def api_update_rank():
    data  = request.get_json(silent=True) or {}
    sigla = (data.get('sigla') or '').strip().upper()[:10]
    rank  = (data.get('rank') or '').strip()
    if rank not in RANK_LEVELS:
        return jsonify({'ok': False, 'erro': 'Função inválida'}), 400
    f = db.session.get(Funcionario, sigla)
    if not f:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    try:
        store = _managed_store(data, rank, f.loja_gerida or '')
    except ValueError as exc:
        return jsonify({'ok': False, 'erro': str(exc)}), 400
    old = f'{f.rank} / {f.loja_gerida or "sem loja"}'
    f.rank = rank
    f.loja_gerida = store
    db.session.commit()
    audit('funcionario_rank', target=sigla, detail=f'{old} -> {rank} / {store or "sem loja"}')
    return jsonify({'ok': True, 'rank': rank, 'loja_gerida': store})

@app.route('/api/funcionario/reset-pin/<sigla>', methods=['POST'])
@supervisor_required
def api_reset_pin(sigla):
    """Revoke access now and issue a single-use, 24-hour recovery invitation."""
    sigla = (sigla or '').strip().upper()[:10]
    f = db.session.get(Funcionario, sigla)
    if not f:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    return _issue_access_invitation(f, reset=True)


@app.route('/api/funcionario/convite/<sigla>', methods=['POST'])
@supervisor_required
def api_invite_employee(sigla):
    """Only the supervisor can provision an employee without valid access."""
    sigla = (sigla or '').strip().upper()[:10]
    employee = db.session.get(Funcionario, sigla)
    if not employee:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    if employee.pin:
        return jsonify({'ok': False, 'erro': 'O acesso já está ativo. Usar Repor acesso.'}), 409
    return _issue_access_invitation(employee, reset=False)


def _issue_access_invitation(employee, *, reset):
    # Never include the secret in a URL, audit detail, or application log.
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    kind = 'reset' if reset else 'enrollment'
    sigla = employee.sigla
    Funcionario.query.filter_by(sigla=sigla).update({
        'pin': '', 'ical_token': '',
        'access_version': Funcionario.access_version + 1,
        'enrollment_hash': invitation_digest(token),
        'enrollment_expires_at': expires.replace(tzinfo=None),
        'enrollment_kind': kind,
    }, synchronize_session=False)
    db.session.add(AuditLog(
        actor=current_user_sigla(), ip=(request.remote_addr or '?')[:64],
        action='pin_reset' if reset else 'enrollment_issued', target=sigla,
        detail=f'{kind}; expires_at={expires.isoformat()}; previous-access-revoked',
    ))
    db.session.commit()
    db.session.expire_all()
    response = jsonify({'ok': True, 'sigla': sigla, 'convite': token,
                        'expira_em': expires.isoformat(), 'acesso_url': url_for('enroll_access')})
    response.headers['Cache-Control'] = 'no-store, private, max-age=0'
    return response

@app.route('/api/funcionario/add', methods=['POST'])
@supervisor_required
def api_add_funcionario():
    data  = request.get_json(silent=True) or {}
    sigla = (data.get('sigla') or '').strip().upper()[:10]
    nome  = (data.get('nome')  or '').strip()[:80]
    try:
        horas = int(data.get('horas', 0))
    except (TypeError, ValueError):
        horas = 0
    horas = max(0, min(horas, 80))   # cap at 80h/week — beyond that is bogus
    tipo  = data.get('tipo_contrato', 'contrato')
    if tipo not in ('contrato', 'turnos'):
        tipo = 'turnos' if horas < AVENCA_THRESHOLD else 'contrato'
    # Accept rank at creation time (falls back to 'staff'). The equipa UI used
    # to need a second update-rank call right after add — no longer required,
    # though the old two-step flow still works.
    rank = (data.get('rank') or 'staff').strip()
    if rank not in RANK_LEVELS:
        rank = 'staff'
    if not sigla or not nome:
        return jsonify({'ok': False, 'erro': 'Campos em falta'}), 400
    # Sigla must be alphanumeric (and dot/hyphen are common e.g. JP.C)
    if not all(c.isalnum() or c in '.-_' for c in sigla):
        return jsonify({'ok': False, 'erro': 'Sigla com caracteres inválidos'}), 400
    # Don't allow creating an ADMIN-named funcionario — reserved name
    if sigla == ADMIN_SIGLA:
        return jsonify({'ok': False, 'erro': 'Sigla reservada'}), 400
    if db.session.get(Funcionario, sigla):
        return jsonify({'ok': False, 'erro': 'Sigla já existe'}), 409
    try:
        store = _managed_store(data, rank)
    except ValueError as exc:
        return jsonify({'ok': False, 'erro': str(exc)}), 400
    db.session.add(Funcionario(sigla=sigla, nome=nome,
                               horas_contrato=horas, tipo_contrato=tipo,
                               rank=rank, loja_gerida=store))
    db.session.commit()
    audit('funcionario_add', target=sigla, detail=f'{nome} {horas}h {tipo} {rank} / {store or "sem loja"}')
    return jsonify({'ok': True, 'tipo_contrato': tipo, 'rank': rank, 'loja_gerida': store})

@app.route('/api/funcionario/update-tipo', methods=['PATCH'])
@supervisor_required
def api_update_tipo_contrato():
    """Toggle a worker between contrato and turnos."""
    data  = request.get_json(silent=True) or {}
    sigla = (data.get('sigla') or '').strip().upper()[:10]
    tipo  = data.get('tipo_contrato', '')
    if tipo not in ('contrato', 'turnos'):
        return jsonify({'ok': False, 'erro': 'Tipo inválido'}), 400
    f = db.session.get(Funcionario, sigla)
    if not f:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    f.tipo_contrato = tipo
    db.session.commit()
    audit('funcionario_tipo', target=sigla, detail=tipo)
    return jsonify({'ok': True})

@app.route('/api/upcoming-weeks')
@supervisor_required
def api_upcoming_weeks():
    # Same starting Monday as the dashboard's `upcoming` list — the Semanas tab
    # zips the two by index to fill in dates and feriados.
    weeks = []
    existing = SemanaConfig.query.all()
    for m in mondays_from_current_week(12):
        dias     = week_from_monday(m)
        feriados = [d for d in dias if d['feriado']]
        codigo = resolve_week_codigo(m, existing)
        weeks.append({
            'monday':   m.isoformat(),
            'codigo':   codigo,
            'label':    week_label(m),
            'feriados': feriados,
            'exists':   any(s.codigo == codigo for s in existing),
        })
    return jsonify(weeks)

# ════════════════════════════════════════════════════════════════════════════════
# PDF EXPORT ROUTES
# ════════════════════════════════════════════════════════════════════════════════

@app.route('/supervisor/exportar-pdf')
@supervisor_required
def exportar_pdf():
    """Generate the full weekly schedule PDF for all stores."""
    from flask import send_file
    from gerar_pdf import gerar_escala_pdf
    import io

    semana_str = request.args.get('semana')
    if not semana_str:
        ativa = get_semana_ativa()
        semana_str = ativa.codigo if ativa else '11-17mai'

    semana_obj = schedule_semana().query.filter_by(codigo=semana_str).first()
    if not semana_obj:
        return 'Semana não encontrada', 404

    dias_full = json.loads(semana_obj.dias_json or '[]')
    dias      = [d['label'] for d in dias_full]
    feriados  = {d['label']: d['feriado_nome'] for d in dias_full if d['feriado']}

    lojas_data = []
    for loja in LOJAS:
        grid, _ = build_grid(loja['id'], semana_str, dias)
        lojas_data.append({
            'nome':     loja['nome'],
            'grid':     grid,
            'dias':     dias,
            'feriados': feriados,
        })

    pdf_bytes = gerar_escala_pdf(_export_schedule_label(semana_obj), lojas_data, func_map_with_tipo(), dias_full=dias_full)

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'escala-amorino-{semana_str}.pdf'
    )


@app.route('/supervisor/exportar-horas-csv')
@supervisor_required
def exportar_horas_csv():
    """Export per-employee planned hours for a week as a CSV for reconciliation.

    One row per scheduled person, with shift hours, descarga hours, hours
    scheduled on a public holiday, the weekly total and the gap vs contracted
    hours. These are not attendance records or payroll calculations. A TOTAL
    row closes the sheet.

    Formatting targets a Portuguese Excel: ';' delimiter + comma decimals so the
    numbers parse as numbers, and a UTF-8 BOM so accented names render right.
    """
    from flask import send_file
    import io, csv

    semana_str = request.args.get('semana')
    if not semana_str:
        ativa = get_semana_ativa()
        semana_str = ativa.codigo if ativa else ''
    semana_obj = schedule_semana().query.filter_by(codigo=semana_str).first()
    if not semana_obj:
        return 'Semana não encontrada', 404

    dias_full = json.loads(semana_obj.dias_json or '[]')
    feriado_dias = {d['label'] for d in dias_full if d.get('feriado')}
    funcs = {f.sigla: f for f in Funcionario.query.all()}

    # Aggregate shift + descarga hours per worker for this week.
    agg = {}   # sigla -> totals dict
    def _row(sig):
        return agg.setdefault(sig, {
            'turnos': 0, 'horas_turnos': 0.0,
            'descargas': 0, 'horas_descargas': 0.0, 'horas_feriado': 0.0,
        })
    for t in schedule_turno().query.filter_by(semana=semana_str).all():
        r = _row(t.funcionario)
        h = _shift_hours(t.horario)
        r['turnos'] += 1
        r['horas_turnos'] += h
        if t.dia in feriado_dias:
            r['horas_feriado'] += h
    for dc in schedule_descarga().query.filter_by(semana=semana_str).all():
        r = _row(dc.funcionario)
        h = descarga_hours(dc.horario)   # 4h floor, real duration when longer
        r['descargas'] += 1
        r['horas_descargas'] += h
        if dc.dia in feriado_dias:
            r['horas_feriado'] += h

    def _num(x):
        # Portuguese-locale decimal: comma separator, 2 places, no grouping.
        return f'{round(x, 2):.2f}'.replace('.', ',')

    # Stable, readable order: by name (fall back to sigla).
    siglas = sorted(agg.keys(),
                    key=lambda s: (funcs[s].nome if s in funcs else s).lower())

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=';')
    # Keep the numerical columns stable for existing reconciliation sheets,
    # while identifying the authorised snapshot inside the downloaded file.
    export_label = _export_schedule_label(semana_obj)
    w.writerow(['Sigla', 'Nome', 'Tipo', 'Contrato (h)', 'Turnos',
                'Horas turnos', 'Descargas', 'Horas descargas',
                'Horas feriado', 'Total horas', 'Diferença', 'Semana', 'Estado da escala'])
    tot = {'turnos': 0, 'horas_turnos': 0.0, 'descargas': 0,
           'horas_descargas': 0.0, 'horas_feriado': 0.0, 'total': 0.0}
    for s in siglas:
        r = agg[s]
        f = funcs.get(s)
        nome = f.nome if f else s
        tipo = (f.tipo_contrato if f else '') or ''
        contrato = f.horas_contrato if f else 0
        total = r['horas_turnos'] + r['horas_descargas']
        # "Diferença vs contrato" only means something for contracted workers.
        is_contrato = bool(f and f.tipo_contrato == 'contrato')
        diff_cell = _num(total - contrato) if is_contrato else ''
        w.writerow([s, nome, tipo, contrato, r['turnos'],
                    _num(r['horas_turnos']), r['descargas'],
                    _num(r['horas_descargas']), _num(r['horas_feriado']),
                    _num(total), diff_cell, semana_str, export_label])
        tot['turnos'] += r['turnos']; tot['horas_turnos'] += r['horas_turnos']
        tot['descargas'] += r['descargas']
        tot['horas_descargas'] += r['horas_descargas']
        tot['horas_feriado'] += r['horas_feriado']; tot['total'] += total
    w.writerow(['', 'TOTAL', '', '', tot['turnos'], _num(tot['horas_turnos']),
                tot['descargas'], _num(tot['horas_descargas']),
                _num(tot['horas_feriado']), _num(tot['total']), '', semana_str, export_label])

    # UTF-8 BOM so Excel detects the encoding (otherwise accents break).
    data = bytes([0xEF, 0xBB, 0xBF]) + buf.getvalue().encode('utf-8')
    return send_file(
        io.BytesIO(data),
        mimetype='text/csv',   # Flask appends '; charset=utf-8' for text types
        as_attachment=True,
        download_name=f'horas-amorino-{semana_str}.csv'
    )


@app.route('/supervisor/exportar-pdf-individual/<sigla>')
@login_required
def exportar_pdf_individual(sigla):
    """Generate a personal schedule PDF for one employee.
    Employees can export their own; supervisor can export anyone's."""
    from flask import send_file
    from gerar_pdf import gerar_escala_individual_pdf
    import io

    sigla = (sigla or '').strip().upper()
    # Only the supervisor or the person themselves may export
    if not is_supervisor() and sigla != current_user_sigla():
        return 'Sem permissão', 403

    semana_str = request.args.get('semana')
    if not semana_str:
        ativa = get_semana_ativa() if is_supervisor() else get_semana_for_employee()
        semana_str = ativa.codigo if ativa else '11-17mai'

    func = db.session.get(Funcionario, sigla)
    if not func:
        return 'Funcionário não encontrado', 404

    semana_obj = schedule_semana().query.filter_by(codigo=semana_str).first()
    if not semana_obj:
        return 'Semana não encontrada', 404

    # Collect all shifts for this employee across all stores
    turnos = schedule_turno().query.filter_by(funcionario=sigla.upper(), semana=semana_str).all()
    loja_map = {l['id']: l['nome'] for l in LOJAS}
    turnos_data = [
        {'dia': t.dia, 'horario': t.horario,
         'loja_nome': loja_map.get(t.loja, t.loja), 'nota': t.nota or '',
         'tipo': 'turno', 'horas': _shift_hours(t.horario)}
        for t in turnos
    ]
    turnos_data.extend(
        {'dia': dc.dia, 'horario': dc.horario,
         'loja_nome': loja_map.get(dc.loja, dc.loja), 'nota': 'Descarga',
         'tipo': 'descarga', 'horas': descarga_hours(dc.horario)}
        for dc in schedule_descarga().query.filter_by(funcionario=sigla, semana=semana_str).all()
    )

    # Get dias_full for ordering + feriado info in PDF
    dias_full = json.loads(semana_obj.dias_json or '[]')

    pdf_bytes = gerar_escala_individual_pdf(
        _export_schedule_label(semana_obj), func.sigla, func.nome,
        func.tipo_contrato, turnos_data, dias_full=dias_full
    )

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'escala-{sigla.lower()}-{semana_str}.pdf'
    )


# ── iCal feed (Calendar subscription) ─────────────────────────────────────────
# Generates a text/calendar feed of all upcoming shifts for a funcionario.
# The phone/desktop calendar app subscribes to a URL like:
#     https://<host>/cal/JC/abc123...XYZ.ics
# The token is a per-funcionario bearer secret — anyone with the URL can read
# the calendar. We accept that trade-off because (a) calendar apps cannot send
# our session cookie, (b) the data is non-sensitive (your own shift list), and
# (c) the supervisor can rotate the token any time.

def _ical_escape(s: str) -> str:
    """Escape per RFC 5545 §3.3.11."""
    return (str(s or '')
            .replace('\\', '\\\\')
            .replace(';',  '\\;')
            .replace(',',  '\\,')
            .replace('\n', '\\n'))

def _ical_fold(line: str) -> str:
    """Fold lines longer than 75 octets per RFC 5545 §3.1."""
    # Naive byte-wise fold (UTF-8 may produce slightly under 75 bytes per line)
    enc = line.encode('utf-8')
    if len(enc) <= 75:
        return line
    out, i = [], 0
    while i < len(enc):
        chunk = enc[i:i + 73]   # leave 2 bytes for CRLF + space
        out.append(chunk.decode('utf-8', errors='ignore'))
        i += 73
    return ('\r\n ').join(out)

def _dtstamp_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')

def _shift_datetime_pair(date_iso: str, horario: str):
    """Return (dt_start, dt_end) in Europe/Lisbon-naive (no TZ info) given an
    ISO date and a horario like '08:00-16:30' or '16:00-00:30 (+1)'.
    The iCal output uses these as floating local times — clients render in
    the user's timezone, which for our Porto staff is Europe/Lisbon."""
    m = re.match(r'(\d{1,2}):(\d{2})\D+(\d{1,2}):(\d{2})', horario or '')
    if not m:
        return None, None
    try:
        d = datetime.fromisoformat(date_iso)
    except (ValueError, TypeError):
        return None, None
    h1, m1, h2, m2 = map(int, m.groups())
    start = d.replace(hour=h1, minute=m1, second=0, microsecond=0)
    end   = d.replace(hour=h2, minute=m2, second=0, microsecond=0)
    if end <= start:
        end += timedelta(days=1)   # midnight crossover
    return start, end

def _ensure_ical_token(func: Funcionario) -> str:
    """Lazily mint and persist a per-funcionario iCal token."""
    if not func.ical_token:
        func.ical_token = secrets.token_urlsafe(32)
        db.session.commit()
    return func.ical_token


def _parse_lembrete_minutes(raw) -> int:
    """Parse the ?lembrete=<minutes> query param for the iCal feed.

    Returns the reminder lead time in minutes before each shift, or 0 for
    'no reminder'. Clamped to a safe allow-list so a hand-edited URL can't
    inject anything weird. Backward-compatible: missing/invalid → 0 (no
    alarm), so existing subscriptions are unchanged.
    """
    ALLOWED = {0, 30, 60, 120, 180, 720}   # none / 30m / 1h / 2h / 3h / night-before(~12h)
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return 0
    return n if n in ALLOWED else 0


def _valarm_lines(minutes: int, label: str) -> list:
    """Build the VALARM block for a relative reminder `minutes` before start.
    Empty list when minutes <= 0 (no alarm)."""
    if minutes <= 0:
        return []
    # Normalise to a clean RFC-5545 duration: whole hours when divisible.
    if minutes % 60 == 0:
        trigger = f'-PT{minutes // 60}H'
    else:
        trigger = f'-PT{minutes}M'
    return [
        'BEGIN:VALARM',
        'ACTION:DISPLAY',
        _ical_fold(f'DESCRIPTION:{_ical_escape(label)}'),
        f'TRIGGER:{trigger}',
        'END:VALARM',
    ]


@app.route('/cal/<sigla>/<token>.ics')
def ical_feed(sigla, token):
    """Public iCal feed for a single funcionario, gated by per-user token.
    No login required (calendar clients can't send cookies)."""
    sigla = (sigla or '').strip().upper()[:10]
    func = db.session.get(Funcionario, sigla)
    # SECURITY: one response for every failure. Answering 404 for an unknown
    # sigla and 403 for a wrong token let anyone — unauthenticated — walk a list
    # of candidate siglas and read back which ones are real staff with a
    # calendar. That is the same roster enumeration /login was hardened against,
    # so this endpoint must not hand it back. The .svg twin below already
    # collapses both cases; keep the two in step.
    if (not func or not func.ical_token
            or not hmac.compare_digest(str(func.ical_token), str(token or ''))):
        log.info('iCal feed: rejected %s from %s', sigla, request.remote_addr)
        return ('Calendar not found.', 404, {'Content-Type': 'text/plain'})

    # Gather all shifts for this funcionario across all weeks; we keep past
    # ones too so the calendar shows full history. Most clients trim ancient.
    rows = (db.session.query(schedule_turno(), schedule_semana())
            .join(schedule_semana(), schedule_turno().semana == schedule_semana().codigo)
            .filter(schedule_turno().funcionario == sigla)
            .order_by(schedule_turno().semana, schedule_turno().dia)
            .all())

    loja_name = {l['id']: l['nome'] for l in LOJAS}
    now_utc = datetime.now(timezone.utc)

    # Reminder lead-time (minutes before shift). 0 = no reminder. Carried in the
    # subscription URL so each person picks their own when they subscribe.
    lembrete_min = _parse_lembrete_minutes(request.args.get('lembrete'))

    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        'PRODID:-//Amorino Escalas//PT',
        'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH',
        f'X-WR-CALNAME:Amorino — {_ical_escape(func.nome)}',
        'X-WR-TIMEZONE:Europe/Lisbon',
        'X-WR-CALDESC:Escala publicada. A frequência de atualização depende da aplicação de calendário. Confirmar alterações na aplicação Amorino.',
    ]

    for turno, semana in rows:
        try:
            dias = json.loads(semana.dias_json or '[]')
        except json.JSONDecodeError:
            continue
        date_iso = next((d.get('date') for d in dias if d.get('label') == turno.dia), None)
        if not date_iso:
            continue
        feriado_nome = next((d.get('feriado_nome', '') for d in dias
                             if d.get('label') == turno.dia and d.get('feriado')), '')
        start, end = _shift_datetime_pair(date_iso, turno.horario)
        if not start:
            continue
        loja = loja_name.get(turno.loja, turno.loja)
        # Stable UID survives re-imports of the calendar
        uid = f'turno-{turno.id}-{turno.semana}@amorino'
        title = f'Amorino {loja}'
        desc_parts = [f'{turno.horario} · {loja}']
        if turno.nota:    desc_parts.append(f'Nota: {turno.nota}')
        if feriado_nome:  desc_parts.append(f'Feriado: {feriado_nome} (paga em dobro)')
        desc = '\\n'.join(_ical_escape(p) for p in desc_parts)

        lines.extend([
            'BEGIN:VEVENT',
            _ical_fold(f'UID:{uid}'),
            f'DTSTAMP:{_dtstamp_utc(turno.updated_at or now_utc)}',
            f'SEQUENCE:{turno.version}',
            f'DTSTART:{start.strftime("%Y%m%dT%H%M%S")}',
            f'DTEND:{end.strftime("%Y%m%dT%H%M%S")}',
            _ical_fold(f'SUMMARY:{_ical_escape(title)}'),
            _ical_fold(f'LOCATION:{_ical_escape(loja)}'),
            _ical_fold(f'DESCRIPTION:{desc}'),
        ])
        # Optional reminder before the shift starts.
        lines.extend(_valarm_lines(lembrete_min, f'Turno {loja} às {turno.horario}'))
        lines.append('END:VEVENT')

    lines.append('END:VCALENDAR')
    body = '\r\n'.join(lines) + '\r\n'

    resp = make_response(body)
    resp.headers['Content-Type']        = 'text/calendar; charset=utf-8'
    resp.headers['Content-Disposition'] = f'inline; filename="amorino-{sigla.lower()}.ics"'
    # Allow caching for a few minutes (calendar clients hit this often)
    resp.headers['Cache-Control']       = 'private, max-age=300'
    return resp


@app.route('/cal/turno/<int:turno_id>.ics')
@login_required
def shift_ics(turno_id):
    """Download a single shift as a .ics event (for Apple/Outlook calendars).

    Session-gated and scoped to the caller's OWN shifts only: we look up the
    turno and refuse if it doesn't belong to the logged-in user. This means the
    endpoint can't be used to enumerate or read anyone else's schedule — unlike
    the subscription feed (which is token-gated for calendar clients that can't
    send cookies), this one is for a logged-in human grabbing their own shift.
    """
    sigla = current_user_sigla()
    if not sigla:
        abort(403)
    turno = db.session.get(schedule_turno(), turno_id)
    if not turno or turno.funcionario != sigla:
        abort(404)

    # Resolve the shift's date + feriado from its week config.
    date_iso, feriado_nome = '', ''
    sem = schedule_semana().query.filter_by(codigo=turno.semana).first()
    if sem:
        try:
            for d in json.loads(sem.dias_json or '[]'):
                if d.get('label') == turno.dia:
                    date_iso = d.get('date', '')
                    feriado_nome = d.get('feriado_nome', '') if d.get('feriado') else ''
                    break
        except json.JSONDecodeError:
            pass

    start, end = _shift_datetime_pair(date_iso, turno.horario)
    if not start:
        abort(404)

    loja = {l['id']: l['nome'] for l in LOJAS}.get(turno.loja, turno.loja)
    uid = f'turno-{turno.id}-{turno.semana}@amorino'
    desc_parts = [f'{turno.horario} · {loja}']
    if turno.nota:   desc_parts.append(f'Nota: {turno.nota}')
    if feriado_nome: desc_parts.append(f'Feriado: {feriado_nome} (paga em dobro)')
    desc = '\\n'.join(_ical_escape(p) for p in desc_parts)
    # A single-event file gets a default 1-hour-before reminder so the download
    # is useful out of the box (the subscription feed handles its own reminders).
    now_utc = datetime.now(timezone.utc)

    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0',
        'PRODID:-//Amorino//Escalas//PT', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
        'BEGIN:VEVENT',
        _ical_fold(f'UID:{uid}'),
        f'DTSTAMP:{_dtstamp_utc(turno.updated_at or now_utc)}',
        f'SEQUENCE:{getattr(turno, "version", 0)}',
        f'DTSTART:{start.strftime("%Y%m%dT%H%M%S")}',
        f'DTEND:{end.strftime("%Y%m%dT%H%M%S")}',
        _ical_fold(f'SUMMARY:{_ical_escape("Amorino " + loja)}'),
        _ical_fold(f'LOCATION:{_ical_escape(loja)}'),
        _ical_fold(f'DESCRIPTION:{desc}'),
    ]
    lines += _valarm_lines(60, f'Turno {loja} às {turno.horario}')
    lines += ['END:VEVENT', 'END:VCALENDAR']
    body = '\r\n'.join(lines) + '\r\n'

    resp = make_response(body)
    resp.headers['Content-Type']        = 'text/calendar; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="amorino-turno.ics"'
    resp.headers['Cache-Control']       = 'private, max-age=60'
    return resp


@app.route('/api/cal-link')
@login_required
def api_cal_link():
    """Return the iCal subscription URL for the current user.
    Creates the token on first call. Supervisors can pass ?sigla=XYZ."""
    target_sigla = (request.args.get('sigla') or '').strip().upper()[:10]
    if target_sigla and is_supervisor():
        func = db.session.get(Funcionario, target_sigla)
    elif current_user_sigla() == ADMIN_SIGLA:
        return jsonify({'ok': False, 'erro': 'O ADMIN não tem calendário pessoal'}), 400
    else:
        func = current_user_func()
    if not func:
        return jsonify({'ok': False, 'erro': 'Funcionário não encontrado'}), 404
    tok = _ensure_ical_token(func)
    url = url_for('ical_feed', sigla=func.sigla, token=tok, _external=True)
    # Most calendar apps prefer webcal:// — provide both
    webcal_url = url.replace('http://', 'webcal://').replace('https://', 'webcal://')
    return jsonify({'ok': True, 'url': url, 'webcal_url': webcal_url, 'sigla': func.sigla})


@app.route('/api/cal-link/rotate', methods=['POST'])
@login_required
def api_cal_link_rotate():
    """Invalidate the existing iCal token and mint a new one. The old
    subscription URL stops working immediately. Use when a token leaks."""
    target_sigla = (request.args.get('sigla') or '').strip().upper()[:10]
    if target_sigla and is_supervisor():
        func = db.session.get(Funcionario, target_sigla)
    elif current_user_sigla() == ADMIN_SIGLA:
        return jsonify({'ok': False, 'erro': 'O ADMIN não tem calendário pessoal'}), 400
    else:
        func = current_user_func()
    if not func:
        return jsonify({'ok': False, 'erro': 'Funcionário não encontrado'}), 404
    func.ical_token = secrets.token_urlsafe(32)
    db.session.commit()
    audit('cal_token_rotate', target=func.sigla)
    url = url_for('ical_feed', sigla=func.sigla, token=func.ical_token, _external=True)
    return jsonify({'ok': True, 'url': url})


@app.route('/cal/<sigla>/<token>.svg')
def ical_qr(sigla, token):
    """SVG QR code for the iCal subscription URL.
    Same auth model as the .ics endpoint — the URL itself contains the token,
    and we don't render the QR unless the token matches."""
    sigla = (sigla or '').strip().upper()[:10]
    func = db.session.get(Funcionario, sigla)
    if not func or not func.ical_token or not hmac.compare_digest(
            str(func.ical_token), str(token or '')):
        return ('', 404, {'Content-Type': 'text/plain'})
    # Build the webcal URL (most calendar apps prefer this scheme). Carry the
    # reminder choice through so a scanned subscription includes the same alarm
    # the person selected in the modal.
    cal_url = url_for('ical_feed', sigla=sigla, token=token, _external=True)
    lembrete_min = _parse_lembrete_minutes(request.args.get('lembrete'))
    if lembrete_min:
        cal_url = f'{cal_url}?lembrete={lembrete_min}'
    webcal  = cal_url.replace('http://', 'webcal://').replace('https://', 'webcal://')
    try:
        import qrcode, qrcode.image.svg
        from io import BytesIO
        img = qrcode.make(webcal,
                          image_factory=qrcode.image.svg.SvgPathImage,
                          box_size=10, border=2)
        buf = BytesIO()
        img.save(buf)
        svg = buf.getvalue()
    except ImportError:
        return ('QR generation requires the qrcode package.', 500,
                {'Content-Type': 'text/plain'})
    resp = make_response(svg)
    resp.headers['Content-Type'] = 'image/svg+xml'
    resp.headers['Cache-Control'] = 'private, max-age=3600'
    return resp


# ═════════════════════════════════════════════════════════════════════════════
# Trocas (shift swap requests)
# ═════════════════════════════════════════════════════════════════════════════
# Three actors per pedido:
#   * de_sigla — who created the request
#   * para_sigla — the colleague being asked
#   * supervisor — approves the final transition
#
# A 'doação' is a swap where turno_destino_id is NULL: the colleague picks
# up the shift but doesn't give one back. Useful for sick days, appointments.

_LOJA_NOME = {l['id']: l['nome'] for l in LOJAS}


def _dias_da_semana(codigo: str, model=None) -> dict:
    """{day label: ISO date} for a week. Cached per request: the trocas page
    dates dozens of turnos that come from a handful of weeks."""
    model = model or schedule_semana()
    cache = g.setdefault('_dias_da_semana', {})
    key = (model.__tablename__, codigo)
    if key not in cache:
        sem = model.query.filter_by(codigo=codigo).first()
        try:
            dias = json.loads(sem.dias_json or '[]') if sem else []
        except json.JSONDecodeError:
            dias = []
        cache[key] = {d.get('label'): d.get('date', '') or '' for d in dias
                      if isinstance(d, dict)} if isinstance(dias, list) else {}
    return cache[key]


def _turno_info(turno) -> dict:
    """A turno as the trocas UI shows it.

    `quando` is the day people read ('Qui 1 out'). It replaces 'Qui 1 · 28-4out',
    which showed the week's internal code — and 'Qui 1' alone is ambiguous in a
    history that spans months. `passado` marks a shift whose day is behind us:
    nothing can be swapped once it has been worked.
    """
    iso = _turno_date_iso(turno)
    quando = turno.dia
    if iso:
        try:
            iso = date.fromisoformat(iso).isoformat()
            quando = f'{turno.dia} {MESES_CURTOS[date.fromisoformat(iso).month - 1]}'
        except (ValueError, TypeError):
            iso = ''
    return {
        'id':        turno.id,
        'sigla':     turno.funcionario,
        'loja':      turno.loja,
        'loja_nome': _LOJA_NOME.get(turno.loja, turno.loja),
        'horario':   turno.horario,
        'dia':       turno.dia,
        'semana':    turno.semana,
        'data':      iso,
        'quando':    quando,
        'passado':   bool(iso and iso < date.today().isoformat()),
        'nota':      turno.nota or '',
    }


def _nome(sigla: str) -> str:
    f = db.session.get(Funcionario, sigla) if sigla else None
    return f.nome if f else (sigla or '')


def _serialize_troca(t: TrocaPedido, include_candidaturas=False) -> dict:
    """Render a TrocaPedido + the underlying turnos into a JSON-safe dict."""
    def turno_info(tid):
        if not tid:
            return None
        turno = db.session.get(schedule_turno(), tid)
        return _turno_info(turno) if turno else {'id': tid, 'missing': True}

    tipo = getattr(t, 'tipo', 'direto') or 'direto'
    is_aberto = tipo.startswith('aberto_')

    out = {
        'id':                  t.id,
        'criado_em':           iso_utc(t.criado_em),
        'atualizado_em':       iso_utc(t.atualizado_em),
        'de_sigla':            t.de_sigla,
        'de_nome':             _nome(t.de_sigla),
        'para_sigla':          t.para_sigla,
        'para_nome':           _nome(t.para_sigla) if t.para_sigla else '',
        'turno_origem':        turno_info(t.turno_origem_id),
        'turno_destino':       turno_info(t.turno_destino_id),
        'is_doacao':           t.turno_destino_id is None,
        'is_cobertura':        t.turno_destino_id is None,   # alias for back-compat
        'motivo':              t.motivo or '',
        'estado':              t.estado,
        'estado_label':        TROCA_STATE_LABELS.get(t.estado, t.estado),
        'resposta_colega':     t.resposta_colega or '',
        'resposta_supervisor': t.resposta_supervisor or '',
        'decisao_em':          iso_utc(t.decisao_em),
        'decisao_em_texto':    _donation_local_time(t.decisao_em),
        'tipo':                tipo,
        'tipo_label':          TROCA_TIPO_LABELS.get(tipo, tipo),
        'is_aberto':           is_aberto,
        'is_aberto_troca':     tipo == 'aberto_troca',
        'is_aberto_doacao':    tipo == 'aberto_doacao',
    }
    if is_donation(t) and t.estado == 'pendente_supervisor':
        out['estado_label'] = 'A aguardar decisão da administração'

    if (include_candidaturas or is_donation(t)) and is_aberto:
        cands = (TrocaCandidatura.query
                 .filter_by(troca_id=t.id)
                 .order_by(TrocaCandidatura.criado_em.asc()).all())
        out['candidaturas'] = [_serialize_candidatura(c, t) for c in cands]
        out['n_candidaturas'] = sum(1 for c in cands if c.status == 'pendente')
        out['n_vetados'] = sum(1 for c in cands if c.status == 'vetado')
        if is_donation(t) and not is_supervisor() and current_user_sigla() != t.de_sigla:
            out['candidaturas'] = [c for c in out['candidaturas'] if c['sigla'] == current_user_sigla()]

    return out

_CANDIDATURA_LABELS = {
    'pendente':  'Candidatura enviada',
    'aceite':    'Escolhido/a',
    'rejeitada': 'Outra candidatura escolhida',
    'retirada':  'Candidatura retirada',
    'vetado':    'Recusado/a pela supervisão',
    'negada':    'Doação recusada pela administração',
    'cancelada': 'Doação cancelada',
    'expirada':  'Doação expirada',
}


def _serialize_candidatura(c: TrocaCandidatura, pedido=None) -> dict:
    offered = None
    if c.turno_oferecido_id:
        t = db.session.get(schedule_turno(), c.turno_oferecido_id)
        if t:
            offered = _turno_info(t)
    out = {
        'id':                 c.id,
        'troca_id':           c.troca_id,
        'sigla':              c.sigla,
        'nome':               _nome(c.sigla),
        'comentario':         c.comentario or '',
        'criado_em':          iso_utc(c.criado_em),
        'criado_em_texto':    _donation_local_time(c.criado_em),
        'status':             c.status,
        'status_label':       _CANDIDATURA_LABELS.get(c.status, c.status),
        'is_vetado':          c.status == 'vetado',
        'turno_oferecido':    offered,
    }
    pedido = pedido or db.session.get(TrocaPedido, c.troca_id)
    out.update(fatal=[], conflitos=[], avisos=[])
    if pedido and is_donation(pedido):
        if c.status == 'pendente':
            out['status_label'] = 'A aguardar decisão da administração'
        if c.status in ACTIVE_CANDIDATES and pedido.estado in DONATION_STATES:
            out['fatal'], out['conflitos'], out['avisos'] = _donation_candidate_problems(pedido, c.sigla)
    return out


def _donation_local_time(dt):
    from helpers.time_helpers import as_utc
    from zoneinfo import ZoneInfo
    return as_utc(dt).astimezone(ZoneInfo('Europe/Lisbon')).strftime('%d/%m/%Y %H:%M') if dt else ''


def _donation_identity_problems(p):
    published = db.session.get(PublishedTurno, p.turno_origem_id)
    draft = db.session.get(Turno, p.turno_origem_id)
    if not published or not draft:
        return ['O turno de origem já não existe.']
    if published.funcionario != p.de_sigla or draft.funcionario != p.de_sigla:
        return ['O turno de origem mudou de dono.']
    actual = shift_identity(published)
    if not actual['date']:
        return ['A data publicada do turno é inválida.']
    try:
        expected = json.loads(p.origem_snapshot_json) if p.origem_snapshot_json else actual
    except (ValueError, TypeError):
        expected = None
    if actual != expected:
        return ['O turno publicado foi alterado. Publicar um novo pedido.']
    fields = ('semana', 'dia', 'loja', 'horario', 'funcionario', 'nota')
    if any(getattr(draft, k) != getattr(published, k) for k in fields):
        return ['O turno tem alterações por publicar. Publicar e rever o pedido antes de aprovar.']
    week = SemanaConfig.query.filter_by(codigo=published.semana).first()
    try:
        days = json.loads(week.dias_json) if week else []
        draft_date = next((d.get('date') for d in days if d.get('label') == draft.dia), '')
    except (ValueError, TypeError):
        draft_date = ''
    if draft_date != actual['date']:
        return ['A data do turno foi alterada. Publicar um novo pedido.']
    if shift_started(published):
        return ['Esse turno já começou.']
    if not db.session.get(Funcionario, p.de_sigla):
        return ['O autor do pedido já não pertence à equipa.']
    return []


def _donation_candidate_problems(p, sigla):
    fatal = _donation_identity_problems(p)
    person = db.session.get(Funcionario, sigla) if sigla else None
    if not person or sigla in (p.de_sigla, ADMIN_SIGLA):
        fatal.append('A pessoa candidata já não é elegível para este turno.')
    if fatal:
        return fatal, [], []
    origin = db.session.get(PublishedTurno, p.turno_origem_id)
    conflicts = list(dict.fromkeys(
        _swap_conflicts(p.de_sigla, sigla, origin, draft=True) +
        _swap_conflicts(p.de_sigla, sigla, origin, authorised=True)))
    if 'troca' not in (origin.nota or '').lower():
        for model in (Turno, PublishedTurno):
            for other in model.query.filter_by(semana=origin.semana, dia=origin.dia, funcionario=sigla).all():
                if other.loja != origin.loja and not _shifts_overlap(other.horario, origin.horario):
                    conflicts.append(_other_store_msg(sigla, other))
        conflicts = list(dict.fromkeys(conflicts))
    # Mirror existing assignment warning thresholds for the prospective state.
    warnings = []
    availability = Disponibilidade.query.filter_by(
        semana=origin.semana, funcionario=sigla, dia=origin.dia).first()
    if availability and availability.tipo == 'indisponivel':
        warnings.append(f'{sigla} marcou-se indisponível neste dia.')
    for shifts, deliveries in ((Turno, Descarga), (PublishedTurno, PublishedDescarga)):
        working = shifts.query.filter_by(semana=origin.semana, funcionario=sigla).all()
        total = sum(_shift_hours(t.horario) for t in working) + _shift_hours(origin.horario)
        total += sum(descarga_hours(d.horario) for d in deliveries.query.filter_by(
            semana=origin.semana, funcionario=sigla).all())
        if person.tipo_contrato == 'contrato' and person.horas_contrato and total - person.horas_contrato > 2:
            warnings.append(f'{sigla} fica com {total:.1f}h (contrato {person.horas_contrato}h).')
        n_days = len({t.dia for t in working} | {origin.dia})
        if n_days >= 6:
            warnings.append(f'{sigla} fica com {n_days} dias de trabalho nesta semana.')
    return fatal, conflicts, list(dict.fromkeys(warnings))


def _turnos_em_pedido_aberto(turno_ids, ignore_troca_id=None) -> set:
    """Which of these turnos already sit in an open request — as the shift being
    given away, as the one asked for back, or offered in a live candidatura.

    A shift can only be promised once. Without this, the same shift could go to
    two colleagues at the same time (or be in the central and in a direct
    request): both could accept, the supervisor would approve the first, and the
    second would be stuck as "o turno mudou de dono" with nobody told why.
    `ignore_troca_id` leaves one request out — the one being acted on.
    """
    ids = {i for i in turno_ids if i}
    if not ids:
        return set()
    q = TrocaPedido.query.filter(
        TrocaPedido.estado.in_(list(TROCA_STATES_OPEN)),
        (TrocaPedido.turno_origem_id.in_(ids) | TrocaPedido.turno_destino_id.in_(ids)),
    )
    if ignore_troca_id:
        q = q.filter(TrocaPedido.id != ignore_troca_id)
    out = set()
    for p in q.all():
        out |= {p.turno_origem_id, p.turno_destino_id} & ids
    cq = (db.session.query(TrocaCandidatura.turno_oferecido_id)
          .join(TrocaPedido, TrocaPedido.id == TrocaCandidatura.troca_id)
          .filter(TrocaCandidatura.turno_oferecido_id.in_(ids),
                  TrocaCandidatura.status.in_(['pendente', 'aceite']),
                  TrocaPedido.estado.in_(list(TROCA_STATES_OPEN))))
    if ignore_troca_id:
        cq = cq.filter(TrocaPedido.id != ignore_troca_id)
    out |= {tid for (tid,) in cq}
    return out


def _troca_turnos_erro(origem, destino=None, ignore_troca_id=None):
    """(message, status) when these shifts can't go into a request, else None:
    a shift that has already been worked, or one promised elsewhere."""
    if _turno_info(origem)['passado']:
        return 'Esse turno já passou.', 400
    if destino is not None and _turno_info(destino)['passado']:
        return 'O turno dado em troca já passou.', 400
    ocupados = _turnos_em_pedido_aberto(
        [origem.id, destino.id if destino is not None else None], ignore_troca_id)
    if origem.id in ocupados:
        return ('Este turno já está num pedido em curso. Para o pedir a outra '
                'pessoa, cancelar primeiro esse pedido na página Trocas.'), 409
    if destino is not None and destino.id in ocupados:
        return 'O turno dado em troca já está noutro pedido em curso.', 409
    return None


def _swap_conflicts(de_sigla: str, para_sigla: str, origem, destino=None, authorised=False, draft=False) -> list:
    """What would break if `origem` (de_sigla's) went to para_sigla and — for a
    troca — `destino` (para_sigla's) came back to de_sigla.

    Every stage asks this same question: the request, the colleague's answer,
    a candidatura, the author's pick and the supervisor's approval. Before, only
    the approval did — so a doação to someone already working those hours
    travelled through two people and failed at the last step, with the
    supervisor holding a request nobody could approve. Now it is refused at the
    first step that can see the problem.

    A descarga occupies its window like a shift does (see Descarga), so it
    counts as an overlap too — the same rule /api/turno/add enforces.
    """
    problems = []

    def check(sigla, turno, ignore_id):
        if _has_turno_conflict(sigla, turno.dia, turno.horario, turno.semana,
                               ignore_turno_id=ignore_id, model=PublishedTurno if authorised else Turno if draft else None):
            problems.append(f'{sigla} ficaria com dois turnos sobrepostos '
                            f'em {turno.dia} ({turno.horario}).')
        else:
            msg = _descarga_overlap_msg(sigla, turno.dia, turno.semana, turno.horario,
                                        model=PublishedDescarga if authorised else Descarga if draft else None)
            if msg:
                problems.append(msg)

    check(para_sigla, origem, destino.id if destino else None)
    if destino:
        check(de_sigla, destino, origem.id)
    return problems


def _troca_acoes_para(sigla: str) -> int:
    """Pedidos that the given user needs to act on right now.

    For employees this counts:
      * Direct pedidos addressed to them (estado=pendente_colega, para_sigla=me)
      * Open pedidos they authored that received at least one candidatura
        (so the author knows there's someone to pick).

    For supervisors: anything pendente_supervisor.
    """
    if sigla == ADMIN_SIGLA or is_supervisor():
        return TrocaPedido.query.filter_by(estado='pendente_supervisor').count()

    direct_n = TrocaPedido.query.filter_by(
        estado='pendente_colega', para_sigla=sigla).count()

    # Open pedidos I wrote that have ≥1 pending candidatura need my attention
    my_open = TrocaPedido.query.filter_by(
        estado='aberto', de_sigla=sigla, tipo='aberto_troca').all()
    author_n = 0
    for p in my_open:
        if TrocaCandidatura.query.filter_by(
                troca_id=p.id, status='pendente').count() > 0:
            author_n += 1

    return direct_n + author_n


# ── "Novidades": what happened to my requests since I last looked ─────────────
# The badge only counted things to DO. An author whose swap was approved or
# refused, or a candidate who was not picked, got no signal at all — the only
# way to find out was to open /trocas and read the history.
#
# "Since I last looked" is when this person last opened /trocas on this device,
# kept in a small cookie (sigla|UTC timestamp) rather than a new DB column. It
# only feeds this count: forging it can hide or show one's OWN news, nothing
# else. Without it (new device), the last 7 days count.
TROCAS_VISTAS_COOKIE = 'amorino_trocas_vistas'


def _trocas_vistas_em(sigla: str) -> datetime:
    """When `sigla` last opened /trocas on this device (naive UTC, like the
    stored timestamps). /trocas sets g.trocas_vistas_agora so the page that
    SHOWS the news doesn't also count it in its own badge."""
    if getattr(g, 'trocas_vistas_agora', None):
        return g.trocas_vistas_agora
    who, _, when = (request.cookies.get(TROCAS_VISTAS_COOKIE) or '').partition('|')
    if who == sigla:
        try:
            return datetime.fromisoformat(when)
        except ValueError:
            pass
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)


def _trocas_novidades(sigla: str, desde: datetime) -> set:
    """Ids of requests with news for `sigla` since `desde`: outcomes that
    someone ELSE caused. One's own actions (creating, cancelling, accepting,
    picking a candidate) are never news."""
    out = set()
    for p in (TrocaPedido.query
              .filter(TrocaPedido.atualizado_em > desde,
                      (TrocaPedido.de_sigla == sigla) | (TrocaPedido.para_sigla == sigla))
              .all()):
        central = (p.tipo or '').startswith('aberto_')
        if p.de_sigla == sigla:
            news = (p.estado in ('aprovado', 'negado', 'recusado_colega', 'expirado')
                    # the colleague accepted (a central post goes to the
                    # supervisor by the author's own pick)
                    or (p.estado == 'pendente_supervisor' and (not central or is_donation(p)))
                    # the supervisor refused the candidate I picked
                    or (p.estado == 'aberto' and TrocaCandidatura.query.filter_by(
                        troca_id=p.id, status='vetado').first() is not None))
        else:
            news = (p.estado in ('aprovado', 'negado', 'cancelado')
                    # I was picked from the central
                    or (p.estado == 'pendente_supervisor' and central))
        if news:
            out.add(p.id)
    # Candidaturas that ended without me (another was picked, or refused).
    for (tid,) in (db.session.query(TrocaCandidatura.troca_id)
                   .join(TrocaPedido, TrocaPedido.id == TrocaCandidatura.troca_id)
                   .filter(TrocaCandidatura.sigla == sigla,
                           TrocaCandidatura.status.in_(['rejeitada', 'vetado', 'negada', 'cancelada', 'expirada']),
                           TrocaPedido.atualizado_em > desde)):
        out.add(tid)
    return out


def _troca_pending_for_user(sigla: str) -> int:
    """The Trocas badge: things to decide + news since the last visit."""
    n = _troca_acoes_para(sigla)
    if sigla != ADMIN_SIGLA and not is_supervisor():
        n += len(_trocas_novidades(sigla, _trocas_vistas_em(sigla)))
    return n


# Make the pending count available to every template (for the topbar badge)
@app.context_processor
def _inject_troca_count():
    sigla = current_user_sigla()
    if not sigla:
        return {'trocas_pendentes': 0, 'trocas_acoes': 0, 'trocas_novidades': 0}
    try:
        acoes = _troca_acoes_para(sigla)
        novas = (0 if sigla == ADMIN_SIGLA or is_supervisor()
                 else len(_trocas_novidades(sigla, _trocas_vistas_em(sigla))))
        return {'trocas_pendentes': acoes + novas,
                'trocas_acoes': acoes, 'trocas_novidades': novas}
    except Exception:
        return {'trocas_pendentes': 0, 'trocas_acoes': 0, 'trocas_novidades': 0}


@app.route('/trocas')
@login_required
def trocas_page():
    """Per-user trocas inbox.

    Renders three buckets:
      1. 'A decidir'    — pedidos onde sou o destinatário e estão pendentes
      2. 'Em curso'     — pedidos que eu criei e ainda estão abertos
      3. 'Histórico'    — fechados (aprovados / negados / cancelados / recusados),
                          mais recentes primeiro
    Supervisor sees pendente_supervisor as 'A decidir' instead.

    Every party keeps sight of a request for as long as it involves them. The
    colleague who accepted used to lose it the moment they did (it was neither
    "to decide" nor "sent by me" any more) until the supervisor decided — and a
    candidate who was not picked, or was refused by the supervisor, never
    learnt it at all.
    """
    # Retire requests whose shift day already passed, so they land in Histórico
    # ("Expirou") instead of showing as actionable.
    _expire_stale_trocas()
    sigla = current_user_sigla()
    sup = is_supervisor()
    # News since the last visit get a "Novo" marker on this page, and this
    # visit becomes the new mark — including for this page's own nav badge.
    novidades = set()
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    if not sup:
        novidades = _trocas_novidades(sigla, _trocas_vistas_em(sigla))
        g.trocas_vistas_agora = agora
    cand_historico = []
    if sup:
        a_decidir = (TrocaPedido.query
                     .filter_by(estado='pendente_supervisor')
                     .order_by(TrocaPedido.criado_em.asc()).all())
        em_curso  = (TrocaPedido.query
                     .filter(TrocaPedido.estado == 'pendente_colega')
                     .order_by(TrocaPedido.criado_em.desc()).all())
        historico = (TrocaPedido.query
                     .filter(TrocaPedido.estado.in_(list(TROCA_STATES_CLOSED)))
                     .order_by(TrocaPedido.atualizado_em.desc()).limit(50).all())
    else:
        a_decidir = (TrocaPedido.query
                     .filter_by(estado='pendente_colega', para_sigla=sigla)
                     .order_by(TrocaPedido.criado_em.asc()).all())
        # Mine: what I sent and is still open (my central posts included, with
        # their candidaturas) + what I accepted or was chosen for and now waits
        # on the supervisor.
        em_curso  = (TrocaPedido.query
                     .filter(((TrocaPedido.de_sigla == sigla) |
                              ((TrocaPedido.para_sigla == sigla) &
                               (TrocaPedido.estado == 'pendente_supervisor'))),
                             TrocaPedido.estado.in_(list(TROCA_STATES_OPEN)))
                     .order_by(TrocaPedido.criado_em.desc()).all())
        historico = (TrocaPedido.query
                     .filter(((TrocaPedido.de_sigla == sigla) | (TrocaPedido.para_sigla == sigla)),
                             TrocaPedido.estado.in_(list(TROCA_STATES_CLOSED)))
                     .order_by(TrocaPedido.atualizado_em.desc()).limit(30).all())
        # Candidaturas that ended without me: another one was picked, or the
        # supervisor refused me. The pedido itself carries on (or closes) under
        # someone else's name, so it would never reach my history on its own.
        for c in (TrocaCandidatura.query
                  .filter(TrocaCandidatura.sigla == sigla,
                          TrocaCandidatura.status.in_(['rejeitada', 'vetado', 'negada', 'cancelada', 'expirada']))
                  .order_by(TrocaCandidatura.criado_em.desc()).limit(10).all()):
            p = db.session.get(TrocaPedido, c.troca_id)
            if p and p.para_sigla != sigla:
                cand_historico.append({'troca': _serialize_troca(p),
                                       'candidatura': _serialize_candidatura(c)})

    # The central: every open post (the supervisor observes all of them); an
    # employee's own posts are listed under "Os meus pedidos" instead.
    central = [r for r in (TrocaPedido.query.filter(_central_filter())
                           .order_by(TrocaPedido.criado_em.desc()).all())
               if sup or r.de_sigla != sigla]
    central_ser = [_central_view(r, sigla, sup) for r in central]

    a_decidir_ser = []
    for t in a_decidir:
        ser = _serialize_troca(t)
        # The approval gate's own pre-flight, shown before the click — to the
        # supervisor before approving (as the dashboard already did) and to the
        # colleague before accepting something that could never be approved.
        ser['fatal'], ser['conflitos'] = _troca_approval_problems(t)
        a_decidir_ser.append(ser)

    def view(ser):
        ser['estado_label'] = _estado_visto_por(ser, sigla, sup)
        return ser

    # A request that ended without a swap (refused, expired, cancelled) can be
    # tried again with someone else, straight from its history card — as long
    # as the shift is still the author's, still ahead, and not already asked.
    hist_ser = [view(_serialize_troca(t)) for t in historico]
    origens = [h['turno_origem'] for h in hist_ser
               if h['de_sigla'] == sigla and h['turno_origem']
               and not h['turno_origem'].get('missing')]
    ocupados = _turnos_em_pedido_aberto([o['id'] for o in origens]) if not sup else set()
    for h in hist_ser:
        o = h['turno_origem']
        h['pode_repetir'] = bool(
            not sup and h['de_sigla'] == sigla and h['estado'] != 'aprovado'
            and o and not o.get('missing') and o['sigla'] == sigla
            and not o['passado'] and o['id'] not in ocupados)

    resp = make_response(render_template('trocas.html',
        a_decidir=[view(s) for s in a_decidir_ser],
        em_curso=[view(_serialize_troca(t, include_candidaturas=(t.estado == 'aberto')))
                  for t in em_curso],
        historico=hist_ser,
        cand_historico=cand_historico,
        central=central_ser,
        novidades=novidades,
        is_admin_view=sup,
        my_sigla=sigla,
    ))
    if not sup:
        resp.set_cookie(TROCAS_VISTAS_COOKIE, f'{sigla}|{agora.isoformat()}',
                        max_age=400 * 86400, path='/', httponly=True,
                        samesite='Lax', secure=_force_https)
    return resp


def _primeiro_nome(nome: str) -> str:
    return (nome or '').split(' ')[0]


def _estado_visto_por(ser: dict, sigla: str, sup: bool) -> str:
    """The state, worded for the person reading it.

    "À espera do/a colega" is right for the author and the supervisor, but the
    colleague reading it on their own card IS that colleague."""
    e = ser['estado']
    if e == 'pendente_colega' and not sup:
        if ser['para_sigla'] == sigla:
            return 'Resposta pendente'
        return f"À espera de {_primeiro_nome(ser['para_nome'])}"
    if e == 'recusado_colega' and not sup and ser['de_sigla'] == sigla:
        return f"Recusado por {_primeiro_nome(ser['para_nome'])}"
    if e == 'aberto':
        return 'Na central'
    if e == 'pendente_supervisor' and ser['is_aberto_doacao']:
        return 'A aguardar decisão da administração'
    return TROCA_STATE_LABELS.get(e, e)


def _central_filter():
    return ((TrocaPedido.estado == 'aberto') |
            ((TrocaPedido.tipo == 'aberto_doacao') & (TrocaPedido.estado == 'pendente_supervisor')))


def _central_view(r: TrocaPedido, sigla: str, sup: bool) -> dict:
    """A central post, annotated for the person looking at it."""
    ser = _serialize_troca(r, include_candidaturas=True)
    ser['is_mine'] = (sigla == r.de_sigla)
    ser['viewer_has_conflict'] = False
    ser['viewer_candidated'] = False
    ser['viewer_vetado'] = False
    if sigla and not sup and not ser['is_mine'] and ser['turno_origem'] \
            and not ser['turno_origem'].get('missing'):
        tor = ser['turno_origem']
        ser['viewer_has_conflict'] = _has_turno_conflict(
            sigla, tor['dia'], tor['horario'], tor['semana'])
        mine = [c for c in ser.get('candidaturas', []) if c['sigla'] == sigla]
        ser['viewer_candidated'] = any(c['status'] in ('pendente', 'aceite') for c in mine)
        # Someone the supervisor already refused for this post cannot apply
        # again — say so, instead of offering a button that can only fail.
        ser['viewer_vetado'] = any(c['status'] == 'vetado' for c in mine)
    if not sup and not ser['is_mine']:
        # Who applied, and what they wrote, is for the author and the supervisor.
        # Everyone else sees only their own candidatura (the count stays).
        ser['candidaturas'] = [c for c in ser.get('candidaturas', [])
                               if c['sigla'] == sigla]
    return ser


@app.route('/api/trocas/contagem')
@login_required
def api_trocas_contagem():
    """Tiny endpoint for the topbar badge to poll."""
    _expire_stale_trocas()
    sigla = current_user_sigla()
    return jsonify({'pendentes': _troca_pending_for_user(sigla) if sigla else 0})


@app.route('/api/trocas/turnos-do-colega')
@login_required
def api_turnos_colega():
    """Return a person's turnos for swap selection.

    Used by both the direct 'Pedir troca' modal and the central candidatura
    modal. Returns shifts across the current AND future weeks (never past weeks
    — you can't swap a shift that already happened), grouped by week so the UI
    can let the user pick across weeks. A swap between different weeks is fully
    supported by the backend: each Turno carries its own `semana`, and approval
    only reassigns ownership.

    Query params:
      sigla       (required) — whose shifts to list
      semana      (optional) — if given, ONLY that week (legacy single-week mode,
                               kept so older callers still work)
      from_semana (optional) — sort this week first (the week of the shift
                               being traded)
      troca       (optional) — id of the shift being traded away; each listed
                               shift then carries `conflitos` for that swap

    Every shift carries `quando` ('Qui 1 out'), `passado`, and `em_pedido`
    (already promised in an open request). Shifts whose day has passed are left
    out in multi-week mode.

    Response:
      {
        ok: true,
        turnos: [...],            # flat list (legacy; all returned shifts)
        semanas: [                # grouped + chronological (current/next first)
          {codigo, label, start_date, is_current, is_this_week, turnos: [...]},
          ...
        ]
      }
    """
    sigla  = (request.args.get('sigla') or '').strip().upper()[:10]
    semana = (request.args.get('semana') or '').strip()[:30] or None
    from_semana = (request.args.get('from_semana') or '').strip()[:30] or None
    if not sigla:
        return jsonify({'ok': False, 'erro': 'Sigla obrigatória'}), 400
    if not db.session.get(Funcionario, sigla):
        return jsonify({'ok': False, 'erro': 'Sigla não encontrada'}), 404

    # Optional: the shift being traded away (`troca`=turno id). With it, each
    # listed shift says whether swapping it for that one would leave either
    # person with two overlapping shifts — so the picker can say so up front
    # instead of the request failing at the supervisor's desk.
    troca_turno = None
    try:
        troca_id = int(request.args.get('troca') or 0)
    except ValueError:
        troca_id = 0
    if troca_id:
        troca_turno = db.session.get(schedule_turno(), troca_id)
        if troca_turno and troca_turno.funcionario == sigla:
            troca_turno = None      # a shift can't be swapped with its own owner

    q = schedule_turno().query.filter_by(funcionario=sigla)
    if semana:
        q = q.filter_by(semana=semana)   # legacy single-week mode
    rows = q.all()
    em_pedido = _turnos_em_pedido_aberto([t.id for t in rows])

    def _turno_dict(t):
        d = _turno_info(t)
        d['em_pedido'] = t.id in em_pedido
        if troca_turno is not None:
            d['conflitos'] = _swap_conflicts(troca_turno.funcionario, sigla,
                                             troca_turno, t)
        return d

    # Build week metadata (label + start date) once, for ordering and display.
    week_meta = {}   # codigo -> {'label', 'start'}
    for sw in schedule_semana().query.all():
        week_meta[sw.codigo] = {
            'label': sw.label or sw.codigo,
            'start': semana_start_date(sw) or '',
        }

    # Anchor = the week of the shift being traded (so same-week options come
    # first), else the week we are in. It used to fall back to the ACTIVE week,
    # which is often next week's draft — and then this week's shifts, dropped
    # as "before the anchor", could not be offered at all.
    today = date.today()
    today_iso = today.isoformat()
    this_monday = (today - timedelta(days=today.weekday())).isoformat()
    anchor_start = (week_meta[from_semana]['start']
                    if from_semana in week_meta and week_meta[from_semana]['start']
                    else this_monday)

    # Group turnos by week.
    by_week = {}
    for t in rows:
        by_week.setdefault(t.semana, []).append(t)

    semanas_out = []
    flat_out = []
    for codigo, turnos in by_week.items():
        meta = week_meta.get(codigo, {'label': codigo, 'start': ''})
        wstart = meta['start']
        # Sort shifts within the week by weekday index then horario.
        turnos_sorted = sorted(
            turnos,
            key=lambda t: (dia_label_to_idx(t.dia) if dia_label_to_idx(t.dia) is not None else 9,
                           t.horario or ''))
        turno_dicts = [_turno_dict(t) for t in turnos_sorted]
        if not semana:
            # Only what can still be swapped: a shift whose day is behind us has
            # been worked (or not) already. This went week by week before, so
            # on a Thursday Monday's shift was still on offer.
            turno_dicts = [d for d in turno_dicts
                           if not d['passado'] and (d['data'] or wstart >= this_monday)]
            if not turno_dicts:
                continue
        flat_out.extend(turno_dicts)
        semanas_out.append({
            'codigo':       codigo,
            'label':        meta['label'],
            'start_date':   wstart,
            'is_current':   (wstart == anchor_start),
            'is_this_week': (wstart == this_monday),
            'turnos':       turno_dicts,
        })

    # Order weeks chronologically, but float the anchor (current) week to the top
    # so the most common case — swapping within the same week — needs no scrolling.
    semanas_out.sort(key=lambda w: (not w['is_current'], w['start_date'] or w['codigo']))

    return jsonify({'ok': True, 'turnos': flat_out, 'semanas': semanas_out})


@app.route('/api/trocas/colegas')
@login_required
def api_troca_colegas():
    """Who to ask for a shift: every colleague, marked when they are already
    working (or on a descarga) during those hours that day.

    Query: turno=<id> — one of the caller's own shifts (the supervisor may ask
    for any). Returns [{sigla, nome, ocupado}] with `ocupado` = '' when free,
    else what they are doing then ('10:00-18:30 · Carmelitas'). Free people
    first, then by name.

    Nothing here is new information — every employee already sees the whole
    schedule on /escala. The difference is seeing it at the moment of choosing,
    instead of a doação to someone who is busy failing at the supervisor's desk.
    """
    sigla = current_user_sigla()
    try:
        tid = int(request.args.get('turno') or 0)
    except ValueError:
        tid = 0
    turno = db.session.get(schedule_turno(), tid) if tid else None
    if not turno:
        return jsonify({'ok': False, 'erro': 'Turno não encontrado.'}), 404
    if turno.funcionario != sigla and not is_supervisor():
        return jsonify({'ok': False, 'erro': 'Esse turno pertence a outra pessoa.'}), 403

    ocupado = {}
    for t in schedule_turno().query.filter_by(semana=turno.semana, dia=turno.dia).all():
        if t.id != turno.id and _shifts_overlap(t.horario, turno.horario):
            ocupado.setdefault(t.funcionario,
                               f'{t.horario} · {_LOJA_NOME.get(t.loja, t.loja)}')
    for dc in schedule_descarga().query.filter_by(semana=turno.semana, dia=turno.dia).all():
        if _shifts_overlap(dc.horario, turno.horario):
            ocupado.setdefault(dc.funcionario, f'descarga {dc.horario}')

    colegas = [{'sigla': f.sigla, 'nome': f.nome, 'ocupado': ocupado.get(f.sigla, '')}
               for f in Funcionario.query.all() if f.sigla != turno.funcionario]
    colegas.sort(key=lambda c: (bool(c['ocupado']), c['nome']))
    return jsonify({'ok': True, 'colegas': colegas})


@app.route('/api/trocas', methods=['POST'])
@login_required
@troca_transaction
def api_troca_criar():
    """Create a swap request.

    Body: {turno_origem_id, para_sigla, [turno_destino_id], [motivo]}

    Rules:
      * turno_origem_id MUST belong to the caller.
      * para_sigla MUST exist and be different from caller.
      * If turno_destino_id is present, MUST belong to para_sigla.
      * No duplicate open requests for the same (origem, destino) pair.
    """
    sigla = current_user_sigla()
    if not sigla or sigla == ADMIN_SIGLA:
        return jsonify({'ok': False, 'erro': 'Apenas funcionários podem pedir trocas.'}), 403

    data = request.get_json(silent=True) or {}
    try:
        origem_id = int(data.get('turno_origem_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'erro': 'turno_origem_id inválido'}), 400
    destino_id_raw = data.get('turno_destino_id')
    destino_id = None
    if destino_id_raw not in (None, '', 0):
        try:
            destino_id = int(destino_id_raw)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'erro': 'turno_destino_id inválido'}), 400

    para_sigla = (data.get('para_sigla') or '').strip().upper()[:10]
    motivo = (data.get('motivo') or '').strip()[:300]

    if destino_id is None:
        # Legacy direct-donation clients publish an open donation. A nomination
        # is neither consent nor authority to select the eventual recipient.
        return _publish_open_request(sigla, origem_id, 'aberto_doacao', motivo)

    if not para_sigla or para_sigla == sigla:
        return jsonify({'ok': False, 'erro': 'Escolher um/a colega diferente.'}), 400
    para = db.session.get(Funcionario, para_sigla)
    if not para:
        return jsonify({'ok': False, 'erro': 'Colega não encontrado/a.'}), 404

    origem = db.session.get(schedule_turno(), origem_id)
    if not origem:
        return jsonify({'ok': False, 'erro': 'Turno de origem não encontrado.'}), 404
    if origem.funcionario != sigla:
        return jsonify({'ok': False, 'erro': 'Esse turno pertence a outra pessoa.'}), 403

    destino = None
    if destino_id is not None:
        destino = db.session.get(schedule_turno(), destino_id)
        if not destino:
            return jsonify({'ok': False, 'erro': 'O turno do/a colega não foi encontrado.'}), 404
        if destino.funcionario != para_sigla:
            return jsonify({'ok': False, 'erro': 'Esse turno não pertence ao/a colega indicado/a.'}), 400

    erro = _troca_turnos_erro(origem, destino)
    if erro:
        return jsonify({'ok': False, 'erro': erro[0]}), erro[1]
    problems = _swap_conflicts(sigla, para_sigla, origem, destino)
    if problems:
        return jsonify({'ok': False, 'conflito': True,
                        'erro': problems[0] + ' Escolher outra pessoa ou outro turno.'}), 409

    p = TrocaPedido(
        de_sigla=sigla,
        para_sigla=para_sigla,
        turno_origem_id=origem_id,
        turno_destino_id=destino_id,
        motivo=motivo,
        estado='pendente_colega',
    )
    db.session.add(p)
    db.session.flush()
    transaction_audit('troca_criar', target=str(p.id),
          detail=f'{sigla}->{para_sigla} origem={origem_id} destino={destino_id} doacao={destino_id is None}')
    log.info('Troca %d criada por %s para %s', p.id, sigla, para_sigla)
    return jsonify({'ok': True, 'troca': _serialize_troca(p)})


@app.route('/api/trocas/<int:tid>/responder', methods=['POST'])
@login_required
@troca_transaction
def api_troca_responder(tid):
    """The colleague (para_sigla) accepts or declines."""
    _expire_stale_trocas()   # a shift that has passed can't be accepted
    sigla = current_user_sigla()
    p = db.session.get(TrocaPedido, tid)
    if not p:
        return jsonify({'ok': False, 'erro': 'Pedido não encontrado.'}), 404
    if is_donation(p):
        return jsonify(ok=False, erro='Candidatar-se à doação na Central de Trocas.'), 409
    if p.para_sigla != sigla:
        return jsonify({'ok': False, 'erro': 'Este pedido é para outra pessoa.'}), 403
    if p.estado != 'pendente_colega':
        return jsonify({'ok': False, 'erro': 'Este pedido já não está à espera de resposta '
                        f'({TROCA_STATE_LABELS.get(p.estado, p.estado).lower()}).'}), 409

    data = request.get_json(silent=True) or {}
    aceitar = bool(data.get('aceitar', False))
    nota = (data.get('nota') or '').strip()[:300]

    if aceitar:
        # Accepting something the supervisor could never approve only moves the
        # dead end one step further along — stop here, where it can be fixed.
        fatal, conflitos = _troca_approval_problems(p)
        if fatal or conflitos:
            return jsonify({'ok': False, 'conflito': bool(conflitos),
                            'erro': (fatal or conflitos)[0]}), 409

    p.resposta_colega = nota
    p.decisao_em = datetime.now(timezone.utc)
    if aceitar:
        p.estado = 'pendente_supervisor'
        transaction_audit('troca_aceitar_colega', target=str(p.id), detail=f'{sigla}')
    else:
        p.estado = 'recusado_colega'
        transaction_audit('troca_recusar_colega', target=str(p.id), detail=f'{sigla}')
    return jsonify({'ok': True, 'troca': _serialize_troca(p)})


@app.route('/api/trocas/<int:tid>/cancelar', methods=['POST'])
@login_required
@troca_transaction
def api_troca_cancelar(tid):
    """Author cancels their own pedido (only while still open)."""
    _expire_stale_trocas()
    sigla = current_user_sigla()
    p = db.session.get(TrocaPedido, tid)
    if not p:
        return jsonify({'ok': False, 'erro': 'Pedido não encontrado.'}), 404
    if p.de_sigla != sigla:
        return jsonify({'ok': False, 'erro': 'Só quem criou pode cancelar.'}), 403
    if p.estado not in TROCA_STATES_OPEN:
        return jsonify({'ok': False, 'erro': 'Pedido já está fechado.'}), 409
    before_state = p.estado
    p.estado = 'cancelado'
    p.decisao_em = datetime.now(timezone.utc)
    if is_donation(p):
        _close_donation_candidates(p, 'cancelada')
    transaction_audit('troca_cancelar', target=str(p.id), detail={
        'antes': before_state, 'depois': p.estado, 'turno': p.turno_origem_id})
    return jsonify({'ok': True, 'troca': _serialize_troca(p)})


def _troca_approval_problems(p):
    """Pre-flight checks for approving a TrocaPedido.

    Returns (fatal, conflitos):
      * fatal     — drift that makes approval impossible (a turno was deleted
                    or changed owner since the request was made). The request
                    can only be refused.
      * conflitos — overlap clashes that will block the approval until the
                    clashing shift is moved (or the request refused).

    Shared by api_troca_decidir (the authoritative gate) and the supervisor
    dashboard, so the supervisor sees problems BEFORE pressing Aprovar instead
    of discovering them via a 409 after the click.
    """
    if is_donation(p):
        return _donation_identity_problems(p), []
    fatal, conflitos = [], []
    origem  = db.session.get(schedule_turno(), p.turno_origem_id)
    destino = db.session.get(schedule_turno(), p.turno_destino_id) if p.turno_destino_id else None
    for shift in (origem, destino):
        if shift:
            authorised = db.session.get(PublishedTurno, shift.id)
            fields = ('semana', 'dia', 'loja', 'horario', 'funcionario', 'nota')
            if not authorised or any(getattr(shift, k) != getattr(authorised, k) for k in fields):
                fatal.append('O turno tem alterações por publicar. Publicar e rever o pedido antes de aprovar.')
    if not origem:
        fatal.append('O turno de origem já não existe.')
    elif origem.funcionario != p.de_sigla:
        fatal.append('O turno de origem mudou de dono.')
    if p.turno_destino_id and not destino:
        fatal.append('O turno do/a colega já não existe.')
    elif destino and destino.funcionario != p.para_sigla:
        fatal.append('O turno do/a colega mudou de dono.')
    if fatal:
        return fatal, conflitos
    draft_problems = _swap_conflicts(p.de_sigla, p.para_sigla, origem, destino)
    authorised_problems = _swap_conflicts(p.de_sigla, p.para_sigla, origem, destino, authorised=True)
    return fatal, list(dict.fromkeys(draft_problems + authorised_problems))


@app.route('/api/trocas/<int:tid>/decidir', methods=['POST'])
@supervisor_required
@troca_transaction
def api_troca_decidir(tid):
    """Supervisor approves or denies the request.

    On approval, the underlying Turno rows are reassigned. We re-validate the
    turnos still exist and haven't drifted (e.g. someone deleted them between
    the colleague's accept and the supervisor's decision).
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or type(data.get('aprovar')) is not bool:
        return jsonify(ok=False, erro='Indicar explicitamente se o pedido é aprovado ou recusado.'), 400
    if data.get('nota') is not None and not isinstance(data['nota'], str):
        return jsonify(ok=False, erro='A nota da decisão deve ser texto.'), 400
    _expire_stale_trocas()
    p = db.session.get(TrocaPedido, tid)
    if not p:
        return jsonify({'ok': False, 'erro': 'Pedido não encontrado.'}), 404
    if is_donation(p):
        return _decide_donation(p, data)
    if p.estado != 'pendente_supervisor':
        return jsonify({'ok': False, 'erro': f'Pedido não está a aguardar supervisão (está {p.estado}).'}), 409

    aprovar = data['aprovar']
    nota = (data.get('nota') or '').strip()[:300]

    if not aprovar and not (p.tipo or '').startswith('aberto_'):
        # A DIRECT request is closed, and stays in both people's history with
        # the supervisor's note. It used to be turned into a central post — a
        # public one, reason included — which the author never chose to publish,
        # while the colleague who had accepted just saw it vanish. The shift is
        # free again: the author can ask someone else in two taps.
        p.estado = 'negado'
        p.resposta_supervisor = nota
        p.decisao_em = datetime.now(timezone.utc)
        transaction_audit('troca_negar', target=str(p.id), detail=f'recusou {p.para_sigla}; fechado')
        return jsonify({'ok': True, 'troca': _serialize_troca(p)})

    if not aprovar:
        p.resposta_supervisor = nota
        p.decisao_em = datetime.now(timezone.utc)
        # A CENTRAL post: "não" means "not THIS person" — not "no swap at all".
        # The post was public already and may have other applicants, so it goes
        # back to its author to choose again, the rejected colleague off the
        # table.
        rejected = p.para_sigla
        # 1) Veto the rejected colleague for this post — they can't be picked
        #    again, nor re-apply; their history says the supervisor refused.
        if rejected:
            cand = (TrocaCandidatura.query
                    .filter_by(troca_id=p.id, sigla=rejected)
                    .order_by(TrocaCandidatura.criado_em.desc()).first())
            if cand:
                cand.status = 'vetado'
            else:
                db.session.add(TrocaCandidatura(
                    troca_id=p.id, sigla=rejected,
                    comentario='(recusado pela supervisão)', status='vetado'))
        # 2) Bring the other applicants back into play: anyone parked as
        #    'rejeitada' when this colleague was chosen returns to 'pendente'
        #    so the author can pick among them again.
        TrocaCandidatura.query.filter(
            TrocaCandidatura.troca_id == p.id,
            TrocaCandidatura.status == 'rejeitada',
        ).update({'status': 'pendente'}, synchronize_session=False)
        # 3) Back on the central for the author to choose again.
        p.para_sigla       = ''
        p.turno_destino_id = None
        p.estado           = 'aberto'
        transaction_audit('troca_negar', target=str(p.id), detail=f'recusou {rejected}; reaberto')
        log.info('Troca %d: supervisão recusou %s — reaberto para %s escolher outro/a',
                 p.id, rejected, p.de_sigla)
        return jsonify({'ok': True, 'troca': _serialize_troca(p, include_candidaturas=True)})

    # APPROVAL — actually move the shifts. Validate one more time via the same
    # pre-flight helper the dashboard uses, so what the supervisor saw flagged
    # is exactly what gets enforced here.
    fatal, conflicts = _troca_approval_problems(p)
    if fatal:
        return jsonify({'ok': False,
                        'erro': fatal[0] + ' O pedido não pode ser aprovado.'}), 409
    if conflicts:
        return jsonify({
            'ok': False,
            'erro': 'A troca criaria sobreposições:\n• '
                    + '\n• '.join(conflicts)
                    + '\nRecusar o pedido ou mover primeiro o turno em conflito.',
            'conflito': True,
        }), 409
    origem = db.session.get(schedule_turno(), p.turno_origem_id)
    destino = db.session.get(schedule_turno(), p.turno_destino_id) if p.turno_destino_id else None

    affected_weeks = {t.semana for t in (origem, destino) if t}
    authorised_snapshots = {w: json.loads(latest_release(w).snapshot_json) for w in affected_weeks}

    now = datetime.now(timezone.utc)
    p.resposta_supervisor = nota
    p.decisao_em = now
    if destino:
        # Symmetrical swap
        origem.funcionario, destino.funcionario = p.para_sigla, p.de_sigla
        origem.updated_at = destino.updated_at = now
        swap_desc = f'{origem.id}↔{destino.id} ({p.de_sigla}↔{p.para_sigla})'
    else:
        # Doação: origem changes owner; destino doesn't exist
        origem.funcionario = p.para_sigla
        origem.updated_at = now
        swap_desc = f'doacao {origem.id} ({p.de_sigla}→{p.para_sigla})'

    p.estado = 'aprovado'
    db.session.flush()
    # Approval explicitly authorises only the swapped shifts. Other staged
    # changes in either week stay in the draft and cannot leak into this release.
    for week, authorised in authorised_snapshots.items():
        owners = {t.id: t.funcionario for t in (origem, destino) if t and t.semana == week}
        for shift in authorised['shifts']:
            if shift['id'] in owners:
                shift['funcionario'] = owners[shift['id']]
        release_week(week, current_user_sigla(), f'Troca aprovada #{p.id}', supplied_snapshot=authorised)
    transaction_audit('troca_aprovar', target=str(p.id), detail=swap_desc)
    log.info('Troca %d aprovada — %s', p.id, swap_desc)
    return jsonify({'ok': True, 'troca': _serialize_troca(p)})


# ═════════════════════════════════════════════════════════════════════════════
# Central de trocas (open marketplace)
# ═════════════════════════════════════════════════════════════════════════════
# In this mode, an employee publishes a request without naming a colleague.
# Any colleague can apply (criar candidatura). The author picks one. From that
# point the request becomes a normal pendente_supervisor with para_sigla set.

def _has_turno_conflict(sigla: str, day_label: str, horario: str, semana: str,
                        ignore_turno_id: int = None, model=None) -> bool:
    """Returns True if `sigla` already has a turno that overlaps the given
    (day, horario) in `semana`. Used to flag visual conflicts in the central."""
    if not sigla or not day_label or not horario or not semana:
        return False
    model = model or schedule_turno()
    q = model.query.filter_by(funcionario=sigla, semana=semana, dia=day_label)
    if ignore_turno_id:
        q = q.filter(model.id != ignore_turno_id)
    for t in q.all():
        if _shifts_overlap(t.horario, horario):
            return True
    return False


def _turno_date_iso(turno) -> str:
    """Calendar date (ISO, '' if unresolvable) of a turno, resolved via its
    semana's dias_json (day label → date)."""
    if not turno:
        return ''
    # Lazy expiry explicitly loads published shifts even in a supervisor
    # request. Resolve against the same projection, never staged week dates.
    model = PublishedSemana if isinstance(turno, PublishedTurno) else SemanaConfig
    return _dias_da_semana(turno.semana, model=model).get(turno.dia, '') or ''


def _close_donation_candidates(p, status, winner_id=None, actor=None):
    for candidate in TrocaCandidatura.query.filter_by(troca_id=p.id).filter(
            TrocaCandidatura.status.in_(ACTIVE_CANDIDATES)).all():
        before = candidate.status
        candidate.status = 'aceite' if candidate.id == winner_id else status
        transaction_audit('doacao_candidatura_decidir', p.id, {
            'candidatura_id': candidate.id, 'sigla': candidate.sigla,
            'antes': before, 'depois': candidate.status}, actor=actor)


def _decide_donation(p, data):
    if p.estado not in DONATION_STATES:
        return jsonify(ok=False, erro='Esta doação já está fechada.'), 409
    approve = data['aprovar']
    note = (data.get('nota') or '').strip()[:300]
    before_state = p.estado
    if not approve:
        p.estado, p.resposta_supervisor = 'negado', note
        p.decisao_em = datetime.now(timezone.utc)
        _close_donation_candidates(p, 'negada')
        transaction_audit('troca_negar', p.id, {'antes': before_state, 'decisao': 'negado', 'turno': p.turno_origem_id})
        return jsonify(ok=True, troca=_serialize_troca(p, include_candidaturas=True))
    cid = data.get('candidatura_id')
    if type(cid) is not int or cid <= 0:
        return jsonify(ok=False, erro='Selecionar uma candidatura válida.'), 400
    candidate = db.session.get(TrocaCandidatura, cid)
    if not candidate or candidate.troca_id != p.id:
        return jsonify(ok=False, erro='Candidatura não encontrada nesta doação.'), 404
    if candidate.status != 'pendente':
        return jsonify(ok=False, erro='Essa candidatura já não está pendente.'), 409
    fatal, conflicts, warnings = _donation_candidate_problems(p, candidate.sigla)
    if fatal or conflicts:
        return jsonify(ok=False, erro=(fatal or conflicts)[0], conflito=bool(conflicts)), 409
    origin = db.session.get(Turno, p.turno_origem_id)
    release = latest_release(origin.semana)
    if not release:
        return jsonify(ok=False, erro='O turno já não pertence a uma escala publicada.'), 409
    authorised = json.loads(release.snapshot_json)
    before = shift_identity(db.session.get(PublishedTurno, origin.id))
    # Recipient selection and every resulting write remain inside BEGIN IMMEDIATE.
    p.para_sigla = candidate.sigla
    p.estado, p.resposta_supervisor = 'aprovado', note
    p.decisao_em = datetime.now(timezone.utc)
    origin.funcionario, origin.updated_at = candidate.sigla, p.decisao_em
    _close_donation_candidates(p, 'rejeitada', winner_id=cid)
    for row in authorised['shifts']:
        if row['id'] == origin.id:
            row['funcionario'] = candidate.sigla
    db.session.flush()
    release_week(origin.semana, current_user_sigla(), f'Doação aprovada #{p.id}',
                 supplied_snapshot=authorised)
    transaction_audit('troca_aprovar', p.id, {'candidatura_id': cid, 'turno': origin.id,
        'antes': {'funcionario': before['funcionario'], 'data': before['date'],
                  'loja': before['loja'], 'horario': before['horario']},
        'depois': {'funcionario': candidate.sigla}, 'decisao': 'aprovado', 'avisos': warnings})
    return jsonify(ok=True, troca=_serialize_troca(p, include_candidaturas=True))


def _expire_stale_trocas() -> int:
    """Donations expire at Lisbon shift start or identity drift, including admin review.

    Other exchanges retain their existing day-based expiry. Lazy reads and all
    writes share the same SQLite reservation as decisions/cancellation/withdrawal.
    """
    owns_transaction = not getattr(g, 'troca_transaction', False)
    if owns_transaction:
        begin_write()
    n = 0
    try:
        for p in TrocaPedido.query.filter(TrocaPedido.estado.in_(TROCA_STATES_OPEN)).all():
            if is_donation(p):
                reasons = _donation_identity_problems(p)
                stale = bool(reasons)
            elif p.estado == 'pendente_supervisor':
                continue  # Preserve retrospective decisions on genuine exchanges.
            else:
                shifts = [db.session.get(PublishedTurno, tid) if tid else None
                          for tid in (p.turno_origem_id, p.turno_destino_id)]
                stale = (not shifts[0] or (p.turno_destino_id and not shifts[1]) or
                         any(_turno_date_iso(t) and _turno_date_iso(t) < date.today().isoformat()
                             for t in shifts if t))
                reasons = ['O turno já passou ou deixou de existir.']
            if stale:
                before = p.estado
                p.estado, p.resposta_supervisor = 'expirado', reasons[0]
                p.decisao_em = datetime.now(timezone.utc)
                if is_donation(p):
                    _close_donation_candidates(p, 'expirada', actor='SYSTEM')
                transaction_audit('troca_expirar', p.id, {'antes': before, 'depois': 'expirado',
                    'turno': p.turno_origem_id, 'motivo': reasons[0]}, actor='SYSTEM')
                n += 1
        if owns_transaction:
            db.session.commit()
        return n
    except BaseException:
        if owns_transaction:
            db.session.rollback()
        raise


@app.route('/api/trocas/abrir', methods=['POST'])
@login_required
@troca_transaction
def api_troca_abrir():
    """Publish an open request on the Central.

    Body: {turno_origem_id, tipo: 'aberto_doacao'|'aberto_troca', motivo?}

    For 'aberto_troca', the author signals they want SOMETHING in return; the
    candidato chooses which of their turnos to offer at candidacy time.
    """
    sigla = current_user_sigla()
    if not sigla or sigla == ADMIN_SIGLA:
        return jsonify({'ok': False, 'erro': 'Apenas funcionários podem publicar pedidos.'}), 403

    data = request.get_json(silent=True) or {}
    try:
        origem_id = int(data.get('turno_origem_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'erro': 'turno_origem_id inválido'}), 400
    tipo = (data.get('tipo') or '').strip()
    if tipo not in ('aberto_doacao', 'aberto_troca'):
        return jsonify({'ok': False, 'erro': 'tipo deve ser aberto_doacao ou aberto_troca'}), 400
    motivo = (data.get('motivo') or '').strip()[:300]

    return _publish_open_request(sigla, origem_id, tipo, motivo)


def _publish_open_request(sigla, origem_id, tipo, motivo):
    _expire_stale_trocas()

    origem = db.session.get(schedule_turno(), origem_id)
    if not origem:
        return jsonify({'ok': False, 'erro': 'Turno não encontrado.'}), 404
    if origem.funcionario != sigla:
        return jsonify({'ok': False, 'erro': 'Esse turno pertence a outra pessoa.'}), 403
    # A past shift, or one already promised (a direct request, a post, or
    # offered back in a candidatura) can't be published.
    erro = _troca_turnos_erro(origem)
    if erro:
        return jsonify({'ok': False, 'erro': erro[0]}), erro[1]

    p = TrocaPedido(
        de_sigla=sigla, para_sigla='',
        turno_origem_id=origem_id, turno_destino_id=None,
        motivo=motivo, estado='aberto', tipo=tipo,
    )
    if tipo == 'aberto_doacao':
        p.origem_snapshot_json = json.dumps(shift_identity(origem), ensure_ascii=False)
        fatal = _donation_identity_problems(p)
        if fatal:
            return jsonify(ok=False, erro=fatal[0]), 409
    db.session.add(p)
    db.session.flush()
    transaction_audit('central_publicar', target=str(p.id),
          detail=f'{sigla} tipo={tipo} turno={origem_id}')
    return jsonify({'ok': True, 'troca': _serialize_troca(p, include_candidaturas=True)})


@app.route('/api/central')
@login_required
def api_central_listar():
    """List all open requests visible on the central. Each request is annotated
    with a conflict flag for the current viewer (does this turno overlap with
    something the viewer already has?)."""
    _expire_stale_trocas()
    sigla = current_user_sigla()
    sup = is_supervisor()
    rows = (TrocaPedido.query
            .filter(_central_filter())
            .order_by(TrocaPedido.criado_em.desc())
            .all())
    return jsonify({'ok': True, 'pedidos': [_central_view(r, sigla, sup) for r in rows]})


@app.route('/api/trocas/<int:tid>/candidatar', methods=['POST'])
@login_required
@troca_transaction
def api_troca_candidatar(tid):
    """Apply to an open request.

    Body: {comentario?: str, turno_oferecido_id?: int (for aberto_troca)}
    """
    sigla = current_user_sigla()
    if not sigla or sigla == ADMIN_SIGLA:
        return jsonify({'ok': False, 'erro': 'Apenas funcionários podem candidatar-se.'}), 403

    _expire_stale_trocas()   # a stale offer must reject candidaturas, not accept them
    p = db.session.get(TrocaPedido, tid)
    if not p:
        return jsonify({'ok': False, 'erro': 'Pedido não encontrado.'}), 404
    if p.estado != 'aberto' and not (is_donation(p) and p.estado == 'pendente_supervisor'):
        return jsonify({'ok': False, 'erro': 'Este pedido já não está aberto.'}), 409
    if p.de_sigla == sigla:
        return jsonify({'ok': False, 'erro': 'Não é possível candidatar-se ao próprio pedido.'}), 400

    data = request.get_json(silent=True) or {}
    comentario = (data.get('comentario') or '').strip()[:300]
    oferecido_id = data.get('turno_oferecido_id')
    oferecido = None
    if oferecido_id not in (None, '', 0):
        try:
            oferecido_id = int(oferecido_id)
        except (TypeError, ValueError):
            return jsonify({'ok': False, 'erro': 'turno_oferecido_id inválido'}), 400
        oferecido = db.session.get(schedule_turno(), oferecido_id)
        if not oferecido or oferecido.funcionario != sigla:
            return jsonify({'ok': False, 'erro': 'O turno oferecido tem de pertencer ao candidato.'}), 400
    else:
        oferecido_id = None

    if p.tipo == 'aberto_troca' and not oferecido_id:
        return jsonify({'ok': False,
                        'erro': 'Este pedido é de troca — é necessário oferecer um turno.'}), 400
    # For aberto_doacao, oferecido must be None
    if p.tipo == 'aberto_doacao' and oferecido_id:
        oferecido_id = None   # silently ignore — author didn't ask for one back
        oferecido = None

    # If the supervisor previously vetoed this person for this request, they
    # can't re-apply — the request was reopened precisely to pick someone else.
    vetado = (TrocaCandidatura.query
              .filter_by(troca_id=tid, sigla=sigla, status='vetado').first())
    if vetado:
        return jsonify({'ok': False,
                        'erro': 'A supervisão já recusou esta pessoa para este pedido.'}), 403

    # Prevent duplicate active candidatures from same sigla
    existing = (TrocaCandidatura.query
                .filter_by(troca_id=tid, sigla=sigla)
                .filter(TrocaCandidatura.status.in_(['pendente', 'aceite']))
                .first())
    if existing:
        return jsonify({'ok': False, 'erro': 'Já existe uma candidatura a este pedido.'}), 409

    origem = db.session.get(schedule_turno(), p.turno_origem_id)
    if not origem:
        return jsonify({'ok': False, 'erro': 'O turno deste pedido já não existe.'}), 409
    if is_donation(p):
        fatal, problems, warnings = _donation_candidate_problems(p, sigla)
        if fatal or problems:
            return jsonify(ok=False, conflito=bool(problems), erro=(fatal or problems)[0]), 409
    erro = _troca_turnos_erro(origem, oferecido, ignore_troca_id=p.id)
    if erro:
        return jsonify({'ok': False, 'erro': erro[0]}), erro[1]
    # The same question the approval will ask — answered now, while the
    # candidate can still pick another shift, not after the author chose them.
    problems = _swap_conflicts(p.de_sigla, sigla, origem, oferecido)
    if problems:
        return jsonify({'ok': False, 'conflito': True, 'erro': problems[0]}), 409

    before_state = p.estado
    c = TrocaCandidatura(
        troca_id=tid, sigla=sigla,
        comentario=comentario, turno_oferecido_id=oferecido_id,
        status='pendente',
    )
    db.session.add(c)
    if is_donation(p):
        p.estado, p.para_sigla = 'pendente_supervisor', ''
    p.atualizado_em = datetime.now(timezone.utc)
    db.session.flush()
    transaction_audit('central_candidatar', target=str(tid), detail={
        'sigla': sigla, 'candidatura_id': c.id, 'oferece': oferecido_id,
        'antes': before_state, 'depois': p.estado})
    return jsonify({'ok': True, 'candidatura': _serialize_candidatura(c)})


@app.route('/api/trocas/<int:tid>/retirar-candidatura', methods=['POST'])
@login_required
@troca_transaction
def api_troca_retirar_candidatura(tid):
    """Candidate withdraws their own candidatura."""
    _expire_stale_trocas()
    sigla = current_user_sigla()
    p = db.session.get(TrocaPedido, tid)
    if not p:
        return jsonify(ok=False, erro='Pedido não encontrado.'), 404
    if p.estado not in TROCA_STATES_OPEN:
        return jsonify(ok=False, erro='Pedido já está fechado.'), 409
    c = (TrocaCandidatura.query
         .filter_by(troca_id=tid, sigla=sigla, status='pendente')
         .first())
    if not c:
        return jsonify({'ok': False, 'erro': 'Não existe candidatura pendente neste pedido.'}), 404
    before_state = p.estado
    c.status = 'retirada'
    db.session.flush()
    if is_donation(p) and not TrocaCandidatura.query.filter_by(troca_id=tid).filter(
            TrocaCandidatura.status.in_(ACTIVE_CANDIDATES)).count():
        p.estado = 'aberto'
    p.atualizado_em = datetime.now(timezone.utc)
    transaction_audit('central_retirar', target=str(tid), detail={
        'sigla': sigla, 'candidatura_id': c.id, 'status': 'retirada',
        'antes': before_state, 'depois': p.estado})
    return jsonify({'ok': True})


@app.route('/api/trocas/<int:tid>/escolher', methods=['POST'])
@login_required
@troca_transaction
def api_troca_escolher(tid):
    """The author picks a winner among the candidatures.

    Body: {candidatura_id}

    Effect:
      * winning candidatura → status='aceite'
      * losers → status='rejeitada'
      * pedido.para_sigla ← chosen sigla; estado ← 'pendente_supervisor'
      * If aberto_troca, pedido.turno_destino_id ← winner's offered turno
    Supervisor then approves/denies via the normal /decidir endpoint.
    """
    _expire_stale_trocas()
    sigla = current_user_sigla()
    p = db.session.get(TrocaPedido, tid)
    if not p:
        return jsonify({'ok': False, 'erro': 'Pedido não encontrado.'}), 404
    if is_donation(p):
        return jsonify(ok=False, erro='Só a administração pode escolher e aprovar uma doação.'), 403
    if p.de_sigla != sigla:
        return jsonify({'ok': False, 'erro': 'Só quem publicou pode escolher.'}), 403
    if p.estado != 'aberto':
        return jsonify({'ok': False, 'erro': 'Este pedido já não está aberto.'}), 409

    data = request.get_json(silent=True) or {}
    try:
        cid = int(data.get('candidatura_id'))
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'erro': 'candidatura_id inválido'}), 400

    winner = db.session.get(TrocaCandidatura, cid)
    if not winner or winner.troca_id != tid:
        return jsonify({'ok': False, 'erro': 'Candidatura não encontrada.'}), 404
    if winner.status != 'pendente':
        return jsonify({'ok': False, 'erro': 'Essa candidatura já não está pendente.'}), 409

    # Re-validate the winner's offered turno still exists & belongs to them (if applicable)
    offered = None
    if p.tipo == 'aberto_troca':
        if not winner.turno_oferecido_id:
            return jsonify({'ok': False, 'erro': 'Candidatura sem turno oferecido.'}), 409
        offered = db.session.get(schedule_turno(), winner.turno_oferecido_id)
        if not offered or offered.funcionario != winner.sigla:
            return jsonify({'ok': False, 'erro': 'O turno oferecido já não existe ou mudou.'}), 409

    # The schedule may have moved since they applied: check the swap as the
    # supervisor will, before the others are set aside.
    origem = db.session.get(schedule_turno(), p.turno_origem_id)
    if not origem:
        return jsonify({'ok': False, 'erro': 'O turno deste pedido já não existe.'}), 409
    problems = _swap_conflicts(p.de_sigla, winner.sigla, origem, offered)
    if problems:
        return jsonify({'ok': False, 'conflito': True,
                        'erro': problems[0] + ' Escolher outra candidatura.'}), 409

    # Apply: winning candidatura is accepted, others rejected
    winner.status = 'aceite'
    TrocaCandidatura.query.filter(
        TrocaCandidatura.troca_id == tid,
        TrocaCandidatura.id != cid,
        TrocaCandidatura.status == 'pendente',
    ).update({'status': 'rejeitada'}, synchronize_session=False)

    # Promote the pedido to a normal "pendente_supervisor"
    p.para_sigla        = winner.sigla
    p.turno_destino_id  = offered.id if offered else None
    p.estado            = 'pendente_supervisor'
    p.decisao_em        = datetime.now(timezone.utc)
    transaction_audit('central_escolher', target=str(tid), detail=f'winner={winner.sigla}')
    log.info('Troca %d: %s escolheu %s', tid, sigla, winner.sigla)
    return jsonify({'ok': True, 'troca': _serialize_troca(p, include_candidaturas=True)})


# ── Personal schedule view (public, accessible by employees) ─────────────────

@app.route('/minha-escala')
@login_required
def minha_escala():
    """Employee personal schedule viewer. Defaults to the logged-in user's sigla;
    supervisors can pick any sigla via the query string."""
    semana_str = request.args.get('semana')
    sigla      = request.args.get('sigla', '').strip().upper()

    # If no sigla was specified, fall back to the logged-in user.
    # The admin doesn't have their own schedule, so they have to pick someone.
    if not sigla and current_user_sigla() and current_user_sigla() != ADMIN_SIGLA:
        sigla = current_user_sigla()

    # Non-supervisors can only view their own schedule.
    if sigla and not is_supervisor() and sigla != current_user_sigla():
        sigla = current_user_sigla() or ''

    semanas = semanas_chronological(schedule_semana())
    if not semana_str:
        # Default to the week the funcionario is actually living right now,
        # not the supervisor's drafting week. Supervisors viewing someone
        # else's schedule still get the working week — they can change it
        # via the dropdown.
        working = get_semana_for_employee()
        semana_str = working.codigo if working else (semanas[0].codigo if semanas else '')

    semana_obj = schedule_semana().query.filter_by(codigo=semana_str).first()
    funcionarios = sorted(Funcionario.query.all(), key=lambda f: f.nome)

    turnos_data = []; func_obj = None; dias_full = []
    if semana_obj:
        dias_full = json.loads(semana_obj.dias_json or '[]')

    if sigla and semana_obj:
        func_obj = db.session.get(Funcionario, sigla)
        if func_obj:
            turnos = schedule_turno().query.filter_by(funcionario=sigla, semana=semana_str).all()
            loja_map = {l['id']: l['nome'] for l in LOJAS}
            # Build ordered by day
            dia_order = {d['label']: i for i, d in enumerate(dias_full)}
            turnos_sorted = sorted(turnos, key=lambda t: dia_order.get(t.dia, 99))
            # A shift already worked can't be swapped, and one already in an
            # open request can't be offered twice — the card says so instead of
            # offering buttons that the server will refuse.
            em_pedido = _turnos_em_pedido_aberto([t.id for t in turnos])
            turnos_data = []
            for t in turnos_sorted:
                info = _turno_info(t)
                turnos_data.append(
                    {'id': t.id,
                     'loja': t.loja,
                     'dia': t.dia, 'horario': t.horario,
                     'loja_nome': loja_map.get(t.loja, t.loja),
                     'quando': info['quando'], 'passado': info['passado'],
                     'em_pedido': t.id in em_pedido,
                     'nota': t.nota or '', 'descarga': False,
                     'feriado': next((d['feriado_nome'] for d in dias_full if d['label']==t.dia and d['feriado']), '')})
            # Descarga assignments show alongside regular shifts, flagged so the
            # template can style them and so they're never mistaken for a shift.
            descargas_emp = schedule_descarga().query.filter_by(
                funcionario=sigla, semana=semana_str).all()
            for dc in sorted(descargas_emp, key=lambda d: dia_order.get(d.dia, 99)):
                turnos_data.append({
                    'id': f'd{dc.id}', 'loja': dc.loja, 'dia': dc.dia,
                    'horario': dc.horario, 'loja_nome': loja_map.get(dc.loja, dc.loja),
                    'nota': 'Descarga', 'descarga': True,
                    'feriado': next((d['feriado_nome'] for d in dias_full if d['label']==dc.dia and d['feriado']), '')})
            turnos_data.sort(key=lambda x: (
                dia_order.get(x['dia'], 99),
                shift_bounds(x['horario'])[0] if shift_bounds(x['horario'])[0] is not None else 1440,
                x['loja_nome'],
            ))

    # Week summary: total scheduled hours — shifts by their window, descargas at
    # a 4h minimum — and a Mon→Sun at-a-glance strip showing which days have a
    # shift, plus today / past / feriado flags.
    today_iso   = date.today().isoformat()
    total_horas = round(sum(descarga_hours(t['horario']) if t['descarga']
                            else _shift_hours(t['horario'])
                            for t in turnos_data), 1)
    dias_com_turno    = {t['dia'] for t in turnos_data if not t['descarga']}
    dias_com_descarga = {t['dia'] for t in turnos_data if t['descarga']}
    week_overview = []
    for d in dias_full:
        lbl = d['label']
        d_iso = d.get('date', '')
        ds = d_iso.split('-')[-1].lstrip('0') if d_iso else ''
        week_overview.append({
            'label':       lbl,
            'date_short':  ds,
            'has_shift':   lbl in dias_com_turno,
            'has_descarga': lbl in dias_com_descarga,
            'is_today':    d_iso == today_iso,
            'is_past':     bool(d_iso and d_iso < today_iso),
            'is_feriado':  bool(d.get('feriado')),
        })

    hoje = next((s for s in semanas if _semana_contains(s, today_iso)), None)
    return render_template('minha_escala.html',
        semanas=semanas, semana_obj=semana_obj, semana_str=semana_str,
        hoje_codigo=hoje.codigo if hoje else '',
        funcionarios=funcionarios, sigla=sigla, func_obj=func_obj,
        turnos=turnos_data, dias_full=dias_full,
        total_horas=total_horas,
        contract_hours=(func_obj.horas_contrato if func_obj else 0),
        is_turnos=(func_obj.is_turnos if func_obj else False),
        week_overview=week_overview)

# ── Per-loja PDF export ───────────────────────────────────────────────────────

@app.route('/supervisor/exportar-pdf-loja/<loja_id>')
@supervisor_required
def exportar_pdf_loja(loja_id):
    """Generate a PDF with just one store's schedule."""
    from flask import send_file
    from gerar_pdf import gerar_escala_pdf
    import io

    semana_str = request.args.get('semana')
    if not semana_str:
        ativa = get_semana_ativa()
        semana_str = ativa.codigo if ativa else '11-17mai'

    semana_obj = schedule_semana().query.filter_by(codigo=semana_str).first()
    loja_info  = next((l for l in LOJAS if l['id'] == loja_id), None)
    if not semana_obj or not loja_info:
        return 'Não encontrado', 404

    dias_full = json.loads(semana_obj.dias_json or '[]')
    dias      = [d['label'] for d in dias_full]
    feriados  = {d['label']: d['feriado_nome'] for d in dias_full if d['feriado']}
    grid, _   = build_grid(loja_id, semana_str, dias)

    pdf_bytes = gerar_escala_pdf(
        _export_schedule_label(semana_obj),
        [{'nome': loja_info['nome'], 'grid': grid, 'dias': dias, 'feriados': feriados}],
        func_map_with_tipo(), dias_full=dias_full
    )
    return send_file(
        io.BytesIO(pdf_bytes), mimetype='application/pdf',
        as_attachment=True, download_name=f'escala-{loja_id}-{semana_str}.pdf'
    )


# ── Copy-week feature ──────────────────────────────────────────────────────────

@app.route('/api/copiar-semana', methods=['POST'])
@supervisor_required
def api_copiar_semana():
    """Copy all turnos from src semana to dst semana. Only adds missing entries."""
    data    = request.get_json(silent=True) or {}
    src     = (data.get('src') or '').strip()[:30]
    dst     = (data.get('dst') or '').strip()[:30]
    overwrite = bool(data.get('overwrite', False))
    only_loja = (data.get('loja') or '').strip()[:30] or None

    if not src or not dst or src == dst:
        return jsonify({'ok': False, 'erro': 'Semanas inválidas'}), 400
    if only_loja and only_loja not in {l['id'] for l in LOJAS}:
        return jsonify({'ok': False, 'erro': 'Loja inválida'}), 400
    if not SemanaConfig.query.filter_by(codigo=src).first():
        return jsonify({'ok': False, 'erro': 'Semana de origem não existe'}), 404
    if not SemanaConfig.query.filter_by(codigo=dst).first():
        return jsonify({'ok': False, 'erro': 'Semana de destino não existe'}), 404

    src_obj = SemanaConfig.query.filter_by(codigo=src).first()
    dst_obj = SemanaConfig.query.filter_by(codigo=dst).first()
    src_dias = [d['label'] for d in json.loads(src_obj.dias_json or '[]')]
    dst_dias = [d['label'] for d in json.loads(dst_obj.dias_json or '[]')]
    if len(src_dias) != len(dst_dias):
        return jsonify({'ok': False, 'erro': 'Semanas com numero de dias diferente'}), 400
    day_map = dict(zip(src_dias, dst_dias))

    deleted = 0
    if overwrite:
        q = schedule_turno().query.filter_by(semana=dst)
        if only_loja:
            q = q.filter_by(loja=only_loja)
        deleted = q.count()
        q.delete()

    q = schedule_turno().query.filter_by(semana=src)
    if only_loja:
        q = q.filter_by(loja=only_loja)
    src_turnos = q.all()

    created = 0
    skipped = 0
    for t in src_turnos:
        new_dia = day_map.get(t.dia)
        if not new_dia:
            continue
        exists = schedule_turno().query.filter_by(
            loja=t.loja, horario=t.horario, dia=new_dia,
            funcionario=t.funcionario, semana=dst
        ).first()
        if exists:
            skipped += 1; continue
        db.session.add(Turno(
            loja=t.loja, horario=t.horario, dia=new_dia,
            funcionario=t.funcionario, nota=t.nota or '', semana=dst,
            updated_at=datetime.now(timezone.utc)
        ))
        created += 1
    db.session.commit()
    audit('semana_copy', target=f'{src}->{dst}',
          detail=f'loja={only_loja or "all"} overwrite={overwrite} '
                 f'created={created} skipped={skipped} deleted={deleted}')
    return jsonify({'ok': True, 'criados': created, 'ignorados': skipped})


# ── Day-first builder: empty slots per day for the supervisor panel ───────────
# Powers the redesigned "Construir o dia" panel: for a given (semana, dia),
# returns every unfilled (loja, horario) cell with preference/availability info,
# plus filled/total counts per day for the day-spine pills.

# Scoring helpers moved to helpers/scoring.py.

@app.route('/api/empties/<semana>/<path:dia>')
@supervisor_required
def api_empties(semana, dia):
    """Headcount-driven empties view for the supervisor panel.

    For the given (semana, dia), returns per-loja headcount status:
      - target: configured headcount for this (loja, weekday)
      - filled: how many people are currently scheduled (counted as unique
        sigla-shifts that day, so 2 people on the same time band = 2)
      - missing: max(0, target - filled)
      - current_siglas: who's already on
      - suggestions: best people to fill the missing seats, ranked by
        preference > availability > workload, deduped from current_siglas
      - slots: per-horario breakdown for the expandable detail view

    Also returns day_totals (headcount math) for the day-spine pills.
    """
    semana = (semana or '').strip()[:30]
    dia    = (dia or '').strip()[:20]
    semana_obj = SemanaConfig.query.filter_by(codigo=semana).first()
    if not semana_obj:
        return jsonify({'ok': False, 'erro': 'Semana inválida'}), 404
    try:
        dias_full = json.loads(semana_obj.dias_json or '[]')
    except json.JSONDecodeError:
        dias_full = []
    dias = [d['label'] for d in dias_full]
    if dia not in dias:
        return jsonify({'ok': False, 'erro': 'Dia inválido'}), 400

    # ── Pre-fetch in 3 queries ────────────────────────────────────────────────
    # 1. All disponibilidades for this week
    disps_by_func = {}
    for d in Disponibilidade.query.filter_by(semana=semana).all():
        disps_by_func.setdefault(d.funcionario, {})[d.dia] = {
            'tipo':  d.tipo,
            'lojas': set((d.lojas_pref or '').split(',')) - {''},
            'slots': set((d.slots_pref or '').split(',')) - {''},
            'texto': d.texto or '',
            # Independent of tipo — a worker can be Disponível AND do descarga.
            # The scorer uses it to rank a descarga-only opt-in above silence.
            'descarga': bool(d.descarga_opt),
        }
    # 2. All turnos for this week
    turnos = schedule_turno().query.filter_by(semana=semana).all()
    # 2b. All descarga assignments for this week (occupy the worker in the
    #     morning; they count toward hours and block overlapping regular slots).
    descargas = schedule_descarga().query.filter_by(semana=semana).all()
    descarga_by_sigla_dia = {}
    for dc in descargas:
        descarga_by_sigla_dia.setdefault((dc.funcionario, dc.dia), []).append(dc.horario)
    # 3. All funcionarios + their headcount targets
    funcionarios = {f.sigla: f for f in Funcionario.query.all()}
    targets_by_loja = {}   # loja -> {dia_idx: target}
    for t in LojaHeadcountTarget.query.all():
        targets_by_loja.setdefault(t.loja, {})[t.dia_idx] = t.target

    # Effective bookable horarios per loja (base − suppressed + custom blocks),
    # in display order. Batched here (2 queries) so the suggestion loop can
    # recommend a concrete time band for one-click assignment without re-querying.
    suppressed_by_loja = {}
    for r in HorarioSupressed.query.all():
        suppressed_by_loja.setdefault(r.loja, set()).add(r.horario)
    custom_by_loja = {}
    for cb in (HorarioBloco.query
               .order_by(HorarioBloco.ordem, HorarioBloco.id).all()):
        custom_by_loja.setdefault(cb.loja, []).append(cb.horario)
    effective_horarios = {}
    for loja in LOJAS:
        lid = loja['id']
        base = [h for h in HORARIOS_BASE.get(lid, [])
                if h not in suppressed_by_loja.get(lid, set())]
        seen = set(base)
        for h in custom_by_loja.get(lid, []):
            if h not in seen:
                base.append(h)
                seen.add(h)
        effective_horarios[lid] = base

    # 4. Past schedule evidence, reused across stores. General affinity needs
    # HISTORICAL_MIN_WEEKS; a supervisor's usual store can be known from one.
    prior_weeks_data = _load_prior_weeks(semana)

    # ── Build indices over turnos ─────────────────────────────────────────────
    # (loja, dia) -> [(horario, sigla), ...]
    loja_day_shifts = {}
    # (sigla, dia) -> [(loja, horario), ...] — for conflict checks
    sigla_day_shifts = {}
    # Weekly hours per sigla — for workload balance in suggestion scoring
    weekly_hours = {sigla: 0.0 for sigla in funcionarios}
    for t in turnos:
        loja_day_shifts.setdefault((t.loja, t.dia), []).append((t.horario, t.funcionario))
        sigla_day_shifts.setdefault((t.funcionario, t.dia), []).append((t.loja, t.horario))
        if t.funcionario in weekly_hours:
            weekly_hours[t.funcionario] += _shift_hours(t.horario)
    for dc in descargas:
        if dc.funcionario in weekly_hours:
            weekly_hours[dc.funcionario] += descarga_hours(dc.horario)

    # Distinct working days include deliveries once per day. Adjacent dated
    # weeks also inform rest gaps, including Sunday→Monday and midnight closes.
    worked_days = {}
    for sigla, day in sigla_day_shifts:
        worked_days.setdefault(sigla, set()).add(day)
    for sigla, day in descarga_by_sigla_dia:
        worked_days.setdefault(sigla, set()).add(day)
    day_date = None
    for day in dias_full:
        if day.get('label') == dia:
            try:
                day_date = date.fromisoformat(day.get('date', ''))
            except (ValueError, TypeError):
                pass
    dated_assignments = {}
    dated_work_days = {}
    if day_date:
        nearby_dates = {}
        for week in SemanaConfig.query.all():
            try:
                week_days = json.loads(week.dias_json or '[]')
            except (ValueError, TypeError):
                continue
            for day in week_days:
                try:
                    actual_date = date.fromisoformat(day.get('date', ''))
                except (ValueError, TypeError, AttributeError):
                    continue
                if abs((actual_date - day_date).days) <= 7:
                    nearby_dates[(week.codigo, day.get('label'))] = actual_date.toordinal()
        other_weeks = {code for code, _ in nearby_dates if code != semana}
        adjacent_shifts = schedule_turno().query.filter(schedule_turno().semana.in_(other_weeks)).all() if other_weeks else []
        adjacent_deliveries = schedule_descarga().query.filter(schedule_descarga().semana.in_(other_weeks)).all() if other_weeks else []
        for assignment in turnos + descargas + adjacent_shifts + adjacent_deliveries:
            ordinal = nearby_dates.get((assignment.semana, assignment.dia))
            if ordinal is not None:
                dated_work_days.setdefault(assignment.funcionario, set()).add(ordinal)
                start, end = shift_bounds(assignment.horario)
                if start is not None:
                    dated_assignments.setdefault(assignment.funcionario, []).append((ordinal, start, end))

    # Fairness for the on-call (turnos) pool: contracted workers are balanced
    # against their contract inside _score_candidate, but turnos workers have no
    # contract to balance against — so we balance them against the pool's own
    # mean weekly hours, nudging suggestions toward whoever is lighter so far.
    _turnos_hours = [weekly_hours.get(s, 0.0) for s, f in funcionarios.items()
                     if f.is_turnos and (weekly_hours.get(s, 0.0) > 0 or s in disps_by_func)]
    _turnos_mean = (sum(_turnos_hours) / len(_turnos_hours)) if _turnos_hours else 0.0

    # ── Per-day filled/target totals (for the spine) ──────────────────────────
    day_totals = {}
    for d_label in dias:
        d_idx = _dia_label_to_idx(d_label)
        d_target = 0
        d_filled = 0
        for loja in LOJAS:
            lid = loja['id']
            if d_idx is not None:
                d_target += targets_by_loja.get(lid, {}).get(d_idx, 0)
            shifts = loja_day_shifts.get((lid, d_label), [])
            # Unique people per loja (consistent with per-loja `filled`); a person
            # with two shifts in the same loja/day counts once toward headcount.
            d_filled += len({sigla for (_h, sigla) in shifts})
        day_totals[d_label] = {'filled': d_filled, 'target': d_target}

    # ── Build the per-loja cards for the requested day ────────────────────────
    dia_idx_today = _dia_label_to_idx(dia)
    lojas_out = []
    for loja in LOJAS:
        lid = loja['id']
        lname = loja['nome']
        target = targets_by_loja.get(lid, {}).get(dia_idx_today, 0) if dia_idx_today is not None else 0
        shifts_today = loja_day_shifts.get((lid, dia), [])
        # Headcount is about PEOPLE, not shift-rows: someone working two time
        # bands in the same loja/day still counts once toward the target.
        # (Counting len(shifts_today) here caused "3/3" to show when only 2
        # distinct people were assigned but one had a second shift.)
        current_siglas = sorted({sigla for (_h, sigla) in shifts_today})
        filled = len(current_siglas)
        missing = max(0, target - filled)

        suggestions = build_suggestions(
            loja=lid, dia=dia, weekday=dia_idx_today,
            horarios=effective_horarios.get(lid, []), current_shifts=shifts_today,
            funcionarios=funcionarios, availability=disps_by_func,
            weekly_hours=weekly_hours, worked_days=worked_days,
            same_day_shifts=sigla_day_shifts, deliveries=descarga_by_sigla_dia,
            prior=prior_weeks_data, missing=missing, day_labels=dias,
            day_date=day_date, dated_assignments=dated_assignments,
            dated_work_days=dated_work_days,
            turnos_mean=_turnos_mean,
        )

        # Slot breakdown for the expandable detail view
        slot_map = {}
        for (h, sigla) in shifts_today:
            slot_map.setdefault(h, []).append(sigla)
        slots_out = [{'horario': h, 'siglas': sorted(siglas)}
                     for h, siglas in sorted(slot_map.items())]

        lojas_out.append({
            'loja':           lid,
            'loja_nome':      lname,
            'target':         target,
            'filled':         filled,
            'missing':        missing,
            'current_siglas': current_siglas,
            'suggestions':    suggestions,
            'slots':          slots_out,
        })

    # Sort: fixed config order (Carmelitas, Clerigos, Norte, Gaia, MBS) — matches
    # the main grid so supervisors don't have to re-locate stores as they switch
    # days. Earlier we sorted by -missing (most attention first); that was
    # informationally efficient but jarring in practice.
    loja_order = {l['id']: i for i, l in enumerate(LOJAS)}
    lojas_out.sort(key=lambda l: loja_order.get(l['loja'], 999))

    # ── Count how many (loja, weekday) targets diverge from historical P75 ────
    # Surfaced in the panel header as a "X alvos desatualizados" hint so the
    # supervisor sees when accumulated history suggests updating their targets,
    # without having to open the modal first.
    stale_targets_count = 0
    counts = prior_weeks_data.get('loja_day_counts', {})
    for (loja_id, dia_idx_pair), observations in counts.items():
        if len(observations) < 4:
            continue
        proposed = p75_ceiling(observations)
        cur = targets_by_loja.get(loja_id, {}).get(dia_idx_pair, 0)
        if proposed != cur:
            stale_targets_count += 1

    return jsonify({
        'ok':                  True,
        'dia':                 dia,
        'lojas':               lojas_out,
        'day_totals':          day_totals,
        'stale_targets_count': stale_targets_count,
    })


@app.route('/api/headcount-targets', methods=['GET'])
@supervisor_required
def api_headcount_targets_get():
    """Returns all headcount targets as a 2D map: {loja: {dia_idx: target}}.

    Used by the supervisor settings modal that lets the user edit the 5×7 grid.
    """
    out = {}
    for t in LojaHeadcountTarget.query.all():
        out.setdefault(t.loja, {})[t.dia_idx] = t.target
    # Make sure every (loja, dia_idx) is present (target=0 if missing)
    for loja in LOJAS:
        out.setdefault(loja['id'], {})
        for d in range(7):
            out[loja['id']].setdefault(d, 0)
    return jsonify({'ok': True, 'targets': out,
                    'dias': ['Seg','Ter','Qua','Qui','Sex','Sab','Dom']})


@app.route('/api/headcount-targets', methods=['POST'])
@supervisor_required
def api_headcount_targets_post():
    """Accepts a {loja: {dia_idx: target}} map and upserts every entry.

    Validation: targets clamped to 0..50 (50 = sanity cap, no real store has
    50 people at once). Unknown lojas / out-of-range dia_idx values are
    silently dropped to avoid accidental DB pollution.
    """
    data = request.get_json(silent=True) or {}
    targets = data.get('targets') or {}
    if not isinstance(targets, dict):
        return jsonify({'ok': False, 'erro': 'targets deve ser objeto'}), 400
    valid_lojas = {l['id'] for l in LOJAS}
    updated = 0
    for loja, by_dia in targets.items():
        if loja not in valid_lojas or not isinstance(by_dia, dict):
            continue
        for d_str, val in by_dia.items():
            try:
                d_idx = int(d_str)
                tgt   = max(0, min(50, int(val)))
            except (ValueError, TypeError):
                continue
            if not (0 <= d_idx <= 6):
                continue
            row = LojaHeadcountTarget.query.filter_by(loja=loja, dia_idx=d_idx).first()
            if row:
                row.target = tgt
            else:
                db.session.add(LojaHeadcountTarget(loja=loja, dia_idx=d_idx, target=tgt))
            updated += 1
    db.session.commit()
    audit('headcount_targets_update', detail=f'updated={updated}')
    return jsonify({'ok': True, 'updated': updated})


@app.route('/api/headcount-targets/suggest')
@supervisor_required
def api_headcount_targets_suggest():
    """Proposes new targets based on historical headcounts.

    Method: for each (loja, weekday), look at the last 8 weeks of actual
    headcounts and compute the median. Only propose if ≥ 4 of those 8 weeks
    have data (otherwise the median is too noisy to trust).

    Returns a delta-style payload — only cells whose proposed value differs
    from the current target are returned. Empty array = "nothing to change".

    Query param `semana` (optional): which week to treat as "current" so we
    don't include it in the lookback. Defaults to the active week.
    """
    semana_codigo = request.args.get('semana', '').strip()[:30]
    if not semana_codigo:
        cur = SemanaConfig.query.filter_by(ativa=True).first()
        semana_codigo = cur.codigo if cur else ''
    prior = _load_prior_weeks(semana_codigo)
    # Take a narrower window for target suggestions: only last 8 weeks
    # (matches the spec — last 12 was for affinity, last 8 for targets).
    # _load_prior_weeks returns 12; we let the caller use what's relevant.
    # For now use all collected counts but require ≥ 4 observations per cell.

    # Build current targets lookup
    current = {}   # (loja, dia_idx) -> target
    for t in LojaHeadcountTarget.query.all():
        current[(t.loja, t.dia_idx)] = t.target

    proposals = []
    counts = prior.get('loja_day_counts', {})
    for (loja, dia_idx), observations in counts.items():
        if len(observations) < 4:
            continue   # too few data points
        # Use P75-ceiling (75th percentile, rounded up) instead of median.
        # Median understaffs for peaks — if observations are [2,2,3,5], median
        # says target=2-3 but the busy day actually needs 5. Staffing should
        # bias toward the busy case; the cost of being short-staffed on a busy
        # Saturday is much higher than slightly over-staffing on a slow Tuesday.
        proposed = p75_ceiling(observations)
        cur_target = current.get((loja, dia_idx), 0)
        if proposed != cur_target:
            proposals.append({
                'loja':         loja,
                'dia_idx':      dia_idx,
                'current':      cur_target,
                'proposed':     proposed,
                'observations': sorted(observations),
            })
    proposals.sort(key=lambda p: (p['loja'], p['dia_idx']))
    return jsonify({
        'ok':           True,
        'n_past_weeks': prior.get('n_weeks', 0),
        'proposals':    proposals,
    })


# ── Workload tracker: hours per employee per week ─────────────────────────────

# Shift helpers moved to helpers/time_helpers.py.

@app.route('/api/workload/<semana>')
@supervisor_required
def api_workload(semana):
    """Returns {sigla: {turnos: N, horas: H, contrato: C}} for given week."""
    turnos = schedule_turno().query.filter_by(semana=semana).all()
    result = {}
    for t in turnos:
        sig = t.funcionario
        if sig not in result:
            result[sig] = {'turnos': 0, 'horas': 0.0}
        result[sig]['turnos'] += 1
        result[sig]['horas']  += _shift_hours(t.horario)
    for sig, h in descarga_horas_semana(semana).items():
        result.setdefault(sig, {'turnos': 0, 'horas': 0.0})['horas'] += h
    # Add contract hours
    for f in Funcionario.query.all():
        if f.sigla in result:
            result[f.sigla]['contrato']     = f.horas_contrato
            result[f.sigla]['tipo']         = f.tipo_contrato
            result[f.sigla]['nome']         = f.nome
            result[f.sigla]['diff']         = round(result[f.sigla]['horas'] - f.horas_contrato, 2)
    return jsonify(result)


@app.route('/supervisor/conflitos')
@supervisor_required
def supervisor_conflitos():
    """Show conflicts/issues with week selector."""
    semana_str = request.args.get('semana', '').strip()
    if not semana_str:
        ativa = get_semana_ativa()
        semana_str = ativa.codigo if ativa else ''
    semana = SemanaConfig.query.filter_by(codigo=semana_str).first()
    semanas = semanas_chronological(SemanaConfig)
    if not semana:
        return render_template('supervisor_conflitos.html',
            semana=None, semanas=semanas, semana_atual=semana_str,
            issues={'over':[],'under':[],'conflict_indisp':[],'double_day':[],'rank_alone':[]},
            funcs={}, lojas=LOJAS)

    turnos = schedule_turno().query.filter_by(semana=semana.codigo).all()
    workload = {}
    for t in turnos:
        sig = t.funcionario
        if sig not in workload:
            workload[sig] = {'turnos': [], 'horas': 0.0}
        workload[sig]['turnos'].append(t)
        workload[sig]['horas'] += _shift_hours(t.horario)
    # Descarga hours count toward the over/under-contract checks below.
    for sig, h in descarga_horas_semana(semana.codigo).items():
        workload.setdefault(sig, {'turnos': [], 'horas': 0.0})['horas'] += h

    funcs_obj = {f.sigla: f for f in Funcionario.query.all()}
    issues = {
        'over': [],
        'under': [],
        'conflict_indisp': [],
        'double_day': [],
        'rank_alone': [],   # NEW: junior staff opening/closing alone
    }

    # Over/under hours (contrato only)
    for sig, wl in workload.items():
        f = funcs_obj.get(sig)
        if not f or f.tipo_contrato != 'contrato':
            continue
        diff = wl['horas'] - f.horas_contrato
        if diff > 2:
            issues['over'].append({'sigla': sig, 'nome': f.nome,
                'horas': round(wl['horas'],2), 'contrato': f.horas_contrato, 'diff': round(diff,2)})
        elif diff < -2:
            issues['under'].append({'sigla': sig, 'nome': f.nome,
                'horas': round(wl['horas'],2), 'contrato': f.horas_contrato, 'diff': round(diff,2)})

    # Indisponivel conflicts
    disps = {}
    for d in Disponibilidade.query.filter_by(semana=semana.codigo).all():
        disps.setdefault(d.funcionario, {})[d.dia] = d
    for t in turnos:
        if t.funcionario in disps and t.dia in disps[t.funcionario]:
            disp = disps[t.funcionario][t.dia]
            if disp.tipo == 'indisponivel':
                issues['conflict_indisp'].append({
                    'sigla': t.funcionario, 'dia': t.dia, 'horario': t.horario,
                    'loja': t.loja, 'nota_disp': disp.texto or ''
                })

    # Sequential store transfers may be marked in the note. That marker never
    # hides an impossible overlap already present in historical/imported data.
    by_person_day = {}
    for t in turnos:
        by_person_day.setdefault((t.funcionario, t.dia), []).append(t)
    for (sig, dia), ts in by_person_day.items():
        if len(ts) > 1:
            lojas_seen = set(t.loja for t in ts)
            if len(lojas_seen) > 1:
                troca = any('troca' in (t.nota or '').lower() for t in ts)
                overlap = any(
                    a.loja != b.loja and _shifts_overlap(a.horario, b.horario)
                    for index, a in enumerate(ts) for b in ts[index + 1:])
                if overlap or not troca:
                    issues['double_day'].append({
                        'sigla': sig, 'dia': dia, 'overlap': overlap,
                        'turnos': [{'loja': t.loja, 'horario': t.horario} for t in ts]
                    })

    # Rank check: opening/closing shifts handled by junior staff alone
    # Group turnos by loja+dia and find the earliest (opening) and latest (closing) shift
    def shift_start_minutes(h):
        m = re.match(r'(\d{1,2}):(\d{2})', h)
        return int(m.group(1))*60 + int(m.group(2)) if m else 0
    def shift_end_minutes(h):
        m = re.match(r'\d{1,2}:\d{2}\D+(\d{1,2}):(\d{2})', h)
        if not m: return 0
        end = int(m.group(1))*60 + int(m.group(2))
        # cross midnight => effective end in next day
        if end < shift_start_minutes(h):
            end += 24*60
        return end

    by_loja_dia = {}
    for t in turnos:
        by_loja_dia.setdefault((t.loja, t.dia), []).append(t)

    for (loja, dia), ts in by_loja_dia.items():
        # Find opening turno (earliest start)
        sorted_by_start = sorted(ts, key=lambda x: shift_start_minutes(x.horario))
        opening = sorted_by_start[0] if sorted_by_start else None
        # Find closing turno (latest end)
        sorted_by_end = sorted(ts, key=lambda x: shift_end_minutes(x.horario), reverse=True)
        closing = sorted_by_end[0] if sorted_by_end else None

        # Check who is in the opening shift block (earliest horario)
        if opening:
            opening_h = opening.horario
            people_opening = [t for t in ts if t.horario == opening_h]
            # If only ONE person opens, and that person has rank < staff (i.e. trainee), flag
            if len(people_opening) == 1:
                p = people_opening[0]
                fp = funcs_obj.get(p.funcionario)
                if fp and not fp.can_open_alone:
                    issues['rank_alone'].append({
                        'sigla': p.funcionario, 'nome': fp.nome, 'loja': loja,
                        'dia': dia, 'horario': opening_h,
                        'tipo': 'abertura',
                        'rank': RANK_LABELS.get(fp.rank, fp.rank),
                    })

        # Check closing
        if closing and closing != opening:
            closing_h = closing.horario
            people_closing = [t for t in ts if t.horario == closing_h]
            if len(people_closing) == 1:
                p = people_closing[0]
                fp = funcs_obj.get(p.funcionario)
                if fp and not fp.can_close_alone:
                    issues['rank_alone'].append({
                        'sigla': p.funcionario, 'nome': fp.nome, 'loja': loja,
                        'dia': dia, 'horario': closing_h,
                        'tipo': 'fecho',
                        'rank': RANK_LABELS.get(fp.rank, fp.rank),
                    })

    # Triage order. The lists used to come out in dict order, which put a +17h
    # overrun below a string of +3h ones and ran the days of a store backwards.
    # Hours: worst first. Per-shift issues: store (as in the editor), then day.
    def _day_ord(dia):
        idx = _dia_label_to_idx(dia)
        return 9 if idx is None else idx
    _loja_ord = {l['id']: n for n, l in enumerate(LOJAS)}
    issues['over'].sort(key=lambda i: (-i['diff'], i['nome']))
    issues['under'].sort(key=lambda i: (i['diff'], i['nome']))
    issues['conflict_indisp'].sort(key=lambda i: (_day_ord(i['dia']), i['sigla']))
    issues['double_day'].sort(key=lambda i: (_day_ord(i['dia']), i['sigla']))
    issues['rank_alone'].sort(key=lambda i: (_loja_ord.get(i['loja'], 99),
                                             _day_ord(i['dia']),
                                             i['tipo'] != 'abertura'))

    return render_template('supervisor_conflitos.html',
        semana=semana, semanas=semanas, semana_atual=semana_str,
        issues=issues,
        funcs={f.sigla: {'nome': f.nome, 'tipo': f.tipo_contrato,
                         'rank': f.rank, 'rank_label': RANK_LABELS.get(f.rank,'')}
               for f in funcs_obj.values()},
        lojas=LOJAS)




@app.route('/api/funcionario/delete/<sigla>', methods=['DELETE'])
@supervisor_required
def api_delete_funcionario(sigla):
    """Delete a funcionario AND their schedule+disponibilidade rows.
    Close live swap workflows before removing their assignments, while keeping
    the completed request history and audit trail.
    HIGHLY destructive — audited with row counts."""
    sigla = (sigla or '').strip().upper()[:10]
    f = db.session.get(Funcionario, sigla)
    if not f:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    # Count what we're about to nuke, for the audit trail
    n_turnos = schedule_turno().query.filter_by(funcionario=sigla).count()
    n_disp   = Disponibilidade.query.filter_by(funcionario=sigla).count()
    n_descargas = schedule_descarga().query.filter_by(funcionario=sigla).count()
    pedidos = TrocaPedido.query.filter(
        TrocaPedido.estado.in_(list(TROCA_STATES_OPEN)),
        (TrocaPedido.de_sigla == sigla) | (TrocaPedido.para_sigla == sigla),
    ).all()
    now = datetime.now(timezone.utc)
    for pedido in pedidos:
        pedido.estado = 'cancelado'
        pedido.atualizado_em = now
        pedido.decisao_em = now
        pedido.resposta_supervisor = 'Pedido cancelado: funcionário removido da equipa.'
    open_request_ids = db.session.query(TrocaPedido.id).filter(
        TrocaPedido.estado.in_(list(TROCA_STATES_OPEN)))
    n_candidaturas = TrocaCandidatura.query.filter(
        TrocaCandidatura.sigla == sigla,
        TrocaCandidatura.status.in_(['pendente', 'aceite']),
        TrocaCandidatura.troca_id.in_(open_request_ids),
    ).update({'status': 'retirada'}, synchronize_session=False)
    schedule_turno().query.filter_by(funcionario=sigla).delete()
    Disponibilidade.query.filter_by(funcionario=sigla).delete()
    schedule_descarga().query.filter_by(funcionario=sigla).delete()
    db.session.delete(f)
    db.session.commit()
    audit('funcionario_delete', target=sigla,
          detail=(f'turnos={n_turnos} disp={n_disp} descargas={n_descargas} '
                  f'trocas_canceladas={len(pedidos)} candidaturas_retiradas={n_candidaturas} '
                  f'nome={f.nome}'))
    log.warning('Funcionario %s deleted by %s (%d turnos, %d disp)',
                sigla, session.get('user_sigla'), n_turnos, n_disp)
    return jsonify({'ok': True, 'removed': {
        'turnos': n_turnos, 'disp': n_disp, 'descargas': n_descargas,
        'trocas': len(pedidos), 'candidaturas': n_candidaturas}})

@app.route('/api/funcionario/update-horas', methods=['PATCH'])
@supervisor_required
def api_update_horas():
    data  = request.get_json(silent=True) or {}
    sigla = (data.get('sigla') or '').strip().upper()[:10]
    horas = data.get('horas')
    if not sigla or horas is None:
        return jsonify({'ok': False, 'erro': 'Campos em falta'}), 400
    try:
        horas = int(horas)
    except (TypeError, ValueError):
        return jsonify({'ok': False, 'erro': 'Horas inválidas'}), 400
    if not (0 <= horas <= 80):
        return jsonify({'ok': False, 'erro': 'Horas fora de intervalo'}), 400
    f = db.session.get(Funcionario, sigla)
    if not f:
        return jsonify({'ok': False, 'erro': 'Não encontrado'}), 404
    old = f.horas_contrato
    f.horas_contrato = horas
    db.session.commit()
    audit('funcionario_horas', target=sigla, detail=f'{old} -> {horas}')
    return jsonify({'ok': True})


@app.route('/supervisor/visao-geral')
@supervisor_required
def management_overview():
    """Read-only week summary, restricted to the existing supervisor role."""
    from helpers.management import build_management_overview
    codigo = request.args.get('semana', '').strip()
    semana = SemanaConfig.query.filter_by(codigo=codigo).first() if codigo else get_semana_ativa()
    if codigo and not semana:
        abort(404)
    return render_template('management_overview.html', semana=semana,
                           semanas=semanas_chronological(SemanaConfig),
                           overview=build_management_overview(semana))


def _export_schedule_label(week):
    release = latest_release(week.codigo)
    if published_read() and release:
        return f'{week.label} — Publicada v{release.version}'
    return f'{week.label} — RASCUNHO (não comunicar à equipa)'


@app.route('/api/publicacao/<codigo>')
@login_required
def api_publicacao_info(codigo):
    if not SemanaConfig.query.filter_by(codigo=codigo).first():
        abort(404)
    return jsonify(ok=True, **publication_info(codigo, include_draft=is_supervisor()))


@app.route('/api/publicacao/<codigo>', methods=['POST'])
@supervisor_required
def api_publicacao_publish(codigo):
    value = snapshot(codigo)
    if not value:
        return jsonify(ok=False, erro='Semana não encontrada.'), 404
    data = request.get_json(silent=True) or {}
    previous = latest_release(codigo)
    version = previous.version if previous else 0
    if data.get('confirmar') is not True:
        return jsonify(ok=False, erro='Confirmar explicitamente a publicação.'), 400
    if (type(data.get('versao_esperada')) is not int or data['versao_esperada'] != version
            or data.get('rascunho_hash') != fingerprint(value)):
        return jsonify(ok=False, erro='A escala foi alterada. Atualizar a revisão antes de publicar.'), 409
    reason = str(data.get('motivo') or '').strip()[:300]
    if not reason:
        return jsonify(ok=False, erro='Indicar o motivo e o plano de comunicação.'), 400
    release = release_week(codigo, current_user_sigla(), reason)
    db.session.commit()
    audit('escala_publicada', target=codigo, detail=f'versao={release.version}')
    return jsonify(ok=True, versao=release.version)


@app.route('/escala/publicacao/<codigo>')
@login_required
def publication_page(codigo):
    week = schedule_semana().query.filter_by(codigo=codigo).first()
    if not week:
        return render_template('erro.html', msg='Esta escala ainda não foi publicada.'), 404
    return render_template('publicacao.html', semana_obj=week,
                           publication=publication_info(codigo, include_draft=is_supervisor()))


@app.context_processor
def inject_publication():
    if not current_user_sigla() or request.endpoint not in {
            'index', 'escala', 'supervisor_escala', 'minha_escala', 'equipa', 'escala_descargas'}:
        return {}
    code = request.args.get('semana', '').strip() if request.endpoint != 'index' else ''
    if not code:
        week = get_semana_ativa() if is_supervisor() else get_semana_for_employee()
        code = week.codigo if week else ''
    return {'schedule_publication': publication_info(code, include_draft=is_supervisor())}


install_schedule_audit(app)


@app.after_request
def mark_schedule_exports(response):
    if request.endpoint in {'exportar_pdf', 'exportar_pdf_loja', 'exportar_pdf_individual', 'exportar_horas_csv'} and response.status_code == 200:
        code = request.args.get('semana')
        week = ((get_semana_ativa() if is_supervisor() else get_semana_for_employee())
                if not code else None)
        code = code or (week.codigo if week else '')
        release = latest_release(code)
        label = f'v{release.version}' if published_read() and release else 'RASCUNHO'
        response.headers['X-Amorino-Schedule-Version'] = label
        response.headers['Cache-Control'] = 'no-store, private'
        disposition = response.headers.get('Content-Disposition', '')
        response.headers['Content-Disposition'] = re.sub(r'\.(pdf|csv)(?="?$)', rf'-{label}.\1', disposition)
    return response


if __name__ == '__main__':
    # SECURITY: debug mode must never be the default. Flask's debug server
    # exposes the Werkzeug interactive console, which grants arbitrary code
    # execution to anyone who can reach a traceback. `python app.py` is the
    # obvious way to start this thing, so the safe value has to be the one you
    # get without thinking. Opt in explicitly for local work:
    #     AMORINO_DEBUG=1 python app.py
    _debug = os.environ.get('AMORINO_DEBUG', '').lower() in ('1', 'true', 'yes')
    if _debug:
        log.warning('DEBUG MODE ON — interactive debugger exposed. '
                    'Never do this on a reachable host.')
    # Bind to loopback unless told otherwise; the dev server should not be
    # listening on every interface by accident.
    _host = os.environ.get('AMORINO_HOST', '127.0.0.1')
    app.run(debug=_debug, host=_host, port=int(os.environ.get('AMORINO_PORT', 5000)))
