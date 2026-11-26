"""
Error handling utilities for Lambda services with retry logic and pattern matching.
Provides decorators for automatic retries and error categorization.
"""

import time
import logging
import functools
import random
from typing import Callable, Dict, List, Optional, Any, Union
from datetime import datetime
import re

log = logging.getLogger(__name__)


# Known error patterns from production
ERROR_PATTERNS = {
    "json_parse": {
        "pattern": r"Unable to parse JSON data.*Syntax error",
        "category": "data_format",
        "severity": "critical",
        "description": "JSON parsing error - malformed input data",
    },
    "auth_failure": {
        "pattern": r"Client authentication failed",
        "category": "authentication",
        "severity": "warning",
        "description": "OAuth client authentication failure",
    },
    "db_timeout": {
        "pattern": r"Maximum execution time.*exceeded|Query execution was interrupted",
        "category": "database",
        "severity": "critical",
        "description": "Database query timeout",
    },
    "connection_refused": {
        "pattern": r"Connection refused|ECONNREFUSED",
        "category": "network",
        "severity": "critical",
        "description": "Service connection refused",
    },
    "rate_limit": {
        "pattern": r"Rate limit exceeded|Too many requests|429",
        "category": "rate_limit",
        "severity": "warning",
        "description": "API rate limit exceeded",
    },
    "not_found": {
        "pattern": r"404 Not Found|Record not found|Entity not found",
        "category": "not_found",
        "severity": "info",
        "description": "Requested resource not found",
    },
    "permission_denied": {
        "pattern": r"Permission denied|Access denied|403 Forbidden",
        "category": "authorization",
        "severity": "critical",
        "description": "Permission denied for operation",
    },
    "es_timeout": {
        "pattern": r"ConnectionTimeout|search_phase_execution_exception.*timeout",
        "category": "elasticsearch",
        "severity": "critical",
        "description": "Elasticsearch query timeout",
    },
    "memory_error": {
        "pattern": r"MemoryError|Cannot allocate memory|Out of memory",
        "category": "resource",
        "severity": "critical",
        "description": "Memory allocation error",
    },
    "ssl_error": {
        "pattern": r"SSL.*error|certificate verify failed",
        "category": "security",
        "severity": "critical",
        "description": "SSL/TLS certificate error",
    },
}


class RetryableError(Exception):
    """Exception that should trigger a retry."""

    pass


class NonRetryableError(Exception):
    """Exception that should not trigger a retry."""

    pass


class ErrorPatternMatcher:
    """Match errors against known patterns for categorization."""

    def __init__(self, patterns: Optional[Dict] = None):
        self.patterns = patterns or ERROR_PATTERNS
        self.compiled_patterns: Dict[str, Any] = {}
        self._compile_patterns()

    def _compile_patterns(self):
        """Pre-compile regex patterns for efficiency."""
        for key, config in self.patterns.items():
            self.compiled_patterns[key] = re.compile(config["pattern"], re.IGNORECASE)

    def match_error(self, error: Union[str, Exception]) -> Optional[Dict]:
        """
        Match an error against known patterns.

        Returns:
            Dict with pattern info if matched, None otherwise
        """
        error_str = str(error)

        for key, pattern in self.compiled_patterns.items():
            if pattern.search(error_str):
                return {"pattern_key": key, **self.patterns[key]}

        return None

    def categorize_error(self, error: Union[str, Exception]) -> Dict:
        """
        Categorize an error and return enriched information.
        """
        match = self.match_error(error)

        if match:
            return {
                "error": str(error),
                "category": match["category"],
                "severity": match["severity"],
                "description": match["description"],
                "pattern_matched": match["pattern_key"],
                "is_retryable": match["category"] in ["network", "database", "elasticsearch", "rate_limit"],
            }

        # Default categorization for unmatched errors
        return {
            "error": str(error),
            "category": "unknown",
            "severity": "warning",
            "description": "Unrecognized error pattern",
            "pattern_matched": None,
            "is_retryable": False,
        }


def with_retry(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True,
    retryable_exceptions: tuple = (Exception,),
    non_retryable_exceptions: tuple = (NonRetryableError,),
    on_retry: Optional[Callable] = None,
) -> Callable:
    """
    Decorator for automatic retry with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts
        initial_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff
        jitter: Add random jitter to prevent thundering herd
        retryable_exceptions: Tuple of exceptions that trigger retry
        non_retryable_exceptions: Tuple of exceptions that should not retry
        on_retry: Optional callback function called on each retry

    Example:
        @with_retry(max_attempts=3, initial_delay=2.0)
        def fetch_data():
            return requests.get('https://api.example.com/data')
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            delay = initial_delay
            last_exception = None

            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)

                except non_retryable_exceptions as e:
                    log.error(f"{func.__name__} failed with non-retryable error: {e}")
                    raise

                except retryable_exceptions as e:
                    attempt += 1
                    last_exception = e

                    if attempt >= max_attempts:
                        log.error(f"{func.__name__} failed after {max_attempts} attempts: {e}")
                        raise

                    # Calculate delay with exponential backoff
                    if jitter:
                        # Add random jitter (0.5 to 1.5 times the delay)
                        actual_delay = delay * (0.5 + random.random())
                    else:
                        actual_delay = delay

                    # Cap at max_delay
                    actual_delay = min(actual_delay, max_delay)

                    log.warning(
                        f"{func.__name__} failed (attempt {attempt}/{max_attempts}), "
                        f"retrying in {actual_delay:.1f}s: {e}"
                    )

                    # Call retry callback if provided
                    if on_retry:
                        on_retry(func.__name__, attempt, e, actual_delay)

                    time.sleep(actual_delay)

                    # Exponential backoff for next attempt
                    delay *= exponential_base

            # Should not reach here, but just in case
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


def categorize_retryable_error(error: Exception) -> bool:
    """
    Determine if an error should be retried based on its pattern.

    Args:
        error: The exception to categorize

    Returns:
        bool: True if error should be retried
    """
    matcher = ErrorPatternMatcher()
    result = matcher.categorize_error(error)
    return result["is_retryable"]


class ErrorAggregator:
    """Aggregate errors for batch reporting."""

    def __init__(self, max_errors: int = 100):
        self.errors: List[Dict] = []
        self.max_errors = max_errors
        self.matcher = ErrorPatternMatcher()

    def add_error(self, error: Union[str, Exception], context: Optional[Dict] = None):
        """Add an error to the aggregator."""
        categorized = self.matcher.categorize_error(error)

        error_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(error),
            "type": type(error).__name__ if isinstance(error, Exception) else "str",
            **categorized,
        }

        if context:
            error_entry["context"] = context

        self.errors.append(error_entry)

        # Keep only the most recent errors
        if len(self.errors) > self.max_errors:
            self.errors = self.errors[-self.max_errors :]

    def get_summary(self) -> Dict:
        """Get error summary statistics."""
        if not self.errors:
            return {"total_errors": 0, "by_category": {}, "by_severity": {}, "recent_errors": []}

        by_category: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}

        for error in self.errors:
            # Count by category
            category = error["category"]
            by_category[category] = by_category.get(category, 0) + 1

            # Count by severity
            severity = error["severity"]
            by_severity[severity] = by_severity.get(severity, 0) + 1

        return {
            "total_errors": len(self.errors),
            "by_category": by_category,
            "by_severity": by_severity,
            "recent_errors": self.errors[-5:],  # Last 5 errors
        }

    def clear(self):
        """Clear all aggregated errors."""
        self.errors = []


def handle_lambda_error(error: Exception, context: Dict) -> Dict:
    """
    Standard error handler for Lambda functions.

    Args:
        error: The exception that occurred
        context: Dictionary with optional function_name and aws_request_id metadata

    Returns:
        Dict: Standardized error response
    """
    matcher = ErrorPatternMatcher()
    categorized = matcher.categorize_error(error)

    log.error(
        f"Lambda error: {error}",
        exc_info=True,
        extra={
            "error_category": categorized["category"],
            "error_severity": categorized["severity"],
            "function_name": context.get("function_name", "unknown"),
            "request_id": context.get("aws_request_id", "unknown"),
        },
    )

    return {
        "statusCode": 500,
        "body": {
            "error": categorized["description"],
            "category": categorized["category"],
            "request_id": context.get("aws_request_id", "unknown"),
            "timestamp": datetime.utcnow().isoformat(),
        },
    }


# Convenience decorators for common retry scenarios
def retry_on_network_error(func: Callable) -> Callable:
    """Retry decorator specifically for network-related errors."""
    return with_retry(
        max_attempts=3,
        initial_delay=2.0,
        retryable_exceptions=(ConnectionError, TimeoutError, OSError),
        on_retry=lambda name, attempt, error, delay: log.info(f"Network retry for {name}: attempt {attempt}"),
    )(func)


def retry_on_db_error(func: Callable) -> Callable:
    """Retry decorator specifically for database errors."""
    return with_retry(
        max_attempts=3,
        initial_delay=1.0,
        max_delay=10.0,
        retryable_exceptions=(Exception,),  # You might want to specify pymysql exceptions
        on_retry=lambda name, attempt, error, delay: log.info(f"Database retry for {name}: attempt {attempt}"),
    )(func)


def retry_on_es_error(func: Callable) -> Callable:
    """Retry decorator specifically for Elasticsearch errors."""
    return with_retry(
        max_attempts=3,
        initial_delay=2.0,
        max_delay=30.0,
        retryable_exceptions=(Exception,),  # You might want to specify ES exceptions
        on_retry=lambda name, attempt, error, delay: log.info(f"Elasticsearch retry for {name}: attempt {attempt}"),
    )(func)
