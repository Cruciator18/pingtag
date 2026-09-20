import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx

logger = logging.getLogger("app.request")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def _route_template(request: Request) -> str:
    """'/t/{public_id}', never the raw URL: real paths will contain secret tokens."""
    route = request.scope.get("route")
    return str(getattr(route, "path", "unmatched"))


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("x-request-id", "")
        # Only accept sane client-supplied IDs (prevents log injection).
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "route": _route_template(request),
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            return response
        except Exception:
            logger.exception(
                "request_failed",
                extra={"method": request.method, "route": _route_template(request)},
            )
            raise
        finally:
            request_id_ctx.reset(token)
