"""CRM Sales submodule.

Handles pipelines, leads, quotes, and sales workflow.
"""

from app.services.crm.sales.service import (
    CrmQuoteLineItems,
    Leads,
    Pipelines,
    PipelineStages,
    Quotes,
    leads,
    pipeline_stages,
    pipelines,
    quote_line_items,
    quotes,
)

__all__ = [
    "CrmQuoteLineItems",
    "Leads",
    "PipelineStages",
    "Pipelines",
    "Quotes",
    "leads",
    "pipeline_stages",
    "pipelines",
    "quote_line_items",
    "quotes",
]
