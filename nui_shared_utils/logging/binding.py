"""Backend-agnostic, request-scoped binding helpers for structured logging.

Two logging backends are in use across the fleet and this module drives both from
one call site:

- **Powertools** (:func:`powertools_binder`): persistent context via
  ``Logger.append_keys`` / ``remove_keys``. Used by short-lived Lambda functions.
- **stdlib** (:func:`stdlib_binder`): a ``contextvars``-backed ``logging.Filter``
  that injects bound fields onto each record. Used by long-running services on the
  ``pythonjsonlogger`` path, which has no native persistent context.

Both binders are **request-scoped**: :meth:`request_scope` sets a ``contextvars``
token on entry and unbinds on exit, so bound context cannot leak across concurrent
requests on a long-running service or across warm Lambda invocations. ``contextvars``
(not thread-locals) so isolation holds under ``asyncio`` too. Wire ``request_scope``
into the request-end tween / handler ``finally``.

The backend is chosen **once at construction**, not by ``hasattr`` duck-typing
(which breaks on ``LoggerAdapter`` and other wrappers).

    binder = powertools_binder(logger)   # or stdlib_binder(logger)
    with binder.request_scope():
        bind_request(binder, method="POST", path=p, request_id=rid)
        bind_user(binder, id=u.id, email=u.email, org_id=u.org_id)
        logger.info("response", extra={F.RESPONSE_STATUS: 200})   # carries bound keys
    # on exit: bound keys removed, no cross-request / cross-invocation leakage

Values are always emitted as dotted keys (``request.method``), never nested dicts.
"""

from __future__ import annotations

import contextvars
import json
import logging
import traceback
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from .fields import F
from .redaction import default_redactor

# Monotonic counter so each binder gets a distinct ContextVar name (ContextVar
# names are for debugging only, but distinct names avoid confusion in tracebacks).
_binder_seq = 0


def _next_seq() -> int:
    global _binder_seq
    _binder_seq += 1
    return _binder_seq


def _drop_none(mapping: Mapping[str, Any]) -> Dict[str, Any]:
    """Return ``mapping`` without keys whose value is ``None``."""
    return {k: v for k, v in mapping.items() if v is not None}


class PowertoolsBinder:
    """Request-scoped binder over an AWS Powertools ``Logger``.

    Binding calls ``logger.append_keys``; :meth:`request_scope` tracks the keys
    bound within the scope and calls ``logger.remove_keys`` on exit so a warm
    invocation does not inherit the previous request's context.
    """

    def __init__(self, logger: Any):
        self.logger = logger
        self._scope_keys: contextvars.ContextVar[Optional[set]] = contextvars.ContextVar(
            f"nui_log_pt_scope_{_next_seq()}", default=None
        )

    def bind(self, mapping: Mapping[str, Any]) -> None:
        """Append the given dotted keys to the logger context (``None`` dropped)."""
        clean = _drop_none(mapping)
        if not clean:
            return
        self.logger.append_keys(**clean)
        tracked = self._scope_keys.get()
        if tracked is not None:
            tracked.update(clean.keys())

    def unbind(self, keys: Iterable[str]) -> None:
        """Remove the given keys from the logger context."""
        keys = list(keys)
        if keys:
            self.logger.remove_keys(keys)

    @contextmanager
    def request_scope(self) -> Iterator["PowertoolsBinder"]:
        """Scope bound keys to the block and restore prior state on exit.

        Keys bound inside the block are removed on exit, but a key that already held
        a process-level value before the scope (e.g. build context bound at startup
        and then rebound in-request) is restored to that prior value rather than
        removed. Restoration uses the logger's ``get_current_keys`` when available;
        without it, scoped keys are simply removed.
        """
        get_keys = getattr(self.logger, "get_current_keys", None)
        prior = dict(get_keys()) if callable(get_keys) else None
        token = self._scope_keys.set(set())
        try:
            yield self
        finally:
            scoped = self._scope_keys.get() or set()
            if scoped:
                if prior is not None:
                    added = [k for k in scoped if k not in prior]
                    restore = {k: prior[k] for k in scoped if k in prior}
                    if added:
                        self.logger.remove_keys(added)
                    if restore:
                        self.logger.append_keys(**restore)
                else:
                    self.logger.remove_keys(list(scoped))
            self._scope_keys.reset(token)


class _ContextVarLogFilter(logging.Filter):
    """A ``logging.Filter`` that injects the current bound fields onto each record.

    Reads the binder's ``contextvars`` value at emit time, so concurrent requests
    (each in its own task / thread context) see only their own bound fields. An
    explicit per-call ``extra=`` value already on the record wins (bound context
    never clobbers it).
    """

    def __init__(self, ctx: "contextvars.ContextVar[Optional[Dict[str, Any]]]"):
        super().__init__()
        self._ctx = ctx

    def filter(self, record: logging.LogRecord) -> bool:
        fields = self._ctx.get()
        if fields:
            for key, value in fields.items():
                if not hasattr(record, key):
                    setattr(record, key, value)
        return True


class StdlibBinder:
    """Request-scoped binder over a standard-library ``logging.Logger``.

    Bound fields live in a ``contextvars.ContextVar`` and are injected onto every
    record by a filter installed on the logger. :meth:`request_scope` snapshots the
    context on entry and resets it on exit, so a long-running service does not bleed
    one request's context into another (async-safe under ``contextvars``).
    """

    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._ctx: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
            f"nui_log_std_ctx_{_next_seq()}", default=None
        )
        self._filter = _ContextVarLogFilter(self._ctx)
        self.install_on(logger)

    def install_on(self, target: Any) -> None:
        """Attach the context filter to a ``Logger`` or ``Handler`` (idempotent).

        A logger's *filters* run only for records emitted via that exact logger, not
        for records propagated up from child loggers (``getLogger("svc.module")``).
        A *handler's* filters, by contrast, run for every record that reaches the
        handler, including propagated ones. So the filter is installed on the
        logger (direct emits) and on the effective handlers up the hierarchy (child
        emits). The filter is a no-op outside a ``request_scope`` (empty context),
        so attaching it to a shared / root handler is safe.

        Call this explicitly for handlers configured *after* the binder is built, or
        when your JSON handler lives on a logger other than the one passed here.
        """
        if isinstance(target, logging.Handler):
            if self._filter not in target.filters:
                target.addFilter(self._filter)
            return
        if self._filter not in target.filters:
            target.addFilter(self._filter)
        node: Optional[logging.Logger] = target
        while node is not None:
            for handler in node.handlers:
                if self._filter not in handler.filters:
                    handler.addFilter(self._filter)
            if not node.propagate:
                break
            node = node.parent

    def bind(self, mapping: Mapping[str, Any]) -> None:
        """Merge the given dotted keys into the current context (``None`` dropped)."""
        clean = _drop_none(mapping)
        if not clean:
            return
        current = self._ctx.get()
        merged = dict(current) if current else {}
        merged.update(clean)
        self._ctx.set(merged)

    def unbind(self, keys: Iterable[str]) -> None:
        """Remove the given keys from the current context."""
        current = self._ctx.get()
        if not current:
            return
        drop = set(keys)
        self._ctx.set({k: v for k, v in current.items() if k not in drop})

    def current_fields(self) -> Dict[str, Any]:
        """Return a snapshot of the currently-bound fields (introspection / tests)."""
        current = self._ctx.get()
        return dict(current) if current else {}

    @contextmanager
    def request_scope(self) -> Iterator["StdlibBinder"]:
        """Isolate binds made inside the block; restore prior context on exit."""
        current = self._ctx.get()
        token = self._ctx.set(dict(current) if current else {})
        try:
            yield self
        finally:
            self._ctx.reset(token)


def powertools_binder(logger: Any) -> PowertoolsBinder:
    """Build a request-scoped binder over an AWS Powertools ``Logger``."""
    return PowertoolsBinder(logger)


def stdlib_binder(logger: logging.Logger) -> StdlibBinder:
    """Build a request-scoped binder over a standard-library ``logging.Logger``."""
    return StdlibBinder(logger)


# --- Field-specific binding helpers (callers never hand-type a dotted string) ---


def bind_request(
    binder: Any,
    *,
    method: Optional[str] = None,
    path: Optional[str] = None,
    ip: Optional[str] = None,
    request_id: Optional[str] = None,
) -> None:
    """Bind request context (``request.method`` / ``.path`` / ``.ip`` / ``.id``)."""
    binder.bind(
        {
            F.REQUEST_METHOD: method,
            F.REQUEST_PATH: path,
            F.REQUEST_IP: ip,
            F.REQUEST_ID: request_id,
        }
    )


def bind_user(
    binder: Any,
    *,
    id: Optional[str] = None,
    email: Optional[str] = None,
    org_id: Optional[str] = None,
    roles: Optional[Any] = None,
) -> None:
    """Bind principal identity (``user.id`` / ``.email`` / ``.org_id`` / ``.roles``)."""
    binder.bind(
        {
            F.USER_ID: id,
            F.USER_EMAIL: email,
            F.USER_ORG_ID: org_id,
            F.USER_ROLES: roles,
        }
    )


def bind_build_context(
    binder: Any,
    *,
    commit: Optional[str] = None,
    branch: Optional[str] = None,
    tag: Optional[str] = None,
) -> None:
    """Bind optional build metadata (``git.commit`` / ``.branch`` / ``.tag``).

    Not bound automatically; the consumer decides whether build metadata is
    available at runtime and calls this once at startup / per request.
    """
    binder.bind(
        {
            F.GIT_COMMIT: commit,
            F.GIT_BRANCH: branch,
            F.GIT_TAG: tag,
        }
    )


def log_exception(binder: Any, exc: BaseException, message: str) -> None:
    """Log ``exc`` at ERROR with explicit ``error.*`` fields.

    ``error.stack`` is set explicitly from the traceback so the canonical field
    always carries the trace. This uses ``logger.error`` (not ``logger.exception``)
    deliberately: ``logger.exception`` sets ``exc_info=True``, which makes the
    backend emit its *own* traceback field (Powertools' ``stack_trace``, stdlib's
    ``exc_text``) on top of ``error.stack`` when called inside an ``except`` block,
    i.e. the same trace twice under two names. With ``logger.error``, ``error.stack``
    is the single source.
    """
    binder.logger.error(
        message,
        extra={
            F.ERROR_TYPE: type(exc).__name__,
            F.ERROR_MESSAGE: str(exc),
            F.ERROR_STACK: "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        },
    )


def _redact_kwargs(allow_only: Optional[Iterable[str]], deny_extra: Optional[Iterable[str]]) -> Dict[str, Any]:
    # ``deny_extra`` is only forwarded when set, so a custom ``redactor`` that does
    # not accept it still works when the caller does not use it.
    kwargs: Dict[str, Any] = {"allow_only": allow_only}
    if deny_extra is not None:
        kwargs["deny_extra"] = deny_extra
    return kwargs


def request_body_fields(
    payload: Any,
    *,
    allow_only: Optional[Iterable[str]] = None,
    deny_extra: Optional[Iterable[str]] = None,
    redactor: Any = default_redactor,
) -> Dict[str, str]:
    """Redact and serialise a request body into a single ``request.body_json`` field.

    Returns a mapping ready to splat into ``extra=`` (or pass to ``binder.bind``).
    The body is redacted (deny-list by default, allow-list via ``allow_only``,
    extended for this call via ``deny_extra``) then JSON-serialised with
    ``default=str`` so non-serialisable values do not raise.
    """
    redacted = redactor(payload, **_redact_kwargs(allow_only, deny_extra))
    return {F.REQUEST_BODY_JSON: json.dumps(redacted, default=str)}


def response_output_fields(
    payload: Any,
    *,
    allow_only: Optional[Iterable[str]] = None,
    deny_extra: Optional[Iterable[str]] = None,
    redactor: Any = default_redactor,
) -> Dict[str, str]:
    """Redact and serialise a response payload into a single ``response.output_json`` field.

    The response twin of :func:`request_body_fields`, for services that log
    unbounded response bodies.
    """
    redacted = redactor(payload, **_redact_kwargs(allow_only, deny_extra))
    return {F.RESPONSE_OUTPUT_JSON: json.dumps(redacted, default=str)}
