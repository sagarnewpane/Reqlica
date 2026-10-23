"""Collect structured handler exceptions without recording sensitive messages."""

import asyncio
import re

from reqlica.projects.inspection import exception_details, record_event


class ErrorInspectionMiddleware:
    def __init__(self, app, directory, code_directory):
        self.app = app
        self.directory = directory
        self.code_directory = code_directory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        except Exception as error:
            ids = [value.decode("ascii", errors="ignore") for name, value in scope.get("headers", [])
                   if name.lower() == b"x-reqlica-request-id"]
            request_id = ids[0] if len(ids) == 1 and re.fullmatch(r"[0-9a-f]{32}", ids[0]) else None
            await asyncio.to_thread(record_event, self.directory, {
                "kind": "runtime_error", "request_id": request_id,
                **exception_details(error, self.code_directory),
            })
            raise
