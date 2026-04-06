"""Authentication web route builder."""


def build_router():
    from app.web.auth.routes import router

    return router


__all__ = ["build_router"]
