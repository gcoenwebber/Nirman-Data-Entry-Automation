"""
Gemini API wrapper with retry logic, rate limiting, and JSON response parsing.

Uses the ``google-genai`` SDK (``google.genai``) — *not* the legacy
``google-generativeai`` package.

All configuration (API key, model name, retry limits, rate caps) is loaded
from ``nirman.config`` so nothing is hard-coded here.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any, Optional

from google import genai
from google.genai import types

from nirman import config

log = logging.getLogger(__name__)


# ── Lazy singleton client ──────────────────────────────────────────────

_client: Optional[genai.Client] = None
_client_lock = threading.Lock()


def get_client() -> genai.Client:
    """Return the lazily-initialised Gemini client (thread-safe singleton).

    The client is created once using ``config.GEMINI_API_KEY`` and reused for
    all subsequent calls.
    """
    global _client
    if _client is None:
        with _client_lock:
            # Double-checked locking
            if _client is None:
                if not config.GEMINI_API_KEY:
                    raise RuntimeError(
                        "GEMINI_API_KEY is not set. "
                        "Add it to your .env file or set the environment variable."
                    )
                log.info("Initialising Gemini client (model default: %s)", config.GEMINI_MODEL)
                _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


# ── Token-bucket rate limiter ──────────────────────────────────────────

class _TokenBucketRateLimiter:
    """Simple token-bucket rate limiter enforcing *requests per minute*.

    Parameters
    ----------
    rpm : int
        Maximum requests per minute.  Each call to :meth:`acquire` consumes
        one token and blocks (sleeps) until a token is available.
    """

    def __init__(self, rpm: int) -> None:
        self._rpm = max(rpm, 1)
        self._interval = 60.0 / self._rpm  # seconds between tokens
        self._lock = threading.Lock()
        self._last_time: float = 0.0

    def acquire(self) -> None:
        """Block until a request token is available."""
        with self._lock:
            now = time.monotonic()
            wait = self._interval - (now - self._last_time)
            if wait > 0:
                log.debug("Rate limiter: sleeping %.2fs", wait)
                time.sleep(wait)
            self._last_time = time.monotonic()


_rate_limiter = _TokenBucketRateLimiter(config.RATE_LIMIT_RPM)


# ── Retry helpers ──────────────────────────────────────────────────────

_RETRY_DELAY_PATTERN = re.compile(r"retryDelay\D*([\d.]+)\s*s", re.IGNORECASE)


def _parse_retry_delay(error: Exception) -> Optional[float]:
    """Try to extract a ``retryDelay`` value (in seconds) from an API error.

    The Gemini API sometimes embeds a ``retryDelay`` hint in the error message
    for 429/RESOURCE_EXHAUSTED responses.  Returns ``None`` if no delay is
    found.
    """
    msg = str(error)
    match = _RETRY_DELAY_PATTERN.search(msg)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


def _is_retryable(error: Exception) -> bool:
    """Determine whether an API error is transient and worth retrying."""
    msg = str(error).lower()
    return any(
        token in msg
        for token in ("503", "unavailable", "429", "resource_exhausted", "resource exhausted")
    )


def _is_rate_limit(error: Exception) -> bool:
    """Return ``True`` if the error is a 429 / RESOURCE_EXHAUSTED."""
    msg = str(error).lower()
    return "429" in msg or "resource_exhausted" in msg or "resource exhausted" in msg


# ── Core API call ──────────────────────────────────────────────────────

def call_gemini(
    contents: Any,
    *,
    model: Optional[str] = None,
    response_mime_type: str = "application/json",
    temperature: float = 0.0,
) -> Any:
    """Call the Gemini API with automatic retry and rate limiting.

    Parameters
    ----------
    contents :
        Prompt contents — text, image parts, or a list of content parts as
        accepted by the ``google-genai`` SDK.
    model :
        Model name.  Defaults to ``config.GEMINI_MODEL``.
    response_mime_type :
        Response MIME type (``"application/json"`` by default).
    temperature :
        Sampling temperature.  ``0.0`` gives deterministic output.

    Returns
    -------
    response
        The raw ``GenerateContentResponse`` from the SDK.

    Raises
    ------
    Exception
        The last exception encountered if all retry attempts are exhausted.
    """
    model = model or config.GEMINI_MODEL
    client = get_client()

    gen_config = types.GenerateContentConfig(
        response_mime_type=response_mime_type,
        temperature=temperature,
    )

    last_error: Optional[Exception] = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        # Respect the global rate limit before each request
        _rate_limiter.acquire()

        try:
            log.debug("Gemini API call attempt %d/%d (model=%s)", attempt, config.MAX_RETRIES, model)

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=gen_config,
            )

            # Log token usage (safely)
            _log_token_usage(response, model)

            return response

        except Exception as exc:
            last_error = exc

            if not _is_retryable(exc):
                log.error("Non-retryable Gemini API error: %s", exc)
                raise

            if attempt == config.MAX_RETRIES:
                log.error(
                    "All %d Gemini API retries exhausted. Last error: %s",
                    config.MAX_RETRIES,
                    exc,
                )
                raise

            # Compute backoff delay
            if _is_rate_limit(exc):
                parsed_delay = _parse_retry_delay(exc)
                delay = parsed_delay if parsed_delay else config.BASE_RETRY_DELAY * (2 ** (attempt - 1))
                log.warning(
                    "Rate limited (429). Attempt %d/%d — retrying in %.1fs",
                    attempt, config.MAX_RETRIES, delay,
                )
            else:
                delay = config.BASE_RETRY_DELAY * (2 ** (attempt - 1))
                log.warning(
                    "Transient API error (503/UNAVAILABLE). Attempt %d/%d — retrying in %.1fs: %s",
                    attempt, config.MAX_RETRIES, delay, exc,
                )

            time.sleep(delay)

    # Should be unreachable, but satisfy type-checkers
    assert last_error is not None
    raise last_error


def _log_token_usage(response: Any, model: str) -> None:
    """Log token counts from the response metadata, if available."""
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        prompt_tokens = getattr(usage, "prompt_token_count", None)
        candidates_tokens = getattr(usage, "candidates_token_count", None)
        total_tokens = getattr(usage, "total_token_count", None)
        log.info(
            "Token usage [%s]: prompt=%s, candidates=%s, total=%s",
            model,
            prompt_tokens,
            candidates_tokens,
            total_tokens,
        )
    except Exception:
        # Never let logging break the main flow
        pass


# ── JSON response parsing ─────────────────────────────────────────────

_FENCE_PATTERN = re.compile(
    r"```(?:json)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def parse_json_response(response: Any) -> Optional[dict | list]:
    """Extract and parse a JSON object / array from a Gemini response.

    Handles responses wrapped in markdown code-fences
    (e.g. ``\\`\\`\\`json ... \\`\\`\\```).

    Parameters
    ----------
    response :
        A ``GenerateContentResponse`` returned by :func:`call_gemini`.

    Returns
    -------
    dict | list | None
        Parsed JSON data, or ``None`` if parsing fails.
    """
    try:
        text = response.text
        if text is None:
            log.warning("Gemini response contained no text")
            return None
    except Exception as exc:
        log.warning("Could not access response.text: %s", exc)
        return None

    text = text.strip()

    # Strip markdown fences if present
    fence_match = _FENCE_PATTERN.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Log a truncated preview to avoid flooding logs
        preview = text[:200] + ("…" if len(text) > 200 else "")
        log.warning("JSON parse failed: %s | Preview: %s", exc, preview)
        return None
