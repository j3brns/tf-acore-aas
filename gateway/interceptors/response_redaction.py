from __future__ import annotations

import json
import re
from typing import Any

DEFAULT_PII_PATTERN_STRINGS: tuple[str, ...] = (
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    r"[A-CEGHJ-PR-TW-Z][A-CEGHJ-NPR-TW-Z]\s*\d{2}\s*\d{2}\s*\d{2}\s*[A-D]",
    r"\d{3}\s*\d{3}\s*\d{4}",
    r"\d{2}-\d{2}-\d{2}",
    r"\b\d{8}\b",
)


def compile_patterns(patterns: list[str], *, logger: Any) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern_str in patterns:
        try:
            compiled.append(re.compile(pattern_str, re.IGNORECASE))
        except re.error:
            logger.warning("Invalid PII regex pattern from SSM", pattern=pattern_str)
    return compiled


def parse_patterns(raw_patterns: str | None, *, logger: Any) -> list[str]:
    if not raw_patterns:
        return []

    decoded = json.loads(raw_patterns)
    if isinstance(decoded, dict):
        return [value for value in decoded.values() if isinstance(value, str)]
    if isinstance(decoded, list):
        return [value for value in decoded if isinstance(value, str)]

    logger.warning("Unexpected PII patterns format in SSM parameter", type=type(decoded).__name__)
    return []


def load_patterns(
    *,
    get_parameter: Any,
    parameter_name: str,
    logger: Any,
) -> list[re.Pattern[str]]:
    response = get_parameter(Name=parameter_name, WithDecryption=False)
    raw_patterns = response.get("Parameter", {}).get("Value")
    compiled = compile_patterns(parse_patterns(raw_patterns, logger=logger), logger=logger)
    if compiled:
        return compiled

    logger.warning("PII pattern set empty, using built-in defaults")
    return compile_patterns(list(DEFAULT_PII_PATTERN_STRINGS), logger=logger)


def default_patterns(*, logger: Any) -> list[re.Pattern[str]]:
    return compile_patterns(list(DEFAULT_PII_PATTERN_STRINGS), logger=logger)


def redact_pii(data: Any, *, patterns: list[re.Pattern[str]], redaction_token: str) -> Any:
    if isinstance(data, str):
        redacted = data
        for pattern in patterns:
            redacted = pattern.sub(redaction_token, redacted)
        return redacted
    if isinstance(data, dict):
        return {
            key: redact_pii(value, patterns=patterns, redaction_token=redaction_token)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [
            redact_pii(value, patterns=patterns, redaction_token=redaction_token) for value in data
        ]
    return data
