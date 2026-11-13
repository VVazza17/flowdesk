"""The failure taxonomy every provider client maps into (M2 slice 4), and credential scrubbing.

Five kinds, all taken from the spec and the ADRs rather than guessed:

  transient         the network or the provider hiccuped; a retry can help
  rate_limited      the provider said slow down (429 and its relatives); a retry can help, later
  permanent         the request itself is wrong or refused: a bad series ID, a bad key, FRED's
                    "No vintage dates exist" 400 when a BACKFILL sends it (ADR-0003 decision 4: a
                    window built from the series' own vintage dates holds at least one, so none is
                    a bug). A retry cannot help. The same 400 from a POLL over a short window is a
                    quiet day, not a failure: see NoVintageDates and the ADR-0003 addendum of
                    2026-11-13
  empty_result      yfinance's silent empty frame. ADR-0002 and §6.12: this is a failure that
                    degrades the feed, never "no new data". A retry cannot be assumed to help
  schema_mismatch   the payload is not the shape the client knows (a column set that varies by
                    security type, §6.12). Rejected, not guessed at

Each client's job is to map its library's exceptions into these. The framework never sees httpx or
yfinance exception types.
"""

import re
from collections.abc import Iterable

TRANSIENT = "transient"
RATE_LIMITED = "rate_limited"
PERMANENT = "permanent"
EMPTY_RESULT = "empty_result"
SCHEMA_MISMATCH = "schema_mismatch"
# Not a provider kind: the framework could not take the rate budget, so it did not call out.
BUDGET_UNAVAILABLE = "budget_unavailable"
# Not a provider kind: something in our own code raised.
UNEXPECTED = "unexpected"


class ProviderError(Exception):
    kind: str = UNEXPECTED
    retryable: bool = False


class Transient(ProviderError):
    kind = TRANSIENT
    retryable = True


class RateLimited(ProviderError):
    kind = RATE_LIMITED
    retryable = True


class Permanent(ProviderError):
    kind = PERMANENT


class GatewayTimeout(Transient):
    """FRED's gateway answered HTTP 504 (measured 2026-11-13: at exactly 60 s, for an
    `output_type=3` window of about 600 or more vintage dates).

    Still `transient` and retryable like every other 5xx, so nothing that handles Transient
    changes. It is a subclass only so that the windowed fetch can tell "this window is too big" from
    "FRED is unwell": the first is answered by halving the window, and retrying the same window
    can never succeed.
    """


class NoVintageDates(Permanent):
    """FRED's HTTP 400 "No vintage dates exist for the specified real-time period".

    It is one response with two meanings, and only the CALLER knows which. A backfill builds its
    windows from the series' own vintage dates, so for it the 400 means the request is wrong or
    FRED's record moved: it stays `permanent` and fails the run. A poll asks "what changed since
    the last poll", and on a day nothing was published the honest answer is this 400: for it, it is
    an empty result. The client returns [] only when the caller says so
    (`FredClient.observations(..., no_vintages_is_empty=True)`); otherwise this is raised and
    classified exactly as Permanent was before (same kind, never retried).
    """


class EmptyResult(ProviderError):
    kind = EMPTY_RESULT


class SchemaMismatch(ProviderError):
    kind = SCHEMA_MISMATCH


class BudgetUnavailable(ProviderError):
    """The limiter could not grant a slot (Redis unreachable, no budget declared, or the wait
    ran out). The framework refuses to call the provider rather than call it unthrottled."""

    kind = BUDGET_UNAVAILABLE


def kind_of(exc: BaseException) -> str:
    return exc.kind if isinstance(exc, ProviderError) else UNEXPECTED


def is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, ProviderError) and exc.retryable


def health_error(exc: BaseException) -> str:
    """What goes into providers_health.last_error: the kind and the exception CLASS, never its text.

    That column is served by /health/providers. The public profile removes the key (IDEA-019), the
    private profile does not, and provider exception text routinely carries the request URL; FRED
    authenticates with `api_key=` in the query string. So the text is not stored here at all.
    """
    return f"{kind_of(exc)}:{type(exc).__name__}"


# `name=value` pairs whose value is a credential, wherever they appear (a URL query, a header dump).
_SECRET_PAIR = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|secret|password|passwd|authorization)"
    r"(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^&\s,;'\"]+)"
)
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
ERROR_TEXT_MAX_CHARS = 500


def scrub(text: str, secrets: Iterable[str] = ()) -> str:
    """Remove credentials from text that will be stored (ingestion_runs.error).

    Two layers: any known secret value is replaced wherever it occurs, and anything that looks like
    `api_key=...` or `Bearer ...` is replaced by pattern. The pattern is a net for secrets nobody
    told us about; only the exact-value layer is a guarantee, so a client must pass its own keys.
    """
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "[redacted]")
    # Bearer first: in `Authorization: Bearer <key>` the pair pattern would otherwise take the word
    # "Bearer" as the value and leave the key itself behind.
    out = _BEARER.sub(lambda m: f"{m.group(1)} [redacted]", out)
    out = _SECRET_PAIR.sub(lambda m: f"{m.group(1)}{m.group(2)}[redacted]", out)
    return out[:ERROR_TEXT_MAX_CHARS]


def run_error(exc: BaseException, secrets: Iterable[str] = ()) -> str:
    """What goes into ingestion_runs.error: `<kind>:<Class>: <scrubbed text>` (private only)."""
    return f"{health_error(exc)}: {scrub(str(exc), secrets)}"[:ERROR_TEXT_MAX_CHARS]
