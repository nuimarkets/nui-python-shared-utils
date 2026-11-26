"""Tests for the token status check (``nui_shared_utils.token_status``) and its hook in ``require_auth``.

Most tests replace ``token_status.send`` with a fake issuer and the wall clock
with a fake one. The deadline tests run real local socket servers that never
accept, stall or drip bytes, and measure wall-clock time.
"""

import datetime
import ipaddress
import json
import logging
import socket
import ssl
import threading
import time
from types import SimpleNamespace
from typing import Any, Callable, List, Optional

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from nui_shared_utils import secrets_helper
from nui_shared_utils import token_status as ts
from nui_shared_utils.jwt_auth import AuthenticationError, check_auth, require_auth, validate_jwt
from nui_shared_utils.token_status import HttpResponse, OversizedResponse, TokenStatus, TransportError
from tests.test_jwt_auth import _sign_jwt, mock_jwt_secret, rsa_keypair  # noqa: F401  (fixtures)

STATUS_URL = "https://issuer.test/api/tokens/status"
TOKEN_URL = "https://issuer.test/oauth/token"
CLIENT_ID = "client-7"
CLIENT_SECRET = "client-secret-value-9f2c"
JTI = "a1" * 40

TOKEN_ENV = [
    "TOKEN_STATUS_URL",
    "TOKEN_STATUS_TOKEN_URL",
    "TOKEN_STATUS_CLIENT_ID",
    "TOKEN_STATUS_CLIENT_SECRET",
    "TOKEN_STATUS_CREDENTIALS_SECRET",
    "TOKEN_REVOCATION_MODE",
    "TOKEN_REVOCATION_FAIL",
    "AWS_LAMBDA_FUNCTION_NAME",
]

# A stalled check ends at the deadline; allow scheduling slack on a busy runner.
SLACK = 0.3


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    for name in TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(ts, "SAMPLE_RATE", 0.0)
    ts.reset_token_status_state()
    yield
    ts.reset_token_status_state()


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(ts, "_now", fake)
    return fake


def answer(active: Any) -> HttpResponse:
    return HttpResponse(200, json.dumps({"active": active}).encode())


def token_response(token: str, expires_in: Any = 31_536_000) -> HttpResponse:
    return HttpResponse(
        200, json.dumps({"token_type": "Bearer", "expires_in": expires_in, "access_token": token}).encode()
    )


class FakeIssuer:
    """Stands in for ``token_status.send``: a token endpoint and a status route."""

    def __init__(self):
        self.status_queue: List[Any] = []
        self.status_default: Any = answer(True)
        self.token_queue: List[Any] = []
        self.status_calls: List[dict] = []
        self.token_calls: List[dict] = []
        self.issued = 0

    def __call__(self, method, url, headers, body, deadline, max_body):
        call = {"method": method, "headers": dict(headers), "body": body, "max_body": max_body, "deadline": deadline}
        if url == TOKEN_URL:
            self.token_calls.append(call)
            if self.token_queue:
                return self._resolve(self.token_queue.pop(0), call)
            self.issued += 1
            return token_response(f"machine-token-{self.issued}")
        if url == STATUS_URL:
            self.status_calls.append(call)
            item = self.status_queue.pop(0) if self.status_queue else self.status_default
            return self._resolve(item, call)
        raise AssertionError(f"unexpected URL {url}")

    @staticmethod
    def _resolve(item, call):
        if isinstance(item, BaseException):
            raise item
        if callable(item):
            return item(call)
        return item

    def bearer(self, index: int) -> str:
        return self.status_calls[index]["headers"]["Authorization"]


def configure(monkeypatch, mode="enforce", fail="open", status_url=STATUS_URL, token_url=TOKEN_URL):
    monkeypatch.setenv("TOKEN_STATUS_URL", status_url)
    monkeypatch.setenv("TOKEN_STATUS_TOKEN_URL", token_url)
    monkeypatch.setenv("TOKEN_STATUS_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("TOKEN_STATUS_CLIENT_SECRET", CLIENT_SECRET)
    if mode is not None:
        monkeypatch.setenv("TOKEN_REVOCATION_MODE", mode)
    if fail is not None:
        monkeypatch.setenv("TOKEN_REVOCATION_FAIL", fail)


@pytest.fixture
def issuer(monkeypatch, clock):
    fake = FakeIssuer()
    monkeypatch.setattr(ts, "send", fake)
    configure(monkeypatch)
    return fake


def claims_for(clock_or_now: Any, jti: str = JTI, exp_in: float = 3600, **extra) -> dict:
    now = clock_or_now() if callable(clock_or_now) else clock_or_now
    claims = {"jti": jti, "exp": now + exp_in, "sub": "42", "user_id": 42, "aud": "client-9", "grant_type": "password"}
    claims.update(extra)
    return claims


def check(clock, jti: str = JTI, exp_in: float = 3600) -> ts.TokenStatusCheck:
    return ts.check_token_status(jti, clock.now + exp_in)


def records(caplog, message: str) -> list:
    return [r for r in caplog.records if r.getMessage() == message]


# ---------------------------------------------------------------------------
# Off unless configured
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOffUnlessConfigured:
    def test_url_unset_makes_no_call_no_cache_and_no_token_fetch(self, monkeypatch, clock, caplog):
        def no_call(*args, **kwargs):
            raise AssertionError("no request expected")

        monkeypatch.setattr(ts, "send", no_call)
        monkeypatch.setattr(ts, "get_secret", no_call)
        configure(monkeypatch, mode="enforce", fail="closed")
        monkeypatch.delenv("TOKEN_STATUS_URL")
        monkeypatch.setenv("TOKEN_STATUS_CREDENTIALS_SECRET", "issuer/client")

        assert ts.should_reject_token(claims_for(clock)) is False
        assert len(ts._cache) == 0
        assert ts._machine_token is None
        assert ts._breaker_until == 0.0

    def test_url_unset_and_nothing_else_set_logs_nothing(self, monkeypatch, clock, caplog):
        caplog.set_level(logging.DEBUG, logger="nui_shared_utils.token_status")
        monkeypatch.setattr(ts, "send", lambda *a, **k: pytest.fail("no request expected"))

        assert ts.should_reject_token(claims_for(clock)) is False
        assert caplog.records == []

    def test_mode_set_without_url_warns_once_a_minute(self, monkeypatch, clock, caplog):
        caplog.set_level(logging.DEBUG, logger="nui_shared_utils.token_status")
        monkeypatch.setenv("TOKEN_REVOCATION_MODE", "enforce")

        ts.should_reject_token(claims_for(clock))
        ts.should_reject_token(claims_for(clock))
        clock.advance(61)
        ts.should_reject_token(claims_for(clock))

        lines = records(caplog, "Token revocation: mode set without TOKEN_STATUS_URL, check off")
        assert len(lines) == 2
        assert lines[0].levelno == logging.WARNING
        assert lines[0].action == "not_configured"
        assert lines[0].mode_setting == "enforce"

    @pytest.mark.parametrize("mode", [None, "", "off", "OFF"])
    def test_mode_off_or_unset_makes_no_call(self, issuer, monkeypatch, clock, mode):
        if mode is None:
            monkeypatch.delenv("TOKEN_REVOCATION_MODE")
        else:
            monkeypatch.setenv("TOKEN_REVOCATION_MODE", mode)
        issuer.status_default = answer(False)

        assert ts.should_reject_token(claims_for(clock)) is False
        assert issuer.status_calls == []
        assert issuer.token_calls == []
        assert len(ts._cache) == 0

    def test_unrecognised_mode_is_off_and_warns(self, issuer, monkeypatch, clock, caplog):
        caplog.set_level(logging.DEBUG, logger="nui_shared_utils.token_status")
        monkeypatch.setenv("TOKEN_REVOCATION_MODE", "enforcing")
        issuer.status_default = answer(False)

        assert ts.should_reject_token(claims_for(clock)) is False
        assert issuer.status_calls == []
        (line,) = records(caplog, "Token revocation: unrecognised setting, using the default")
        assert line.mode_setting == "enforcing"
        assert line.action == "invalid_setting"

    def test_unrecognised_fail_is_open_and_warns(self, issuer, monkeypatch, clock, caplog):
        caplog.set_level(logging.DEBUG, logger="nui_shared_utils.token_status")
        monkeypatch.setenv("TOKEN_REVOCATION_FAIL", "shut")
        issuer.status_default = HttpResponse(500, b"")

        assert ts.should_reject_token(claims_for(clock)) is False
        (line,) = records(caplog, "Token revocation: unrecognised setting, using the default")
        assert line.fail_setting == "shut"

    def test_settings_are_case_insensitive(self, issuer, monkeypatch, clock):
        monkeypatch.setenv("TOKEN_REVOCATION_MODE", " Enforce ")
        monkeypatch.setenv("TOKEN_REVOCATION_FAIL", "CLOSED")
        issuer.status_default = HttpResponse(503, b"")

        assert ts.should_reject_token(claims_for(clock)) is True

    def test_validate_jwt_makes_no_status_call(self, issuer, rsa_keypair):
        private_key, _, public_key = rsa_keypair
        issuer.status_default = answer(False)
        token = _sign_jwt(private_key, {"jti": JTI, "sub": "42", "exp": time.time() + 3600})

        assert validate_jwt(token, public_key)["jti"] == JTI
        assert issuer.status_calls == []

    def test_check_token_status_without_url_is_unavailable_with_no_call(self, monkeypatch, clock):
        monkeypatch.setattr(ts, "send", lambda *a, **k: pytest.fail("no request expected"))

        result = check(clock)

        assert result.status is TokenStatus.UNAVAILABLE
        assert result.reason == "not_configured"


# ---------------------------------------------------------------------------
# Results, modes and the fail setting
# ---------------------------------------------------------------------------

ANSWERS = {"active": answer(True), "revoked": answer(False), "unavailable": HttpResponse(500, b"")}


@pytest.mark.unit
class TestModesAndFailSetting:
    @pytest.mark.parametrize("fail", ["open", "closed"])
    @pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
    @pytest.mark.parametrize("result", ["active", "revoked", "unavailable"])
    def test_decision(self, issuer, monkeypatch, clock, result, mode, fail):
        configure(monkeypatch, mode=mode, fail=fail)
        issuer.status_default = ANSWERS[result]

        rejected = ts.should_reject_token(claims_for(clock))

        expected = mode == "enforce" and (result == "revoked" or (result == "unavailable" and fail == "closed"))
        assert rejected is expected
        assert len(issuer.status_calls) == (0 if mode == "off" else 1)

    def test_shadow_writes_the_cache_as_enforce_would(self, issuer, monkeypatch, clock):
        configure(monkeypatch, mode="shadow")
        issuer.status_default = answer(False)

        assert ts.should_reject_token(claims_for(clock)) is False
        assert ts._cache[JTI].status is TokenStatus.REVOKED

        configure(monkeypatch, mode="enforce")
        assert ts.should_reject_token(claims_for(clock)) is True
        assert len(issuer.status_calls) == 1

    def test_missing_jti_is_unavailable_and_fail_decides(self, issuer, monkeypatch, clock):
        configure(monkeypatch, mode="enforce", fail="closed")
        claims = claims_for(clock)
        del claims["jti"]

        assert ts.should_reject_token(claims) is True
        assert issuer.status_calls == []

        configure(monkeypatch, mode="enforce", fail="open")
        assert ts.should_reject_token(claims) is False

    def test_non_string_jti_counts_as_missing(self, issuer, clock):
        result = ts.check_token_status("", clock.now + 60)
        assert result.reason == "missing_jti"
        assert ts.should_reject_token(claims_for(clock, jti=12345)) is False  # fail open
        assert issuer.status_calls == []

    def test_missing_jti_does_not_open_the_breaker(self, issuer, clock):
        ts.check_token_status("", clock.now + 60)

        assert check(clock).status is TokenStatus.ACTIVE
        assert len(issuer.status_calls) == 1


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCache:
    def test_two_checks_within_30_seconds_make_one_call(self, issuer, clock):
        first = check(clock)
        clock.advance(29.5)
        second = check(clock)

        assert first.status is TokenStatus.ACTIVE and first.cache_hit is False
        assert second.status is TokenStatus.ACTIVE and second.cache_hit is True
        assert len(issuer.status_calls) == 1

    def test_active_lapses_at_t0_plus_30(self, issuer, clock):
        check(clock)
        clock.advance(30)

        result = check(clock)

        assert result.cache_hit is False
        assert len(issuer.status_calls) == 2

    def test_active_until_counts_from_t0_not_from_the_write(self, issuer, clock):
        t0 = clock.now

        def slow_answer(call):
            clock.advance(5)  # the call takes 5 s
            return answer(True)

        issuer.status_queue.append(slow_answer)
        check(clock)

        entry = ts._cache[JTI]
        assert entry.active_until == t0 + 30
        assert entry.expires_at == t0 + 30  # floor(min(t0 + 30, exp) - now) from a write at t0 + 5

        clock.now = t0 + 29.9
        assert check(clock).cache_hit is True
        clock.now = t0 + 30
        assert check(clock).cache_hit is False

    def test_an_entry_whose_active_until_passed_is_a_miss(self, issuer, clock):
        ts._cache[JTI] = ts._Entry(TokenStatus.ACTIVE, expires_at=clock.now + 100, active_until=clock.now - 1)

        result = check(clock)

        assert result.cache_hit is False
        assert len(issuer.status_calls) == 1

    def test_active_entry_never_outlives_exp(self, issuer, clock):
        check(clock, exp_in=10)

        assert ts._cache[JTI].expires_at == clock.now + 10
        clock.advance(10)
        assert check(clock, exp_in=0).cache_hit is False

    def test_expired_token_skips_the_positive_write(self, issuer, clock):
        result = check(clock, exp_in=-5)  # inside validate_jwt's clock skew

        assert result.status is TokenStatus.ACTIVE
        assert JTI not in ts._cache
        check(clock, exp_in=-5)
        assert len(issuer.status_calls) == 2

    def test_positive_write_skipped_when_under_a_second_is_left(self, issuer, clock):
        check(clock, exp_in=0.5)

        assert JTI not in ts._cache

    def test_revoked_is_kept_until_exp_plus_60(self, issuer, clock):
        issuer.status_default = answer(False)
        check(clock, exp_in=100)

        assert ts._cache[JTI].expires_at == clock.now + 160
        clock.advance(159)
        assert check(clock, exp_in=-59).status is TokenStatus.REVOKED
        assert len(issuer.status_calls) == 1

    def test_revoked_is_kept_at_least_60_seconds(self, issuer, clock):
        issuer.status_default = answer(False)
        check(clock, exp_in=-20)

        assert ts._cache[JTI].expires_at == clock.now + 60

    def test_revoked_overwrites_a_live_active(self, issuer, clock):
        def active_meanwhile(call):
            # Another check for the same token stores active while this call is in flight.
            ts._remember(JTI, True, clock.now + 3600, clock.now)
            return answer(False)

        issuer.status_queue.append(active_meanwhile)
        check(clock)

        result = check(clock)

        assert result.status is TokenStatus.REVOKED and result.cache_hit is True
        assert len(issuer.status_calls) == 1

    def test_positive_answer_after_a_revoked_write_leaves_revoked(self, issuer, clock):
        def revoked_meanwhile(call):
            # Another check for the same token writes revoked while this call is in flight.
            ts._remember(JTI, False, clock.now + 3600, clock.now)
            return answer(True)

        issuer.status_queue.append(revoked_meanwhile)

        assert check(clock).status is TokenStatus.ACTIVE  # this call's own answer
        later = check(clock)
        assert later.status is TokenStatus.REVOKED and later.cache_hit is True
        assert len(issuer.status_calls) == 1

    def test_unavailable_caches_nothing(self, issuer, clock):
        issuer.status_default = HttpResponse(500, b"")
        check(clock)

        assert JTI not in ts._cache

    def test_cache_is_bounded_least_recently_used_first(self, issuer, monkeypatch, clock):
        monkeypatch.setattr(ts, "CACHE_MAX_ENTRIES", 3)
        for jti in ("j1", "j2", "j3"):
            check(clock, jti=jti)
        check(clock, jti="j1")  # a hit refreshes j1
        check(clock, jti="j4")  # evicts j2

        assert list(ts._cache) == ["j3", "j1", "j4"]
        assert len(issuer.status_calls) == 4


# ---------------------------------------------------------------------------
# Breaker
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBreaker:
    def test_an_issuer_failure_opens_the_breaker_for_10_seconds(self, issuer, clock):
        issuer.status_default = HttpResponse(502, b"")
        assert check(clock).reason == "http_502"
        issuer.status_default = answer(True)

        clock.advance(9.9)
        suppressed = check(clock, jti="other")
        clock.advance(0.1)
        resumed = check(clock, jti="other")

        assert suppressed.status is TokenStatus.UNAVAILABLE and suppressed.reason == "breaker_open"
        assert resumed.status is TokenStatus.ACTIVE
        assert len(issuer.status_calls) == 2

    def test_breaker_open_does_not_extend_the_breaker(self, issuer, clock):
        issuer.status_default = HttpResponse(500, b"")
        check(clock)
        clock.advance(5)
        check(clock)
        clock.advance(5)
        issuer.status_default = answer(True)

        assert check(clock).status is TokenStatus.ACTIVE

    def test_cached_answers_are_served_while_the_breaker_is_open(self, issuer, clock):
        check(clock, jti="cached")
        issuer.status_default = HttpResponse(500, b"")
        check(clock, jti="other")

        result = check(clock, jti="cached")

        assert result.status is TokenStatus.ACTIVE and result.cache_hit is True

    @pytest.mark.parametrize("fail, rejected", [("open", False), ("closed", True)])
    def test_breaker_open_is_unavailable_and_fail_decides(self, issuer, monkeypatch, clock, fail, rejected):
        configure(monkeypatch, mode="enforce", fail=fail)
        issuer.status_default = HttpResponse(500, b"")
        ts.should_reject_token(claims_for(clock, jti="first"))

        assert ts.should_reject_token(claims_for(clock)) is rejected
        assert len(issuer.status_calls) == 1

    @pytest.mark.parametrize(
        "outcome, reason",
        [
            (TransportError("refused"), "connection"),
            (TransportError("deadline"), "connection"),
            (HttpResponse(500, b""), "http_500"),
            (HttpResponse(503, b""), "http_503"),
            (HttpResponse(429, b""), "http_429"),
            (HttpResponse(302, b""), "http_302"),
            (HttpResponse(204, b""), "http_204"),
            (HttpResponse(200, b"not json"), "malformed"),
            (answer("false"), "malformed"),
            (OversizedResponse(), "oversized"),
            (HttpResponse(401, b""), "http_401"),
        ],
        ids=[
            "connection",
            "timeout",
            "http_500",
            "http_503",
            "http_429",
            "redirect",
            "http_204",
            "not-json",
            "non-boolean",
            "oversized",
            "second-401",
        ],
    )
    def test_an_issuer_failure_opens_the_breaker(self, issuer, clock, outcome, reason):
        issuer.status_default = outcome
        assert check(clock).reason == reason
        calls = len(issuer.status_calls)
        issuer.status_default = answer(True)

        result = check(clock, jti="other")

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, "breaker_open")
        assert len(issuer.status_calls) == calls

    def test_a_second_401_opens_the_breaker_after_one_retry(self, issuer, clock):
        issuer.status_queue = [HttpResponse(401, b""), HttpResponse(401, b"")]

        assert check(clock).reason == "http_401"
        assert check(clock, jti="other").reason == "breaker_open"
        assert len(issuer.status_calls) == 2
        assert len(issuer.token_calls) == 2

    @pytest.mark.parametrize("status", [400, 403, 404, 409, 410, 422])
    def test_a_per_token_answer_opens_nothing(self, issuer, clock, status):
        issuer.status_queue = [HttpResponse(status, b"")]
        assert check(clock).reason == f"http_{status}"

        result = check(clock, jti="other")

        assert result.status is TokenStatus.ACTIVE
        assert len(issuer.status_calls) == 2

    @pytest.mark.parametrize(
        "token_reply",
        [HttpResponse(500, b""), HttpResponse(401, b""), TransportError("refused"), HttpResponse(200, b"[]")],
        ids=["http_500", "http_401", "connection", "malformed"],
    )
    def test_no_machine_token_opens_nothing(self, issuer, clock, token_reply):
        issuer.token_queue = [token_reply]
        assert check(clock).reason == "machine_token"

        result = check(clock, jti="other")

        assert result.status is TokenStatus.ACTIVE
        assert len(issuer.status_calls) == 1

    def test_missing_token_settings_open_nothing(self, issuer, monkeypatch, clock):
        monkeypatch.delenv("TOKEN_STATUS_TOKEN_URL")
        assert check(clock).reason == "machine_token"
        configure(monkeypatch)

        assert check(clock, jti="other").status is TokenStatus.ACTIVE

    def test_a_budget_spent_before_the_call_opens_nothing(self, issuer, monkeypatch, clock):
        monkeypatch.setattr(ts, "DEADLINE_SECONDS", 0.2)

        def answer_at_the_deadline(call):
            time.sleep(max(0.0, call["deadline"] - time.monotonic()))
            return token_response("slow")

        issuer.token_queue = [answer_at_the_deadline]
        assert check(clock).reason == "deadline"
        assert issuer.status_calls == []

        result = check(clock, jti="other")

        assert result.status is TokenStatus.ACTIVE
        assert issuer.bearer(0) == "Bearer slow"

    def test_a_401_whose_retry_gets_no_machine_token_opens_nothing(self, issuer, clock):
        issuer.status_queue = [HttpResponse(401, b"")]
        issuer.token_queue = [token_response("first"), HttpResponse(500, b"")]
        assert (check(clock).reason, len(issuer.status_calls)) == ("machine_token", 1)

        result = check(clock, jti="other")

        assert result.status is TokenStatus.ACTIVE
        assert len(issuer.status_calls) == 2

    @pytest.mark.parametrize(
        "reason, opens",
        [
            ("connection", True),
            ("malformed", True),
            ("oversized", True),
            ("http_100", True),
            ("http_204", True),
            ("http_301", True),
            ("http_399", True),
            ("http_400", False),
            ("http_401", True),
            ("http_403", False),
            ("http_428", False),
            ("http_429", True),
            ("http_430", False),
            ("http_499", False),
            ("http_500", True),
            ("http_599", True),
            ("http_999", True),
            ("deadline", False),
            ("machine_token", False),
            ("unexpected", False),
            ("missing_jti", False),
            ("not_configured", False),
            ("breaker_open", False),
        ],
    )
    def test_the_reason_alone_says_whether_the_breaker_opened(self, reason, opens):
        assert ts._issuer_failed(reason) is opens


# ---------------------------------------------------------------------------
# What counts as an answer
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnswers:
    @pytest.mark.parametrize(
        "response, reason",
        [
            (HttpResponse(500, b""), "http_500"),
            (HttpResponse(404, b""), "http_404"),
            (HttpResponse(302, b""), "http_302"),
            (HttpResponse(200, b'{"revoked": true}'), "malformed"),
            (answer("false"), "malformed"),
            (answer(0), "malformed"),
            (answer(None), "malformed"),
            (HttpResponse(200, b"[false]"), "malformed"),
            (HttpResponse(200, b"false"), "malformed"),
            (HttpResponse(200, b"not json"), "malformed"),
            (HttpResponse(200, b"\xff\xfe"), "malformed"),
            (HttpResponse(200, b"[" * 1000 + b"]" * 1000), "malformed"),
            (HttpResponse(200, b""), "malformed"),
        ],
    )
    def test_only_a_200_with_a_boolean_active_is_an_answer(self, issuer, clock, response, reason):
        issuer.status_default = response

        result = check(clock)

        assert result.status is TokenStatus.UNAVAILABLE
        assert result.reason == reason

    def test_only_an_explicit_false_is_revoked(self, issuer, clock):
        issuer.status_default = HttpResponse(200, b'{"active": false, "reason": "revoked"}')

        assert check(clock).status is TokenStatus.REVOKED

    def test_connection_error_is_unavailable(self, issuer, clock):
        issuer.status_default = TransportError("refused")

        assert check(clock).reason == "connection"

    def test_oversized_body_is_unavailable(self, issuer, clock):
        issuer.status_default = OversizedResponse()

        assert check(clock).reason == "oversized"

    def test_status_request_shape(self, issuer, clock):
        check(clock)

        (call,) = issuer.status_calls
        assert call["method"] == "POST"
        assert json.loads(call["body"]) == {"jti": JTI}
        assert call["headers"]["Authorization"] == "Bearer machine-token-1"
        assert call["headers"]["Content-Type"] == "application/json"
        assert call["max_body"] == ts.MAX_BODY_BYTES == 1024

    def test_status_call_gets_the_deadline_left_after_the_token(self, issuer, clock):
        started = time.monotonic()
        check(clock)

        token_deadline = issuer.token_calls[0]["deadline"]
        assert issuer.status_calls[0]["deadline"] == token_deadline
        assert started < token_deadline <= started + ts.DEADLINE_SECONDS + 0.05


# ---------------------------------------------------------------------------
# Machine token
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMachineToken:
    def test_fetched_once_and_reused(self, issuer, clock):
        for jti in ("j1", "j2", "j3"):
            check(clock, jti=jti)

        assert len(issuer.token_calls) == 1
        assert {call["headers"]["Authorization"] for call in issuer.status_calls} == {"Bearer machine-token-1"}

    def test_token_request_is_a_client_credentials_grant(self, issuer, clock):
        check(clock)

        (call,) = issuer.token_calls
        assert call["method"] == "POST"
        assert call["headers"]["Content-Type"] == "application/x-www-form-urlencoded"
        assert call["body"] == (
            f"grant_type=client_credentials&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}".encode()
        )
        assert call["max_body"] == ts.MAX_TOKEN_BODY_BYTES

    def test_401_drops_the_token_refetches_and_retries_once(self, issuer, clock):
        issuer.status_queue = [HttpResponse(401, b""), answer(False)]

        result = check(clock)

        assert result.status is TokenStatus.REVOKED
        assert len(issuer.token_calls) == 2
        assert [issuer.bearer(0), issuer.bearer(1)] == ["Bearer machine-token-1", "Bearer machine-token-2"]
        check(clock, jti="next")
        assert issuer.bearer(2) == "Bearer machine-token-2"

    def test_a_second_401_is_unavailable(self, issuer, clock):
        issuer.status_default = HttpResponse(401, b"")

        result = check(clock)

        assert result.reason == "http_401"
        assert len(issuer.status_calls) == 2
        assert len(issuer.token_calls) == 2

    def test_403_is_unavailable_with_no_refetch(self, issuer, clock):
        issuer.status_queue = [HttpResponse(403, b"")]

        result = check(clock)

        assert result.reason == "http_403"
        assert len(issuer.status_calls) == 1
        assert len(issuer.token_calls) == 1
        check(clock)
        assert issuer.bearer(1) == "Bearer machine-token-1"  # the token was kept, and no breaker held the call
        assert len(issuer.token_calls) == 1

    def test_401_keeps_a_token_another_check_already_replaced(self, issuer, clock):
        ts._get_machine_token(time.monotonic() + 2)  # machine-token-1
        ts._invalidate_machine_token("some-older-token")

        assert ts._machine_token.value == "machine-token-1"

    @pytest.mark.parametrize(
        "token_reply, detail",
        [
            (HttpResponse(500, b""), "http_500"),
            (HttpResponse(401, b""), "http_401"),
            (TransportError("refused"), "connection"),
            (OversizedResponse(), "oversized"),
            (HttpResponse(200, b"not json"), "malformed"),
            (HttpResponse(200, b"[]"), "malformed"),
            (HttpResponse(200, b'{"expires_in": 3600}'), "malformed"),
            (token_response(""), "malformed"),
            (token_response("abc\r\nX-Injected: 1"), "malformed"),
            (token_response("abc def"), "malformed"),
            (token_response("abc", expires_in=0), "malformed"),
            (token_response("abc", expires_in=True), "malformed"),
            (token_response("abc", expires_in="3600"), "malformed"),
            (token_response("abc", expires_in=None), "malformed"),
        ],
    )
    def test_token_fetch_failure_is_unavailable(self, issuer, clock, token_reply, detail):
        issuer.token_queue = [token_reply]

        result = check(clock)

        assert result.status is TokenStatus.UNAVAILABLE
        assert result.reason == "machine_token"
        assert result.detail == detail
        assert issuer.status_calls == []

    @pytest.mark.parametrize(
        "unset", ["TOKEN_STATUS_TOKEN_URL", "TOKEN_STATUS_CLIENT_ID", "TOKEN_STATUS_CLIENT_SECRET"]
    )
    def test_missing_token_settings_are_unavailable(self, issuer, monkeypatch, clock, unset):
        monkeypatch.delenv(unset)

        result = check(clock)

        assert (result.reason, result.detail) == ("machine_token", "not_configured")
        assert issuer.token_calls == [] and issuer.status_calls == []

    def test_acquisition_raising_anything_is_unavailable(self, issuer, monkeypatch, clock):
        def boom(deadline):
            raise RuntimeError("no token")

        monkeypatch.setattr(ts, "_fetch_machine_token", boom)

        result = check(clock)

        assert (result.status, result.reason, result.detail) == (TokenStatus.UNAVAILABLE, "machine_token", "unexpected")

    def test_refetched_a_day_before_expiry(self, issuer, clock):
        issuer.token_queue = [token_response("first", expires_in=2 * 86_400)]
        check(clock, jti="j1")
        clock.advance(86_400 - 1)
        check(clock, jti="j2")
        assert len(issuer.token_calls) == 1

        clock.advance(1)
        check(clock, jti="j3")

        assert len(issuer.token_calls) == 2
        assert issuer.bearer(2) == "Bearer machine-token-1"

    def test_failed_refetch_uses_the_still_valid_token_and_warns(self, issuer, clock, caplog):
        caplog.set_level(logging.DEBUG, logger="nui_shared_utils.token_status")
        issuer.token_queue = [token_response("first", expires_in=2 * 86_400), HttpResponse(500, b"")]
        check(clock, jti="j1")
        clock.advance(86_400 + 10)

        result = check(clock, jti="j2")

        assert result.status is TokenStatus.ACTIVE
        assert issuer.bearer(1) == "Bearer first"
        (line,) = records(caplog, "Token revocation: machine token refresh failed, using the current one")
        assert line.detail == "http_500"

    def test_a_stalled_refresh_still_leaves_time_for_the_status_call(self, issuer, clock):
        issuer.token_queue = [token_response("first", expires_in=2 * 86_400)]
        check(clock, jti="j1")
        clock.advance(86_400 + 10)

        def stall_until_deadline(call):
            time.sleep(max(0.0, call["deadline"] - time.monotonic()))
            raise TransportError("timeout")

        issuer.token_queue = [stall_until_deadline]
        started = time.monotonic()
        result = check(clock, jti="j2")

        assert result.status is TokenStatus.ACTIVE
        assert issuer.bearer(1) == "Bearer first"
        assert time.monotonic() - started < ts.DEADLINE_SECONDS

    def test_a_first_fetch_gets_the_whole_budget(self, issuer, clock):
        check(clock)

        assert issuer.token_calls[0]["deadline"] == issuer.status_calls[0]["deadline"]

    def test_a_refetch_after_expiry_gets_the_whole_budget(self, issuer, clock):
        # With no valid token to fall back on, a halved budget would only fail sooner.
        issuer.token_queue = [token_response("first", expires_in=2 * 86_400)]
        check(clock, jti="j1")
        clock.advance(2 * 86_400)

        check(clock, jti="j2")

        assert issuer.token_calls[1]["deadline"] == issuer.status_calls[1]["deadline"]

    def test_failed_refetch_after_expiry_is_unavailable(self, issuer, clock):
        issuer.token_queue = [token_response("first", expires_in=2 * 86_400), HttpResponse(500, b"")]
        check(clock, jti="j1")
        clock.advance(2 * 86_400)

        result = check(clock, jti="j2")

        assert (result.reason, result.detail) == ("machine_token", "http_500")


@pytest.mark.unit
class TestClientCredentialsSecret:
    @pytest.fixture
    def from_secret(self, issuer, monkeypatch, mock_secrets_manager):
        monkeypatch.delenv("TOKEN_STATUS_CLIENT_ID")
        monkeypatch.delenv("TOKEN_STATUS_CLIENT_SECRET")
        monkeypatch.setenv("TOKEN_STATUS_CREDENTIALS_SECRET", "issuer/status-client")
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"client_id": "secret-client", "client_secret": "secret-value-31"})
        }
        return mock_secrets_manager

    def test_reads_the_named_secret(self, issuer, from_secret, clock):
        assert check(clock).status is TokenStatus.ACTIVE

        from_secret.get_secret_value.assert_called_once_with(SecretId="issuer/status-client")
        assert issuer.token_calls[0]["body"] == (
            b"grant_type=client_credentials&client_id=secret-client&client_secret=secret-value-31"
        )

    def test_env_wins_over_the_secret(self, issuer, from_secret, monkeypatch, clock):
        monkeypatch.setenv("TOKEN_STATUS_CLIENT_ID", CLIENT_ID)
        monkeypatch.setenv("TOKEN_STATUS_CLIENT_SECRET", CLIENT_SECRET)

        check(clock)

        from_secret.get_secret_value.assert_not_called()
        assert f"client_id={CLIENT_ID}".encode() in issuer.token_calls[0]["body"]

    @pytest.mark.parametrize(
        "secret", [{"client_id": "x"}, {"client_secret": "y"}, {"client_id": "", "client_secret": "y"}, {"id": 1}]
    )
    def test_incomplete_secret_is_unavailable(self, issuer, from_secret, clock, secret):
        from_secret.get_secret_value.return_value = {"SecretString": json.dumps(secret)}

        result = check(clock)

        assert (result.reason, result.detail) == ("machine_token", "credentials_secret")
        assert issuer.token_calls == []

    def test_secret_read_failure_is_unavailable(self, issuer, from_secret, clock):
        from_secret.get_secret_value.side_effect = RuntimeError("denied")

        result = check(clock)

        assert (result.reason, result.detail) == ("machine_token", "credentials_secret")

    @pytest.mark.parametrize("age, reads, client_id", [(299, 1, "secret-client"), (300, 2, "rotated-client")])
    def test_a_token_fetch_reads_a_new_secret_300_seconds_after_the_last_read(
        self, issuer, from_secret, clock, monkeypatch, age, reads, client_id
    ):
        # get_secret keeps a read for 300 seconds; each machine-token fetch asks it again.
        secrets_now = [1_000.0]
        monkeypatch.setattr(secrets_helper, "time", SimpleNamespace(monotonic=lambda: secrets_now[0]))
        issuer.token_queue = [token_response("first", expires_in=2 * 86_400)]
        check(clock, jti="j1")
        from_secret.get_secret_value.return_value = {
            "SecretString": json.dumps({"client_id": "rotated-client", "client_secret": "rotated-value-31"})
        }
        secrets_now[0] += age
        clock.advance(2 * 86_400)

        assert check(clock, jti="j2").status is TokenStatus.ACTIVE

        assert from_secret.get_secret_value.call_count == reads
        assert issuer.token_calls[1]["body"].startswith(
            f"grant_type=client_credentials&client_id={client_id}&".encode()
        )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogging:
    @pytest.fixture(autouse=True)
    def capture(self, caplog):
        caplog.set_level(logging.DEBUG, logger="nui_shared_utils.token_status")

    def test_startup_line_on_the_first_miss_then_hourly_and_after_a_settings_change(
        self, issuer, monkeypatch, clock, caplog
    ):
        message = "Token revocation: check enabled"
        ts.should_reject_token(claims_for(clock))
        ts.should_reject_token(claims_for(clock, jti="other"))
        assert len(records(caplog, message)) == 1

        line = records(caplog, message)[0]
        assert line.levelno == logging.INFO
        assert (line.feature, line.action, line.mode, line.fail) == ("token_revocation", "startup", "enforce", "open")
        assert line.url_host == "issuer.test"
        assert line.sample_rate == 0.0

        configure(monkeypatch, mode="shadow")
        ts.should_reject_token(claims_for(clock, jti="third"))
        assert len(records(caplog, message)) == 2

        clock.advance(3600)
        configure(monkeypatch, mode="enforce")
        ts.should_reject_token(claims_for(clock, jti="fourth"))
        assert len(records(caplog, message)) == 3

    def test_startup_line_waits_for_a_miss(self, issuer, clock, caplog):
        ts.should_reject_token(claims_for(clock, jti="first"))
        clock.advance(3600)
        ts._cache[JTI] = ts._Entry(TokenStatus.REVOKED, clock.now + 100)
        caplog.clear()

        ts.should_reject_token(claims_for(clock))  # a hit
        assert records(caplog, "Token revocation: check enabled") == []

        ts.should_reject_token(claims_for(clock, jti="miss"))
        assert len(records(caplog, "Token revocation: check enabled")) == 1

    def test_would_reject_in_shadow(self, issuer, monkeypatch, clock, caplog):
        configure(monkeypatch, mode="shadow")
        monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "orders-api")
        issuer.status_default = answer(False)

        ts.should_reject_token(claims_for(clock))

        (line,) = records(caplog, "Token revocation: would reject")
        assert line.levelno == logging.WARNING
        assert line.action == "would_reject"
        assert (line.jti, line.sub, line.user_id, line.aud, line.grant_type) == (JTI, "42", 42, "client-9", "password")
        assert line.validator == "orders-api"
        assert (line.result, line.reason, line.mode, line.fail) == ("revoked", None, "shadow", "open")
        assert records(caplog, "Token revocation: rejected") == []

    def test_rejected_in_enforce(self, issuer, clock, caplog):
        issuer.status_default = answer(False)

        ts.should_reject_token(claims_for(clock, aud=["a", "b"], user_id={"nested": 1}))

        (line,) = records(caplog, "Token revocation: rejected")
        assert line.action == "rejected"
        assert line.aud == ["a", "b"]
        assert line.user_id is None

    def test_unavailable_closed_in_shadow_is_a_would_reject(self, issuer, monkeypatch, clock, caplog):
        configure(monkeypatch, mode="shadow", fail="closed")
        issuer.status_default = HttpResponse(500, b"")

        assert ts.should_reject_token(claims_for(clock)) is False

        (line,) = records(caplog, "Token revocation: would reject")
        assert (line.result, line.reason) == ("unavailable", "http_500")

    def test_unavailable_warning_at_most_once_a_minute(self, issuer, clock, caplog):
        message = "Token revocation: status unavailable"
        issuer.status_default = HttpResponse(500, b"")
        ts.should_reject_token(claims_for(clock))
        clock.advance(ts.BREAKER_SECONDS)
        ts.should_reject_token(claims_for(clock))
        assert len(records(caplog, message)) == 1

        line = records(caplog, message)[0]
        assert (line.action, line.reason, line.admitted) == ("unavailable", "http_500", True)

        clock.advance(60)
        ts.should_reject_token(claims_for(clock))
        assert len(records(caplog, message)) == 2

    def test_unavailable_warning_carries_the_machine_token_detail(self, issuer, monkeypatch, clock, caplog):
        configure(monkeypatch, fail="closed")
        issuer.token_queue = [HttpResponse(401, b"")]

        ts.should_reject_token(claims_for(clock))

        (line,) = records(caplog, "Token revocation: status unavailable")
        assert (line.reason, line.detail, line.admitted) == ("machine_token", "http_401", False)

    def test_breaker_open_warning(self, issuer, clock, caplog):
        issuer.status_default = HttpResponse(500, b"")
        ts.should_reject_token(claims_for(clock, jti="first"))
        ts.should_reject_token(claims_for(clock))
        ts.should_reject_token(claims_for(clock))

        (line,) = records(caplog, "Token revocation: breaker open, status not checked")
        assert (line.action, line.reason, line.admitted) == ("breaker_open", "breaker_open", True)

    def test_sampled_check_line(self, issuer, monkeypatch, clock, caplog):
        monkeypatch.setattr(ts, "SAMPLE_RATE", 1.0)
        ts.should_reject_token(claims_for(clock))
        ts.should_reject_token(claims_for(clock))

        miss, hit = records(caplog, "Token revocation: check")
        assert miss.levelno == logging.INFO
        assert (miss.action, miss.result, getattr(miss, "cache.hit")) == ("check", "active", False)
        assert getattr(hit, "cache.hit") is True
        assert isinstance(miss.duration_ms, float)

    def test_no_check_line_at_sample_rate_zero(self, issuer, clock, caplog):
        ts.should_reject_token(claims_for(clock))

        assert records(caplog, "Token revocation: check") == []

    def test_a_failing_logger_does_not_change_the_decision(self, issuer, monkeypatch, clock):
        def broken(*args, **kwargs):
            raise RuntimeError("log sink down")

        monkeypatch.setattr(ts.log, "log", broken)
        monkeypatch.setattr(ts, "SAMPLE_RATE", 1.0)
        monkeypatch.setenv("TOKEN_REVOCATION_FAIL", "shut")  # also logs the settings warning
        issuer.status_default = answer(False)

        assert ts.should_reject_token(claims_for(clock)) is True

    def test_a_failing_logger_during_a_token_refresh_keeps_the_current_token(self, issuer, monkeypatch, clock):
        def broken(*args, **kwargs):
            raise RuntimeError("log sink down")

        issuer.token_queue = [token_response("first", expires_in=2 * 86_400), HttpResponse(500, b"")]
        check(clock, jti="j1")
        clock.advance(86_400 + 10)
        monkeypatch.setattr(ts.log, "log", broken)

        result = check(clock, jti="j2")

        assert result.status is TokenStatus.ACTIVE
        assert issuer.bearer(1) == "Bearer first"

    def test_refresh_warning_never_carries_the_token(self, issuer, clock, caplog):
        # The one log line with a live machine token in scope.
        issuer.token_queue = [token_response("machine-token-current", expires_in=2 * 86_400), HttpResponse(500, b"")]
        check(clock, jti="j1")
        clock.advance(86_400 + 10)

        check(clock, jti="j2")

        (line,) = records(caplog, "Token revocation: machine token refresh failed, using the current one")
        assert "machine-token-current" not in f"{line.getMessage()} {line.__dict__!r}"

    def test_secret_and_tokens_never_reach_a_log(self, issuer, monkeypatch, clock, caplog, mock_secrets_manager):
        monkeypatch.setattr(ts, "SAMPLE_RATE", 1.0)
        user_token = "user.bearer.token-value"
        issuer.token_queue = [
            HttpResponse(500, b""),
            token_response("machine-token-first", expires_in=2 * 86_400),
            HttpResponse(200, b"not json"),
        ]
        issuer.status_queue = [HttpResponse(401, b""), answer(False), HttpResponse(500, b"")]
        for step in range(4):
            ts.should_reject_token(claims_for(clock, jti=f"j{step}", token=user_token))
            clock.advance(86_400 + ts.BREAKER_SECONDS)

        monkeypatch.delenv("TOKEN_STATUS_CLIENT_SECRET")
        monkeypatch.setenv("TOKEN_STATUS_CREDENTIALS_SECRET", "issuer/status-client")
        mock_secrets_manager.get_secret_value.return_value = {
            "SecretString": json.dumps({"client_id": "x", "client_secret": "secret-from-store-77"})
        }
        ts.reset_token_status_state()
        ts.should_reject_token(claims_for(clock, jti="j9"))

        assert len(caplog.records) > 5
        logged = " ".join(f"{r.getMessage()} {r.__dict__!r}" for r in caplog.records)
        for value in (CLIENT_SECRET, "secret-from-store-77", "machine-token-", "user.bearer"):
            assert value not in logged


# ---------------------------------------------------------------------------
# Never raises
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNeverRaises:
    @pytest.mark.parametrize("fail, rejected", [("open", False), ("closed", True)])
    def test_an_error_inside_the_check_is_unavailable(self, issuer, monkeypatch, clock, fail, rejected):
        configure(monkeypatch, mode="enforce", fail=fail)

        def boom(*args, **kwargs):
            raise RuntimeError("bug")

        monkeypatch.setattr(ts, "_cached_status", boom)

        assert ts.check_token_status(JTI, clock.now + 60).reason == "unexpected"
        assert ts.should_reject_token(claims_for(clock)) is rejected

    def test_an_error_in_the_guard_admits(self, issuer, monkeypatch, clock):
        monkeypatch.setattr(ts, "_setting", lambda *a: (_ for _ in ()).throw(RuntimeError("bug")))

        assert ts.should_reject_token(claims_for(clock)) is False

    @pytest.mark.parametrize(
        "exp",
        [10**400, -(10**400), float("inf"), float("nan"), 1e308],
        ids=["huge-int", "huge-negative-int", "inf", "nan", "1e308"],
    )
    def test_an_extreme_exp_still_applies_the_answer(self, issuer, monkeypatch, clock, exp):
        # validate_jwt accepts a huge int and 1e308 and refuses the rest, but should_reject_token
        # is public and may be handed any of them, so the guard must not trade the issuer's
        # answer for an arithmetic error.
        configure(monkeypatch, mode="enforce", fail="open")
        issuer.status_default = answer(False)
        claims = claims_for(clock)
        claims["exp"] = exp

        assert ts.should_reject_token(claims) is True
        assert ts.should_reject_token(claims) is True
        assert len(issuer.status_calls) == 1  # the revoked answer was cached

    def test_a_failed_cache_write_keeps_the_answer(self, issuer, monkeypatch, clock):
        def broken_store(jti, entry):
            raise RuntimeError("cache write failed")

        monkeypatch.setattr(ts, "_store", broken_store)
        issuer.status_default = answer(False)

        result = check(clock)

        assert (result.status, result.reason) == (TokenStatus.REVOKED, None)
        assert ts.should_reject_token(claims_for(clock)) is True

    @pytest.mark.parametrize("broken", ["_announce", "_report"])
    def test_a_telemetry_error_does_not_change_the_decision(self, issuer, monkeypatch, clock, broken):
        def boom(*args, **kwargs):
            raise RuntimeError("telemetry bug")

        monkeypatch.setattr(ts, broken, boom)
        issuer.status_default = answer(False)

        assert ts.should_reject_token(claims_for(clock)) is True

    @pytest.mark.parametrize("exp", [None, "soon", True, [1]])
    def test_unusable_exp_counts_as_expired(self, issuer, clock, exp):
        claims = claims_for(clock)
        claims["exp"] = exp

        assert ts.should_reject_token(claims) is False
        assert JTI not in ts._cache  # an active answer for an "expired" token is not cached


# ---------------------------------------------------------------------------
# require_auth and check_auth with a signed RS256 token
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRequireAuth:
    @pytest.fixture
    def signed_event(self, rsa_keypair, mock_jwt_secret, monkeypatch):
        monkeypatch.setenv("JWT_PUBLIC_KEY_SECRET", "test/jwt-key")
        private_key, _, _ = rsa_keypair
        token = _sign_jwt(private_key, {"jti": JTI, "sub": "42", "exp": time.time() + 3600})
        return {"path": "/orders", "headers": {"Authorization": f"Bearer {token}"}}

    @pytest.fixture
    def real_clock_issuer(self, monkeypatch):
        fake = FakeIssuer()
        monkeypatch.setattr(ts, "send", fake)
        configure(monkeypatch)
        return fake

    def test_enforce_refuses_a_revoked_token(self, real_clock_issuer, signed_event):
        real_clock_issuer.status_default = answer(False)

        with pytest.raises(AuthenticationError, match="did not confirm the token is active"):
            require_auth(signed_event)
        assert json.loads(real_clock_issuer.status_calls[0]["body"]) == {"jti": JTI}

    def test_check_auth_answers_401_for_a_revoked_token(self, real_clock_issuer, signed_event):
        real_clock_issuer.status_default = answer(False)

        claims, error = check_auth(signed_event, public_paths={"/health"})

        assert claims is None
        assert error["statusCode"] == 401
        assert len(real_clock_issuer.status_calls) == 1

    def test_enforce_refuses_a_revoked_token_whose_exp_overflows_a_float(
        self, real_clock_issuer, rsa_keypair, mock_jwt_secret, monkeypatch
    ):
        monkeypatch.setenv("JWT_PUBLIC_KEY_SECRET", "test/jwt-key")
        private_key, _, _ = rsa_keypair
        token = _sign_jwt(private_key, {"jti": JTI, "sub": "42", "exp": 10**400})
        real_clock_issuer.status_default = answer(False)

        with pytest.raises(AuthenticationError, match="did not confirm the token is active"):
            require_auth({"headers": {"Authorization": f"Bearer {token}"}})

    def test_enforce_admits_an_active_token(self, real_clock_issuer, signed_event):
        assert require_auth(signed_event)["jti"] == JTI
        assert len(real_clock_issuer.status_calls) == 1

    def test_shadow_admits_a_revoked_token(self, real_clock_issuer, signed_event, monkeypatch):
        monkeypatch.setenv("TOKEN_REVOCATION_MODE", "shadow")
        real_clock_issuer.status_default = answer(False)

        assert require_auth(signed_event)["sub"] == "42"

    def test_a_token_that_fails_validation_is_never_checked(self, real_clock_issuer, signed_event):
        signed_event["headers"]["Authorization"] += "x"

        with pytest.raises(AuthenticationError, match="signature verification failed"):
            require_auth(signed_event)
        assert real_clock_issuer.status_calls == []

    def test_url_unset_leaves_require_auth_unchanged(self, signed_event, monkeypatch):
        monkeypatch.setattr(ts, "send", lambda *a, **k: pytest.fail("no request expected"))
        monkeypatch.setenv("TOKEN_REVOCATION_MODE", "enforce")

        assert require_auth(signed_event)["jti"] == JTI


# ---------------------------------------------------------------------------
# Real sockets
# ---------------------------------------------------------------------------


def _read_request(conn: socket.socket) -> Optional[dict]:
    conn.settimeout(5)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    while len(body) < length:
        body += conn.recv(4096)
    method, path, _ = lines[0].split(" ", 2)
    return {"method": method, "path": path, "headers": headers, "body": body, "raw_head": head}


class LocalServer:
    """A TCP server on 127.0.0.1 that runs ``handler(conn, request, server)`` per connection."""

    def __init__(self, handler: Optional[Callable] = None, accept: bool = True, backlog: int = 8, tls=None):
        self.handler = handler
        self.tls = tls
        self.requests: List[dict] = []
        self.closing = threading.Event()
        self._conns: List[socket.socket] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(backlog)
        self.port = self.sock.getsockname()[1]
        if accept:
            self.sock.settimeout(0.1)
            threading.Thread(target=self._accept_loop, daemon=True).start()

    def url(self, path: str = "/", scheme: str = "http") -> str:
        return f"{scheme}://127.0.0.1:{self.port}{path}"

    def _accept_loop(self):
        while not self.closing.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            if self.tls is not None:
                conn.settimeout(5)
                conn = self.tls.wrap_socket(conn, server_side=True)
                self._conns.append(conn)
            request = None
            if getattr(self.handler, "reads_request", True):
                request = _read_request(conn)
                if request is not None:
                    self.requests.append(request)
            self.handler(conn, request, self)
        except OSError:
            pass
        finally:
            conn.close()

    def hold(self):
        """Keep the connection open, saying nothing, until the test ends."""
        self.closing.wait(10)

    def close(self):
        self.closing.set()
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass
        self.sock.close()


@pytest.fixture
def servers():
    started: List[LocalServer] = []

    def start(*args, **kwargs) -> LocalServer:
        server = LocalServer(*args, **kwargs)
        started.append(server)
        return server

    yield start
    for server in started:
        server.close()


def respond(raw: bytes) -> Callable:
    def handler(conn, request, server):
        conn.sendall(raw)

    return handler


def json_reply(obj: Any, status: str = "200 OK", extra_headers: str = "") -> bytes:
    body = json.dumps(obj).encode()
    return (
        f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
        f"{extra_headers}Connection: close\r\n\r\n"
    ).encode() + body


TOKEN_REPLY = json_reply({"token_type": "Bearer", "expires_in": 31_536_000, "access_token": "local-machine-token"})


def stall(conn, request, server):
    server.hold()


def stall_after_headers(conn, request, server):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 16\r\n\r\n")
    server.hold()


def drip(raw: bytes, interval: float = 0.5) -> Callable:
    def handler(conn, request, server):
        for byte in raw:
            if server.closing.wait(interval):
                return
            conn.sendall(bytes([byte]))

    return handler


def silent(conn, request, server):
    server.hold()


silent.reads_request = False  # type: ignore[attr-defined]


def run_real(monkeypatch, status_url: str, token_url: str, mode: str = "enforce", fail: str = "open"):
    configure(monkeypatch, mode=mode, fail=fail, status_url=status_url, token_url=token_url)
    started = time.monotonic()
    result = ts.check_token_status(JTI, time.time() + 3600)
    return result, time.monotonic() - started


def _self_signed_cert(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(cert_file), str(key_file))
    client_ctx = ssl.create_default_context(cadata=cert_pem.decode())
    return server_ctx, client_ctx


@pytest.mark.unit
class TestHttpExchange:
    def test_round_trip_over_real_sockets(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(respond(json_reply({"active": False})))

        result, _ = run_real(monkeypatch, status_server.url("/api/tokens/status?v=1"), token_server.url("/oauth/token"))

        assert result.status is TokenStatus.REVOKED
        (token_request,) = token_server.requests
        assert token_request["method"] == "POST"
        assert token_request["path"] == "/oauth/token"
        assert token_request["headers"]["content-type"] == "application/x-www-form-urlencoded"
        assert token_request["headers"]["connection"] == "close"
        assert b"grant_type=client_credentials" in token_request["body"]
        (status_request,) = status_server.requests
        assert status_request["path"] == "/api/tokens/status?v=1"
        assert status_request["headers"]["host"] == f"127.0.0.1:{status_server.port}"
        assert status_request["headers"]["authorization"] == "Bearer local-machine-token"
        assert status_request["headers"]["connection"] == "close"
        assert status_request["headers"]["user-agent"] == ts.USER_AGENT
        assert json.loads(status_request["body"]) == {"jti": JTI}
        assert status_request["raw_head"].startswith(b"POST /api/tokens/status?v=1 HTTP/1.1\r\n")

    def test_redirect_is_not_followed(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        target = servers(respond(json_reply({"active": True})))
        redirecting = servers(
            respond(f"HTTP/1.1 307 Temporary Redirect\r\nLocation: {target.url('/x')}\r\n\r\n".encode())
        )

        result, _ = run_real(monkeypatch, redirecting.url("/status"), token_server.url("/token"))

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, "http_307")
        time.sleep(0.1)
        assert target.requests == []

    def test_chunked_answer(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        chunked = (
            b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5;ext=1\r\n{"act\r\nb\r\nive": true}\r\n0\r\n\r\n'
        )
        status_server = servers(respond(chunked))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert result.status is TokenStatus.ACTIVE

    def test_chunked_answer_with_trailers(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        chunked = (
            b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n11\r\n{"active": false}\r\n0\r\nX-Trace: a\r\n\r\n'
        )
        status_server = servers(respond(chunked))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert result.status is TokenStatus.REVOKED

    @pytest.mark.parametrize(
        "ending",
        [b"0\r\n", b"0\r\nX-Trace: a\r\n", b"0\r\nX-Trace: a"],
        ids=["no-final-crlf", "unterminated-trailer", "cut-mid-trailer"],
    )
    def test_a_chunked_answer_cut_before_its_end_is_not_an_answer(self, servers, monkeypatch, ending):
        token_server = servers(respond(TOKEN_REPLY))
        raw = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\n{"active": true}\r\n' + ending
        status_server = servers(respond(raw))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, "connection")

    def test_answer_read_to_close_without_a_length(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(respond(b'HTTP/1.0 200 OK\r\n\r\n{"active": false}'))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert result.status is TokenStatus.REVOKED

    def test_a_1024_byte_body_is_read(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        body = b'{"active": true}'.ljust(1024)
        status_server = servers(respond(b"HTTP/1.1 200 OK\r\n\r\n" + body))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert result.status is TokenStatus.ACTIVE

    @pytest.mark.parametrize(
        "raw",
        [
            b"HTTP/1.1 200 OK\r\nContent-Length: 1025\r\n\r\n" + b'{"active": true}'.ljust(1025),
            b"HTTP/1.1 200 OK\r\n\r\n" + b'{"active": true}'.ljust(1025),
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n400\r\n" + b" " * 1024 + b"\r\n1\r\n \r\n0\r\n\r\n",
        ],
        ids=["content-length", "read-to-close", "chunked"],
    )
    def test_a_body_over_1024_bytes_is_oversized(self, servers, monkeypatch, raw):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(respond(raw))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, "oversized")

    def test_reading_stops_at_the_cap(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))

        def endless(conn, request, server):
            conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n" + b" " * 2048)
            server.hold()

        status_server = servers(endless)

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, "oversized")
        assert elapsed < 1

    @pytest.mark.parametrize(
        "raw",
        [
            b"SSH-2.0-OpenSSH\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nContent-Length: 6\r\n\r\nfalse",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n",
            b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n",
            b"HTTP/1.1 200 OK\r\nContent-Length: 50\r\n\r\n{}",
            b"",
        ],
        ids=["not-http", "conflicting-length", "unknown-encoding", "bad-chunk", "short-body", "empty"],
    )
    def test_a_broken_reply_is_a_connection_failure(self, servers, monkeypatch, raw):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(respond(raw))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, "connection")

    @pytest.mark.parametrize(
        "framing",
        [
            "Content-Length: 65536\r\n\r\n" + " " * 65536,
            "\r\n" + " " * 65536,
            "Transfer-Encoding: chunked\r\n\r\n10000\r\n" + " " * 65536 + "\r\n0\r\n\r\n",
        ],
        ids=["content-length", "read-to-close", "chunked"],
    )
    @pytest.mark.parametrize("status, opens", [("502 Bad Gateway", True), ("404 Not Found", False)], ids=["502", "404"])
    def test_the_status_decides_before_the_body_cap(self, servers, monkeypatch, framing, status, opens):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(respond(f"HTTP/1.1 {status}\r\n{framing}".encode()))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))
        follow_up = ts.check_token_status("other", time.time() + 3600)

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, f"http_{status[:3]}")
        assert (follow_up.reason == "breaker_open") is opens
        time.sleep(0.1)
        assert len(status_server.requests) == (1 if opens else 2)

    def test_an_oversized_401_is_retried(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))

        def refuse_then_answer(conn, request, server):
            if len(server.requests) == 1:
                conn.sendall(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 65536\r\n\r\n" + b" " * 65536)
            else:
                conn.sendall(json_reply({"active": False}))

        status_server = servers(refuse_then_answer)

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        assert result.status is TokenStatus.REVOKED
        assert len(status_server.requests) == 2
        assert len(token_server.requests) == 2

    def test_an_oversized_401_refused_again_opens_the_breaker(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(respond(b"HTTP/1.1 401 Unauthorized\r\nContent-Length: 65536\r\n\r\n" + b" " * 65536))

        result, _ = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))
        follow_up = ts.check_token_status("other", time.time() + 3600)

        assert (result.status, result.reason) == (TokenStatus.UNAVAILABLE, "http_401")
        assert follow_up.reason == "breaker_open"
        assert len(status_server.requests) == 2
        assert len(token_server.requests) == 2

    def test_connection_refused(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        closed = LocalServer(accept=False)
        port = closed.port
        closed.close()

        result, elapsed = run_real(monkeypatch, f"http://127.0.0.1:{port}/status", token_server.url("/token"))

        assert result.reason == "connection"
        assert elapsed < 1

    @pytest.mark.parametrize("url", ["ftp://127.0.0.1/status", "http:///status", "http://127.0.0.1:99999/status"])
    def test_an_unusable_url_is_a_connection_failure(self, servers, monkeypatch, url):
        token_server = servers(respond(TOKEN_REPLY))

        result, _ = run_real(monkeypatch, url, token_server.url("/token"))

        assert result.reason == "connection"

    def test_https_round_trip(self, servers, monkeypatch, tmp_path):
        server_ctx, client_ctx = _self_signed_cert(tmp_path)
        monkeypatch.setattr(ts, "_tls_context", lambda: client_ctx)
        token_server = servers(respond(TOKEN_REPLY), tls=server_ctx)
        status_server = servers(respond(json_reply({"active": True})), tls=server_ctx)

        result, _ = run_real(monkeypatch, status_server.url("/status", "https"), token_server.url("/token", "https"))

        assert result.status is TokenStatus.ACTIVE
        assert status_server.requests[0]["headers"]["authorization"] == "Bearer local-machine-token"

    def test_https_with_an_untrusted_certificate_fails(self, servers, monkeypatch, tmp_path):
        server_ctx, _ = _self_signed_cert(tmp_path)
        monkeypatch.setattr(ts, "_tls_context_cache", None)
        token_server = servers(respond(TOKEN_REPLY), tls=server_ctx)

        result, _ = run_real(monkeypatch, STATUS_URL, token_server.url("/token", "https"))

        assert (result.reason, result.detail) == ("machine_token", "connection")
        assert token_server.requests == []


@pytest.mark.unit
@pytest.mark.slow
class TestDeadline:
    """Every stall ends within the 2 s deadline as unavailable, measured as wall clock."""

    def assert_ends_at_deadline(self, elapsed: float, result, reason: str, detail: Optional[str] = None):
        assert result.status is TokenStatus.UNAVAILABLE
        assert (result.reason, result.detail) == (reason, detail)
        assert elapsed < ts.DEADLINE_SECONDS + SLACK
        assert result.latency_ms < (ts.DEADLINE_SECONDS + SLACK) * 1000

    def test_status_server_never_accepts(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        never = servers(accept=False)

        result, elapsed = run_real(monkeypatch, never.url("/status"), token_server.url("/token"))

        self.assert_ends_at_deadline(elapsed, result, "connection")
        assert elapsed >= ts.DEADLINE_SECONDS - 0.1

    def test_status_server_with_a_full_backlog(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        never = servers(accept=False, backlog=0)
        fillers = []
        for _ in range(4):
            filler = socket.socket()
            filler.setblocking(False)
            filler.connect_ex(("127.0.0.1", never.port))  # in progress; fills the accept queue
            fillers.append(filler)
        try:
            result, elapsed = run_real(monkeypatch, never.url("/status"), token_server.url("/token"))
        finally:
            for filler in fillers:
                filler.close()

        self.assert_ends_at_deadline(elapsed, result, "connection")

    def test_status_server_stalls_after_the_headers(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(stall_after_headers)

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        self.assert_ends_at_deadline(elapsed, result, "connection")
        assert elapsed >= ts.DEADLINE_SECONDS - 0.1

    def test_status_server_drips_headers_and_body(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(drip(json_reply({"active": True})))

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        self.assert_ends_at_deadline(elapsed, result, "connection")
        assert elapsed >= ts.DEADLINE_SECONDS - 0.1

    def test_status_server_drips_the_body_after_prompt_headers(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))

        def headers_then_drip(conn, request, server):
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n")
            drip(b" " * 1000)(conn, request, server)

        status_server = servers(headers_then_drip)

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        self.assert_ends_at_deadline(elapsed, result, "connection")

    def test_token_server_stalls(self, servers, monkeypatch):
        token_server = servers(stall)
        status_server = servers(respond(json_reply({"active": True})))

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        self.assert_ends_at_deadline(elapsed, result, "machine_token", "connection")
        assert status_server.requests == []

    def test_token_server_drips(self, servers, monkeypatch):
        token_server = servers(drip(TOKEN_REPLY))
        status_server = servers(respond(json_reply({"active": True})))

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        self.assert_ends_at_deadline(elapsed, result, "machine_token", "connection")

    def test_one_deadline_covers_the_token_and_the_status_call(self, servers, monkeypatch):
        def slow_token(conn, request, server):
            if not server.closing.wait(1.5):
                conn.sendall(TOKEN_REPLY)

        token_server = servers(slow_token)
        status_server = servers(stall)

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token"))

        self.assert_ends_at_deadline(elapsed, result, "connection")
        assert len(status_server.requests) == 1

    def test_tls_handshake_stalls(self, servers, monkeypatch):
        token_server = servers(silent)
        status_server = servers(respond(json_reply({"active": True})))

        result, elapsed = run_real(monkeypatch, status_server.url("/status"), token_server.url("/token", "https"))

        self.assert_ends_at_deadline(elapsed, result, "machine_token", "connection")

    def test_dns_lookup_stalls(self, monkeypatch):
        real_getaddrinfo = socket.getaddrinfo

        def slow_getaddrinfo(*args, **kwargs):
            time.sleep(4)
            return real_getaddrinfo(*args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", slow_getaddrinfo)

        result, elapsed = run_real(monkeypatch, "http://issuer.test/status", "http://issuer.test/token")

        self.assert_ends_at_deadline(elapsed, result, "machine_token", "connection")

    def test_credentials_secret_read_stalls(self, servers, monkeypatch):
        token_server = servers(respond(TOKEN_REPLY))
        configure(monkeypatch, status_url=token_server.url("/status"), token_url=token_server.url("/token"))
        monkeypatch.delenv("TOKEN_STATUS_CLIENT_SECRET")
        monkeypatch.setenv("TOKEN_STATUS_CREDENTIALS_SECRET", "issuer/status-client")
        monkeypatch.setattr(ts, "get_secret", lambda name: time.sleep(4) or {})

        started = time.monotonic()
        result = ts.check_token_status(JTI, time.time() + 3600)
        elapsed = time.monotonic() - started

        self.assert_ends_at_deadline(elapsed, result, "machine_token", "credentials_secret")
        assert token_server.requests == []

    def test_require_auth_with_a_stalled_issuer_admits_under_fail_open(
        self, servers, monkeypatch, rsa_keypair, mock_jwt_secret
    ):
        monkeypatch.setenv("JWT_PUBLIC_KEY_SECRET", "test/jwt-key")
        private_key, _, _ = rsa_keypair
        token = _sign_jwt(private_key, {"jti": JTI, "sub": "42", "exp": time.time() + 3600})
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(stall)
        configure(monkeypatch, status_url=status_server.url("/status"), token_url=token_server.url("/token"))

        started = time.monotonic()
        claims = require_auth({"headers": {"Authorization": f"Bearer {token}"}})

        assert claims["jti"] == JTI
        assert time.monotonic() - started < ts.DEADLINE_SECONDS + SLACK

    def test_require_auth_against_a_real_issuer_refuses_a_revoked_token(
        self, servers, monkeypatch, rsa_keypair, mock_jwt_secret
    ):
        monkeypatch.setenv("JWT_PUBLIC_KEY_SECRET", "test/jwt-key")
        private_key, _, _ = rsa_keypair
        token = _sign_jwt(private_key, {"jti": JTI, "sub": "42", "exp": time.time() + 3600})
        token_server = servers(respond(TOKEN_REPLY))
        status_server = servers(respond(json_reply({"active": False})))
        configure(monkeypatch, status_url=status_server.url("/status"), token_url=token_server.url("/token"))

        with pytest.raises(AuthenticationError):
            require_auth({"headers": {"Authorization": f"Bearer {token}"}})
        assert json.loads(status_server.requests[0]["body"]) == {"jti": JTI}
