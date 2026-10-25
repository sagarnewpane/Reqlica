"""Dynamic CORS for published project traffic, outside worker/auth/quota handling."""

import asyncio
import re

from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from reqlica.projects.cors import runtime_cors

_PROJECT_PATH = re.compile(r"^/mock/([0-9a-f]{32})(?:/|$)")


class ProjectCorsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        match = _PROJECT_PATH.match(scope["path"])
        origins = [value for name, value in scope.get("headers", []) if name.lower() == b"origin"]
        if match is None or not origins:
            return await self.app(scope, receive, send)
        try:
            settings = await asyncio.to_thread(runtime_cors, match[1])
        except FileNotFoundError:
            # Let worker lifecycle handling decide unknown/stopped project errors.
            return await self.app(scope, receive, send)
        except (OSError, ValueError):
            return await JSONResponse({"detail":"CORS settings unavailable"}, status_code=503)(scope, receive, send)
        if not settings.enabled:
            return await self.app(scope, receive, send)
        if len(origins) != 1:
            return await JSONResponse({"detail":"Invalid Origin headers"}, status_code=400)(scope, receive, send)

        async def clean_app(scope, receive, cors_send):
            async def clean_send(message):
                if message["type"] == "http.response.start":
                    message = {**message, "headers": [(key, value) for key, value in message.get("headers", [])
                                                      if not key.lower().startswith(b"access-control-")]}
                await cors_send(message)
            await self.app(scope, receive, clean_send)

        cors = CORSMiddleware(clean_app, **settings.model_dump(exclude={"enabled"}))
        await cors(scope, receive, send)
