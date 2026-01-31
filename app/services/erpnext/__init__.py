"""ERPNext integration module.

Provides one-time import of data from ERPNext/Frappe:
- HD Tickets → Tickets
- Projects → Projects
- Tasks → Project Tasks
- Contacts → Persons
- Customers → Organizations/Subscribers
- Leads → CRM Leads
- Quotations → CRM Quotes
"""

from app.services.erpnext.client import ERPNextClient
from app.services.erpnext.importer import ERPNextImporter

__all__ = [
    "ERPNextClient",
    "ERPNextImporter",
]
