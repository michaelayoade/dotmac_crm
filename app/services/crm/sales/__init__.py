"""CRM Sales submodule.

Handles pipelines, leads, quotes, and sales workflow.
"""

from app.services.crm.sales.service import (
    Pipelines,
    PipelineStages,
    Leads,
    Quotes,
    CrmQuoteLineItems,
    pipelines,
    pipeline_stages,
    leads,
    quotes,
    quote_line_items,
)

__all__ = [
    "Pipelines",
    "PipelineStages",
    "Leads",
    "Quotes",
    "CrmQuoteLineItems",
    "pipelines",
    "pipeline_stages",
    "leads",
    "quotes",
    "quote_line_items",
]
