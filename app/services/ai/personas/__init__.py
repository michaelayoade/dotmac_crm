# Import personas to register them.
from app.services.ai.personas import campaign_optimizer as _campaign_optimizer  # noqa: F401
from app.services.ai.personas import customer_success as _customer_success  # noqa: F401
from app.services.ai.personas import dispatch_planner as _dispatch_planner  # noqa: F401
from app.services.ai.personas import inbox_analyst as _inbox_analyst  # noqa: F401
from app.services.ai.personas import performance_coach as _performance_coach  # noqa: F401
from app.services.ai.personas import project_advisor as _project_advisor  # noqa: F401
from app.services.ai.personas import ticket_analyst as _ticket_analyst  # noqa: F401
from app.services.ai.personas import vendor_analyst as _vendor_analyst  # noqa: F401
from app.services.ai.personas._registry import persona_registry

__all__ = ["persona_registry"]
