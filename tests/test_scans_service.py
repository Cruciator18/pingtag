from decimal import Decimal

import pytest

from app.models import TagKind
from app.services.scans import (
    MESSAGE_MAX,
    REASONS,
    ScanValidationError,
    parse_optional_float,
    validate_scan_input,
)


def test_every_kind_has_unique_reasons_including_other() -> None:
    for kind in TagKind:
        keys = [r.key for r in REASONS[kind]]
        assert len(keys) == len(set(keys))
        assert "other" in keys


def test_valid_input_is_cleaned_and_location_rounded() -> None:
    data = validate_scan_input(TagKind.CAR, "blocking", "  hi \n there ", 12.34567, -77.98765)
    assert data.message == "hi there"
    assert data.lat == Decimal("12.346")
    assert data.lng == Decimal("-77.988")


def test_empty_message_and_no_location() -> None:
    data = validate_scan_input(TagKind.PET, "found", "   ", None, None)
    assert data.message is None
    assert data.lat is None
    assert data.lng is None


@pytest.mark.parametrize("reason", ["", "BLOCKING", "injured", "nonsense"])
def test_reason_must_belong_to_the_tag_kind(reason: str) -> None:
    with pytest.raises(ScanValidationError):
        validate_scan_input(TagKind.CAR, reason, "", None, None)


@pytest.mark.parametrize("message", ["x" * (MESSAGE_MAX + 1), "bad\x00msg", "zero\u200bwidth"])
def test_bad_messages_are_rejected(message: str) -> None:
    with pytest.raises(ScanValidationError):
        validate_scan_input(TagKind.CAR, "blocking", message, None, None)


@pytest.mark.parametrize(
    ("lat", "lng"),
    [
        (12.0, None),
        (None, 12.0),
        (91.0, 0.0),
        (0.0, 181.0),
        (float("nan"), 0.0),
        (float("inf"), 0.0),
    ],
)
def test_bad_locations_are_rejected(lat: float | None, lng: float | None) -> None:
    with pytest.raises(ScanValidationError):
        validate_scan_input(TagKind.CAR, "blocking", "", lat, lng)


def test_parse_optional_float() -> None:
    assert parse_optional_float("") is None
    assert parse_optional_float(" 12.5 ") == 12.5
    with pytest.raises(ScanValidationError):
        parse_optional_float("abc")
