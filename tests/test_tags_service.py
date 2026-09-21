import re

import pytest

from app.models import TagKind
from app.services import qr
from app.services.tags import (
    LABEL_MAX,
    NOTE_MAX,
    TagValidationError,
    generate_public_id,
    validate_tag_input,
)


def test_public_id_format_and_uniqueness() -> None:
    ids = {generate_public_id() for _ in range(2000)}
    assert len(ids) == 2000
    assert all(re.fullmatch(r"[a-z2-7]{16}", i) for i in ids)


def test_validation_trims_and_normalises() -> None:
    data = validate_tag_input("  Red   Swift \n", "car", "  parked   here \n ")
    assert data.label == "Red Swift"
    assert data.kind is TagKind.CAR
    assert data.public_note == "parked here"


def test_empty_note_becomes_none() -> None:
    assert validate_tag_input("Bruno", "pet", "   ").public_note is None


@pytest.mark.parametrize(
    "label",
    ["", "   ", "x" * (LABEL_MAX + 1), "bad\x00label", "zero\u200bwidth"],
)
def test_bad_labels_are_rejected(label: str) -> None:
    with pytest.raises(TagValidationError):
        validate_tag_input(label, "car", "")


def test_bad_kind_and_long_note_are_rejected() -> None:
    with pytest.raises(TagValidationError):
        validate_tag_input("Ok", "boat", "")
    with pytest.raises(TagValidationError):
        validate_tag_input("Ok", "car", "n" * (NOTE_MAX + 1))


def test_scan_url_building() -> None:
    assert qr.build_scan_url("https://ping.example/", "abc") == "https://ping.example/t/abc"


def test_qr_error_correction_is_q_or_better() -> None:
    code = qr.make_qr("https://ping.example/t/abcdefghijklmnop")
    assert code.error in {"Q", "H"}


def test_qr_outputs() -> None:
    url = "https://ping.example/t/abcdefghijklmnop"
    assert b"<svg" in qr.render_svg(url)
    assert qr.render_png(url).startswith(b"\x89PNG\r\n\x1a\n")


def test_download_name_is_sanitised() -> None:
    assert qr.download_name('Ma "Swift"; <b>', "svg") == "pingtag-ma-swift-b.svg"
    assert qr.download_name("!!!", "png") == "pingtag-tag.png"
