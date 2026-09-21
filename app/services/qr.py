import io
import re

import segno


def build_scan_url(base_url: str, public_id: str) -> str:
    return f"{base_url.rstrip('/')}/t/{public_id}"


def make_qr(url: str) -> segno.QRCode:
    # Error correction Q (~25% damage tolerance); segno raises it to H if it fits the same size.
    return segno.make(url, error="q", micro=False)


def render_svg(url: str, scale: int = 10) -> bytes:
    buf = io.BytesIO()
    make_qr(url).save(buf, kind="svg", scale=scale, border=4, dark="#000000", light="#ffffff")
    return buf.getvalue()


def render_png(url: str, scale: int = 10) -> bytes:
    buf = io.BytesIO()
    make_qr(url).save(buf, kind="png", scale=scale, border=4, dark="#000000", light="#ffffff")
    return buf.getvalue()


def download_name(label: str, ext: str) -> str:
    """Filename built only from [a-z0-9-], so it is safe inside a Content-Disposition header."""
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40] or "tag"
    return f"pingtag-{slug}.{ext}"
