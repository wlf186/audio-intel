"""Scoped form limits; all other endpoints keep their existing parsing policy."""
from __future__ import annotations

from typing import Any
from fastapi import HTTPException, Request
from fastapi.routing import APIRoute


def document_route_class(config: Any) -> type[APIRoute]:
    class DocumentRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()
            if self.path != "/api/v1/tts/document-jobs":
                return handler

            async def route(request: Request):
                from .api import _admission_authenticated, require_api_key, SESSION_COOKIE
                if not _admission_authenticated(request):
                    # Authentication must precede the explicit form parser too.
                    from fastapi.security import HTTPAuthorizationCredentials
                    scheme, _, token = request.headers.get("authorization", "").partition(" ")
                    require_api_key(request, HTTPAuthorizationCredentials(scheme=scheme, credentials=token) if token else None,
                                    request.cookies.get(SESSION_COOKIE))
                receive = request._receive
                total = 0
                limit = min(config.max_upload_bytes, 100 * 1024**2) + 1024**2

                async def bounded_receive():
                    nonlocal total
                    message = await receive()
                    total += len(message.get("body", b""))
                    if total > limit:
                        raise HTTPException(413, "Document submission exceeds the limit")
                    return message

                request._receive = bounded_receive
                async with request.form(max_files=1, max_fields=config.max_document_sections + 20) as form:
                    if sum(len(str(value).encode("utf-8")) for _, value in form.multi_items() if isinstance(value, str)) > 1024**2:
                        raise HTTPException(413, "Document form fields exceed the limit")
                    return await handler(request)
            return route
    return DocumentRoute
