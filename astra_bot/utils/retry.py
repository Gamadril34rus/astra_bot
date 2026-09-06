"""
Retry decorator with exponential backoff for external API calls.

Block 1.2: Retry-logic for all external calls (Binance/BingX/Telegram).

- 3 attempts by default
- Delays: 2s -> 5s -> 15s (exponential)
- Handles: ConnectionError, Timeout, RateLimit, HTTPError, aiohttp.ClientError
- Logs full traceback to logs/errors.log
- Graceful degradation: returns None or fallback after retries exhausted
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
import traceback
from pathlib import Path
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Default retryable exceptions
RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
)

try:
    import aiohttp

    RETRYABLE_EXCEPTIONS = RETRYABLE_EXCEPTIONS + (aiohttp.ClientError, aiohttp.ClientConnectorError)
except ImportError:
    pass

try:
    import httpx

    RETRYABLE_EXCEPTIONS = RETRYABLE_EXCEPTIONS + (httpx.HTTPError, httpx.TimeoutException)
except ImportError:
    pass

# Try to import tenacity if available, but we implement own logic as fallback
try:
    from tenacity import (
        retry as tenacity_retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        before_sleep_log,
    )

    HAS_TENACITY = True
except ImportError:
    HAS_TENACITY = False


def _log_error_to_file(exc: Exception, func_name: str) -> None:
    """Log full traceback to logs/errors.log (Block 1.5)."""
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "errors.log"
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"[{timestamp}] {func_name} failed: {exc}\n")
            f.write(traceback.format_exc())
            f.write(f"\n{'='*60}\n")
        # Rotate if too large (>1MB)
        if log_file.stat().st_size > 1_000_000:
            # Keep last 500KB
            content = log_file.read_text(encoding="utf-8")[-500_000:]
            log_file.write_text(content, encoding="utf-8")
    except Exception as e:
        logger.debug("Failed to write error log: %s", e)


def retry(
    attempts: int = 3,
    delays: tuple[float, ...] = (2.0, 5.0, 15.0),
    exceptions: tuple[type[Exception], ...] = RETRYABLE_EXCEPTIONS,
    log_errors: bool = True,
    fallback: Any = None,
):
    """
    Synchronous retry decorator.

    Usage:
        @retry(attempts=3, delays=(2,5,15))
        def fetch_data():
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < attempts - 1:
                        delay = delays[attempt] if attempt < len(delays) else delays[-1]
                        # Add jitter ±20%
                        jitter = delay * 0.2 * (random.random() * 2 - 1)
                        actual_delay = max(0.5, delay + jitter)
                        logger.warning(
                            "%s attempt %d/%d failed: %s, retrying in %.1fs",
                            func.__name__,
                            attempt + 1,
                            attempts,
                            exc,
                            actual_delay,
                        )
                        time.sleep(actual_delay)
                    else:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            attempts,
                            exc,
                        )
                        if log_errors:
                            _log_error_to_file(exc, func.__name__)
                except Exception as exc:
                    # Non-retryable exception - log and re-raise or fallback
                    logger.error("%s non-retryable error: %s", func.__name__, exc)
                    if log_errors:
                        _log_error_to_file(exc, func.__name__)
                    if fallback is not None:
                        return fallback
                    raise
            # All retries exhausted
            if fallback is not None:
                logger.warning("%s returning fallback after %d failures", func.__name__, attempts)
                return fallback
            if last_exc:
                raise last_exc
            return fallback

        return wrapper  # type: ignore

    return decorator


def retry_async(
    attempts: int = 3,
    delays: tuple[float, ...] = (2.0, 5.0, 15.0),
    exceptions: tuple[type[Exception], ...] = RETRYABLE_EXCEPTIONS,
    log_errors: bool = True,
    fallback: Any = None,
):
    """
    Asynchronous retry decorator.

    Usage:
        @retry_async(attempts=3, delays=(2,5,15))
        async def fetch_data():
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < attempts - 1:
                        delay = delays[attempt] if attempt < len(delays) else delays[-1]
                        jitter = delay * 0.2 * (random.random() * 2 - 1)
                        actual_delay = max(0.5, delay + jitter)
                        logger.warning(
                            "%s attempt %d/%d failed: %s, retrying in %.1fs",
                            func.__name__,
                            attempt + 1,
                            attempts,
                            exc,
                            actual_delay,
                        )
                        await asyncio.sleep(actual_delay)
                    else:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            attempts,
                            exc,
                        )
                        if log_errors:
                            _log_error_to_file(exc, func.__name__)
                except Exception as exc:
                    # For API errors that are not in retryable list but should be retried
                    # Check by name: RateLimit, HTTPError, etc.
                    exc_name = type(exc).__name__
                    should_retry = any(
                        keyword in exc_name.lower()
                        for keyword in ["ratelimit", "timeout", "connection", "http", "network", "exchange"]
                    )
                    if should_retry and attempt < attempts - 1:
                        last_exc = exc
                        delay = delays[attempt] if attempt < len(delays) else delays[-1]
                        jitter = delay * 0.2 * (random.random() * 2 - 1)
                        actual_delay = max(0.5, delay + jitter)
                        logger.warning(
                            "%s attempt %d/%d failed (retryable by name %s): %s, retrying in %.1fs",
                            func.__name__,
                            attempt + 1,
                            attempts,
                            exc_name,
                            exc,
                            actual_delay,
                        )
                        await asyncio.sleep(actual_delay)
                        continue
                    logger.error("%s non-retryable error: %s", func.__name__, exc)
                    if log_errors:
                        _log_error_to_file(exc, func.__name__)
                    if fallback is not None:
                        return fallback
                    raise
            if fallback is not None:
                logger.warning("%s returning fallback after %d failures", func.__name__, attempts)
                return fallback
            if last_exc:
                raise last_exc
            return fallback

        return wrapper  # type: ignore

    return decorator


# If tenacity is available, provide a tenacity-based decorator as alternative
def tenacity_retry_decorator(attempts: int = 3):
    """Tenacity-based retry if library is available."""
    if not HAS_TENACITY:
        return retry_async(attempts=attempts)

    def decorator(func):
        return tenacity_retry(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(multiplier=1, min=2, max=15),
            retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )(func)

    return decorator
