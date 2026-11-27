"""
Token status check: ask the token issuer whether a verified access token is
still active, caching an ``active`` answer for at most 30 seconds and a
``revoked`` one until a minute past the token's expiry, and at least a minute.

A resource server that validates JWTs on its own (signature and expiry) cannot
see a revocation recorded at the issuer, so a revoked token keeps working until
it expires. ``require_auth`` closes that gap: after ``validate_jwt`` succeeds it
calls :func:`should_reject_token`, which asks the issuer about the token's
``jti`` on a cache miss:

- 200 ``{"active": true}``: cached as ``active`` until ``t0 + 30`` seconds, where
  t0 is when the check started, never past the token's ``exp``, and only if no
  entry exists, so a slow positive answer never replaces a newer ``revoked``.
- 200 ``{"active": false}``: cached as ``revoked``, overwriting, until 60
  seconds past the token's ``exp``, and at least 60 seconds.
- Anything else: ``unavailable``, and nothing is cached. When the outcome says
  the issuer or the path to it failed (see :func:`_issuer_failed`), a 10 second
  breaker makes cache misses skip the call.

The cache, the breaker and this process's own client-credentials token live in
the process (one warm Lambda container), with the cache bounded to 10,000
entries. The whole check, the token fetch included, runs inside a 2 second
wall-clock deadline. It is off unless ``TOKEN_STATUS_URL`` is set, and it never
raises.

HTTP is a minimal HTTP/1.1 exchange with ``Connection: close`` over a socket
(TLS for https), standard library only. The socket timeout is set to the time
left before the deadline ahead of every receive, so a server that stalls or
drips bytes cannot hold a request past it.
"""

import json
import logging
import math
import os
import random
import re
import socket
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Tuple, Union
from urllib.parse import urlencode, urlsplit

from .secrets_helper import get_secret

log = logging.getLogger(__name__)

ACTIVE_TTL_SECONDS = 30
"""Seconds an ``active`` answer is trusted, counted from the start of its check."""

REVOKED_GRACE_SECONDS = 60
"""Seconds past the token's ``exp`` a ``revoked`` answer is kept."""

BREAKER_SECONDS = 10
"""Seconds cache misses skip the call after the issuer, or the path to it, failed."""

DEADLINE_SECONDS = 2.0
"""Wall-clock budget for one check: machine token, status call and body."""

CONNECT_TIMEOUT_SECONDS = 1.0
"""Cap on each TCP connection attempt, inside the deadline."""

MAX_BODY_BYTES = 1024
"""The status body is read up to this many bytes; a longer one is not an answer."""

MAX_TOKEN_BODY_BYTES = 16 * 1024
"""Cap on the token endpoint's body. An RS256 access token alone can pass 1 KB."""

MAX_HEADER_BYTES = 16 * 1024
"""Cap on a response's status line and headers."""

CACHE_MAX_ENTRIES = 10_000
"""Answers kept per process; the least recently used goes first."""

TOKEN_REFRESH_MARGIN_SECONDS = 86_400
"""The machine token is refetched this long before it expires."""

SAMPLE_RATE = 0.01
"""Fraction of checks logged with result, cache hit or miss and latency."""

STARTUP_LOG_INTERVAL = 3600
"""Seconds between repeats of the startup line from one process."""

WARNING_LOG_INTERVAL = 60
"""Seconds between repeats of each rate-limited warning from one process."""

MODE_OFF = "off"
MODE_SHADOW = "shadow"
MODE_ENFORCE = "enforce"
FAIL_OPEN = "open"
FAIL_CLOSED = "closed"

USER_AGENT = "nui-python-shared-utils token-status"

_MIN_CALL_SECONDS = 0.01
"""Less budget than this cannot carry a call."""

_FEATURE = "token_revocation"
_LOGGED_CLAIMS = ("jti", "sub", "user_id", "aud", "grant_type")
_STATUS_LINE = re.compile(rb"HTTP/1\.[01] ([0-9]{3})(?: |$)")
_CHUNK_SIZE = re.compile(rb"[0-9A-Fa-f]{1,8}")
# RFC 6750 b64token: nothing that could end a header line.
_BEARER_TOKEN = re.compile(r"[A-Za-z0-9\-._~+/]+=*")


class TokenStatus(str, Enum):
    """The issuer's answer about one token, as the check sees it."""

    ACTIVE = "active"
    REVOKED = "revoked"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class TokenStatusCheck:
    """
    One check's result plus what the guard logs about it.

    ``reason`` says why the status is ``unavailable``: ``not_configured``,
    ``missing_jti``, ``breaker_open``, ``machine_token``, ``deadline``,
    ``connection``, ``oversized``, ``http_<status>``, ``malformed`` or
    ``unexpected``. ``detail`` narrows ``machine_token`` down (for example
    ``not_configured`` or ``http_401``) and never carries a credential.
    """

    status: TokenStatus
    cache_hit: bool
    reason: Optional[str]
    latency_ms: float
    detail: Optional[str] = None


@dataclass(frozen=True)
class HttpResponse:
    """A response from :func:`send`. ``body`` is empty unless the status is 200."""

    status: int
    body: bytes


class TransportError(Exception):
    """The exchange failed: DNS, connection, TLS, the deadline, or a reply that is not HTTP/1.x."""


class OversizedResponse(Exception):
    """The response body is longer than the caller's cap."""


class _MachineTokenError(Exception):
    """No machine token. ``detail`` says why, without any credential."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class _Entry:
    status: TokenStatus
    expires_at: float
    active_until: float = 0.0


@dataclass(frozen=True)
class _MachineToken:
    value: str = field(repr=False)
    refresh_at: float
    expires_at: float


# Per-process state, shared by every check in a warm container.
_lock = threading.Lock()
_cache: "OrderedDict[str, _Entry]" = OrderedDict()
_breaker_until = 0.0
_machine_token: Optional[_MachineToken] = None
_last_logged: Dict[str, float] = {}
_tls_context_cache: Any = None


def _now() -> float:
    """Wall-clock seconds, the clock ``exp`` is on. Deadlines use ``time.monotonic()``."""
    return time.time()


def reset_token_status_state() -> None:
    """Forget every cached answer, the breaker, the machine token and the log windows. For tests."""
    global _breaker_until, _machine_token
    with _lock:
        _cache.clear()
        _breaker_until = 0.0
        _machine_token = None
        _last_logged.clear()


# ---------------------------------------------------------------------------
# Guard: mode, fail setting and logging
# ---------------------------------------------------------------------------


def should_reject_token(claims: Mapping[str, Any]) -> bool:
    """
    Whether to refuse a token whose signature and expiry are already verified.

    Reads ``TOKEN_STATUS_URL``, ``TOKEN_REVOCATION_MODE`` (``off``, ``shadow``,
    ``enforce``; default ``off``) and ``TOKEN_REVOCATION_FAIL`` (``open``,
    ``closed``; default ``open``) on each call.

    - URL unset, or mode ``off``: False, with no cache read, no call and no token fetch.
    - ``shadow``: checks and logs ``would reject``, returns False.
    - ``enforce``: True for ``revoked``, and for ``unavailable`` when fail is ``closed``.

    An error after the settings are read is an ``unavailable`` result, which the
    fail setting decides, and a logging error changes nothing. Never raises.
    """
    try:
        return _decide(claims)
    except Exception:
        return False


def _decide(claims: Mapping[str, Any]) -> bool:
    url = os.environ.get("TOKEN_STATUS_URL", "").strip()
    mode, mode_valid = _setting("TOKEN_REVOCATION_MODE", (MODE_OFF, MODE_SHADOW, MODE_ENFORCE), MODE_OFF)

    if not url:
        if mode != MODE_OFF:
            _warn_not_configured()
        return False

    if mode == MODE_OFF:
        if not mode_valid:
            _warn_invalid_settings()
        return False

    fail, fail_valid = _setting("TOKEN_REVOCATION_FAIL", (FAIL_OPEN, FAIL_CLOSED), FAIL_OPEN)
    if not fail_valid:
        _warn_invalid_settings()

    raw_jti = claims.get("jti")
    jti = raw_jti if isinstance(raw_jti, str) else ""

    # Everything from here to the decision is guarded, so an unexpected error is an
    # `unavailable` result that the fail setting decides, never a silent admit.
    try:
        check = _check(url, jti, _exp_seconds(claims.get("exp")))
    except Exception:
        check = TokenStatusCheck(TokenStatus.UNAVAILABLE, False, "unexpected", 0.0)

    if not check.cache_hit:
        _quietly(_announce, mode, fail, url)

    if check.status is TokenStatus.REVOKED:
        reject = True
    elif check.status is TokenStatus.UNAVAILABLE:
        reject = fail == FAIL_CLOSED
    else:
        reject = False

    _quietly(_report, check, reject, mode, fail, jti, claims)

    return reject and mode == MODE_ENFORCE


def _exp_seconds(raw: Any) -> float:
    """
    The ``exp`` claim as float seconds. A value that is not a number, or is NaN,
    counts as 0 (already expired), so no ``active`` answer is cached for it. An
    integer too large for a float is infinity, which ``validate_jwt`` accepts.
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0.0
    try:
        value = float(raw)
    except OverflowError:
        return math.inf if raw > 0 else -math.inf
    return 0.0 if math.isnan(value) else value


def _quietly(fn: Callable[..., Any], *args: Any) -> None:
    """Run telemetry that must not change whether a token is admitted."""
    try:
        fn(*args)
    except Exception:
        pass


def _setting(name: str, allowed: Tuple[str, ...], default: str) -> Tuple[str, bool]:
    """The value to use, and whether the configured one was recognised. Case-insensitive."""
    raw = os.environ.get(name)
    value = (raw or "").strip().lower()
    if value == "":
        return default, True
    if value in allowed:
        return value, True
    return default, False


def _report(check: TokenStatusCheck, reject: bool, mode: str, fail: str, jti: str, claims: Mapping[str, Any]) -> None:
    context: Dict[str, Any] = {
        "feature": _FEATURE,
        "result": check.status.value,
        "reason": check.reason,
        "mode": mode,
        "fail": fail,
    }
    enforcing = mode == MODE_ENFORCE

    if reject:
        _log(
            logging.WARNING,
            "Token revocation: rejected" if enforcing else "Token revocation: would reject",
            {
                **context,
                "action": "rejected" if enforcing else "would_reject",
                **_claim_fields(jti, claims),
                "validator": os.environ.get("AWS_LAMBDA_FUNCTION_NAME"),
            },
        )

    if check.status is TokenStatus.UNAVAILABLE:
        breaker_open = check.reason == "breaker_open"
        if _first_in_window("breaker_open" if breaker_open else "unavailable", WARNING_LOG_INTERVAL):
            fields = {
                **context,
                "action": "breaker_open" if breaker_open else "unavailable",
                "admitted": not (reject and enforcing),
            }
            if check.detail is not None:
                fields["detail"] = check.detail
            _log(
                logging.WARNING,
                (
                    "Token revocation: breaker open, status not checked"
                    if breaker_open
                    else "Token revocation: status unavailable"
                ),
                fields,
            )

    if _sampled():
        _log(
            logging.INFO,
            "Token revocation: check",
            {
                **context,
                "action": "check",
                "cache.hit": check.cache_hit,
                "duration_ms": round(check.latency_ms, 3),
            },
        )


def _announce(mode: str, fail: str, url: str) -> None:
    """The startup line: first check per process that misses the cache, then hourly, and after a settings change."""
    if not _first_in_window(f"startup:{mode}:{fail}", STARTUP_LOG_INTERVAL):
        return
    try:
        url_host = urlsplit(url).hostname
    except ValueError:
        url_host = None
    _log(
        logging.INFO,
        "Token revocation: check enabled",
        {
            "feature": _FEATURE,
            "action": "startup",
            "mode": mode,
            "fail": fail,
            "url_host": url_host,
            "sample_rate": SAMPLE_RATE,
        },
    )


def _warn_invalid_settings() -> None:
    if not _first_in_window("invalid_setting", WARNING_LOG_INTERVAL):
        return
    _log(
        logging.WARNING,
        "Token revocation: unrecognised setting, using the default",
        {
            "feature": _FEATURE,
            "action": "invalid_setting",
            "mode_setting": os.environ.get("TOKEN_REVOCATION_MODE"),
            "fail_setting": os.environ.get("TOKEN_REVOCATION_FAIL"),
        },
    )


def _warn_not_configured() -> None:
    if not _first_in_window("not_configured", WARNING_LOG_INTERVAL):
        return
    _log(
        logging.WARNING,
        "Token revocation: mode set without TOKEN_STATUS_URL, check off",
        {
            "feature": _FEATURE,
            "action": "not_configured",
            "mode_setting": os.environ.get("TOKEN_REVOCATION_MODE"),
        },
    )


def _claim_fields(jti: str, claims: Mapping[str, Any]) -> Dict[str, Any]:
    fields: Dict[str, Any] = {}
    for claim in _LOGGED_CLAIMS:
        value = claims.get(claim)
        fields[claim] = value if isinstance(value, (str, int, float, bool, list)) else None
    fields["jti"] = jti
    return fields


def _sampled() -> bool:
    if SAMPLE_RATE <= 0:
        return False
    return SAMPLE_RATE >= 1 or random.random() < SAMPLE_RATE


def _first_in_window(name: str, seconds: float) -> bool:
    """True the first time this process asks for ``name`` in each window."""
    now = _now()
    with _lock:
        last = _last_logged.get(name)
        if last is not None and now - last < seconds:
            return False
        _last_logged[name] = now
        return True


def _log(level: int, message: str, fields: Dict[str, Any]) -> None:
    try:
        log.log(level, message, extra=fields)
    except Exception:
        pass  # Logging must not change whether the token is admitted.


# ---------------------------------------------------------------------------
# Client: cache, breaker, call
# ---------------------------------------------------------------------------


def check_token_status(jti: str, exp: float) -> TokenStatusCheck:
    """
    Ask whether a token is still active, through the cache. For callers that
    apply their own policy; ``require_auth`` goes through :func:`should_reject_token`.

    Call it only after the token's signature and expiry are verified. With
    ``TOKEN_STATUS_URL`` unset the result is ``unavailable`` (``not_configured``)
    with no call. Never raises.
    """
    try:
        return _check(os.environ.get("TOKEN_STATUS_URL", "").strip(), jti, exp)
    except Exception:
        return TokenStatusCheck(TokenStatus.UNAVAILABLE, False, "unexpected", 0.0)


def _check(url: str, jti: str, exp: float) -> TokenStatusCheck:
    started = time.monotonic()
    try:
        status, cache_hit, reason, detail = _resolve(url, jti, exp, started + DEADLINE_SECONDS)
    except Exception:
        status, cache_hit, reason, detail = TokenStatus.UNAVAILABLE, False, "unexpected", None
    return TokenStatusCheck(status, cache_hit, reason, (time.monotonic() - started) * 1000.0, detail)


def _resolve(url: str, jti: str, exp: float, deadline: float) -> Tuple[TokenStatus, bool, Optional[str], Optional[str]]:
    if not url:
        return TokenStatus.UNAVAILABLE, False, "not_configured", None
    if not jti:
        return TokenStatus.UNAVAILABLE, False, "missing_jti", None

    t0 = _now()

    cached = _cached_status(jti, t0)
    if cached is not None:
        return cached, True, None, None

    if _breaker_is_open(t0):
        return TokenStatus.UNAVAILABLE, False, "breaker_open", None

    answer, detail = _ask(url, jti, deadline)

    if not isinstance(answer, bool):
        if _issuer_failed(answer):
            _open_breaker()
        return TokenStatus.UNAVAILABLE, False, answer, detail

    try:
        _remember(jti, answer, exp, t0)
    except Exception:
        pass  # A failed cache write leaves the issuer's answer intact.
    return (TokenStatus.ACTIVE if answer else TokenStatus.REVOKED), False, None, None


def _cached_status(jti: str, now: float) -> Optional[TokenStatus]:
    """A cached ``revoked``, or a cached ``active`` whose active_until is still ahead. Anything else is a miss."""
    with _lock:
        entry = _cache.get(jti)
        if entry is None:
            return None
        if entry.expires_at <= now:
            del _cache[jti]
            return None
        _cache.move_to_end(jti)
    if entry.status is TokenStatus.REVOKED:
        return TokenStatus.REVOKED
    if entry.status is TokenStatus.ACTIVE and entry.active_until > now:
        return TokenStatus.ACTIVE
    return None


def _remember(jti: str, active: bool, exp: float, t0: float) -> None:
    now = _now()

    if active:
        if exp - now <= 0:
            return
        # Both bounds count from t0, so the entry is gone by active_until and
        # the first miss after it can store a fresh answer.
        ttl = math.floor(min(t0 + ACTIVE_TTL_SECONDS, exp) - now)
        if ttl <= 0:
            return
        entry = _Entry(TokenStatus.ACTIVE, now + ttl, t0 + ACTIVE_TTL_SECONDS)
        with _lock:
            existing = _cache.get(jti)
            if existing is not None and existing.expires_at > now:
                return  # Set-if-absent: never replace a newer revoked.
            _store(jti, entry)
        return

    ttl = max(REVOKED_GRACE_SECONDS, exp + REVOKED_GRACE_SECONDS - now)
    # A token that never expires keeps its revoked entry until the LRU bound evicts it.
    expires_at = now + math.ceil(ttl) if math.isfinite(ttl) else math.inf
    with _lock:
        _store(jti, _Entry(TokenStatus.REVOKED, expires_at))


def _store(jti: str, entry: _Entry) -> None:
    """Caller holds ``_lock``."""
    _cache[jti] = entry
    _cache.move_to_end(jti)
    while len(_cache) > CACHE_MAX_ENTRIES:
        _cache.popitem(last=False)


def _breaker_is_open(now: float) -> bool:
    with _lock:
        return _breaker_until > now


def _open_breaker() -> None:
    global _breaker_until
    with _lock:
        _breaker_until = _now() + BREAKER_SECONDS


def _issuer_failed(reason: str) -> bool:
    """
    Whether a reason says the issuer, or the path to it, failed, so the next
    miss would most likely fail the same way: a connection failure or a timeout
    on the status call, a 5xx, a 429, a redirect or any other status that is
    neither 200 nor a 4xx, a 200 that is no answer (``malformed``,
    ``oversized``), and a 401 after the retry. These open the breaker.

    The second 401 follows a fresh token, so the issuer refuses this service's
    credential, and every check in the process would end the same way, each
    costing a client-credentials grant. An outcome that ends before the status
    call (no machine token, the budget spent) or that is about this one token
    (any other 4xx) opens nothing.

    A function of the reason alone, so a logged reason says whether the breaker opened.
    """
    if reason in ("connection", "malformed", "oversized"):
        return True
    if not reason.startswith("http_"):
        return False
    code = int(reason[len("http_") :])
    return code in (401, 429) or code < 400 or code >= 500


def _ask(url: str, jti: str, deadline: float) -> Tuple[Union[bool, str], Optional[str]]:
    """
    Call the status route, retrying once with a new machine token if the route
    refuses this process's token (401). A 403 is no answer, like any other
    status: a new token from the same client cannot change a refusal of its
    scope, or one from something in front of the route.

    The status decides before the body cap: :func:`send` reads only a 200's
    body, so a non-200 is ``http_<status>`` whatever its size, and a 401 is
    retried whatever its size. Only a 200 over the cap is ``oversized``.

    Returns the ``active`` answer, or why there is none plus a detail.
    """
    attempt = 0
    while True:
        attempt += 1

        if deadline - time.monotonic() < _MIN_CALL_SECONDS:
            return "deadline", None

        try:
            token = _get_machine_token(deadline)
        except _MachineTokenError as e:
            return "machine_token", e.detail
        except Exception:
            return "machine_token", "unexpected"

        if deadline - time.monotonic() < _MIN_CALL_SECONDS:
            return "deadline", None

        try:
            response = send(
                "POST",
                url,
                {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                },
                json.dumps({"jti": jti}).encode("utf-8"),
                deadline,
                MAX_BODY_BYTES,
            )
        except OversizedResponse:
            return "oversized", None
        except Exception:
            return "connection", None

        if response.status == 401 and attempt == 1:
            _invalidate_machine_token(token)
            continue

        if response.status != 200:
            return f"http_{response.status}", None

        active = _read_active(response.body)
        if active is None:
            return "malformed", None
        return active, None


def _read_active(body: bytes) -> Optional[bool]:
    """Only a JSON object whose ``active`` is a boolean is an answer."""
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    active = data.get("active")
    return active if isinstance(active, bool) else None


# ---------------------------------------------------------------------------
# Machine token (client-credentials grant)
# ---------------------------------------------------------------------------


def _get_machine_token(deadline: float) -> str:
    """
    This process's token for the status route, fetched lazily and reused until
    a day before it expires. If a refetch fails while the current token is still
    valid, the current one is used, and the refetch gets only half the time left,
    so a stalled token endpoint still leaves time for the status call.
    """
    global _machine_token
    now = _now()
    with _lock:
        current = _machine_token

    if current is not None and now < current.refresh_at:
        return current.value

    budget = deadline
    if current is not None and now < current.expires_at:
        budget = time.monotonic() + (deadline - time.monotonic()) / 2

    try:
        fresh = _fetch_machine_token(budget)
    except _MachineTokenError as e:
        if current is not None and now < current.expires_at:
            if _first_in_window("token_refresh_failed", WARNING_LOG_INTERVAL):
                _log(
                    logging.WARNING,
                    "Token revocation: machine token refresh failed, using the current one",
                    {
                        "feature": _FEATURE,
                        "action": "token_refresh_failed",
                        "detail": e.detail,
                        "token_expires_at": current.expires_at,
                    },
                )
            return current.value
        raise

    with _lock:
        _machine_token = fresh
    return fresh.value


def _invalidate_machine_token(refused: str) -> None:
    """Drop the refused token, unless another check has already replaced it."""
    global _machine_token
    with _lock:
        if _machine_token is not None and _machine_token.value == refused:
            _machine_token = None


def _fetch_machine_token(deadline: float) -> _MachineToken:
    token_url = os.environ.get("TOKEN_STATUS_TOKEN_URL", "").strip()
    if not token_url:
        raise _MachineTokenError("not_configured")

    client_id, client_secret = _client_credentials(deadline)

    body = urlencode(
        {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    ).encode("ascii")

    try:
        response = send(
            "POST",
            token_url,
            {
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            body,
            deadline,
            MAX_TOKEN_BODY_BYTES,
        )
    except OversizedResponse:
        raise _MachineTokenError("oversized") from None
    except Exception:
        raise _MachineTokenError("connection") from None

    if response.status != 200:
        raise _MachineTokenError(f"http_{response.status}")

    try:
        data = json.loads(response.body.decode("utf-8"))
    except Exception:
        raise _MachineTokenError("malformed") from None

    if not isinstance(data, dict):
        raise _MachineTokenError("malformed")

    token = data.get("access_token")
    expires_in = data.get("expires_in")

    if not isinstance(token, str) or not _BEARER_TOKEN.fullmatch(token):
        raise _MachineTokenError("malformed")
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise _MachineTokenError("malformed")

    expires_at = _now() + expires_in
    return _MachineToken(token, expires_at - TOKEN_REFRESH_MARGIN_SECONDS, expires_at)


def _client_credentials(deadline: float) -> Tuple[str, str]:
    """
    The OAuth client for the machine token, in the package's usual order:
    ``TOKEN_STATUS_CLIENT_ID`` plus ``TOKEN_STATUS_CLIENT_SECRET``, else the
    ``client_id`` and ``client_secret`` fields of the Secrets Manager secret
    named by ``TOKEN_STATUS_CREDENTIALS_SECRET``. The secret is read through
    ``get_secret``, which keeps a read for 300 seconds, inside the check's deadline.
    """
    client_id = os.environ.get("TOKEN_STATUS_CLIENT_ID", "")
    client_secret = os.environ.get("TOKEN_STATUS_CLIENT_SECRET", "")
    if client_id and client_secret:
        return client_id, client_secret

    secret_name = os.environ.get("TOKEN_STATUS_CREDENTIALS_SECRET", "").strip()
    if not secret_name:
        raise _MachineTokenError("not_configured")

    try:
        secret = _call_before(deadline, lambda: get_secret(secret_name))
    except Exception:
        raise _MachineTokenError("credentials_secret") from None

    client_id = secret.get("client_id") if isinstance(secret, dict) else None
    client_secret = secret.get("client_secret") if isinstance(secret, dict) else None
    if not (isinstance(client_id, str) and client_id and isinstance(client_secret, str) and client_secret):
        raise _MachineTokenError("credentials_secret")
    return client_id, client_secret


# ---------------------------------------------------------------------------
# HTTP: one exchange, bounded by a deadline
# ---------------------------------------------------------------------------


def send(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    deadline: float,
    max_body: int,
) -> HttpResponse:
    """
    One HTTP/1.1 request with ``Connection: close`` that ends by ``deadline``
    (a ``time.monotonic()`` value), DNS included. Follows no redirects.

    The body is read only for a 200, up to ``max_body`` bytes. Raises
    :class:`TransportError` or :class:`OversizedResponse`. Tests can patch this
    function to stand in for the issuer.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        raise TransportError("invalid port") from None
    if scheme not in ("http", "https") or not parts.hostname:
        raise TransportError("unsupported URL")
    host = parts.hostname
    port = port or (443 if scheme == "https" else 80)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")

    lines = [f"{method} {target} HTTP/1.1", f"Host: {parts.netloc.rpartition('@')[2]}"]
    for name, value in headers.items():
        if "\r" in value or "\n" in value:
            raise TransportError("invalid header value")
        lines.append(f"{name}: {value}")
    lines += [f"Content-Length: {len(body)}", "Connection: close", "", ""]
    request = "\r\n".join(lines).encode("latin-1") + body

    sock = _connect(host, port, deadline)
    try:
        if scheme == "https":
            _set_timeout(sock, deadline)
            sock = _tls_context().wrap_socket(sock, server_hostname=host)
        _set_timeout(sock, deadline)
        sock.sendall(request)
        return _read_response(_Reader(sock, deadline), max_body)
    except OSError as e:
        raise TransportError(type(e).__name__) from None
    finally:
        sock.close()


def _tls_context() -> Any:
    global _tls_context_cache
    if _tls_context_cache is None:
        import ssl

        _tls_context_cache = ssl.create_default_context()
    return _tls_context_cache


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise TransportError("deadline")
    return left


def _set_timeout(sock: socket.socket, deadline: float) -> None:
    sock.settimeout(_remaining(deadline))


def _call_before(deadline: float, fn: Callable[[], Any]) -> Any:
    """
    Run ``fn`` on a daemon thread and wait for it until ``deadline``. For calls
    with no timeout of their own (``getaddrinfo``) or a longer one (boto3). A
    call still running at the deadline is left to finish on its thread.
    """
    outcome: Dict[str, Any] = {}
    done = threading.Event()

    def run() -> None:
        try:
            outcome["value"] = fn()
        except Exception as e:
            outcome["error"] = e
        finally:
            done.set()

    threading.Thread(target=run, name="token-status", daemon=True).start()
    if not done.wait(_remaining(deadline)):
        raise TransportError("deadline")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def _connect(host: str, port: int, deadline: float) -> socket.socket:
    try:
        addresses = _call_before(deadline, lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
    except TransportError:
        raise
    except Exception:
        raise TransportError("DNS lookup failed") from None

    for family, socktype, proto, _canonname, sockaddr in addresses:
        timeout = min(CONNECT_TIMEOUT_SECONDS, _remaining(deadline))
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(timeout)
            sock.connect(sockaddr)
            return sock
        except OSError:
            sock.close()
    raise TransportError("connection failed")


class _Reader:
    """Buffered reads from a socket, each receive bounded by the time left."""

    def __init__(self, sock: socket.socket, deadline: float):
        self._sock = sock
        self._deadline = deadline
        self._buffer = bytearray()
        self._eof = False

    def _fill(self) -> bool:
        if self._eof:
            return False
        _set_timeout(self._sock, self._deadline)
        chunk = self._sock.recv(4096)
        if not chunk:
            self._eof = True
            return False
        self._buffer += chunk
        return True

    def read_until(self, delimiter: bytes, limit: int) -> bytes:
        while True:
            index = self._buffer.find(delimiter)
            if index != -1:
                data = bytes(self._buffer[:index])
                del self._buffer[: index + len(delimiter)]
                return data
            if len(self._buffer) > limit:
                raise TransportError("line too long")
            if not self._fill():
                raise TransportError("connection closed early")

    def read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            if not self._fill():
                raise TransportError("connection closed early")
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def read_to_close(self, limit: int) -> bytes:
        while len(self._buffer) <= limit:
            if not self._fill():
                break
        if len(self._buffer) > limit:
            raise OversizedResponse()
        return bytes(self._buffer)


def _read_response(reader: _Reader, max_body: int) -> HttpResponse:
    head = reader.read_until(b"\r\n\r\n", MAX_HEADER_BYTES)
    lines = head.split(b"\r\n")
    match = _STATUS_LINE.match(lines[0])
    if match is None:
        raise TransportError("not an HTTP/1.x response")
    status = int(match.group(1))
    if status != 200:
        return HttpResponse(status, b"")

    framing: Dict[bytes, bytes] = {}
    for line in lines[1:]:
        name, sep, value = line.partition(b":")
        key = name.strip().lower()
        if not sep or key not in (b"content-length", b"transfer-encoding"):
            continue
        value = value.strip()
        if key in framing and framing[key] != value:
            raise TransportError("conflicting framing headers")
        framing[key] = value

    transfer_encoding = framing.get(b"transfer-encoding")
    if transfer_encoding is not None:
        if transfer_encoding.lower() != b"chunked":
            raise TransportError("unsupported transfer encoding")
        return HttpResponse(status, _read_chunked(reader, max_body))

    content_length = framing.get(b"content-length")
    if content_length is not None:
        if not content_length.isdigit():
            raise TransportError("invalid content length")
        length = int(content_length)
        if length > max_body:
            raise OversizedResponse()
        return HttpResponse(status, reader.read_exact(length))

    return HttpResponse(status, reader.read_to_close(max_body))


def _read_chunked(reader: _Reader, max_body: int) -> bytes:
    body = bytearray()
    while True:
        size_line = reader.read_until(b"\r\n", 1024)
        size_text = size_line.split(b";", 1)[0].strip()
        if not _CHUNK_SIZE.fullmatch(size_text):
            raise TransportError("invalid chunk size")
        size = int(size_text, 16)
        if size == 0:
            _read_trailers(reader)
            return bytes(body)
        if len(body) + size > max_body:
            raise OversizedResponse()
        body += reader.read_exact(size)
        if reader.read_exact(2) != b"\r\n":
            raise TransportError("invalid chunk")


def _read_trailers(reader: _Reader) -> None:
    """Read the trailer section through its empty line; a body cut before it is incomplete."""
    total = 0
    while True:
        line = reader.read_until(b"\r\n", MAX_HEADER_BYTES)
        if not line:
            return
        total += len(line) + 2
        if total > MAX_HEADER_BYTES:
            raise TransportError("trailer section too long")
