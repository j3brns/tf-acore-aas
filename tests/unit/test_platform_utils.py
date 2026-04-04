from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from src import platform_utils


def test_iso_utc_strips_microseconds_and_uses_z_suffix() -> None:
    value = datetime(2026, 4, 4, 12, 0, 1, 999999, tzinfo=UTC)
    assert platform_utils.iso_utc(value) == "2026-04-04T12:00:01Z"


def test_coerce_optional_string_trims_and_empty_to_none() -> None:
    assert platform_utils.coerce_optional_string(" hi ") == "hi"
    assert platform_utils.coerce_optional_string("   ") is None
    assert platform_utils.coerce_optional_string(None) is None


def test_coerce_positive_int_uses_default_on_invalid_and_minimum_one() -> None:
    assert platform_utils.coerce_positive_int("7", default=3) == 7
    assert platform_utils.coerce_positive_int("0", default=3) == 1
    assert platform_utils.coerce_positive_int("bad", default=3) == 3


def test_json_default_serializes_decimal() -> None:
    assert platform_utils.json_default(Decimal("2")) == 2
    assert platform_utils.json_default(Decimal("2.5")) == 2.5


def test_parse_json_object_or_empty_handles_invalid_or_non_object() -> None:
    assert platform_utils.parse_json_object_or_empty('{"a":1}') == {"a": 1}
    assert platform_utils.parse_json_object_or_empty("[]") == {}
    assert platform_utils.parse_json_object_or_empty("bad") == {}
