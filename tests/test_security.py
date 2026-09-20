import re

from app.core.security import keyed_hash, new_token, sha256_hex


def test_new_token_is_unique_and_url_safe() -> None:
    tokens = {new_token() for _ in range(1000)}
    assert len(tokens) == 1000
    assert all(len(t) >= 43 for t in tokens)
    assert all(re.fullmatch(r"[A-Za-z0-9_-]+", t) for t in tokens)


def test_sha256_known_value() -> None:
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_keyed_hash_is_deterministic_and_salt_dependent() -> None:
    a = keyed_hash("203.0.113.7", "salt-a")
    assert a == keyed_hash("203.0.113.7", "salt-a")
    assert a != keyed_hash("203.0.113.7", "salt-b")
    assert "203.0.113.7" not in a
