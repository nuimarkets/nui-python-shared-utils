"""Typed failures, so a negative control can assert which check rejected an artifact."""

from typing import NoReturn

CODES = frozenset(
    {
        "ADAPTER_CONFIG",
        "BACKSTOP_DRIFT",
        "BUILD",
        "CLOSURE_DEPENDENCY",
        "CLOSURE_MISSING",
        "CLOSURE_ROOT",
        "DUPLICATE_MEMBER",
        "EXTRACTION_MISMATCH",
        "FORBIDDEN_PATH",
        "MAJOR_DRIFT",
        "METADATA_DUPLICATE",
        "MISSING_REQUIRED_PATH",
        "NEGATIVE_INEFFECTIVE",
        "NATIVE_ABI",
        "NATIVE_ELF",
        "NATIVE_EXPECTED",
        "NATIVE_UNDECLARED_LIB",
        "NO_PACKAGE",
        "OFFLINE_GUARD",
        "PIN_FORMAT",
        "PIN_MISMATCH",
        "REQUIRES_PYTHON",
        "SECRET_FILE",
        "SELF_CHECK",
        "SIZE_LIMIT",
        "TEST_MAPPING_STALE",
        "TEST_ONLY_LEAK",
        "TOOL_IN_ARTIFACT",
        "UNREACHABLE",
        "UNREADABLE_ZIP",
        "UNSAFE_MEMBER",
    }
)


class ValidationError(AssertionError):
    """A check rejected its input. ``code`` names the check; ``detail`` says why."""

    def __init__(self, code, detail):
        if code not in CODES:
            raise ValueError(f"unknown validation code {code!r}")
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def fail(code: str, detail: str) -> NoReturn:
    raise ValidationError(code, detail)
