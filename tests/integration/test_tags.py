import re
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text

from app.services.tags import MAX_ACTIVE_TAGS
from tests.integration.helpers import Env, sign_in

pytestmark = pytest.mark.integration


@pytest.fixture
async def owner(env: Env) -> Env:
    await sign_in(env, "owner@example.com")
    return env


@pytest.fixture
async def other(owner: Env) -> AsyncIterator[Env]:
    transport = httpx.ASGITransport(app=owner.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        second = Env(client=client, email=owner.email, app=owner.app)
        await sign_in(second, "other@example.com")
        yield second


async def create_tag(env: Env, label: str = "Red Swift", kind: str = "car", note: str = "") -> str:
    r = await env.client.post("/tags", data={"label": label, "kind": kind, "public_note": note})
    assert r.status_code == 303, r.text
    return r.headers["location"].rsplit("/", 1)[1]


async def public_id_of(env: Env, tag_id: str) -> str:
    async with env.app.state.engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT public_id FROM tags WHERE id = :i"), {"i": uuid.UUID(tag_id)}
        )
    return str(value)


async def status_of(env: Env, tag_id: str) -> str:
    async with env.app.state.engine.connect() as conn:
        value = await conn.scalar(
            text("SELECT status FROM tags WHERE id = :i"), {"i": uuid.UUID(tag_id)}
        )
    return str(value)


async def test_create_and_view_tag(owner: Env) -> None:
    tag_id = await create_tag(owner, "Red Swift", "car", "Blocking? Ring the bell")
    page = await owner.client.get(f"/tags/{tag_id}")
    assert page.status_code == 200
    pid = await public_id_of(owner, tag_id)
    assert re.fullmatch(r"[a-z2-7]{16}", pid)
    assert f"http://test/t/{pid}" in page.text
    assert "Red Swift" in page.text
    assert "Blocking? Ring the bell" in page.text
    assert "Red Swift" in (await owner.client.get("/dashboard")).text


async def test_anonymous_requests_are_redirected_to_login(env: Env) -> None:
    some_id = uuid.uuid4()
    for path in ("/dashboard", "/tags/new", f"/tags/{some_id}", f"/tags/{some_id}/qr.svg"):
        r = await env.client.get(path)
        assert (r.status_code, r.headers["location"]) == (303, "/login"), path
    data = {"label": "x", "kind": "car", "public_note": ""}
    for path in ("/tags", f"/tags/{some_id}/rotate", f"/tags/{some_id}/revoke"):
        r = await env.client.post(path, data=data)
        assert (r.status_code, r.headers["location"]) == (303, "/login"), path


@pytest.mark.parametrize(
    ("label", "kind", "note"),
    [
        ("", "car", ""),
        ("x" * 81, "car", ""),
        ("Ok", "boat", ""),
        ("Ok", "car", "n" * 281),
    ],
)
async def test_invalid_input_is_rejected(owner: Env, label: str, kind: str, note: str) -> None:
    r = await owner.client.post("/tags", data={"label": label, "kind": kind, "public_note": note})
    assert r.status_code == 400
    assert 'role="alert"' in r.text
    assert 'class="card"' not in (await owner.client.get("/dashboard")).text


async def test_labels_are_html_escaped(owner: Env) -> None:
    tag_id = await create_tag(owner, label="<script>alert(1)</script>")
    page = await owner.client.get(f"/tags/{tag_id}")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("GET", ""),
        ("GET", "/edit"),
        ("GET", "/print"),
        ("GET", "/qr.svg"),
        ("GET", "/qr.png"),
        ("POST", "/edit"),
        ("POST", "/pause"),
        ("POST", "/resume"),
        ("POST", "/rotate"),
        ("POST", "/revoke"),
    ],
)
async def test_other_users_cannot_touch_my_tag(
    owner: Env, other: Env, method: str, suffix: str
) -> None:
    tag_id = await create_tag(owner)
    original_pid = await public_id_of(owner, tag_id)
    payload = {"label": "hacked", "kind": "car", "public_note": ""} if method == "POST" else None

    r = await other.client.request(method, f"/tags/{tag_id}{suffix}", data=payload)

    assert r.status_code == 404
    assert await public_id_of(owner, tag_id) == original_pid
    assert await status_of(owner, tag_id) == "active"
    page = await owner.client.get(f"/tags/{tag_id}")
    assert "Red Swift" in page.text
    assert "hacked" not in page.text


async def test_dashboards_are_isolated(owner: Env, other: Env) -> None:
    await create_tag(owner, "Private Tag")
    assert "Private Tag" not in (await other.client.get("/dashboard")).text


async def test_pause_resume_and_revoke_lifecycle(owner: Env) -> None:
    tag_id = await create_tag(owner)

    assert (await owner.client.post(f"/tags/{tag_id}/pause")).status_code == 303
    assert await status_of(owner, tag_id) == "paused"
    assert "Resume tag" in (await owner.client.get(f"/tags/{tag_id}")).text

    assert (await owner.client.post(f"/tags/{tag_id}/resume")).status_code == 303
    assert await status_of(owner, tag_id) == "active"
    assert "Pause tag" in (await owner.client.get(f"/tags/{tag_id}")).text

    assert (await owner.client.post(f"/tags/{tag_id}/revoke")).status_code == 303
    assert await status_of(owner, tag_id) == "revoked"

    # Revoked is terminal.
    for action in ("pause", "resume", "rotate"):
        r = await owner.client.post(f"/tags/{tag_id}/{action}")
        assert r.status_code == 409, action
    assert (await owner.client.get(f"/tags/{tag_id}/edit")).status_code == 409
    assert (await owner.client.get(f"/tags/{tag_id}/qr.svg")).status_code == 404
    assert (await owner.client.get(f"/tags/{tag_id}/print")).status_code == 404
    detail = await owner.client.get(f"/tags/{tag_id}")
    assert detail.status_code == 200
    assert "no longer works" in detail.text
    assert await status_of(owner, tag_id) == "revoked"


async def test_rotate_replaces_public_id(owner: Env) -> None:
    tag_id = await create_tag(owner)
    old = await public_id_of(owner, tag_id)
    assert (await owner.client.post(f"/tags/{tag_id}/rotate")).status_code == 303
    new = await public_id_of(owner, tag_id)
    assert new != old
    assert re.fullmatch(r"[a-z2-7]{16}", new)
    page = await owner.client.get(f"/tags/{tag_id}")
    assert new in page.text
    assert old not in page.text


async def test_edit_updates_fields(owner: Env) -> None:
    tag_id = await create_tag(owner, "Old name", "car", "")
    form = await owner.client.get(f"/tags/{tag_id}/edit")
    assert form.status_code == 200
    assert 'value="Old name"' in form.text

    r = await owner.client.post(
        f"/tags/{tag_id}/edit", data={"label": "New name", "kind": "pet", "public_note": "Friendly"}
    )
    assert r.status_code == 303
    detail = await owner.client.get(f"/tags/{tag_id}")
    assert "New name" in detail.text
    assert "Friendly" in detail.text
    assert "Old name" not in detail.text

    bad = await owner.client.post(
        f"/tags/{tag_id}/edit", data={"label": "", "kind": "pet", "public_note": ""}
    )
    assert bad.status_code == 400
    assert "New name" in (await owner.client.get(f"/tags/{tag_id}")).text


async def test_qr_endpoints(owner: Env) -> None:
    tag_id = await create_tag(owner, label='Ma "Swift"; <b>')

    svg = await owner.client.get(f"/tags/{tag_id}/qr.svg")
    assert svg.status_code == 200
    assert svg.headers["content-type"].startswith("image/svg+xml")
    assert svg.headers["cache-control"] == "no-store"
    assert b"<svg" in svg.content
    assert "content-disposition" not in svg.headers

    png = await owner.client.get(f"/tags/{tag_id}/qr.png?download=1")
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content.startswith(b"\x89PNG")
    assert png.headers["content-disposition"] == 'attachment; filename="pingtag-ma-swift-b.png"'


async def test_print_sheet(owner: Env) -> None:
    tag_id = await create_tag(owner)
    page = await owner.client.get(f"/tags/{tag_id}/print")
    assert page.status_code == 200
    assert page.text.count(f"/tags/{tag_id}/qr.svg") == 3


async def test_tag_limit_counts_only_live_tags(owner: Env) -> None:
    ids = [await create_tag(owner, f"Tag {i}") for i in range(MAX_ACTIVE_TAGS)]
    data = {"label": "One too many", "kind": "car", "public_note": ""}

    r = await owner.client.post("/tags", data=data)
    assert r.status_code == 400
    assert "limit" in r.text.lower()

    await owner.client.post(f"/tags/{ids[0]}/revoke")
    await create_tag(owner, "Fits now")
