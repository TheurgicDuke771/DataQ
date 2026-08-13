import copy
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from backend.app.core import otel
from backend.app.core.config import get_settings

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

_PII_KEYS: frozenset[str] = frozenset(
    {
        # Credentials
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "access_key",
        "private_key",
        "passphrase",
        # The Iceberg SQL/hive catalog's SECOND credential (#1181) — an exact-match
        # key alongside "secret", since "catalog_secret" is a distinct string a
        # bare "secret" entry does not catch. #849's lesson applies here too: don't
        # rely on no call site ever logging it, redact by key regardless.
        "catalog_secret",
        # Vault/OpenBao (ADR 0039, #1054). The key set is the sturdier of the two
        # mechanisms here — it cannot be fooled by a token SHAPE nobody anticipated —
        # and an OpenBao token (`hvs.…` / `s.…`) matches none of the bare-token
        # regexes. #849's lesson is that a DEPENDENCY does the logging, so auditing
        # our own call sites is the half that fails.
        "x-vault-token",
        "client_token",
        "secret_id",
        "role_id",
        # Personal contact
        "email",
        "phone",
        "ssn",
        "credit_card",
        "card_number",
        # Azure AD claims (per 2026-05-28 security audit) — AAD object IDs
        # and identifiers are GDPR-grade personal data under Article 4(1).
        "oid",
        "aad_oid",
        "aad_object_id",
        "upn",
        "preferred_username",
        # Credential-bearing headers in their HYPHENATED header spelling (#849 review).
        # `authorization` was already covered above, but a headers dict is keyed
        # `x-api-key`, not `x_api_key`, and the key match is exact. Key-based redaction is
        # the sturdier of the two mechanisms — it cannot be fooled by a token SHAPE nobody
        # anticipated, which is exactly how the PAT leak happened.
        "api-key",
        "x-api-key",
        "cookie",
        "set-cookie",
        "user_id",
        "name",
        "display_name",
    }
)
_REDACTED = "<redacted>"

# Secret-bearing query params / `key=value` pairs embedded in *string* values
# (the key-based redaction below only catches dict KEYS). The prime case is the
# ADF webhook URL `…/events/adf?token=<secret>` (ADR 0006) surfacing inside a log
# message string — e.g. an access line or an error that interpolated the URL —
# where it would otherwise slip past the key redactor (#494).
_SECRET_QS_RE = re.compile(
    r"(?i)\b(token|sig|signature|secret|api[_-]?key|access[_-]?key|password)=[^&\s\"']+"
)
# URL-userinfo credentials (`scheme://user:secret@host`, e.g. a SQLAlchemy engine
# URL `databricks://token:<PAT>@host/…`) — a different shape than the query-param
# scrub above, missed by it until #536.
_URL_USERINFO_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@\"']+):[^@/\s\"']+@")

# BARE credentials — a token sitting in a message with no `key=` prefix and no URL
# around it, so neither scrub above sees it (#849).
#
# This is not hypothetical: `fastapi_azure_auth` logs
# ``log.warning('Malformed token received. %s. Error: %s', access_token, …)``, and a
# DataQ PAT is not a JWT, so **every PAT-authenticated request** drove that line and
# shipped the raw token to App Insights. The token is a live bearer credential: anyone
# with read access to telemetry could authenticate as its owner.
#
# The lesson is the one CLAUDE.md §10 already states — redact at the LOGGER, not the
# call site. We do not control what a third-party library logs, and "grep the codebase
# for places we log tokens" would never have found this one, because we don't log it:
# a dependency does.
#
# `dq_live_` is duplicated from `api_key_service.TOKEN_PREFIX` deliberately — core.logging
# must not import the service layer (import cycle) — and a drift-guard test pins the two
# together so a renamed prefix can't silently stop being redacted. `dq_sess_` is the same
# arrangement for `session_service.TOKEN_PREFIX` (ADR 0032): a session token is a live
# browser credential exactly like a PAT, so it must not survive a log line a PAT wouldn't.
# `_PII_KEYS` already redacts `cookie`/`set-cookie` KEYS; this is the other half — the
# token sitting bare inside a message string, which is how #849 actually happened.
_BEARER_TOKEN_RE = re.compile(
    r"(?i)(?:"
    r"dq_live_[A-Za-z0-9_\-]{6,}"  # a DataQ PAT (ADR 0026)
    r"|dq_sess_[A-Za-z0-9_\-]{6,}"  # an OTP session token (ADR 0032)
    r"|eyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-]*"  # a JWT (AAD access token)
    # A `Bearer <token>` header echo. Deliberately requires the value to LOOK like a
    # token — ≥16 chars AND containing a digit or one of `-._~+/=` — because
    # `bearer\s+\w{8,}` also matches prose ("bearer authentication required") and an
    # over-eager redactor is not a safe failure: it hides the diagnostics an operator
    # needs, and the usual response is to weaken or disable the scrubber entirely, at
    # which point nothing is redacted at all (#849 review).
    r"|\bbearer\s+(?=[A-Za-z0-9._~+/\-]*[0-9\-._~+/])[A-Za-z0-9._~+/\-]{16,}=*"
    r")"
)


def _scrub_secret_strings(text: str) -> str:
    text = _SECRET_QS_RE.sub(lambda m: f"{m.group(1)}={_REDACTED}", text)
    text = _URL_USERINFO_RE.sub(lambda m: f"{m.group(1)}:{_REDACTED}@", text)
    return _BEARER_TOKEN_RE.sub(_REDACTED, text)


def _redact_pii(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    def walk(value: Any) -> Any:
        if isinstance(value, dict):
            return {k: (_REDACTED if k.lower() in _PII_KEYS else walk(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, str):
            return _scrub_secret_strings(value)
        return value

    result: EventDict = walk(event_dict)
    return result


def _add_request_id(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    rid = request_id_var.get()
    if rid is not None:
        event_dict["request_id"] = rid
    return event_dict


def _extract_exc_info_exception(raw: Any) -> BaseException | None:
    """Pull the exception instance out of an ``exc_info`` value, whatever shape it
    currently has. structlog callers set ``exc_info`` to ``True`` (``BoundLogger.
    exception()``'s default), a bare exception instance, or a ``(type, value, tb)``
    tuple (what ``ProcessorFormatter`` copies over from a foreign stdlib
    ``LogRecord.exc_info``) — the shape depends on where in the chain a processor
    sits and who produced the record. Mirrors structlog's own internal
    ``processors._figure_out_exc_info`` (used by ``format_exc_info``/
    ``dict_tracebacks``) rather than importing that private helper directly."""
    if isinstance(raw, BaseException):
        return raw
    if isinstance(raw, tuple) and len(raw) == 3 and isinstance(raw[1], BaseException):
        return raw[1]
    if raw:
        # `True` (or any other truthy sentinel): "look up the exception currently
        # being handled", the stdlib `exc_info=True` convention.
        return sys.exc_info()[1]
    return None


def _already_logged_exception(raw_exc_info: Any) -> BaseException | None:
    """Extract the exception out of *raw_exc_info* (whatever shape — see
    ``_extract_exc_info_exception``) and return it only if
    ``alerting.base.was_already_logged`` says it was already reported with a
    full traceback elsewhere. Returns ``None`` when there's nothing to extract
    OR the exception isn't marked, so callers can treat any non-``None`` result
    as "downgrade this".

    The one piece shared between the two places that need this check: the
    structlog processor below (``_downgrade_already_logged_exceptions``, which
    downgrades the ``event_dict`` every native/foreign structlog record goes
    through) and ``_RedactingOTelLogHandler.emit`` (which downgrades the RAW
    stdlib ``logging.LogRecord`` a second time — see that class's docstring for
    why the structlog-side downgrade alone doesn't reach the OTel export path).
    Factoring it out means the "pull the exception out of whatever shape we
    were handed, then check the marker" logic can't drift between the two call
    sites the way #1261 found it had drifted across the two ORIGINAL per-caller
    checks.
    """
    exc = _extract_exc_info_exception(raw_exc_info)
    if exc is None:
        return None

    # Lazy import: `core/logging.py` is a foundational module imported very early
    # (module scope of `main.py`, `worker/celery_app.py`, `worker/tasks.py`,
    # every datasource/alerting module for `get_logger`); `alerting/base.py` is a
    # higher-level domain module. It has no import-time dependency back on
    # `core.logging` itself (its
    # only DataQ import is `db.models`, and the `alerting` package's `__init__.py`
    # is docstring-only), so this does not actually cycle — but keeping the
    # import inside the guarded, exception-only branch means the rest of the app
    # never pays for or depends on the domain import at module-load time, and a
    # future edit to `alerting/base.py` that adds its own `core.logging` import
    # (every one of its sibling files in that package already does, for
    # `get_logger`) can't turn this into a real cycle.
    from backend.app.alerting.base import was_already_logged

    if not was_already_logged(exc):
        return None
    return exc


def _record_marks_already_logged_exception(record: logging.LogRecord) -> bool:
    """True if the raw stdlib ``LogRecord`` *record* should be downgraded
    before OTel export because its exception was already reported with a full
    traceback elsewhere — the raw-``LogRecord`` counterpart of
    ``_downgrade_already_logged_exceptions`` below, used by
    ``_RedactingOTelLogHandler.emit`` (#1261 follow-up review).

    Two distinct shapes reach a root handler, and — verified empirically, not
    assumed — they carry the "already downgraded" signal completely
    differently:

    * A **foreign** record — a bare, non-structlog ``logging.getLogger(x).
      exception(...)`` bridged in via ``foreign_pre_chain`` — carries the real
      ``(type, value, tb)`` in ``record.exc_info``, untouched by structlog
      (structlog never intercepted the call). ``_already_logged_exception``
      (shared with the processor below) extracts and checks it directly.

    * A **native** record — every call site in this app, via ``get_logger()``
      — has ``record.exc_info`` equal to ``None`` even for
      ``log.exception(...)``. The configured wrapper
      (``structlog.make_filtering_bound_logger``) implements ``.exception()``
      as ``self.error(event, exc_info=True, **kw)``: that ``exc_info=True``
      becomes an ``event_dict`` KEY, not a real Python keyword argument, and
      the processor chain consumes/drops that key (either
      ``_downgrade_already_logged_exceptions`` popping it, or the traceback
      renderer rendering-and-popping it) before ``wrap_for_formatter`` hands
      the FINAL ``event_dict`` to the underlying stdlib ``Logger.error(...)``
      call — so the raw ``LogRecord`` this handler receives was never given an
      ``exc_info`` to read in the first place. What DOES survive onto the
      record is ``record.msg`` itself: `wrap_for_formatter` stamps the fully
      processed ``event_dict`` there (``"level"`` key included) before the
      record is even constructed, so the downgrade decision is legible
      straight off ``record.msg["level"]``. Nothing else in this app's
      processor chain rewrites ``level`` away from the invoked method's own
      name, so "the invoked method was ERROR-or-above but the rendered
      ``level`` says warning" is an unambiguous fingerprint of this exact
      processor having fired — no need to re-derive "was it marked" a second
      time from data that's already gone.
    """
    if isinstance(record.msg, dict):
        return record.msg.get("level") == "warning" and record.levelno >= logging.ERROR
    return _already_logged_exception(record.exc_info) is not None


def _downgrade_already_logged_exceptions(
    _logger: Any, _name: str, event_dict: EventDict
) -> EventDict:
    """Downgrade a log record whose exception was already reported with a full
    traceback elsewhere, so a caller never has to remember to check that itself.

    #1226 fixed a real bug: when every alerting channel fails,
    ``CompositePublisher._fan_out_delivered_first`` (``alerting/composite.py``)
    logs a full traceback per failing channel before re-raising the last one, and
    the caller's own ``except Exception: log.exception(...)`` logged that SAME
    traceback a second time. The fix (#1260) added
    ``alerting.base.mark_already_logged``/``was_already_logged`` and had each
    caller check it before choosing ``log.warning`` over ``log.exception``. #1261:
    that check was duplicated verbatim in both callers and opt-in per caller — a
    future third caller that forgets it silently reintroduces the exact bug, with
    nothing to catch the omission.

    This is the same shape as PII redaction (CLAUDE.md §10 / #849): the fix
    belongs in the processor chain, applied to EVERY log record once, not
    repeated at every call site. Any caller's ``log.exception(...)`` — including
    a foreign (non-structlog) ``logging.exception(...)`` bridged in via
    ``foreign_pre_chain`` — gets the downgrade for free *on the rendered stdout
    body*. This alone does NOT reach the OTel/App Insights export path — see
    ``_RedactingOTelLogHandler`` below for the raw-``LogRecord`` half of this fix
    (#1261 follow-up review).

    Must run AFTER ``add_log_level`` (so there is a ``level`` to overwrite) and
    BEFORE the exception is rendered to a string (``_dict_tracebacks_no_locals``)
    — while ``event_dict["exc_info"]`` is still the raw shape, not yet consumed.

    Dropping ``exc_info`` also drops the traceback, which was the only place the
    exception's TYPE was visible on the downgraded line (`_poll_staleness_loop`'s
    original comment on this, #1260 review) — restored here as ``error_type`` so
    every downgraded caller keeps that correlator, not just the ones that used to
    remember to add it by hand. ``setdefault`` so a caller-supplied ``error_type``
    (a different meaning in a future call site) is never clobbered.

    Caveat (#1314): ``structlog.make_filtering_bound_logger`` gates a record on
    the SEVERITY OF THE METHOD ACTUALLY CALLED, before any processor runs — this
    processor can only rewrite the ``level`` FIELD after that gate has already
    let the record through. A caller that used to choose ``log.warning`` vs
    ``log.exception`` by hand (pre-#1261) would have its downgraded line
    correctly suppressed under ``LOG_LEVEL=ERROR``/``CRITICAL``; centralizing
    the choice into an unconditional ``log.exception(...)`` means a downgraded
    line now always clears that gate and appears (correctly labeled
    ``"warning"``) even under a stricter configured threshold. Every `LOG_LEVEL`
    this repo actually ships is ``INFO`` (`.env.app*.example`,
    `containerapps.tf`), so this is a documented, accepted trade-off rather than
    a fix in progress — restoring exact pre-#1261 filtering semantics would
    require reintroducing a per-caller method choice, undoing the very
    centralization #1261 exists for.
    """
    # `_already_logged_exception` already extracted the exception AND confirmed
    # `was_already_logged(exc)` — nothing left to check here.
    exc = _already_logged_exception(event_dict.get("exc_info"))
    if exc is None:
        return event_dict

    event_dict["level"] = "warning"
    event_dict.setdefault("error_type", type(exc).__name__)
    event_dict.pop("exc_info", None)
    return event_dict


# Traceback → dict WITHOUT frame locals (#536): `dict_tracebacks`' default
# transformer captures every frame's locals, which can carry anything in scope —
# connection URLs with embedded credentials (the live-smoke leak: a SQLAlchemy
# `…://token:<PAT>@host` engine URL), sample rows, PII — and are unredactable in
# general. Frame files/lines/names remain; locals are debugging sugar we forgo.
_dict_tracebacks_no_locals = structlog.processors.ExceptionRenderer(
    structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
)


def _configure_otel_log_export(
    root: logging.Logger,
    level: int,
    formatter: logging.Formatter,
    service_name: str,
) -> None:
    """Bridge stdlib logging → OpenTelemetry → the configured exporter(s) (#524).

    Replaces the EOL opencensus ``AzureLogHandler`` (and its Py3.13 ``createLock``
    hardening, #393/#405). A **no-op** when no exporter is configured, matching the
    old connection-string gate. **Fork-safe**: the SDK's ``BatchLogRecordProcessor``
    re-inits its export thread in forked children (celery prefork) via
    ``os.register_at_fork``, so attaching here — exactly where the old handler
    attached — is correct in the worker as well as the API.

    The lazy imports (repo convention, ``secrets.py``) keep telemetry-off
    deployments from paying the OTel-logs import cost.

    **Best-effort**: the whole setup is isolated in a try/except so an observability
    misconfig (bad OTLP endpoint/headers, SDK drift) degrades to stdout-only logging
    instead of crashing the API lifespan or the celery signal handlers — a telemetry
    fault must never take the process down (the #405 blast radius). The stdout
    handler is already attached by the time we get here.
    """
    settings = get_settings()
    exporters = otel.build_log_exporters(settings=settings)
    if not exporters:
        return

    try:
        import warnings

        from opentelemetry._logs import set_logger_provider
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

        class _RedactingOTelLogHandler(LoggingHandler):  # type: ignore[misc]  # LoggingHandler is Any (follow_imports=skip)
            """OTel bridge that redacts the EXPORTED ATTRIBUTES, not just the body.

            The body is already scrubbed — ``LoggingHandler._translate`` renders it
            through our redacting ``ProcessorFormatter`` (``if self.formatter:``).
            But the base ``_get_attributes`` copies every non-reserved log-record var
            into the exported OTel attributes verbatim, *bypassing the formatter* —
            so a foreign record's ``extra=`` (or a library's custom record attribute)
            could ship a secret / PII to the backend un-redacted. Run those
            attributes through the same PII/secret scrubber the formatter applies to
            the body (#494/#536). This is stricter than the old opencensus handler,
            which only exported the formatted message.

            Also downgrades the RAW ``LogRecord`` for an exception already logged
            elsewhere, independent of ``_downgrade_already_logged_exceptions``
            above (#1261 follow-up review — a real gap, not a hypothetical one).
            The inherited ``_translate``/``_get_attributes`` read
            ``record.levelno`` (and, for a foreign record, ``record.exc_info``)
            directly off the raw ``LogRecord`` rather than the processed
            ``event_dict`` — so a "downgraded" exception's rendered stdout body
            can correctly say ``warning`` while the SAME record's raw
            ``levelno`` still says ERROR to anything reading the record
            directly, which is exactly what the OTel bridge does (see
            ``_record_marks_already_logged_exception`` for exactly which field
            carries the signal for which record shape — it is NOT uniformly
            ``record.exc_info``, verified empirically). Left alone, that
            silently reintroduces #1226's duplicate-alert-noise problem on the
            telemetry channel, invisible to any test that only asserts against
            captured stdout JSON."""

            @staticmethod
            def _get_attributes(record: logging.LogRecord) -> Any:
                return _redact_pii(None, "", dict(LoggingHandler._get_attributes(record)))

            def emit(self, record: logging.LogRecord) -> None:
                if _record_marks_already_logged_exception(record):
                    # Shallow-copy rather than mutate in place: `record` is the
                    # SAME object every handler on `root` receives (Logger.
                    # callHandlers hands one instance to each), so mutating it
                    # here could leak the downgrade into another handler's view
                    # of the record. Copying keeps this handler's rewrite fully
                    # local regardless of handler-registration order.
                    record = copy.copy(record)
                    record.exc_info = None
                    record.exc_text = None
                    record.levelno = logging.WARNING
                    record.levelname = logging.getLevelName(logging.WARNING)
                super().emit(record)

        provider = LoggerProvider(resource=otel.build_resource(service_name))
        for exporter in exporters:
            provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
        set_logger_provider(provider)

        with warnings.catch_warnings():
            # The sdk `LoggingHandler` carries a DeprecationWarning nudging toward
            # `opentelemetry-instrumentation-logging` — which only injects trace
            # context into records; it is NOT an export bridge. The sdk handler IS
            # the bridge (it's what the azure-monitor distro uses under the hood).
            # Suppress just that message so an unrelated deprecation still surfaces.
            warnings.filterwarnings(
                "ignore", message=r".*LoggingHandler.*", category=DeprecationWarning
            )
            handler = _RedactingOTelLogHandler(level=level, logger_provider=provider)
        # Same redacting ProcessorFormatter as stdout, so records exported to the
        # backend pass through `_redact_pii` (incl. the secret-string scrubber) — a
        # foreign record carrying a secret in its message is scrubbed before export
        # (#494). App-level structlog records are already redacted upstream.
        handler.setFormatter(formatter)
        # Break the exporter's feedback loop AT THE BRIDGE (#852).
        #
        # `azure.core`'s HTTP policy logs a request AND a response line for every call the
        # SDK makes — including the exporter's own uploads. Those records reach root,
        # re-enter this handler, are exported, and generate more uploads. Measured in prod
        # at ~10/sec.
        #
        # Setting that logger's level was not enough: it works in the API but NOT in the
        # Celery worker, where something in the worker/prefork logging setup restores INFO.
        # Rather than keep chasing who resets it, drop the records here, at the one place
        # they must pass through to reach the backend. A filter on the handler cannot be
        # defeated by a level reset elsewhere.
        #
        # Only the EXPORT is filtered — the records still reach stdout, so a real transport
        # problem is still visible in the container log.
        handler.addFilter(lambda record: not record.name.startswith("azure.core"))
        root.addHandler(handler)

        # Break the feedback loop: the SDK/exporter log their own "failed to export"
        # warnings on these namespaces; propagated to root they re-enter THIS bridge
        # and get re-queued for export — an amplifier that fires exactly when the
        # backend is unreachable. Keep them off the export pipeline (a well-known
        # OTel root-bridge footgun).
        for noisy in ("opentelemetry", "azure.monitor.opentelemetry"):
            logging.getLogger(noisy).propagate = False

        # Positive confirmation so "my logs never reached the backend" is diagnosable
        # (misconfig looks identical to telemetry-off otherwise). Endpoint is infra
        # config, not a secret; the App Insights connection string is NOT logged.
        logging.getLogger(__name__).info(
            "otel_log_export_configured service=%s exporters=%d azure=%s otlp=%s",
            service_name,
            len(exporters),
            bool(settings.applicationinsights_connection_string),
            settings.otel_exporter_otlp_endpoint or "off",
        )
    except Exception:
        # Degrade to stdout-only rather than take the process down (the #405 blast
        # radius: orchestration polling / scheduled dispatch / gap recovery / purge).
        logging.getLogger(__name__).exception("otel_log_export_setup_failed")


def configure_logging(service_name: str = "dataq") -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_pii,
        # Must run after `add_log_level` (there's a `level` to overwrite) and
        # before `_dict_tracebacks_no_locals` below (exc_info must still be raw).
        _downgrade_already_logged_exceptions,
    ]

    # Bridge stdlib logging (uvicorn.access, uvicorn.error, etc.) through the
    # same processor chain so every line out of the app is JSON with a
    # request_id when available. Without this the uvicorn access log emits
    # human-readable text that App Insights can't correlate (#50).
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _dict_tracebacks_no_locals,
            # Re-run the redactor AFTER the traceback is rendered to a dict —
            # the pre-chain pass ran before the exception existed as strings, so
            # exception messages/frames never met the scrubber (#536). Idempotent
            # on everything else.
            _redact_pii,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # `azure.core`'s HTTP logging policy logs a full request AND response line for EVERY
    # HTTP call the Azure SDK makes — Key Vault reads, and (once the OTel bridge below is
    # attached) the exporter's own uploads to App Insights. That last one is a
    # self-sustaining amplifier: the upload is logged, the log record reaches root,
    # re-enters the export bridge, is uploaded, and is logged again. Measured in prod at
    # ~10 records/second — 19,000 in half an hour — which drowned the application's real
    # logs (a genuine orchestration-poll event became unfindable in the noise) and burned
    # ingestion quota (#852).
    #
    # Silenced at INFO rather than detached (`propagate = False`), so a genuine transport
    # WARNING from the SDK still reaches stdout and the backend; only the per-request
    # chatter that feeds the loop is dropped.
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)

    # Detach uvicorn's pre-configured handlers; let logs propagate to root so
    # they hit the structlog ProcessorFormatter above.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True

    # uvicorn.access is SILENCED, not propagated: its access line includes the raw
    # query string (get_path_with_query_string), so it would log the ADF webhook
    # `?token=<secret>` (ADR 0006) to stdout AND straight to App Insights (#494).
    # The request middleware (main.py) emits a structured, path-only access log
    # (method/path/status/duration/client/request_id) for every request that reaches
    # the app — so app-level access logging is unaffected; only server-layer-only
    # lines (e.g. malformed requests rejected before ASGI dispatch) go unlogged, an
    # accepted tradeoff for not leaking the secret.
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers = []
    access_logger.propagate = False

    # Export logs to the configured backend(s) via OpenTelemetry (#524, replacing
    # the EOL opencensus AzureLogHandler). No-op when telemetry is off.
    _configure_otel_log_export(root, level, formatter, service_name)

    structlog.configure(
        processors=[
            *shared_processors,
            _dict_tracebacks_no_locals,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
