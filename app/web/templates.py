from __future__ import annotations

from typing import Any, cast

from fastapi.templating import Jinja2Templates as FastAPIJinja2Templates
from starlette.templating import _TemplateResponse


class Jinja2Templates(FastAPIJinja2Templates):
    """Compatibility wrapper that accepts legacy TemplateResponse argument order."""

    def TemplateResponse(self, *args: Any, **kwargs: Any) -> _TemplateResponse:
        if args and isinstance(args[0], str):
            name = args[0]
            context = args[1] if len(args) > 1 else kwargs.get("context", {})
            status_code = args[2] if len(args) > 2 else kwargs.get("status_code", 200)
            headers = args[3] if len(args) > 3 else kwargs.get("headers")
            media_type = args[4] if len(args) > 4 else kwargs.get("media_type")
            background = args[5] if len(args) > 5 else kwargs.get("background")
            if "request" not in context:
                raise ValueError('context must include a "request" key')
            request = context["request"]
            return super().TemplateResponse(
                request,
                name,
                context,
                status_code=status_code,
                headers=headers,
                media_type=media_type,
                background=background,
            )
        if not args and "request" not in kwargs and "request" in kwargs.get("context", {}):
            kwargs["request"] = kwargs["context"]["request"]
        if not args and "name" in kwargs and "context" in kwargs:
            return super().TemplateResponse(
                kwargs["request"],
                cast(str, kwargs["name"]),
                kwargs["context"],
                status_code=kwargs.get("status_code", 200),
                headers=kwargs.get("headers"),
                media_type=kwargs.get("media_type"),
                background=kwargs.get("background"),
            )
        return super().TemplateResponse(*args, **kwargs)
