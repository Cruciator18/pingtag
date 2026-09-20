import asyncio
import logging
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ops"])

PROBE_TIMEOUT_S = 2.0


async def _probe(name: str, check: Callable[[], Awaitable[object]]) -> tuple[str, bool]:
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            await check()
    except Exception:
        # Details go to the logs, never to the response.
        logger.warning("readiness_probe_failed", extra={"dependency": name}, exc_info=True)
        return name, False
    return name, True


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness: the process is up. Never touches dependencies."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    """Readiness: Postgres and Redis are reachable."""
    state = request.app.state

    async def check_db() -> None:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    async def check_redis() -> object:
        return await state.redis.ping()

    results = await asyncio.gather(_probe("database", check_db), _probe("redis", check_redis))
    checks = {name: ("ok" if ok else "fail") for name, ok in results}
    ready = all(ok for _, ok in results)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "unavailable", "checks": checks},
    )
