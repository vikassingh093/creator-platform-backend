"""
Activity Tracker Middleware — Automatically tracks user online status.

How it works:
  1. Runs AFTER every request (response phase)
  2. Extracts JWT from Authorization header
  3. Decodes user_id from token (without DB call — uses cached payload)
  4. Sets Redis key last_active:{user_id} with TTL of 120s
  5. If anything fails → silently continues (never breaks the actual API response)

Performance:
  - Single Redis SET per request (~0.1ms)
  - No DB queries in the middleware
  - JWT decode is a lightweight operation
"""

import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.services.jwt_service import verify_token
from app.services.activity_service import update_user_activity

logger = logging.getLogger(__name__)


class ActivityTrackerMiddleware(BaseHTTPMiddleware):
    """
    Middleware to track user activity for online status.
    Runs after every authenticated request and updates Redis TTL key.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # ── Step 1: Process the actual request first (don't delay it) ──
        response = await call_next(request)

        # ── Step 2: After response, try to track activity (best-effort) ──
        try:
            self._track_activity(request)
        except Exception as e:
            # NEVER let activity tracking break a real API response
            logger.debug(f"Activity tracking skipped: {e}")

        return response

    def _track_activity(self, request: Request) -> None:
        """
        Extract user_id from JWT and update Redis activity key.
        This is a best-effort operation — failures are silently ignored.
        """
        # ── Skip non-API routes (static files, health checks, docs) ──
        path = request.url.path
        skip_prefixes = ("/uploads", "/docs", "/openapi.json", "/redoc", "/favicon")
        if any(path.startswith(prefix) for prefix in skip_prefixes):
            return

        # ── Extract Bearer token from Authorization header ──
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return  # No token → not authenticated → skip

        token = auth_header.replace("Bearer ", "").strip()
        if not token:
            return

        # ── Decode JWT to get user_id (no DB call) ──
        payload = verify_token(token)
        if not payload:
            return  # Invalid/expired token → skip

        # Extract user_id from JWT payload (matches auth_middleware pattern)
        user_id = payload.get("sub") or payload.get("user_id") or payload.get("id")
        if not user_id:
            return

        # ── Update Redis activity key ──
        update_user_activity(int(user_id))