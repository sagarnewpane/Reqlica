"""Explicit JSON responses with FastAPI-compatible value encoding."""

from typing import Any

from fastapi.encoders import jsonable_encoder
from starlette.responses import JSONResponse


class APIJSONResponse(JSONResponse):
    """Encode models, datetime, UUID and nested values before JSON rendering.

    Unsupported objects still fail rather than silently becoming strings.
    """

    def render(self, content: Any) -> bytes:
        return super().render(jsonable_encoder(content))
