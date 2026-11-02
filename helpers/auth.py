"""Auth helpers: PIN hashing/verification, login rate limiting, CSRF tokens.

These are mostly pure functions, but `_login_key`, `get_csrf_token`, and
`_csrf_token_from_request` access the Flask `request` and `session` globals.
That's fine — those globals are set automatically by Flask during request
handling, so any code path that calls them must be inside a request context.

The actual Flask hooks (@app.before_request, @app.context_processor) stay in
app.py because they bind to the app instance.
"""

import hmac
import hashlib
import os
import secrets
import time
from collections import defaultdict, deque

from flask import request, session
from werkzeug.security import generate_password_hash, check_password_hash

from config import (
    ADMIN_SIGLA, LOGIN_WINDOW, LOGIN_MAX_DEFAULT, LOGIN_MAX_ADMIN,
    LOGIN_GLOBAL_WINDOW, LOGIN_GLOBAL_MAX, WEAK_PINS,
)


# ── PIN hashing ────────────────────────────────────────────────────────────────

def _looks_hashed(stored: str) -> bool:
    """Heuristic: does this look like a werkzeug-style salted hash?
    Format is e.g. 'pbkdf2:sha256:600000$saltsalt$hashhash'.
    """
    if not stored:
        return False
    return ('$' in stored
            and stored.startswith(('pbkdf2:', 'scrypt:', 'argon2')))


def hash_pin(pin: str) -> str:
    """Return a salted hash for a PIN."""
    return generate_password_hash(str(pin or ''), method='pbkdf2:sha256')


def verify_pin(stored: str, supplied: str) -> bool:
    """Check a supplied PIN against the stored value.
    Accepts both hashed and (legacy) plaintext storage. Always constant-time.
    """
    stored = str(stored or '')
    supplied = str(supplied or '')
    if _looks_hashed(stored):
        try:
            return check_password_hash(stored, supplied)
        except Exception:
            return False
    return hmac.compare_digest(stored, supplied)


def safe_pin_eq(a: str, b: str) -> bool:
    """Back-compat shim — prefer verify_pin in new code."""
    return verify_pin(a, b)


def invitation_digest(token: str) -> str:
    """Digest a 256-bit random credential; never persist the usable secret.

    SHA-256 is appropriate for a uniformly random token (unlike a human PIN,
    which must continue using salted PBKDF2).
    """
    return hashlib.sha256(str(token or '').encode('utf-8')).hexdigest()


def _is_sequential(pin: str) -> bool:
    """True if pin is a run of consecutive digits up or down (1234, 4321, 6789)."""
    if len(pin) < 4 or not pin.isdigit():
        return False
    deltas = {ord(b) - ord(a) for a, b in zip(pin, pin[1:])}
    return deltas == {1} or deltas == {-1}


def is_weak_pin(pin: str, sigla: str = '') -> bool:
    """Reject trivially guessable new PINs (existing credentials grandfathered).

    A PIN is weak if it is in the blocklist, is all the same character (0000,
    aaaa), is a simple ascending/descending digit run (1234, 4321), or equals
    the user's own sigla. Keep this in sync with the SECURITY.md PIN policy.
    """
    p = str(pin or '').strip()
    if not p:
        return True
    low = p.lower()
    if low in WEAK_PINS:
        return True
    if len(set(p)) == 1:            # all identical chars: 0000, 1111, aaaa
        return True
    if _is_sequential(p):           # 1234, 2345, 9876 ...
        return True
    if sigla and low == sigla.strip().lower():
        return True
    return False


# ── Login rate limiting ────────────────────────────────────────────────────────
# In-memory sliding-window counters. Not as robust as Flask-Limiter, but zero
# deps and sufficient for an app with a small fixed user base.

LOGIN_ATTEMPTS        = defaultdict(deque)   # (sigla, ip) -> timestamps
LOGIN_ATTEMPTS_GLOBAL = defaultdict(deque)   # sigla -> timestamps

# Kept for back-compat with earlier code paths
LOGIN_MAX = LOGIN_MAX_DEFAULT

# Hard ceiling on how many distinct (sigla, ip) buckets we keep. The counters
# are keyed by attacker-supplied values, so without a cap a flood of unique
# siglas/IPs would grow this dict until the process runs out of memory. When we
# hit the ceiling we drop the buckets that have already aged out; if that isn't
# enough we clear the map wholesale. Losing per-IP history is a far smaller
# problem than dying, and the global per-sigla counter still applies.
LOGIN_ATTEMPTS_MAX_KEYS = 10_000


def _client_ip() -> str:
    """The caller's IP, for rate-limit keying.

    SECURITY: X-Forwarded-For is attacker-controlled unless a reverse proxy we
    trust overwrites it. Honouring it unconditionally lets anyone defeat the
    per-IP login throttle by sending a fresh header value on every attempt, so
    it is only read when the operator opts in with AMORINO_TRUST_PROXY=1 (set
    it only when the app genuinely sits behind a proxy that rewrites XFF).
    """
    if os.environ.get('AMORINO_TRUST_PROXY', '').lower() in ('1', 'true', 'yes'):
        fwd = request.headers.get('X-Forwarded-For') or ''
        if fwd:
            # Right-most entry is the one our own proxy appended; the earlier
            # hops are whatever the client claimed.
            return fwd.split(',')[-1].strip()[:64]
    return (request.remote_addr or '?')[:64]


def _login_key(sigla: str) -> tuple:
    return (sigla.upper(), _client_ip())


def _login_global_key(sigla: str) -> str:
    return sigla.upper()


def _prune_map(m: dict, window: int, now: float) -> None:
    """Drop aged-out buckets from one counter map, then hard-clear if the
    ceiling still isn't respected."""
    if len(m) < LOGIN_ATTEMPTS_MAX_KEYS:
        return
    for k in [k for k, dq in m.items() if not dq or (now - dq[-1]) > window]:
        m.pop(k, None)
    if len(m) >= LOGIN_ATTEMPTS_MAX_KEYS:
        m.clear()


def _prune_attempts(now: float) -> None:
    """Keep both counter maps bounded. Both are keyed on attacker-supplied
    values (sigla, and IP when a proxy is trusted), so neither may grow freely."""
    _prune_map(LOGIN_ATTEMPTS, LOGIN_WINDOW, now)
    _prune_map(LOGIN_ATTEMPTS_GLOBAL, LOGIN_GLOBAL_WINDOW, now)


def login_rate_limited(sigla: str) -> bool:
    """Returns True if this caller has exceeded the login attempt budget.

    Read-only: checking must not create a bucket, or every probe with a novel
    sigla would leave a permanent entry behind.
    """
    if not sigla:
        return False
    now = time.time()
    # Per (sigla, ip)
    dq = LOGIN_ATTEMPTS.get(_login_key(sigla))
    if dq:
        while dq and (now - dq[0]) > LOGIN_WINDOW:
            dq.popleft()
        cap = LOGIN_MAX_ADMIN if sigla.upper() == ADMIN_SIGLA else LOGIN_MAX_DEFAULT
        if len(dq) >= cap:
            return True
    # Global per-sigla
    gdq = LOGIN_ATTEMPTS_GLOBAL.get(_login_global_key(sigla))
    if gdq:
        while gdq and (now - gdq[0]) > LOGIN_GLOBAL_WINDOW:
            gdq.popleft()
        if len(gdq) >= LOGIN_GLOBAL_MAX:
            return True
    return False


def login_record_failure(sigla: str) -> None:
    if not sigla:
        return
    now = time.time()
    _prune_attempts(now)
    LOGIN_ATTEMPTS[_login_key(sigla)].append(now)
    gdq = LOGIN_ATTEMPTS_GLOBAL[_login_global_key(sigla)]
    while gdq and (now - gdq[0]) > LOGIN_GLOBAL_WINDOW:
        gdq.popleft()
    gdq.append(now)


def login_clear_attempts(sigla: str) -> None:
    if not sigla:
        return
    LOGIN_ATTEMPTS.pop(_login_key(sigla), None)
    LOGIN_ATTEMPTS_GLOBAL.pop(_login_global_key(sigla), None)


# ── CSRF tokens ────────────────────────────────────────────────────────────────
# Hand-rolled double-submit pattern. The @before_request hook that enforces it
# lives in app.py — this file only provides the helpers.

def get_csrf_token() -> str:
    """Get-or-create the CSRF token for the current session."""
    tok = session.get('_csrf')
    if not tok:
        tok = secrets.token_urlsafe(32)
        session['_csrf'] = tok
    return tok


def csrf_token_from_request() -> str:
    """Extract the supplied CSRF token from form data, JSON body, or header."""
    if request.is_json:
        try:
            body = request.get_json(silent=True) or {}
            if isinstance(body, dict) and body.get('csrf_token'):
                return str(body['csrf_token'])
        except Exception:
            pass
    return (request.headers.get('X-CSRF-Token')
            or request.form.get('csrf_token')
            or request.values.get('csrf_token')
            or '')


# Back-compat alias
_csrf_token_from_request = csrf_token_from_request
