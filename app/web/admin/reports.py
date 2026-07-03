"""Admin reports web routes."""

import csv
import io
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypedDict
from urllib.parse import quote, urlencode
from uuid import UUID
from xml.sax.saxutils import escape  # nosec B406 - only XML-escapes generated workbook values
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, Form, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.csrf import get_csrf_token
from app.db import get_db
from app.models.crm.conversation import Conversation
from app.models.dispatch import TechnicianProfile
from app.models.person import Person, PersonChannel
from app.models.projects import ProjectTask, ProjectTaskAssignee, TaskStatus
from app.models.subscriber import Subscriber, SubscriberStatus
from app.models.tickets import Ticket, TicketComment, TicketStatus
from app.models.workforce import WorkOrder, WorkOrderStatus
from app.services import operations_sla_reports as operations_sla_reports_service
from app.services.auth_dependencies import require_any_permission
from app.services.crm import reports as crm_reports_service
from app.services.crm import team as crm_team_service
from app.services.person_identity import is_placeholder_email
from app.services.quarterly_reports import build_quarterly_report
from app.tasks.subscribers import sync_subscribers_from_selfcare
from app.web.admin._auth_helpers import get_current_user, get_sidebar_stats
from app.web.templates import Jinja2Templates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory="templates")


@router.get("", response_class=HTMLResponse)
def reports_index():
    # No dedicated reports landing page; send to the operations report (NOTE-110).
    return RedirectResponse(url="/admin/reports/operations", status_code=307)


REPORTS_ONLINE_LAST_24H_READ_PERMISSIONS = (
    "reports:online-last-24h:read",
    "reports:operations",
    "reports:subscribers",
    "reports",
)
REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS = (
    "reports:online-last-24h:write",
    "reports:operations",
    "reports:subscribers",
    "reports",
)
REPORTS_BILLING_RISK_READ_PERMISSIONS = (
    "reports:billing-risk:read",
    "reports:billing",
    "reports:subscribers",
    "reports",
)
REPORTS_BILLING_RISK_WRITE_PERMISSIONS = (
    "reports:billing-risk:write",
    "reports:billing",
    "reports:subscribers",
    "reports",
)
REPORTS_REVENUE_SERVICE_READ_PERMISSIONS = (
    "reports:revenue-service:read",
    "reports:billing",
    "reports:subscribers",
    "reports",
)


class _ProjectTaskPersonAccumulator(TypedDict):
    id: str
    name: str
    assigned_tasks: int
    completed_tasks: int
    open_tasks: int
    blocked_tasks: int
    overdue_tasks: int
    on_time_tasks: int
    cycle_hours_total: float
    cycle_hours_count: int
    effort_accuracy_total: float
    effort_accuracy_count: int


_NCC_OPERATOR_NAME = "Dotmac"
_NCC_OPERATOR_PREFIX = "DOTMAC"
_NCC_EXPORT_TITLE = "Dotmac NCC Report"
_NCC_COLUMNS = [
    "MSISDN",
    "First Name",
    "Last Name",
    "Email",
    "Age",
    "Gender",
    "created date time",
    "Subject",
    "Category",
    "category code (auto)",
    "sub category code",
    "Description (auto)",
    "Ticket ID",
    "Complaint type",
    "Status",
    "Resolved date",
    "Resolved within SLA",
    "Resolution Note",
    "User Note",
    "user notes datetime",
    "Language",
    "Ticket source",
    "alt phone number",
    "created by",
    "State",
    "LGA",
    "Town",
    "Phone Type",
    "VALIDATION STATUS",
]
_NCC_CATEGORY_SLA: dict[str, dict[str, int | str]] = {
    "Billing": {"code": "A", "feedback_hours": 4, "resolution_hours": 24},
    "Call Center / Customer Care": {"code": "B", "feedback_hours": 4, "resolution_hours": 24},
    "Quality of Service (Voice)": {"code": "C", "feedback_hours": 4, "resolution_hours": 72},
    "Quality of Service (Data)": {"code": "D", "feedback_hours": 4, "resolution_hours": 72},
    "Quality of Experience": {"code": "E", "feedback_hours": 4, "resolution_hours": 72},
    "Faulty Terminals": {"code": "F", "feedback_hours": 48, "resolution_hours": 72},
    "BTS Issues": {"code": "G", "feedback_hours": 72, "resolution_hours": 720},
    "Sales Promotions & Advertisement": {"code": "H", "feedback_hours": 2, "resolution_hours": 24},
    "Recharge / Top-Up Issues": {"code": "I", "feedback_hours": 4, "resolution_hours": 24},
    "SMS / MMS": {"code": "J", "feedback_hours": 4, "resolution_hours": 24},
    "Other SIM-Related Issues": {"code": "K", "feedback_hours": 4, "resolution_hours": 24},
    "SIM Replacement": {"code": "L", "feedback_hours": 2, "resolution_hours": 12},
    "Value-Added Services (VAS)": {"code": "M", "feedback_hours": 4, "resolution_hours": 24},
    "Mobile Number Portability (MNP)": {"code": "N", "feedback_hours": 4, "resolution_hours": 24},
    "Do-Not-Disturb (DND) Service": {"code": "O", "feedback_hours": 4, "resolution_hours": 12},
    "International Roaming": {"code": "P", "feedback_hours": 4, "resolution_hours": 72},
    "Data Depletion": {"code": "Q", "feedback_hours": 4, "resolution_hours": 24},
    "Failed Payment Transactions": {"code": "R", "feedback_hours": 2, "resolution_hours": 12},
}
_NCC_ACCEPTED_CATEGORIES = set(_NCC_CATEGORY_SLA)
_NCC_ACCEPTED_CATEGORY_CODES = {str(value["code"]) for value in _NCC_CATEGORY_SLA.values()}
_NCC_SUBCATEGORY_ROWS: tuple[dict[str, object], ...] = (
    {
        "category": "Billing",
        "issue_code": "A1",
        "name": "Dropped Balance / Unexplained Deduction",
        "description": "Unexplained balance change, overcharging, silent-call charges, undelivered SMS charges, or recharge not reflecting.",
    },
    {
        "category": "Billing",
        "issue_code": "A2",
        "name": "Inability to Change Tariff Plan",
        "description": "Consumer is unable to migrate from one tariff plan to another.",
    },
    {
        "category": "Billing",
        "issue_code": "A3",
        "name": "Suspension of Postpaid Line",
        "description": "Consumer line is suspended due to a disputed bill.",
    },
    {
        "category": "Billing",
        "issue_code": "A4",
        "name": "Renewal of Data Subscription",
        "description": "Consumer is unable to renew a data subscription.",
    },
    {
        "category": "Billing",
        "issue_code": "A5",
        "name": "Reduction in Validity Period",
        "description": "Consumer data validity period is reduced arbitrarily.",
    },
    {
        "category": "Billing",
        "issue_code": "A6",
        "name": "Data Subscription Not Rolled Over",
        "description": "Consumer is unable to roll over unused data.",
    },
    {
        "category": "Billing",
        "issue_code": "A50",
        "name": "Others (Billing)",
        "description": "Any other billing related issue not captured above.",
    },
    {
        "category": "Call Center / Customer Care",
        "issue_code": "B1",
        "name": "Inability to Connect to Call Center Helpline",
        "description": "Consumer is unable to connect to the service provider customer care helpline.",
    },
    {
        "category": "Call Center / Customer Care",
        "issue_code": "B2",
        "name": "Downtime of Service Provider's Call Centre",
        "description": "Service provider call center is not operational.",
    },
    {
        "category": "Call Center / Customer Care",
        "issue_code": "B3",
        "name": "Poor Customer Service",
        "description": "Consumer is poorly attended to by a customer care representative or call center agent.",
    },
    {
        "category": "Call Center / Customer Care",
        "issue_code": "B4",
        "name": "Incorrect Responses / Information from Agents",
        "description": "Consumer is given wrong or misleading information by customer care representatives.",
    },
    {
        "category": "Call Center / Customer Care",
        "issue_code": "B5",
        "name": "Inability to Connect to Live Agent",
        "description": "Consumer is unable to connect to a live agent within the expected timeframe.",
    },
    {
        "category": "Call Center / Customer Care",
        "issue_code": "B50",
        "name": "Others (Customer Care)",
        "description": "Any other customer care related issue not captured above.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C1",
        "name": "Call Interference / Voice Clarity / Background Noise",
        "description": "Consumer cannot clearly hear during a call or experiences call interference/background noise.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C2",
        "name": "Inability to Receive Calls",
        "description": "Consumer cannot receive calls from same network, other networks, or outside the country.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C3",
        "name": "Inability to Make Calls",
        "description": "Consumer cannot make successful calls within or outside the network/country despite sufficient airtime.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C4",
        "name": "Call Divert Issues",
        "description": "Consumer is unable to activate call divert.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C5",
        "name": "Unauthorized Call Divert Activation",
        "description": "Call divert is activated on the consumer line without request.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C6",
        "name": "Call Barring",
        "description": "Consumer is prohibited from making or receiving calls for a period due to network-related barring.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C7",
        "name": "Poor Signal",
        "description": "Consumer has poor reception at a particular location.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C8",
        "name": "No Network",
        "description": "Consumer does not have network reception.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C9",
        "name": "Dropped Call",
        "description": "Consumer call is abruptly disconnected and cannot successfully complete.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C10",
        "name": "Call Crossing (Wrong Routing)",
        "description": "A call is routed to a wrong person or line.",
    },
    {
        "category": "Quality of Service (Voice)",
        "issue_code": "C50",
        "name": "Others (QoS Voice)",
        "description": "Any other call setup or voice quality issue not captured above.",
    },
    {
        "category": "Quality of Service (Data)",
        "issue_code": "D1",
        "name": "Poor Internet Service",
        "description": "Consumer experiences poor internet service.",
    },
    {
        "category": "Quality of Service (Data)",
        "issue_code": "D2",
        "name": "No Internet Network",
        "description": "Consumer does not have internet service.",
    },
    {
        "category": "Quality of Service (Data)",
        "issue_code": "D3",
        "name": "Low / Poor Internet Speed",
        "description": "Consumer has low or poor internet speed.",
    },
    {
        "category": "Quality of Service (Data)",
        "issue_code": "D4",
        "name": "Others (QoS Data)",
        "description": "Any other data related issue not captured above.",
    },
    {
        "category": "Quality of Experience",
        "issue_code": "E1",
        "name": "Call Masking and Refiling",
        "description": "Consumer receives an international call showing a local number.",
    },
    {
        "category": "Quality of Experience",
        "issue_code": "E2",
        "name": "Disconnection of Internet Services",
        "description": "Consumer internet service is disconnected.",
    },
    {
        "category": "Quality of Experience",
        "issue_code": "E3",
        "name": "Installation of Internet Services Equipment",
        "description": "Consumer internet service equipment is not installed at the agreed time.",
    },
    {
        "category": "Quality of Experience",
        "issue_code": "E4",
        "name": "Others (Quality of Experience)",
        "description": "Any other quality of experience issue not captured above.",
    },
    {
        "category": "Faulty Terminals",
        "issue_code": "F1",
        "name": "Faulty Terminals (Phones, Routers, Modems)",
        "description": "Consumer has problems with phones, routers, modems, or other terminals.",
    },
    {
        "category": "Faulty Terminals",
        "issue_code": "F50",
        "name": "Others (Faulty Terminals)",
        "description": "Any other faulty terminal related issue not captured above.",
    },
    {
        "category": "BTS Issues",
        "issue_code": "G1",
        "name": "Base Station Issues",
        "description": "Problems arising from installation or location of a base station, mast, or tower.",
    },
    {
        "category": "BTS Issues",
        "issue_code": "G2",
        "name": "Pollution from BTS Site / Generator",
        "description": "Consumer complains of environmental pollution from a BTS site generator.",
    },
    {
        "category": "BTS Issues",
        "issue_code": "G50",
        "name": "Others (BTS)",
        "description": "Any other BTS related issue not captured above.",
    },
    {
        "category": "Sales Promotions & Advertisement",
        "issue_code": "H1",
        "name": "Bonus / Promotions Issues",
        "description": "Consumer does not receive promotion bonus/incentive or receives misleading/incomplete offer information.",
    },
    {
        "category": "Sales Promotions & Advertisement",
        "issue_code": "H50",
        "name": "Others (Promotions)",
        "description": "Any other promotion related issue not captured above.",
    },
    {
        "category": "Recharge / Top-Up Issues",
        "issue_code": "I1",
        "name": "Mutilated Vouchers",
        "description": "Consumer is unable to identify numbers on a voucher.",
    },
    {
        "category": "Recharge / Top-Up Issues",
        "issue_code": "I2",
        "name": "Recharge Barring",
        "description": "Consumer is barred from recharging after several wrong attempts.",
    },
    {
        "category": "Recharge / Top-Up Issues",
        "issue_code": "I3",
        "name": "Inability to Check Airtime / Data Balance",
        "description": "Consumer cannot check data or airtime balance via USSD or IVR.",
    },
    {
        "category": "Recharge / Top-Up Issues",
        "issue_code": "I5",
        "name": "Invalid Voucher",
        "description": "Consumer purchases an invalid voucher or receives an invalid prompt when loading a voucher.",
    },
    {
        "category": "Recharge / Top-Up Issues",
        "issue_code": "I6",
        "name": "Over Recharge",
        "description": "Consumer recharges over the intended value where resolution is not third-party dependent.",
    },
    {
        "category": "Recharge / Top-Up Issues",
        "issue_code": "I50",
        "name": "Others (Recharge)",
        "description": "Any other recharge/top-up related issue not captured above.",
    },
    {
        "category": "SMS / MMS",
        "issue_code": "J1",
        "name": "Inability to Send SMS",
        "description": "Consumer is unable to send SMS or is charged for SMS that is not delivered.",
    },
    {
        "category": "SMS / MMS",
        "issue_code": "J2",
        "name": "Inability to Receive SMS",
        "description": "Consumer is unable to receive SMS locally or from outside the country.",
    },
    {
        "category": "SMS / MMS",
        "issue_code": "J3",
        "name": "MMS Charges (Undelivered MMS)",
        "description": "Consumer is charged for undelivered MMS.",
    },
    {
        "category": "SMS / MMS",
        "issue_code": "J50",
        "name": "Others (SMS/MMS)",
        "description": "Any other SMS/MMS related issue not captured above.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K1",
        "name": "Request for SIM Block",
        "description": "Consumer requests that a SIM be blocked.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K2",
        "name": "SIM Blocked - PUK Required",
        "description": "Consumer requires PUK from the service provider to unblock a SIM.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K3",
        "name": "Unauthorized Suspension of Mobile Line",
        "description": "Consumer line is wrongfully suspended by the service provider.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K4",
        "name": "SIM Registration (Incorrect Details)",
        "description": "Consumer SIM registration details are incorrect.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K5",
        "name": "Incomplete SIM Registration",
        "description": "Consumer is asked to re-register a SIM.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K6",
        "name": "NIN-SIM Linkage Issues",
        "description": "Consumer NIN is not successfully linked by the service provider.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K7",
        "name": "Inactive SIM",
        "description": "Consumer SIM is barred, suspended, or deactivated due to inactivity/NIN-SIM linkage or in error.",
    },
    {
        "category": "Other SIM-Related Issues",
        "issue_code": "K50",
        "name": "Others (SIM-Related)",
        "description": "Any other SIM related issue not captured above.",
    },
    {
        "category": "SIM Replacement",
        "issue_code": "L1",
        "name": "Fraudulent / Unauthorized SIM Swap",
        "description": "Consumer SIM is reported swapped without consent.",
    },
    {
        "category": "SIM Replacement",
        "issue_code": "L2",
        "name": "Inactive SIM Replacement",
        "description": "SIM replacement is completed but the SIM remains inactive.",
    },
    {
        "category": "SIM Replacement",
        "issue_code": "L3",
        "name": "Retrieval of Deceased Relative's SIM",
        "description": "Consumer cannot complete SIM replacement for a deceased relative after providing requirements.",
    },
    {
        "category": "SIM Replacement",
        "issue_code": "L50",
        "name": "Others (SIM Replacement)",
        "description": "Any other SIM swap/replacement related issue.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M1",
        "name": "Inability to Activate / Deactivate VAS",
        "description": "Consumer is unable to opt in or opt out of VAS services.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M2",
        "name": "VAS Charges (Unrendered / Wrong Service)",
        "description": "Consumer is charged for VAS not rendered or receives the wrong VAS.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M3",
        "name": "Forceful Activation of VAS",
        "description": "Consumer is opted into VAS without consent.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M4",
        "name": "Inability to Listen to Voice SMS / Voicemail",
        "description": "Consumer is unable to listen to Voice SMS from the service provider network.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M5",
        "name": "Inability to Access / Activate Voice SMS",
        "description": "Consumer is unable to send Voice SMS.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M6",
        "name": "Inability to Access Voice Mail",
        "description": "Consumer is unable to recover voicemail.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M7",
        "name": "Failed Voice SMS",
        "description": "Consumer is charged for Voice SMS that is not delivered.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M8",
        "name": "Inability to Activate / Deactivate Voicemail Box",
        "description": "Consumer is unable to deactivate or activate voicemail.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M9",
        "name": "Voicemail Password Reset / Retrieval",
        "description": "Consumer is unable to change or recover voicemail password.",
    },
    {
        "category": "Value-Added Services (VAS)",
        "issue_code": "M50",
        "name": "Others (VAS)",
        "description": "Any other VAS related issue not captured above.",
    },
    {
        "category": "Mobile Number Portability (MNP)",
        "issue_code": "N1",
        "name": "Porting Issues",
        "description": "Consumer is unable to successfully port from one service provider to another within the porting timeline.",
    },
    {
        "category": "Mobile Number Portability (MNP)",
        "issue_code": "N50",
        "name": "Others (MNP)",
        "description": "Any other MNP related issue not captured above.",
    },
    {
        "category": "Do-Not-Disturb (DND) Service",
        "issue_code": "O1",
        "name": "Inability to Opt In / Out of DND",
        "description": "Consumer is unable to opt in or out of DND fully or partially.",
    },
    {
        "category": "Do-Not-Disturb (DND) Service",
        "issue_code": "O2",
        "name": "Receipt of Unsolicited SMS / Calls After Full DND",
        "description": "Consumer continues to receive unsolicited SMS/calls after activating full DND.",
    },
    {
        "category": "Do-Not-Disturb (DND) Service",
        "issue_code": "O50",
        "name": "Others (DND)",
        "description": "Any other DND related issue not captured above.",
    },
    {
        "category": "International Roaming",
        "issue_code": "P1",
        "name": "Inability to Send / Receive SMS While Roaming",
        "description": "Consumer is unable to send or receive SMS while outside the country.",
    },
    {
        "category": "International Roaming",
        "issue_code": "P2",
        "name": "Inability to Make / Receive Calls While Roaming",
        "description": "Consumer is unable to make or receive calls while outside the country.",
    },
    {
        "category": "International Roaming",
        "issue_code": "P3",
        "name": "Inability to Roam",
        "description": "Consumer is unable to roam.",
    },
    {
        "category": "International Roaming",
        "issue_code": "P4",
        "name": "Internet Service Not Working While Roaming",
        "description": "Consumer is unable to browse while outside the country.",
    },
    {
        "category": "International Roaming",
        "issue_code": "P5",
        "name": "Inability to Recharge While Roaming",
        "description": "Consumer is unable to recharge while outside the country.",
    },
    {
        "category": "International Roaming",
        "issue_code": "P6",
        "name": "Overcharged While Roaming",
        "description": "Consumer is overcharged for calls/data while outside the country.",
    },
    {
        "category": "International Roaming",
        "issue_code": "P50",
        "name": "Others (Roaming)",
        "description": "Any other roaming related issue.",
    },
    {
        "category": "Data Depletion",
        "issue_code": "Q1",
        "name": "Data Depletion",
        "description": "Consumer data gets used up or exhausted faster than expected.",
    },
    {
        "category": "Data Depletion",
        "issue_code": "Q50",
        "name": "Others (Data Depletion)",
        "description": "Any other data depletion related issue.",
    },
    {
        "category": "Failed Payment Transactions",
        "issue_code": "R1",
        "name": "Inability to Recharge / Failed Sharing / Mobile App",
        "description": "Consumer cannot purchase airtime/data via IVR/USSD, is debited for failed sharing, or is charged for failed third-party/mobile app top-up.",
    },
    {
        "category": "Failed Payment Transactions",
        "issue_code": "R50",
        "name": "Others (Failed Payment Transactions)",
        "description": "Any other failed payment transaction related issue.",
    },
)
_NCC_SUBCATEGORY_BY_CODE = {str(row["issue_code"]): row for row in _NCC_SUBCATEGORY_ROWS}
_NCC_ACCEPTED_SUBCATEGORY_CODES = {f"{row['issue_code']} - {row['name']}" for row in _NCC_SUBCATEGORY_ROWS}
_NCC_SUBCATEGORY_ALIASES = {
    value.replace(" - ", " - ").replace(" \u2013 ", " - "): value for value in _NCC_ACCEPTED_SUBCATEGORY_CODES
}
_NCC_SUBCATEGORY_ALIASES.update({value.replace(" - ", " \u2013 "): value for value in _NCC_ACCEPTED_SUBCATEGORY_CODES})
_NCC_ACCEPTED_LANGUAGES = {"English", "Hausa", "Igbo", "Yoruba", "Pidgin", "Others"}
_NCC_ACCEPTED_TICKET_SOURCES = {
    "Phone Call",
    "Email",
    "Web Portal",
    "Mobile App",
    "Walk-in",
    "SMS",
    "Social Media",
    "Other",
}
_NCC_REQUIRED_COLUMNS = {
    "MSISDN",
    "First Name",
    "Last Name",
    "Age",
    "Gender",
    "created date time",
    "Category",
    "sub category code",
    "Ticket ID",
    "Complaint type",
    "Status",
    "Language",
    "Ticket source",
    "State",
    "LGA",
}
_NCC_REQUIRED_DROPDOWN_COLUMNS = {
    "Gender",
    "Category",
    "sub category code",
    "Complaint type",
    "Status",
    "Language",
    "Ticket source",
    "State",
    "LGA",
}
_NCC_STATE_LGAS: dict[str, tuple[str, ...]] = {
    "ABIA": (
        "Aba North",
        "Aba South",
        "Arochukwu",
        "Bende",
        "Ikwuano",
        "Isiala-Ngwa North",
        "Isiala-Ngwa South",
        "Isuikwuato",
        "Obi Ngwa",
        "Ohafia",
        "Osisioma Ngwa",
        "Ugwunagbo",
        "Ukwa East",
        "Ukwa West",
        "Umuahia North",
        "Umuahia South",
        "Umu-Nneochi",
    ),
    "ADAMAWA": (
        "Demsa",
        "Fufore",
        "Ganye",
        "Girei",
        "Gombi",
        "Guyuk",
        "Hong",
        "Jada",
        "Lamurde",
        "Madagali",
        "Maiha",
        "Mayo-Belwa",
        "Michika",
        "Mubi North",
        "Mubi South",
        "Numan",
        "Shelleng",
        "Song",
        "Toungo",
        "Yola North",
        "Yola South",
    ),
    "AKWA IBOM": (
        "Abak",
        "Eastern Obolo",
        "Eket",
        "Esit-Eket",
        "Essien Udim",
        "Etim Ekpo",
        "Etinan",
        "Ibeno",
        "Ibesikpo Asutan",
        "Ibiono-Ibom",
        "Ika",
        "Ikono",
        "Ikot Abasi",
        "Ikot Ekpene",
        "Ini",
        "Itu",
        "Mbo",
        "Mkpat-Enin",
        "Nsit-Atai",
        "Nsit-Ibom",
        "Nsit-Ubium",
        "Obot Akara",
        "Okobo",
        "Onna",
        "Oron",
        "Oruk Anam",
        "Udung-Uko",
        "Ukanafun",
        "Uruan",
        "Urue-Offong/Oruko",
        "Uyo",
    ),
    "ANAMBRA": (
        "Aguata",
        "Anambra East",
        "Anambra West",
        "Anaocha",
        "Awka North",
        "Awka South",
        "Ayamelum",
        "Dunukofia",
        "Ekwusigo",
        "Idemili North",
        "Idemili South",
        "Ihiala",
        "Njikoka",
        "Nnewi North",
        "Nnewi South",
        "Ogbaru",
        "Onitsha North",
        "Onitsha South",
        "Orumba North",
        "Orumba South",
        "Oyi",
    ),
    "BAUCHI": (
        "Alkaleri",
        "Bauchi",
        "Bogoro",
        "Damban",
        "Darazo",
        "Dass",
        "Gamawa",
        "Ganjuwa",
        "Giade",
        "Itas/Gadau",
        "Jama'are",
        "Katagum",
        "Kirfi",
        "Misau",
        "Ningi",
        "Shira",
        "Tafawa Balewa",
        "Toro",
        "Warji",
        "Zaki",
    ),
    "BAYELSA": ("Brass", "Ekeremor", "Kolokuma/Opokuma", "Nembe", "Ogbia", "Sagbama", "Southern Ijaw", "Yenagoa"),
    "BENUE": (
        "Ado",
        "Agatu",
        "Apa",
        "Buruku",
        "Gboko",
        "Guma",
        "Gwer East",
        "Gwer West",
        "Katsina-Ala",
        "Konshisha",
        "Kwande",
        "Logo",
        "Makurdi",
        "Obi",
        "Ogbadibo",
        "Ohimini",
        "Oju",
        "Okpokwu",
        "Otukpo",
        "Tarka",
        "Ukum",
        "Ushongo",
        "Vandeikya",
    ),
    "BORNO": (
        "Abadam",
        "Askira/Uba",
        "Bama",
        "Bayo",
        "Biu",
        "Chibok",
        "Damboa",
        "Dikwa",
        "Gubio",
        "Guzamala",
        "Gwoza",
        "Hawul",
        "Jere",
        "Kaga",
        "Kala/Balge",
        "Konduga",
        "Kukawa",
        "Kwaya Kusar",
        "Mafa",
        "Magumeri",
        "Maiduguri",
        "Marte",
        "Mobbar",
        "Monguno",
        "Ngala",
        "Nganzai",
        "Shani",
    ),
    "CROSS RIVER": (
        "Abi",
        "Akamkpa",
        "Akpabuyo",
        "Bakassi",
        "Bekwarra",
        "Biase",
        "Boki",
        "Calabar Municipal",
        "Calabar South",
        "Etung",
        "Ikom",
        "Obanliku",
        "Obubra",
        "Obudu",
        "Odukpani",
        "Ogoja",
        "Yakuur",
        "Yala",
    ),
    "DELTA": (
        "Aniocha North",
        "Aniocha South",
        "Bomadi",
        "Burutu",
        "Ethiope East",
        "Ethiope West",
        "Ika North-East",
        "Ika South",
        "Isoko North",
        "Isoko South",
        "Ndokwa East",
        "Ndokwa West",
        "Okpe",
        "Oshimili North",
        "Oshimili South",
        "Patani",
        "Sapele",
        "Udu",
        "Ughelli North",
        "Ughelli South",
        "Ukwuani",
        "Uvwie",
        "Warri North",
        "Warri South",
        "Warri South West",
    ),
    "EBONYI": (
        "Abakaliki",
        "Afikpo North",
        "Afikpo South",
        "Ebonyi",
        "Ezza North",
        "Ezza South",
        "Ikwo",
        "Ishielu",
        "Ivo",
        "Izzi",
        "Ohaozara",
        "Ohaukwu",
        "Onicha",
    ),
    "EDO": (
        "Akoko-Edo",
        "Egor",
        "Esan Central",
        "Esan North-East",
        "Esan South-East",
        "Esan West",
        "Etsako Central",
        "Etsako East",
        "Etsako West",
        "Igueben",
        "Ikpoba-Okha",
        "Oredo",
        "Orhionmwon",
        "Ovia North-East",
        "Ovia South-West",
        "Owan East",
        "Owan West",
        "Uhunmwonde",
    ),
    "EKITI": (
        "Ado-Ekiti",
        "Efon",
        "Ekiti East",
        "Ekiti South-West",
        "Ekiti West",
        "Emure",
        "Gbonyin",
        "Ido-Osi",
        "Ijero",
        "Ikere",
        "Ikole",
        "Ilejemeje",
        "Irepodun/Ifelodun",
        "Ise/Orun",
        "Moba",
        "Oye",
    ),
    "ENUGU": (
        "Aninri",
        "Awgu",
        "Enugu East",
        "Enugu North",
        "Enugu South",
        "Ezeagu",
        "Igbo-Etiti",
        "Igbo-Eze North",
        "Igbo-Eze South",
        "Isi-Uzo",
        "Nkanu East",
        "Nkanu West",
        "Nsukka",
        "Oji-River",
        "Udenu",
        "Udi",
        "Uzo-Uwani",
    ),
    "FEDERAL CAPITAL TERRITORY": ("Abaji", "Bwari", "Gwagwalada", "Kuje", "Kwali", "Municipal Area Council"),
    "GOMBE": (
        "Akko",
        "Balanga",
        "Billiri",
        "Dukku",
        "Funakaye",
        "Gombe",
        "Kaltungo",
        "Kwami",
        "Nafada",
        "Shongom",
        "Yamaltu/Deba",
    ),
    "IMO": (
        "Aboh-Mbaise",
        "Ahiazu-Mbaise",
        "Ehime-Mbano",
        "Ezinihitte",
        "Ideato North",
        "Ideato South",
        "Ihitte/Uboma",
        "Ikeduru",
        "Isiala Mbano",
        "Isu",
        "Mbaitoli",
        "Ngor-Okpala",
        "Njaba",
        "Nkwerre",
        "Nwangele",
        "Obowo",
        "Oguta",
        "Ohaji/Egbema",
        "Okigwe",
        "Onuimo",
        "Orlu",
        "Orsu",
        "Oru East",
        "Oru West",
        "Owerri Municipal",
        "Owerri North",
        "Owerri West",
    ),
    "JIGAWA": (
        "Auyo",
        "Babura",
        "Biriniwa",
        "Birnin Kudu",
        "Buji",
        "Dutse",
        "Gagarawa",
        "Garki",
        "Gumel",
        "Guri",
        "Gwaram",
        "Gwiwa",
        "Hadejia",
        "Jahun",
        "Kafin Hausa",
        "Kaugama",
        "Kazaure",
        "Kiri Kasama",
        "Kiyawa",
        "Maigatari",
        "Malam Madori",
        "Miga",
        "Ringim",
        "Roni",
        "Sule-Tankarkar",
        "Taura",
        "Yankwashi",
    ),
    "KADUNA": (
        "Birnin Gwari",
        "Chikun",
        "Giwa",
        "Igabi",
        "Ikara",
        "Jaba",
        "Jema'a",
        "Kachia",
        "Kaduna North",
        "Kaduna South",
        "Kagarko",
        "Kajuru",
        "Kaura",
        "Kauru",
        "Kubau",
        "Kudan",
        "Lere",
        "Makarfi",
        "Sabon Gari",
        "Sanga",
        "Soba",
        "Zangon Kataf",
        "Zaria",
    ),
    "KANO": (
        "Ajingi",
        "Albasu",
        "Bagwai",
        "Bebeji",
        "Bichi",
        "Bunkure",
        "Dala",
        "Dambatta",
        "Dawakin Kudu",
        "Dawakin Tofa",
        "Doguwa",
        "Fagge",
        "Gabasawa",
        "Garko",
        "Garum Mallam",
        "Gaya",
        "Gezawa",
        "Gwale",
        "Gwarzo",
        "Kabo",
        "Kano Municipal",
        "Karaye",
        "Kibiya",
        "Kiru",
        "Kumbotso",
        "Kunchi",
        "Kura",
        "Madobi",
        "Makoda",
        "Minjibir",
        "Nasarawa",
        "Rano",
        "Rimin Gado",
        "Rogo",
        "Shanono",
        "Sumaila",
        "Takai",
        "Tarauni",
        "Tofa",
        "Tsanyawa",
        "Tudun Wada",
        "Ungogo",
        "Warawa",
        "Wudil",
    ),
    "KATSINA": (
        "Bakori",
        "Batagarawa",
        "Batsari",
        "Baure",
        "Bindawa",
        "Charanchi",
        "Dan Musa",
        "Dandume",
        "Danja",
        "Daura",
        "Dutsi",
        "Dutsin-Ma",
        "Faskari",
        "Funtua",
        "Ingawa",
        "Jibia",
        "Kafur",
        "Kaita",
        "Kankara",
        "Kankia",
        "Katsina",
        "Kurfi",
        "Kusada",
        "Mai'adua",
        "Malumfashi",
        "Mani",
        "Mashi",
        "Matazu",
        "Musawa",
        "Rimi",
        "Sabuwa",
        "Safana",
        "Sandamu",
        "Zango",
    ),
    "KEBBI": (
        "Aleiro",
        "Arewa Dandi",
        "Argungu",
        "Augie",
        "Bagudo",
        "Birnin Kebbi",
        "Bunza",
        "Dandi",
        "Fakai",
        "Gwandu",
        "Jega",
        "Kalgo",
        "Koko/Besse",
        "Maiyama",
        "Ngaski",
        "Sakaba",
        "Shanga",
        "Suru",
        "Wasagu/Danko",
        "Yauri",
        "Zuru",
    ),
    "KOGI": (
        "Adavi",
        "Ajaokuta",
        "Ankpa",
        "Bassa",
        "Dekina",
        "Ibaji",
        "Idah",
        "Igalamela-Odolu",
        "Ijumu",
        "Kabba/Bunu",
        "Kogi",
        "Lokoja",
        "Mopa-Muro",
        "Ofu",
        "Ogori/Magongo",
        "Okehi",
        "Okene",
        "Olamaboro",
        "Omala",
        "Yagba East",
        "Yagba West",
    ),
    "KWARA": (
        "Asa",
        "Baruten",
        "Edu",
        "Ekiti",
        "Ifelodun",
        "Ilorin East",
        "Ilorin South",
        "Ilorin West",
        "Irepodun",
        "Isin",
        "Kaiama",
        "Moro",
        "Offa",
        "Oke-Ero",
        "Oyun",
        "Pategi",
    ),
    "LAGOS": (
        "Agege",
        "Ajeromi-Ifelodun",
        "Alimosho",
        "Amuwo-Odofin",
        "Apapa",
        "Badagry",
        "Epe",
        "Eti-Osa",
        "Ibeju-Lekki",
        "Ifako-Ijaiye",
        "Ikeja",
        "Ikorodu",
        "Kosofe",
        "Lagos Island",
        "Lagos Mainland",
        "Mushin",
        "Ojo",
        "Oshodi-Isolo",
        "Shomolu",
        "Surulere",
    ),
    "NASARAWA": (
        "Akwanga",
        "Awe",
        "Doma",
        "Karu",
        "Keana",
        "Keffi",
        "Kokona",
        "Lafia",
        "Nasarawa",
        "Nasarawa Egon",
        "Obi",
        "Toto",
        "Wamba",
    ),
    "NIGER": (
        "Agaie",
        "Agwara",
        "Bida",
        "Borgu",
        "Bosso",
        "Chanchaga",
        "Edati",
        "Gbako",
        "Gurara",
        "Katcha",
        "Kontagora",
        "Lapai",
        "Lavun",
        "Magama",
        "Mariga",
        "Mashegu",
        "Mokwa",
        "Moya",
        "Paikoro",
        "Rafi",
        "Rijau",
        "Shiroro",
        "Suleja",
        "Tafa",
        "Wushishi",
    ),
    "OGUN": (
        "Abeokuta North",
        "Abeokuta South",
        "Ado-Odo/Ota",
        "Egbado North",
        "Egbado South",
        "Ewekoro",
        "Ifo",
        "Ijebu East",
        "Ijebu North",
        "Ijebu North-East",
        "Ijebu Ode",
        "Ikenne",
        "Imeko-Afon",
        "Ipokia",
        "Obafemi-Owode",
        "Odeda",
        "Odogbolu",
        "Ogun Waterside",
        "Remo North",
        "Shagamu",
    ),
    "ONDO": (
        "Akoko North-East",
        "Akoko North-West",
        "Akoko South-East",
        "Akoko South-West",
        "Akure North",
        "Akure South",
        "Ese-Odo",
        "Idanre",
        "Ifedore",
        "Ilaje",
        "Ile-Oluji/Okeigbo",
        "Irele",
        "Odigbo",
        "Okitipupa",
        "Ondo East",
        "Ondo West",
        "Ose",
        "Owo",
    ),
    "OSUN": (
        "Atakumosa East",
        "Atakumosa West",
        "Ayedade",
        "Ayedire",
        "Boluwaduro",
        "Boripe",
        "Ede North",
        "Ede South",
        "Egbedore",
        "Ejigbo",
        "Ife Central",
        "Ife East",
        "Ife North",
        "Ife South",
        "Ifedayo",
        "Ifelodun",
        "Ila",
        "Ilesa East",
        "Ilesa West",
        "Irepodun",
        "Irewole",
        "Isokan",
        "Iwo",
        "Obokun",
        "Odo-Otin",
        "Ola-Oluwa",
        "Olorunda",
        "Oriade",
        "Orolu",
        "Osogbo",
    ),
    "OYO": (
        "Afijio",
        "Akinyele",
        "Atiba",
        "Atisbo",
        "Egbeda",
        "Ibadan North",
        "Ibadan North-East",
        "Ibadan North-West",
        "Ibadan South-East",
        "Ibadan South-West",
        "Ibarapa Central",
        "Ibarapa East",
        "Ibarapa North",
        "Ido",
        "Irepo",
        "Iseyin",
        "Itesiwaju",
        "Iwajowa",
        "Kajola",
        "Lagelu",
        "Ogbomosho North",
        "Ogbomosho South",
        "Ogo Oluwa",
        "Olorunsogo",
        "Oluyole",
        "Ona-Ara",
        "Orelope",
        "Ori-Ire",
        "Oyo East",
        "Oyo West",
        "Saki East",
        "Saki West",
        "Surulere",
    ),
    "PLATEAU": (
        "Barkin Ladi",
        "Bassa",
        "Bokkos",
        "Jos East",
        "Jos North",
        "Jos South",
        "Kanam",
        "Kanke",
        "Langtang North",
        "Langtang South",
        "Mangu",
        "Mikang",
        "Pankshin",
        "Qua'an Pan",
        "Riyom",
        "Shendam",
        "Wase",
    ),
    "RIVERS": (
        "Abua/Odual",
        "Ahoada East",
        "Ahoada West",
        "Akuku-Toru",
        "Andoni",
        "Asari-Toru",
        "Bonny",
        "Degema",
        "Eleme",
        "Emohua",
        "Etche",
        "Gokana",
        "Ikwerre",
        "Khana",
        "Obio/Akpor",
        "Ogba/Egbema/Ndoni",
        "Ogu/Bolo",
        "Okrika",
        "Omuma",
        "Opobo/Nkoro",
        "Oyigbo",
        "Port Harcourt",
        "Tai",
    ),
    "SOKOTO": (
        "Binji",
        "Bodinga",
        "Dange-Shuni",
        "Gada",
        "Goronyo",
        "Gudu",
        "Gwadabawa",
        "Illela",
        "Isa",
        "Kebbe",
        "Kware",
        "Rabah",
        "Sabon Birni",
        "Shagari",
        "Silame",
        "Sokoto North",
        "Sokoto South",
        "Tambuwal",
        "Tangaza",
        "Tureta",
        "Wamako",
        "Wurno",
        "Yabo",
    ),
    "TARABA": (
        "Ardo-Kola",
        "Bali",
        "Donga",
        "Gashaka",
        "Gassol",
        "Ibi",
        "Jalingo",
        "Karim-Lamido",
        "Kumi",
        "Lau",
        "Sardauna",
        "Takum",
        "Ussa",
        "Wukari",
        "Yorro",
        "Zing",
    ),
    "YOBE": (
        "Bade",
        "Bursari",
        "Damaturu",
        "Fika",
        "Fune",
        "Geidam",
        "Gujba",
        "Gulani",
        "Jakusko",
        "Karasuwa",
        "Machina",
        "Nangere",
        "Nguru",
        "Potiskum",
        "Tarmuwa",
        "Yunusari",
        "Yusufari",
    ),
    "ZAMFARA": (
        "Anka",
        "Bakura",
        "Birnin Magaji/Kiyaw",
        "Bukkuyum",
        "Bungudu",
        "Gummi",
        "Gusau",
        "Kaura Namoda",
        "Maradun",
        "Maru",
        "Shinkafi",
        "Talata-Mafara",
        "Tsafe",
        "Zurmi",
    ),
    "INTERNATIONAL": ("International",),
}
_NCC_STATE_ALIASES = {"FCT": "FEDERAL CAPITAL TERRITORY"}
_NCC_LGA_LOOKUP_BY_STATE = {
    state: {re.sub(r"[^a-z0-9]+", " ", lga.strip().lower()).strip(): lga for lga in lgas}
    for state, lgas in _NCC_STATE_LGAS.items()
}

_ONLINE_LAST_24H_TICKET_STATUS_OPTIONS = [
    {"value": "all", "label": "All ticket states"},
    {"value": "no_ticket", "label": "No tickets"},
    {"value": "new", "label": "New"},
    {"value": "open", "label": "Open"},
    {"value": "pending", "label": "Pending"},
    {"value": "waiting_on_customer", "label": "Waiting On Customer"},
    {"value": "lastmile_rerun", "label": "Lastmile Rerun"},
    {"value": "site_under_construction", "label": "Site Under Construction"},
    {"value": "on_hold", "label": "On Hold"},
    {"value": "closed", "label": "Closed"},
    {"value": "canceled", "label": "Canceled"},
    {"value": "merged", "label": "Merged"},
]

_ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS = [
    {"value": "all", "label": "All notifications"},
    {"value": "notified", "label": "Notified"},
    {"value": "unnotified", "label": "Not Notified"},
]

_ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS = [
    {"value": "last_24h", "label": "Currently online"},
    {"value": "active_last24_not_online", "label": "Not active in the last 24h"},
]
_ONLINE_LAST_24H_WHATSAPP_TARGET_NAMES = {"dotmac fiber helpdesk"}
_ONLINE_LAST_24H_EMAIL_TARGET_NAMES = {"sales mail", "noc mail", "support mail"}
_ONLINE_LAST_24H_ROWS_TTL_SECONDS = 120.0
_ONLINE_LAST_24H_ROWS_CACHE: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}
_ONLINE_LAST_24H_ROWS_CACHE_LOCK = threading.Lock()


def _clone_report_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _online_last_24h_cache_key(
    *,
    status: str,
    region: str,
    search: str,
    ticket_status: str,
    notification_state: str,
    activity_segment: str,
    subscriber_ids: list[Any] | None,
) -> tuple[Any, ...]:
    subscriber_scope = None if subscriber_ids is None else tuple(sorted(str(value) for value in subscriber_ids))
    return (
        status,
        region,
        search.strip().lower(),
        ticket_status,
        notification_state,
        activity_segment,
        subscriber_scope,
    )


def _online_last_24h_cached_rows(
    cache_key: tuple[Any, ...],
    builder: Callable[[], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], bool]:
    now = time.monotonic()
    with _ONLINE_LAST_24H_ROWS_CACHE_LOCK:
        cached = _ONLINE_LAST_24H_ROWS_CACHE.get(cache_key)
        if cached is not None:
            expires_at, rows = cached
            if expires_at > now:
                return _clone_report_rows(rows), True
            _ONLINE_LAST_24H_ROWS_CACHE.pop(cache_key, None)

    rows = builder()
    safe_rows = _clone_report_rows(rows)
    with _ONLINE_LAST_24H_ROWS_CACHE_LOCK:
        _ONLINE_LAST_24H_ROWS_CACHE[cache_key] = (
            time.monotonic() + _ONLINE_LAST_24H_ROWS_TTL_SECONDS,
            safe_rows,
        )
    return _clone_report_rows(safe_rows), False


def _online_last_24h_allowed_target_ids(db: Session, channel: str) -> set[str]:
    from app.services.crm.web_campaigns import outreach_channel_target_options

    selected_channel = (channel or "").strip().lower()
    allowed_names = (
        _ONLINE_LAST_24H_WHATSAPP_TARGET_NAMES
        if selected_channel == "whatsapp"
        else _ONLINE_LAST_24H_EMAIL_TARGET_NAMES
        if selected_channel == "email"
        else set()
    )
    if not allowed_names:
        return set()
    options = outreach_channel_target_options(db).get(selected_channel, [])
    return {
        str(option.get("target_id") or "").strip()
        for option in options
        if str(option.get("name") or "").strip().lower() in allowed_names
    }


def _ticket_status_kpi_label(status_value: str) -> str:
    if not status_value:
        return "No Ticket"
    return status_value.replace("_", " ").title()


def _online_last_24h_ticket_status_cards(rows: list[dict]) -> list[dict[str, int | str]]:
    tracked_statuses = ["open", "closed", "canceled", "pending"]
    ticket_status_counts: dict[str, int] = {}

    for row in rows:
        status_value = str(row.get("ticket_status") or "").strip().lower()
        if not status_value:
            continue
        ticket_status_counts[status_value] = ticket_status_counts.get(status_value, 0) + 1

    return [
        {
            "label": _ticket_status_kpi_label(status_value),
            "value": ticket_status_counts.get(status_value, 0),
        }
        for status_value in tracked_statuses
    ]


def _online_last_24h_base_station_options(rows: list[dict]) -> list[str]:
    return sorted(
        {str(row.get("base_station") or "").strip() for row in rows if str(row.get("base_station") or "").strip()}
    )


def _normalize_online_last_24h_base_station_values(base_station: list[str] | str | object) -> list[str]:
    if isinstance(base_station, list):
        values = base_station
    elif isinstance(base_station, str):
        values = [base_station]
    else:
        values = []
    normalized: list[str] = []
    for value in values:
        for part in str(value).split(","):
            candidate = part.strip()
            if candidate and candidate not in normalized:
                normalized.append(candidate)
    return normalized


def _filter_online_last_24h_base_stations(rows: list[dict], selected_base_stations: list[str]) -> list[dict]:
    selected = {value.strip().lower() for value in selected_base_stations if value and value.strip()}
    if not selected:
        return rows
    return [row for row in rows if str(row.get("base_station") or "").strip().lower() in selected]


def _filter_online_last_24h_notification_state(rows: list[dict], notification_state: str) -> list[dict]:
    normalized = (notification_state or "all").strip().lower()
    if normalized == "notified":
        return [row for row in rows if str(row.get("notification_state") or "").strip().lower() == "notified"]
    if normalized == "unnotified":
        return [row for row in rows if str(row.get("notification_state") or "").strip().lower() == "unnotified"]
    return rows


def _sort_online_last_24h_rows(rows: list[dict]) -> list[dict]:
    """Sort report rows by last seen, newest first."""
    return sorted(rows, key=lambda row: str(row.get("last_seen_at_iso") or row.get("last_seen_at") or ""), reverse=True)


def _normalize_segment_filters(segments: list[str] | str | None, segment: str | None) -> list[str]:
    """Normalize repeated/comma-separated segment query values."""
    raw_values: list[str] = []
    if isinstance(segments, list):
        raw_values.extend(segments)
    elif isinstance(segments, str):
        raw_values.append(segments)
    if segment:
        raw_values.append(segment)

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        for part in str(raw_value).split(","):
            candidate = part.strip().lower().replace(" ", "_")
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
    return normalized


def _segment_labels(selected_segments: list[str]) -> set[str]:
    mapping = {
        "overdue": "Overdue",
        "suspended": "Suspended",
        "due_soon": "Due Soon",
        "churned": "Churned",
        "pending": "Pending",
    }
    return {mapping[key] for key in selected_segments if key in mapping}


def _parse_date_range(
    days: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime, datetime]:
    """Parse date range from days or custom dates."""
    now = datetime.now(UTC)
    end_dt = now

    if start_date and end_date:
        try:
            start_dt = datetime.fromisoformat(start_date).replace(tzinfo=UTC)
            end_dt = datetime.fromisoformat(end_date).replace(tzinfo=UTC)
            # Ensure end_date is end of day
            end_dt = end_dt.replace(hour=23, minute=59, second=59)
            return start_dt, end_dt
        except ValueError:
            pass

    # Fall back to days
    days = days or 30
    start_dt = now - timedelta(days=days)
    return start_dt, end_dt


def _csv_response(data: list[dict], filename: str) -> StreamingResponse:
    """Create a CSV streaming response."""
    if not data:
        output = io.StringIO()
        output.write("No data available\n")
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=data[0].keys())
    writer.writeheader()
    writer.writerows(data)
    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _excel_column_letter(index: int) -> str:
    result = ""
    current = index
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        result = chr(65 + remainder) + result
    return result


_NCC_COLUMN_LETTERS = {column: _excel_column_letter(index) for index, column in enumerate(_NCC_COLUMNS, start=1)}


def _excel_serial_from_display_timestamp(value: str) -> float | None:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return None
    try:
        timestamp = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=UTC)
    except ValueError:
        return None
    excel_epoch = datetime(1899, 12, 30, tzinfo=UTC)
    delta = timestamp - excel_epoch
    return delta.days + (delta.seconds / 86400)


def _xlsx_response(content: bytes, filename: str) -> Response:
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _ncc_submission_week(value: datetime) -> int:
    return ((value.day - 1) // 7) + 1


def _ncc_export_filename(value: datetime | None = None) -> str:
    report_dt = value or datetime.now(UTC)
    if report_dt.tzinfo is None:
        report_dt = report_dt.replace(tzinfo=UTC)
    return f"{_NCC_OPERATOR_NAME}_Week{_ncc_submission_week(report_dt)}_{report_dt:%Y%m}.xlsx"


def _ncc_export_column_widths(records: list[dict[str, str]], columns: list[str]) -> list[float]:
    fixed_widths = {
        "MSISDN": 18,
        "First Name": 22,
        "Last Name": 22,
        "Email": 28,
        "Age": 10,
        "Gender": 12,
        "created date time": 22,
        "Subject": 28,
        "Category": 24,
        "category code (auto)": 20,
        "sub category code": 22,
        "Description (auto)": 42,
        "Ticket ID": 16,
        "Complaint type": 24,
        "Status": 18,
        "Resolved date": 22,
        "Resolved within SLA": 20,
        "Resolution Note": 36,
        "User Note": 36,
        "user notes datetime": 22,
        "Language": 14,
        "Ticket source": 18,
        "alt phone number": 20,
        "created by": 24,
        "State": 14,
        "LGA": 14,
        "Town": 18,
        "Phone Type": 28,
        "VALIDATION STATUS": 28,
    }
    widths: list[float] = []
    for column in columns:
        width = fixed_widths.get(column, max(len(column) + 2, 14))
        if column not in fixed_widths:
            max_value_length = max((len(str(row.get(column) or "")) for row in records), default=0)
            width = min(max(max_value_length + 2, len(column) + 2, 14), 24)
        widths.append(float(width))
    return widths


def _ncc_status_style_id(status_variant: str) -> int:
    mapping = {
        "success": 5,
        "warning": 6,
        "error": 7,
        "info": 8,
    }
    return mapping.get(status_variant, 9)


def _ncc_workbook_dropdown_lists() -> dict[str, list[str]]:
    return {
        "Gender": ["Female", "Male", "N/A"],
        "Category": list(_NCC_CATEGORY_SLA),
        "category code (auto)": [str(value["code"]) for value in _NCC_CATEGORY_SLA.values()],
        "sub category code": [f"{row['issue_code']} - {row['name']}" for row in _NCC_SUBCATEGORY_ROWS],
        "Complaint type": ["First Level", "Second Level"],
        "Status": ["Resolved", "Pending"],
        "Resolved within SLA": ["Yes", "No"],
        "Language": ["English", "Hausa", "Igbo", "Yoruba", "Pidgin", "Others"],
        "Ticket source": ["Phone Call", "Email", "Web Portal", "Mobile App", "Walk-in", "SMS", "Social Media", "Other"],
        "State": list(_NCC_STATE_LGAS),
        "LGA": sorted({lga for lgas in _NCC_STATE_LGAS.values() for lga in lgas}),
    }


def _ncc_validation_status(record: dict[str, str]) -> str:
    errors: list[str] = []

    def add_error(column: str, message: str) -> None:
        col_ref = _NCC_COLUMN_LETTERS.get(column, "?")
        errors.append(f"{column} {message} (col {col_ref})")

    for column in _NCC_REQUIRED_COLUMNS:
        value = _clean_text(record.get(column))
        if column in {"Age", "Gender"} and value == "N/A":
            continue
        if column == "Last Name" and value == "Unknown":
            continue
        if not value or not _ncc_clean_basic_text(value):
            add_error(column, "is required")

    msisdn = _clean_text(record.get("MSISDN"))
    if msisdn and not (msisdn.startswith("234") or any(char.isalpha() for char in msisdn)):
        add_error("MSISDN", "must start with 234")
    if msisdn and msisdn.startswith("234") and len("".join(char for char in msisdn if char.isdigit())) != 13:
        add_error("MSISDN", "must be 13 digits including 234")
    if not re.fullmatch(r"[A-Za-z]+", _clean_text(record.get("First Name"))):
        add_error("First Name", "must contain letters only")
    if not re.fullmatch(r"[A-Za-z-]+", _clean_text(record.get("Last Name"))):
        add_error("Last Name", "must contain letters only; hyphen is allowed")
    if _ncc_name_contains_test(record.get("First Name")):
        add_error("First Name", "must not contain test data")
    if _ncc_name_contains_test(record.get("Last Name")):
        add_error("Last Name", "must not contain test data")
    if _clean_text(record.get("Age")) != "N/A" and not _ncc_clean_age(record.get("Age")):
        add_error("Age", "must be N/A or a whole number from 13 to 150")
    if _clean_text(record.get("Gender")) not in {"Female", "Male", "N/A"}:
        add_error("Gender", "must be Female, Male, or N/A")
    if _clean_text(record.get("Ticket ID")) and not re.fullmatch(
        rf"{re.escape(_NCC_OPERATOR_PREFIX)}-\d{{8}}-[A-Za-z0-9-]+",
        _clean_text(record.get("Ticket ID")),
    ):
        add_error("Ticket ID", f"must use format {_NCC_OPERATOR_PREFIX}-YYYYMMDD-Number")
    if _clean_text(record.get("Category")) and not _ncc_clean_category(record.get("Category")):
        add_error("Category", "must match an NCC accepted category")
    if _clean_text(record.get("sub category code")) and not _ncc_clean_subcategory_code(
        record.get("sub category code"),
        category=record.get("Category"),
    ):
        add_error("sub category code", "must match the selected NCC category")
    if _ncc_clean_status(record.get("Status")) == "Resolved":
        if not _ncc_clean_basic_text(record.get("Resolved date")):
            add_error("Resolved date", "is required when Status is Resolved")
        if not _ncc_clean_basic_text(record.get("Resolved within SLA")):
            add_error("Resolved within SLA", "is required when Status is Resolved")
        if not _ncc_clean_basic_text(record.get("Resolution Note")):
            add_error("Resolution Note", "is required when Status is Resolved")
    if _ncc_clean_category(record.get("Category")) == "Data Depletion" and not _ncc_clean_basic_text(
        record.get("Phone Type")
    ):
        add_error("Phone Type", "is required when Category is Data Depletion")
    return f"[FAIL] {'; '.join(errors)}" if errors else "[OK] All validations passed"


def _build_ncc_workbook(records: list[dict[str, str]], columns: list[str]) -> bytes:
    long_text_columns = {"Description (auto)", "Resolution Note", "User Note"}
    widths = _ncc_export_column_widths(records, columns)
    dropdown_lists = _ncc_workbook_dropdown_lists()
    output = io.BytesIO()

    def cell_xml(ref: str, value: str, style_id: int) -> str:
        return (
            f'<c r="{ref}" s="{style_id}" t="inlineStr"><is><t xml:space="preserve">'
            f"{escape(str(value or ''))}</t></is></c>"
        )

    def dropdown_sheet_xml() -> str:
        dropdown_columns = list(dropdown_lists)
        max_values = max((len(values) for values in dropdown_lists.values()), default=0)
        rows: list[str] = []
        header_cells = [
            cell_xml(f"{_excel_column_letter(index)}1", column, 1)
            for index, column in enumerate(dropdown_columns, start=1)
        ]
        rows.append(f'<row r="1">{"".join(header_cells)}</row>')
        for row_number in range(2, max_values + 2):
            cells: list[str] = []
            for column_index, dropdown_column in enumerate(dropdown_columns, start=1):
                values = dropdown_lists[dropdown_column]
                value_index = row_number - 2
                if value_index >= len(values):
                    continue
                cells.append(cell_xml(f"{_excel_column_letter(column_index)}{row_number}", values[value_index], 2))
            rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        last_column = _excel_column_letter(len(dropdown_columns))
        last_row = max_values + 1
        return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:{last_column}{last_row}"/>
  <sheetViews><sheetView workbookViewId="0"/></sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <sheetData>{"".join(rows)}</sheetData>
</worksheet>"""

    def data_validations_xml(max_row: int) -> str:
        dropdown_columns = list(dropdown_lists)
        validations: list[str] = []
        if "Age" in columns:
            age_letter = _excel_column_letter(columns.index("Age") + 1)
            validations.append(  # nosec B608 - generated XLSX XML, not SQL
                f'<dataValidation type="custom" allowBlank="0" showErrorMessage="1" '
                f'errorTitle="Invalid Age" error="Age must be N/A or a whole number from 13 to 150." '
                f'sqref="{age_letter}2:{age_letter}{max_row}">'
                f'<formula1>OR({age_letter}2="N/A",AND(ISNUMBER({age_letter}2),'
                f"{age_letter}2=INT({age_letter}2),{age_letter}2&gt;=13,{age_letter}2&lt;=150))</formula1>"
                "</dataValidation>"
            )
        for column in columns:
            values = dropdown_lists.get(column)
            if not values:
                continue
            report_column_index = columns.index(column) + 1
            list_column_index = dropdown_columns.index(column) + 1
            report_letter = _excel_column_letter(report_column_index)
            list_letter = _excel_column_letter(list_column_index)
            formula = f"'_NCC_Dropdowns'!${list_letter}$2:${list_letter}${len(values) + 1}"
            allow_blank = "0" if column in _NCC_REQUIRED_DROPDOWN_COLUMNS else "1"
            validations.append(
                f'<dataValidation type="list" allowBlank="{allow_blank}" showErrorMessage="1" '  # nosec B608 - generated XLSX XML, not SQL
                f'errorTitle="Invalid {escape(column)}" '
                f'error="Select an accepted NCC value from the dropdown." '
                f'sqref="{report_letter}2:{report_letter}{max_row}">'
                f"<formula1>{escape(formula)}</formula1>"
                "</dataValidation>"
            )
        if not validations:
            return ""
        return f'<dataValidations count="{len(validations)}">{"".join(validations)}</dataValidations>'

    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        )
        generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        archive.writestr(
            "docProps/core.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>{escape(_NCC_EXPORT_TITLE)}</dc:title>
  <dc:creator>Dotmac CRM</dc:creator>
  <cp:lastModifiedBy>Dotmac CRM</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{generated_at}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{generated_at}</dcterms:modified>
</cp:coreProperties>""",
        )
        archive.writestr(
            "docProps/app.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Dotmac CRM</Application>
</Properties>""",
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        )
        archive.writestr(
            "xl/workbook.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="NCC Reports" sheetId="1" r:id="rId1"/>
    <sheet name="_NCC_Dropdowns" sheetId="2" state="hidden" r:id="rId2"/>
  </sheets>
</workbook>""",
        )
        archive.writestr(
            "xl/styles.xml",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="1">
    <numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/>
  </numFmts>
  <fonts count="2">
    <font>
      <sz val="11"/>
      <color theme="1"/>
      <name val="Calibri"/>
      <family val="2"/>
    </font>
    <font>
      <b/>
      <sz val="11"/>
      <color rgb="FFFFFFFF"/>
      <name val="Calibri"/>
      <family val="2"/>
    </font>
  </fonts>
  <fills count="7">
    <fill><patternFill patternType="none"/></fill>
    <fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF16A34A"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFDCFCE7"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFEF3C7"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFFEE2E2"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFDBEAFE"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="2">
    <border>
      <left/><right/><top/><bottom/><diagonal/>
    </border>
    <border>
      <left style="thin"><color rgb="FFD1D5DB"/></left>
      <right style="thin"><color rgb="FFD1D5DB"/></right>
      <top style="thin"><color rgb="FFD1D5DB"/></top>
      <bottom style="thin"><color rgb="FFD1D5DB"/></bottom>
      <diagonal/>
    </border>
  </borders>
  <cellStyleXfs count="1">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
  </cellStyleXfs>
  <cellXfs count="12">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="5" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>
    <xf numFmtId="0" fontId="0" fillId="3" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
    <xf numFmtId="0" fontId="0" fillId="4" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="left" vertical="top" wrapText="1"/></xf>
  </cellXfs>
  <cellStyles count="1">
    <cellStyle name="Normal" xfId="0" builtinId="0"/>
  </cellStyles>
</styleSheet>""",
        )

        last_column_letter = _excel_column_letter(len(columns))
        last_row_number = len(records) + 1
        validation_max_row = max(last_row_number, 1000)
        cols_xml = "".join(
            f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
            for index, width in enumerate(widths, start=1)
        )
        rows_xml: list[str] = []
        header_cells = [
            cell_xml(f"{_excel_column_letter(index)}1", column, 1) for index, column in enumerate(columns, start=1)
        ]
        rows_xml.append(f'<row r="1" ht="24" customHeight="1">{"".join(header_cells)}</row>')
        for row_number, row in enumerate(records, start=2):
            cells: list[str] = []
            validation_status = _clean_text(row.get("VALIDATION STATUS"))
            row_style_id = (
                10 if validation_status.startswith("[OK]") else 11 if validation_status.startswith("[FAIL]") else None
            )
            for column_index, column in enumerate(columns, start=1):
                value = " ".join(str(row.get(column) or "").strip().split())
                if not value:
                    continue
                cell_ref = f"{_excel_column_letter(column_index)}{row_number}"
                if row_style_id is not None:
                    style_id = row_style_id
                elif column == "Status":
                    style_id = _ncc_status_style_id(str(row.get("_status_variant") or ""))
                elif column in long_text_columns:
                    style_id = 3
                else:
                    style_id = 2
                cells.append(cell_xml(cell_ref, value, style_id))
            rows_xml.append(f'<row r="{row_number}">{"".join(cells)}</row>')

        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:{last_column_letter}{last_row_number}"/>
  <sheetViews>
    <sheetView workbookViewId="0">
      <pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>
      <selection pane="bottomLeft" activeCell="A2" sqref="A2"/>
    </sheetView>
  </sheetViews>
  <sheetFormatPr defaultRowHeight="18"/>
  <cols>{cols_xml}</cols>
  <sheetData>{"".join(rows_xml)}</sheetData>
  <autoFilter ref="A1:{last_column_letter}{last_row_number}"/>
  {data_validations_xml(validation_max_row)}
</worksheet>""",
        )
        archive.writestr("xl/worksheets/sheet2.xml", dropdown_sheet_xml())

    return output.getvalue()


def _append_query_flag(url: str, key: str, value: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{quote(key)}={quote(value)}"


def _toast_redirect(url: str, *, message: str, toast_type: str = "success", status_code: int = 303) -> RedirectResponse:
    headers = {
        "HX-Trigger": json.dumps(
            {
                "showToast": {
                    "type": toast_type,
                    "message": message,
                }
            }
        )
    }
    return RedirectResponse(url=url, status_code=status_code, headers=headers)


def _latest_subscriber_sync_at(db: Session) -> datetime | None:
    latest = db.scalar(select(func.max(Subscriber.last_synced_at)))
    if latest is None:
        return None
    if latest.tzinfo is None:
        return latest.replace(tzinfo=UTC)
    return latest.astimezone(UTC)


def _resolve_lifecycle_date_range(
    db: Session,
    days: int | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[datetime, datetime]:
    """Resolve lifecycle report range, defaulting to inception when days is 0/None."""
    if start_date and end_date:
        return _parse_date_range(days, start_date, end_date)

    if days and days > 0:
        return _parse_date_range(days, start_date, end_date)

    now = datetime.now(UTC)
    activation_event_at = func.coalesce(Subscriber.activated_at, Subscriber.created_at)
    inception = db.scalar(select(func.min(activation_event_at)))
    if inception is None:
        return now - timedelta(days=30), now
    if inception.tzinfo is None:
        inception = inception.replace(tzinfo=UTC)
    else:
        inception = inception.astimezone(UTC)
    return inception, now


def _display_timestamp(value: datetime | None) -> str:
    if value is None:
        return ""
    normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if normalized > datetime.now(UTC):
        return ""
    return normalized.strftime("%d-%m-%Y %H:%M:%S")


def _display_enum(value: object | None) -> str:
    if value is None:
        return ""
    raw = getattr(value, "value", value)
    text = str(raw).strip()
    if not text:
        return ""
    return text.replace("_", " ").title()


def _person_name(person: Person | None) -> str:
    if person is None:
        return ""
    full_name = " ".join(part for part in [person.first_name, person.last_name] if part).strip()
    return full_name or (person.display_name or "")


def _calculate_age(date_of_birth, reference_at: datetime | None) -> str:
    if not date_of_birth:
        return "N/A"
    reference_date = (
        (reference_at.astimezone(UTC).date() if reference_at and reference_at.tzinfo else reference_at.date())
        if reference_at
        else datetime.now(UTC).date()
    )
    years = reference_date.year - date_of_birth.year
    if (reference_date.month, reference_date.day) < (date_of_birth.month, date_of_birth.day):
        years -= 1
    return str(max(years, 0))


def _ticket_primary_person(ticket: Ticket) -> Person | None:
    if ticket.customer is not None:
        return ticket.customer
    if ticket.subscriber is not None and ticket.subscriber.person is not None:
        return ticket.subscriber.person
    return None


def _ticket_alt_phone(person: Person | None, channels: list[PersonChannel]) -> str:
    if person is None:
        return ""
    normalized_primary = (person.phone or "").strip()
    for channel in channels:
        address = (channel.address or "").strip()
        if not address or address == normalized_primary:
            continue
        return address
    return ""


def _ticket_email(person: Person | None, channels: list[PersonChannel]) -> str:
    primary_email = _ncc_clean_email(person.email if person else "")
    if primary_email:
        return primary_email
    for channel in channels:
        channel_type = getattr(channel.channel_type, "value", str(channel.channel_type))
        if channel_type != "email":
            continue
        email = _ncc_clean_email(channel.address)
        if email:
            return email
    return ""


def _ticket_msisdn(person: Person | None, channels: list[PersonChannel]) -> str:
    primary_msisdn = _complete_ncc_msisdn_or_empty(person.phone if person else None)
    if primary_msisdn:
        return primary_msisdn
    for channel in channels:
        channel_type = getattr(channel.channel_type, "value", str(channel.channel_type))
        if channel_type not in {"phone", "sms", "whatsapp"}:
            continue
        msisdn = _complete_ncc_msisdn_or_empty(channel.address)
        if msisdn:
            return msisdn
    return ""


def _first_msisdn_from_people(people: list[Person]) -> str:
    for person in people:
        msisdn = _complete_ncc_msisdn_or_empty(person.phone)
        if msisdn:
            return msisdn
    return ""


def _normalized_person_match_label(value: object) -> str:
    tokens = re.findall(r"[A-Za-z0-9]+", _clean_text(value).lower())
    normalized_tokens = [
        token for token in tokens if token not in _NCC_NAME_HONORIFICS and token not in _NCC_NAME_PLACEHOLDERS
    ]
    return " ".join(normalized_tokens)


def _raw_person_match_label(value: object) -> str:
    return _clean_text(value).lower()


def _subscriber_person_match_labels(subscriber: Subscriber | None) -> list[str]:
    if subscriber is None:
        return []
    values = [
        getattr(subscriber, "display_name", ""),
        subscriber.subscriber_number,
        subscriber.external_id,
        subscriber.organization.name if subscriber.organization else "",
    ]
    labels: list[str] = []
    for value in values:
        label = _normalized_person_match_label(value)
        if label and label not in labels:
            labels.append(label)
    return labels


def _ticket_ncc_person(ticket: Ticket, fallback_people_by_label: dict[str, list[Person]]) -> Person | None:
    person = _ticket_primary_person(ticket)
    if person is not None:
        return person
    for label in _subscriber_person_match_labels(ticket.subscriber):
        fallback_people = fallback_people_by_label.get(label) or []
        if fallback_people:
            return fallback_people[0]
    return None


def _person_email_match_labels(person: Person | None) -> list[str]:
    if person is None:
        return []
    labels: list[str] = []
    for value in (person.email, person.display_name):
        email = _ncc_clean_email(value)
        if email and email not in labels:
            labels.append(email)
    return labels


def _ticket_msisdn_from_exact_person_matches(
    ticket: Ticket,
    person: Person | None,
    fallback_people_by_label: dict[str, list[Person]],
    fallback_people_by_email: dict[str, list[Person]],
) -> str:
    labels: list[str] = []
    if person is not None:
        labels.extend(
            label
            for label in (
                _normalized_person_match_label(person.display_name),
                _normalized_person_match_label(f"{person.first_name} {person.last_name}"),
            )
            if label
        )
    labels.extend(_subscriber_person_match_labels(ticket.subscriber))
    for label in labels:
        msisdn = _first_msisdn_from_people(fallback_people_by_label.get(label, []))
        if msisdn:
            return msisdn
    for email in _person_email_match_labels(person):
        msisdn = _first_msisdn_from_people(fallback_people_by_email.get(email, []))
        if msisdn:
            return msisdn
    return ""


def _split_name(value: str) -> tuple[str, str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return "", ""
    parts = cleaned.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _title_case_name(value: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    if not cleaned:
        return ""
    if "@" in cleaned:
        return cleaned
    return cleaned.title()


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _title_case_report_value(value: object) -> str:
    cleaned = _clean_text(value)
    if not cleaned or "@" in cleaned:
        return cleaned.lower() if "@" in cleaned else cleaned
    titled = cleaned.title()
    replacements = {
        "Ap/": "AP/",
        "Ap ": "AP ",
        "Sla": "SLA",
        "Ncc": "NCC",
        "Fct": "FCT",
        "Lga": "LGA",
        "Id": "ID",
        "Lan": "LAN",
        "Wan": "WAN",
        "Wifi": "WiFi",
        "Ip": "IP",
        "Dns": "DNS",
        "Onu": "ONU",
        "Ont": "ONT",
        "Olt": "OLT",
        "Los": "LOS",
        "Cpe": "CPE",
        "Noc": "NOC",
        "Crm": "CRM",
        "Sms": "SMS",
        "Whatsapp": "WhatsApp",
    }
    for source, target in replacements.items():
        titled = titled.replace(source, target)
    return titled


def _normalize_msisdn(value: str | None) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    for candidate in re.findall(r"\+?\d+", cleaned):
        digits = "".join(char for char in candidate if char.isdigit())
        if digits.startswith("2340") and len(digits) == 14:
            return f"234{digits[4:]}"
        if digits.startswith("234") and len(digits) == 13:
            return digits
        if digits.startswith("0") and len(digits) == 11:
            return f"234{digits[1:]}"
        if len(digits) == 10:
            return f"234{digits}"
    device_id = "".join(char for char in cleaned if char.isalnum())
    if (
        device_id
        and any(char.isalpha() for char in device_id)
        and any(char.isdigit() for char in device_id)
        and len(device_id) <= 40
    ):
        return device_id.upper()
    digits = "".join(char for char in cleaned if char.isdigit())
    if not digits:
        return ""
    if digits.startswith("234") and len(digits) == 13:
        return digits
    if digits.startswith("2340") and len(digits) == 14:
        return f"234{digits[4:]}"
    if digits.startswith("0") and len(digits) == 11:
        return f"234{digits[1:]}"
    if len(digits) == 10:
        return f"234{digits}"
    return digits if digits.startswith("234") else ""


def _complete_ncc_msisdn_or_empty(value: str | None) -> str:
    normalized = _normalize_msisdn(value)
    if not normalized:
        return ""
    if any(char.isalpha() for char in normalized):
        return normalized if normalized.isalnum() else ""
    digits = "".join(char for char in normalized if char.isdigit())
    return normalized if normalized.startswith("234") and len(digits) == 13 else ""


_NCC_EMPTY_MARKERS = {
    "-",
    "--",
    "---",
    "n/a",
    "na",
    "nil",
    "none",
    "null",
    "unknown",
    "not available",
    "not applicable",
    "not specified",
}


def _ncc_clean_basic_text(value: object) -> str:
    cleaned = _clean_text(value)
    if cleaned.lower() in _NCC_EMPTY_MARKERS:
        return ""
    return cleaned


def _ncc_clean_email(value: object) -> str:
    email_text = _ncc_clean_basic_text(value).lower()
    candidates = [email_text, *re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", email_text)]
    for email in candidates:
        if email and not is_placeholder_email(email) and re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return email
    return ""


def _ncc_clean_age(value: object) -> str:
    age_text = _clean_text(value)
    if age_text.lower() in {"n/a", "na"}:
        return "N/A"
    if not age_text or not age_text.isdigit():
        return ""
    age = int(age_text)
    return str(age) if 13 <= age <= 150 else ""


def _ncc_clean_gender(value: object) -> str:
    normalized = _clean_text(value).lower()
    if normalized in {"n/a", "na", "unknown"}:
        return "N/A"
    if normalized == "female":
        return "Female"
    if normalized == "male":
        return "Male"
    return ""


def _ncc_status_value(ticket: Ticket) -> str:
    status_value = str(getattr(ticket.status, "value", ticket.status) or "").strip().lower()
    return "Resolved" if status_value == "closed" else "Pending"


def _ncc_clean_status(value: object) -> str:
    status = _ncc_clean_basic_text(value)
    return status if status in {"Resolved", "Pending"} else ""


def _ncc_resolved_within_sla_value(ticket: Ticket) -> str:
    if _ncc_status_value(ticket) != "Resolved":
        return ""
    resolved_at = ticket.resolved_at or ticket.closed_at
    due_at = ticket.due_at
    if resolved_at is None or due_at is None:
        return "No"
    normalized_resolved = resolved_at.astimezone(UTC) if resolved_at.tzinfo else resolved_at.replace(tzinfo=UTC)
    normalized_due = due_at.astimezone(UTC) if due_at.tzinfo else due_at.replace(tzinfo=UTC)
    return "Yes" if normalized_resolved <= normalized_due else "No"


def _ncc_clean_yes_no_for_status(value: object, *, status: object) -> str:
    if _ncc_clean_status(status) != "Resolved":
        return ""
    cleaned = _ncc_clean_basic_text(value)
    return cleaned if cleaned in {"Yes", "No"} else ""


def _ncc_clean_timestamp(value: object) -> str:
    timestamp_text = _ncc_clean_basic_text(value)
    if not timestamp_text:
        return ""
    try:
        timestamp = datetime.strptime(timestamp_text, "%d-%m-%Y %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return ""
    if timestamp > datetime.now(UTC):
        return ""
    return timestamp.strftime("%d-%m-%Y %H:%M:%S")


def _ncc_clean_resolved_timestamp(value: object, *, status: object, created_at: object) -> str:
    if _ncc_clean_status(status) != "Resolved":
        return ""
    resolved = _ncc_clean_timestamp(value)
    created = _ncc_clean_timestamp(created_at)
    if not resolved or not created:
        return ""
    resolved_dt = datetime.strptime(resolved, "%d-%m-%Y %H:%M:%S").replace(tzinfo=UTC)
    created_dt = datetime.strptime(created, "%d-%m-%Y %H:%M:%S").replace(tzinfo=UTC)
    return resolved if resolved_dt >= created_dt else ""


def _ncc_clean_subject(value: object) -> str:
    subject = _ncc_clean_title_text(value)
    return subject if len(subject) <= 200 else ""


def _ncc_clean_title_text(value: object) -> str:
    cleaned = _ncc_clean_basic_text(value)
    if not cleaned:
        return ""
    return _title_case_report_value(cleaned)


def _ncc_clean_name(value: object, *, allow_hyphen: bool = False) -> str:
    cleaned = _ncc_clean_basic_text(value)
    if not cleaned:
        return ""
    titled = _title_case_name(cleaned)
    if len(titled) > 50:
        return ""
    for char in titled:
        if char.isalpha() or (allow_hyphen and char == "-"):
            continue
        return ""
    return titled


def _ncc_last_name_fallback(value: object) -> str:
    cleaned = _ncc_clean_name(value, allow_hyphen=True)
    return cleaned or "Unknown"


def _ncc_name_contains_test(value: object) -> bool:
    return bool(re.search(r"\btest\b", _clean_text(value), re.IGNORECASE))


def _ncc_ticket_id(ticket: Ticket) -> str:
    created_at = ticket.created_at
    if created_at is None:
        created_at = datetime.now(UTC)
    normalized_created = created_at.astimezone(UTC) if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    raw_number = ticket.number or str(ticket.id)
    cleaned_number = re.sub(r"[^A-Za-z0-9-]+", "", str(raw_number)) or str(ticket.id).replace("-", "")[:12]
    return f"{_NCC_OPERATOR_PREFIX}-{normalized_created:%Y%m%d}-{cleaned_number}"


def _normalized_ncc_category_key(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _ncc_category_value(ticket_type: object, *, subject: object = "", description: object = "") -> str:
    searchable = " ".join(
        part
        for part in (
            _normalized_ncc_category_key(ticket_type),
            _normalized_ncc_category_key(subject),
            _normalized_ncc_category_key(description),
        )
        if part
    )
    if any(term in searchable for term in ("failed payment", "payment failed", "payment transaction")):
        return "Failed Payment Transactions"
    if any(term in searchable for term in ("billing", "invoice", "charged", "charge", "balance", "refund")):
        return "Billing"
    if any(term in searchable for term in ("call down", "call center", "customer care", "support")):
        return "Call Center / Customer Care"
    if any(term in searchable for term in ("bts", "base station", "basestation")):
        return "BTS Issues"
    if any(term in searchable for term in ("router replacement", "faulty terminal", "cpe", "ont", "onu", "terminal")):
        return "Faulty Terminals"
    if "data depletion" in searchable:
        return "Data Depletion"
    return "Quality of Service (Data)"


def _ncc_clean_category(value: object) -> str:
    category = _ncc_clean_basic_text(value)
    return category if category in _NCC_ACCEPTED_CATEGORIES else ""


def _ncc_category_code_value(category: object) -> str:
    cleaned_category = _ncc_clean_category(category)
    return str(_NCC_CATEGORY_SLA[cleaned_category]["code"]) if cleaned_category else ""


def _ncc_clean_category_code(value: object, *, category: object) -> str:
    code = _ncc_clean_basic_text(value)
    expected = _ncc_category_code_value(category)
    return code if expected and code == expected and code in _NCC_ACCEPTED_CATEGORY_CODES else ""


def _ncc_subcategory_dropdown_value(issue_code: str) -> str:
    row = _NCC_SUBCATEGORY_BY_CODE.get(issue_code)
    return f"{row['issue_code']} - {row['name']}" if row else ""


def _ncc_subcategory_value(
    category: object, ticket_type: object, *, subject: object = "", description: object = ""
) -> str:
    cleaned_category = _ncc_clean_category(category)
    searchable = " ".join(
        part
        for part in (
            _normalized_ncc_category_key(ticket_type),
            _normalized_ncc_category_key(subject),
            _normalized_ncc_category_key(description),
        )
        if part
    )
    if cleaned_category == "Billing":
        return _ncc_subcategory_dropdown_value("A50")
    if cleaned_category == "Call Center / Customer Care":
        return _ncc_subcategory_dropdown_value("B50")
    if cleaned_category == "Quality of Service (Data)":
        if any(term in searchable for term in ("slow", "speed", "bandwidth")):
            return _ncc_subcategory_dropdown_value("D3")
        if any(term in searchable for term in ("outage", "disconnection", "no internet")):
            return _ncc_subcategory_dropdown_value("D2")
        if any(term in searchable for term in ("troubleshooting", "intermittent", "authentication")):
            return _ncc_subcategory_dropdown_value("D1")
        return _ncc_subcategory_dropdown_value("D4")
    if cleaned_category == "Faulty Terminals":
        return (
            _ncc_subcategory_dropdown_value("F1") if "router" in searchable else _ncc_subcategory_dropdown_value("F50")
        )
    if cleaned_category == "BTS Issues":
        return (
            _ncc_subcategory_dropdown_value("G1")
            if "bts" in searchable or "base station" in searchable
            else _ncc_subcategory_dropdown_value("G50")
        )
    if cleaned_category == "Data Depletion":
        return _ncc_subcategory_dropdown_value("Q1")
    if cleaned_category == "Failed Payment Transactions":
        return _ncc_subcategory_dropdown_value("R1")
    code = _ncc_category_code_value(cleaned_category)
    return _ncc_subcategory_dropdown_value(f"{code}50") if code else ""


def _ncc_clean_subcategory_code(value: object, *, category: object) -> str:
    subcategory = _ncc_clean_basic_text(value)
    subcategory = _NCC_SUBCATEGORY_ALIASES.get(subcategory, subcategory)
    if subcategory not in _NCC_ACCEPTED_SUBCATEGORY_CODES:
        return ""
    issue_code, _separator, _name = subcategory.partition(" - ")
    row = _NCC_SUBCATEGORY_BY_CODE.get(issue_code)
    return subcategory if row and row["category"] == _ncc_clean_category(category) else ""


def _ncc_description_for_subcategory(value: object) -> str:
    subcategory = _ncc_clean_basic_text(value)
    issue_code, _separator, _name = subcategory.partition(" - ")
    row = _NCC_SUBCATEGORY_BY_CODE.get(issue_code)
    return _ncc_clean_long_text(row.get("description")) if row else ""


def _ncc_complaint_type_value(ticket: Ticket) -> str:
    return (
        "Second Level" if ticket.service_team_id or ticket.assigned_to_person_id or ticket.assignees else "First Level"
    )


def _ncc_clean_complaint_type(value: object) -> str:
    complaint_type = _ncc_clean_basic_text(value)
    return complaint_type if complaint_type in {"First Level", "Second Level"} else ""


def _ncc_clean_long_text(value: object) -> str:
    cleaned = _ncc_clean_basic_text(value)
    if not cleaned:
        return ""
    if cleaned.isupper():
        cleaned = cleaned.lower()
    return cleaned[:1].upper() + cleaned[1:]


def _ncc_clean_phone_type(value: object, *, category: object) -> str:
    cleaned = _ncc_clean_basic_text(value)
    if not cleaned:
        return ""
    if cleaned.isupper():
        cleaned = cleaned.lower()
    return cleaned[:1].upper() + cleaned[1:]


def _ncc_clean_resolution_note(value: object, *, status: object) -> str:
    if _ncc_clean_status(status) != "Resolved":
        return ""
    cleaned = _ncc_clean_long_text(value)
    return cleaned if cleaned and len(cleaned) <= 500 else ""


def _ncc_clean_user_note(value: object) -> str:
    cleaned = _ncc_clean_long_text(value)
    return cleaned if len(cleaned) <= 300 else ""


def _ncc_clean_language(value: object) -> str:
    language = _ncc_clean_basic_text(value)
    return language if language in _NCC_ACCEPTED_LANGUAGES else ""


def _ncc_ticket_source_value(channel: object) -> str:
    channel_value = str(getattr(channel, "value", channel) or "").strip().lower()
    mapping = {
        "phone": "Phone Call",
        "email": "Email",
        "web": "Web Portal",
        "chat": "Web Portal",
        "api": "Other",
        "sms": "SMS",
        "mobile_app": "Mobile App",
        "walk_in": "Walk-in",
        "walk-in": "Walk-in",
        "social": "Social Media",
        "social_media": "Social Media",
    }
    return mapping.get(channel_value, "Other")


def _ncc_clean_ticket_source(value: object) -> str:
    source = _ncc_clean_basic_text(value)
    return source if source in _NCC_ACCEPTED_TICKET_SOURCES else ""


def _ncc_clean_alt_phone(value: object) -> str:
    digits = "".join(char for char in _ncc_clean_basic_text(value) if char.isdigit())
    return digits if 10 <= len(digits) <= 15 else ""


def _ncc_clean_state(value: object) -> str:
    state = _ncc_clean_basic_text(value).upper()
    state = _NCC_STATE_ALIASES.get(state, state)
    return state if state in _NCC_STATE_LGAS else ""


def _ncc_clean_lga(value: object, *, state: object) -> str:
    cleaned_state = _ncc_clean_state(state)
    lookup_key = re.sub(r"[^a-z0-9]+", " ", _ncc_clean_basic_text(value).lower()).strip()
    return _NCC_LGA_LOOKUP_BY_STATE.get(cleaned_state, {}).get(lookup_key, "")


def _clean_ncc_record(record: dict[str, str]) -> dict[str, str]:
    cleaned = {key: _ncc_clean_basic_text(value) for key, value in record.items()}

    cleaned["MSISDN"] = _complete_ncc_msisdn_or_empty(cleaned.get("MSISDN"))
    cleaned["alt phone number"] = _ncc_clean_alt_phone(cleaned.get("alt phone number"))
    first_name, last_name = _normalize_person_name_parts(cleaned.get("First Name", ""), cleaned.get("Last Name", ""))
    cleaned["First Name"] = _ncc_clean_name(first_name)
    cleaned["Last Name"] = _ncc_last_name_fallback(last_name)
    cleaned["Email"] = _ncc_clean_email(cleaned.get("Email"))
    cleaned["Age"] = _ncc_clean_age(record.get("Age"))
    cleaned["Gender"] = _ncc_clean_gender(record.get("Gender"))
    cleaned["created date time"] = _ncc_clean_timestamp(cleaned.get("created date time"))
    cleaned["Subject"] = _ncc_clean_subject(cleaned.get("Subject"))
    cleaned["Status"] = _ncc_clean_status(cleaned.get("Status"))
    cleaned["Resolved date"] = _ncc_clean_resolved_timestamp(
        cleaned.get("Resolved date"),
        status=cleaned.get("Status"),
        created_at=cleaned.get("created date time"),
    )
    cleaned["Resolved within SLA"] = _ncc_clean_yes_no_for_status(
        cleaned.get("Resolved within SLA"),
        status=cleaned.get("Status"),
    )
    cleaned["Complaint type"] = _ncc_clean_complaint_type(cleaned.get("Complaint type"))

    for column in (
        "created by",
        "Town",
    ):
        cleaned[column] = _ncc_clean_title_text(cleaned.get(column))
    cleaned["Category"] = _ncc_clean_category(cleaned.get("Category"))
    cleaned["category code (auto)"] = _ncc_clean_category_code(
        cleaned.get("category code (auto)"),
        category=cleaned.get("Category"),
    )
    cleaned["sub category code"] = _ncc_clean_subcategory_code(
        cleaned.get("sub category code"),
        category=cleaned.get("Category"),
    )
    cleaned["Description (auto)"] = _ncc_description_for_subcategory(cleaned.get("sub category code"))
    cleaned["Resolution Note"] = _ncc_clean_resolution_note(
        cleaned.get("Resolution Note"), status=cleaned.get("Status")
    )
    cleaned["User Note"] = _ncc_clean_user_note(cleaned.get("User Note"))
    cleaned["user notes datetime"] = (
        _ncc_clean_timestamp(cleaned.get("user notes datetime")) if cleaned["User Note"] else ""
    )
    cleaned["Language"] = _ncc_clean_language(cleaned.get("Language"))
    cleaned["Ticket source"] = _ncc_clean_ticket_source(cleaned.get("Ticket source"))
    cleaned["State"] = _ncc_clean_state(cleaned.get("State"))
    cleaned["LGA"] = _ncc_clean_lga(cleaned.get("LGA"), state=cleaned.get("State"))
    cleaned["Phone Type"] = _ncc_clean_phone_type(cleaned.get("Phone Type"), category=cleaned.get("Category"))
    cleaned["VALIDATION STATUS"] = _ncc_validation_status(cleaned)

    return cleaned


_NCC_NAME_HONORIFICS = {
    "mr",
    "mrs",
    "miss",
    "ms",
    "dr",
    "prof",
    "chief",
    "alhaji",
    "alh",
    "pastor",
    "barrister",
    "hon",
    "honourable",
    "engr",
    "eng",
}
_NCC_NAME_PLACEHOLDERS = {"unknown", "none", "null", "na", "n", "a"}


def _ncc_name_tokens(value: object) -> list[str]:
    cleaned = _clean_text(value)
    if not cleaned:
        return []
    if "@" in cleaned:
        cleaned = cleaned.split("@", 1)[0]
    cleaned = cleaned.replace("'", "").replace("\u2019", "").replace("`", "")
    tokens = re.findall(r"[A-Za-z]+", cleaned)
    return [
        token
        for token in tokens
        if token.strip(".").lower() not in _NCC_NAME_HONORIFICS
        and token.strip(".").lower() not in _NCC_NAME_PLACEHOLDERS
    ]


def _normalize_person_name_parts(first_name: object, last_name: object) -> tuple[str, str]:
    first_tokens = _ncc_name_tokens(first_name)
    last_tokens = _ncc_name_tokens(last_name)

    if first_tokens and last_tokens:
        return first_tokens[0], last_tokens[-1]
    if len(first_tokens) > 1:
        return first_tokens[0], first_tokens[-1]
    if first_tokens:
        return first_tokens[0], first_tokens[0]
    if len(last_tokens) > 1:
        return last_tokens[0], last_tokens[-1]
    if last_tokens:
        return last_tokens[0], last_tokens[0]
    return "", ""


def _looks_like_business_name(value: str) -> bool:
    cleaned = (value or "").strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    business_markers = {
        "ltd",
        "limited",
        "enterprise",
        "enterprises",
        "services",
        "service",
        "global",
        "company",
        "ventures",
        "ventues",
        "nigeria",
        "school",
        "bank",
        "hotel",
        "clinic",
        "hospital",
        "church",
        "mosque",
        "foundation",
        "group",
        "logistics",
        "network",
        "networks",
        "technologies",
        "technology",
        "tech",
        "interior",
        "concept",
        "plaza",
        "mart",
        "stores",
        "apartments",
        "estate",
        "hub",
        "resort",
        "resorts",
        "suite",
        "suites",
        "integrated",
        "royal",
        "events",
    }
    words = {
        token
        for token in "".join(char if char.isalnum() or char.isspace() else " " for char in lowered).split()
        if token
    }
    return any(marker in words for marker in business_markers)


def _label_to_name_parts(value: str, *, treat_as_business: bool = False) -> tuple[str, str]:
    cleaned = (value or "").strip()
    if not cleaned:
        return "", ""
    dash_parts = [part.strip() for part in re.split(r"\s+-\s+", cleaned) if part.strip()]
    if dash_parts and any(term in dash_parts[0].lower() for term in ("customer", "complaint", "disconnection")):
        cleaned = dash_parts[-1]
    for prefix in ("customer link disconnection", "customer realignment"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip(" -")
            break
    first_name, last_name = _split_name(cleaned)
    return _normalize_person_name_parts(first_name, last_name)


def _looks_like_technical_ticket_name_source(value: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not normalized:
        return False
    if normalized in {"test", "this is a test"}:
        return True
    technical_phrases = {
        "multiple cabinet disconnection",
        "cabinet disconnection",
        "cabinet migration",
        "core link disconnection",
        "lan troubleshooting",
        "multiple bts down",
        "bts down",
        "ap outage",
        "air fiber outage",
        "access point outage",
        "device configuration",
        "router configuration",
        "power optimization",
        "power optimisation",
        "fiber link optimization",
        "fiber link optimisation",
    }
    if any(phrase in normalized for phrase in technical_phrases):
        return True
    tokens = set(normalized.split())
    return bool(tokens & {"gpon", "pon", "olt", "lan", "port", "bts", "ap", "outage", "troubleshooting"})


def _ticket_name_parts(ticket: Ticket, person: Person | None) -> tuple[str, str]:
    first_name = (person.first_name or "").strip() if person else ""
    last_name = (person.last_name or "").strip() if person else ""
    if first_name or last_name:
        first_name, last_name = _normalize_person_name_parts(first_name, last_name)
        if first_name and last_name:
            return _title_case_name(first_name), _title_case_name(last_name)

    fallback_values: list[str] = []
    if person and person.display_name:
        fallback_values.append(person.display_name)
    if person and person.email:
        fallback_values.append(person.email)
    if ticket.subscriber and ticket.subscriber.person and ticket.subscriber.person.display_name:
        fallback_values.append(ticket.subscriber.person.display_name)
    if ticket.subscriber and ticket.subscriber.person and ticket.subscriber.person.email:
        fallback_values.append(ticket.subscriber.person.email)
    if ticket.subscriber and ticket.subscriber.display_name:
        fallback_values.append(ticket.subscriber.display_name)
    if ticket.subscriber and ticket.subscriber.organization and ticket.subscriber.organization.name:
        fallback_values.append(ticket.subscriber.organization.name)
    if ticket.subscriber and ticket.subscriber.subscriber_number:
        fallback_values.append(ticket.subscriber.subscriber_number)
    ticket_title = getattr(ticket, "title", "")
    ticket_type = getattr(ticket, "ticket_type", "")
    if ticket_title and not (
        _looks_like_technical_ticket_name_source(ticket_title) or _looks_like_technical_ticket_name_source(ticket_type)
    ):
        fallback_values.append(ticket_title)

    for fallback in fallback_values:
        first_name, last_name = _label_to_name_parts(fallback)
        if first_name or last_name:
            return _title_case_name(first_name), _ncc_last_name_fallback(last_name)
    return "", ""


def _ncc_status_variant(ticket: Ticket) -> str:
    status_value = getattr(ticket.status, "value", ticket.status)
    normalized = str(status_value or "").strip().lower()
    if normalized in {"closed"}:
        return "success"
    if normalized in {"canceled"}:
        return "error"
    if normalized in {"pending", "waiting_on_customer", "lastmile_rerun", "site_under_construction", "on_hold"}:
        return "warning"
    if normalized in {"merged"}:
        return "inactive"
    return "info"


def _normalized_ticket_type_code(ticket_type: str | None) -> str:
    raw = (ticket_type or "").strip()
    if not raw:
        return ""
    cleaned = "".join(char if char.isalnum() else "_" for char in raw.upper())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_")


def _subcategory_code(ticket_type: str | None) -> str:
    normalized = _normalized_ticket_type_code(ticket_type)
    mapping = {
        "AP_AIR_FIBER_OUTAGE": "OUTAGE_AP_AIR_FIBER",
        "AP_LAN_TROUBLESHOOTING": "TROUBLESHOOTING_AP_LAN",
        "CABINET_DISCONNECTION": "DISCONNECTION_CABINET",
        "CALL_DOWN_SUPPORT": "SUPPORT_CALL_DOWN",
        "CORE_LINK_DISCONNECTION": "DISCONNECTION_CORE_LINK",
        "CUSTOMER_LINK_DISCONNECTION": "DISCONNECTION_CUSTOMER_LINK",
        "CUSTOMER_REALIGNMENT": "REALIGNMENT_CUSTOMER",
        "LAN_TROUBLESHOOTING": "TROUBLESHOOTING_LAN",
        "MULTIPLE_CABINET_DISCONNECTION": "DISCONNECTION_CABINET_MULTIPLE",
        "MULTIPLE_CORE_LINK_DISCONNECTION": "DISCONNECTION_CORE_LINK_MULTIPLE",
        "MULTIPLE_CUSTOMER_LINK_DISCONNECTION": "DISCONNECTION_CUSTOMER_LINK_MULTIPLE",
        "POWER_OPTIMIZATION": "OPTIMIZATION_POWER",
        "ROUTER_TROUBLESHOOTING": "TROUBLESHOOTING_ROUTER",
        "SLOW_BROWSING_INTERMITTENT_CONNECTIVITY": "PERFORMANCE_SLOW_INTERMITTENT",
    }
    return mapping.get(normalized, normalized)


def _normalize_ncc_region(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    if not cleaned:
        return ""
    normalized = "".join(char if char.isalnum() or char.isspace() else " " for char in cleaned)
    return " ".join(normalized.split())


def _map_ncc_location(ticket_region: str | None) -> tuple[str, str, str]:
    town = (ticket_region or "").strip()
    normalized = _normalize_ncc_region(ticket_region)
    if not normalized:
        return "", town, ""

    area_council_aliases = {
        "Municipal Area Council": {
            "wuse",
            "maitama",
            "asokoro",
            "garki",
            "jabi",
            "gudu",
            "apo",
            "durumi",
            "utako",
            "mabushi",
            "gwarimpa",
            "lugbe",
            "lokogoma",
            "life camp",
            "lifecamp",
            "katampe",
            "katampe extension",
            "kado",
            "dakibiyu",
            "wuye",
            "wuye district",
            "games village",
            "guzape",
            "guzape district",
            "galadimawa",
            "kabusa",
            "wumba",
            "wumba district",
            "kyami",
            "karmo",
            "karmo district",
            "jikwoyi",
            "karshi",
            "nyanya",
            "orozo",
            "kurudu",
            "kpeyegyi",
            "dakwo",
            "duboyi",
            "kaura",
            "idu",
            "idu industrial",
            "jahi",
            "jahi district",
            "utako district",
            "garki district",
            "gudu district",
            "jabi district",
            "asokoro district",
            "maitama district",
            "wuse 2",
            "wuse ii",
            "wuse zone 1",
            "wuse zone 2",
            "wuse zone 3",
            "wuse zone 4",
            "wuse zone 5",
            "wuse zone 6",
            "wuse zone 7",
        },
        "Bwari": {
            "kubwa",
            "dutse",
            "dutse alhaji",
            "bwari",
            "dei dei",
            "mpape",
            "dawaki",
            "ushafa",
            "byazhin",
        },
        "Gwagwalada": {
            "gwagwalada",
            "zuba",
            "paiko",
            "tunga maje",
            "ibwa",
        },
        "Kuje": {
            "kuje",
            "chukuku",
            "piyanko",
            "rubochi",
        },
        "Abaji": {
            "abaji",
            "yaba",
        },
        "Kwali": {
            "kwali",
            "sheda",
            "pai",
            "yangoji",
            "dafa",
        },
    }

    for lga, aliases in area_council_aliases.items():
        if normalized in aliases:
            return lga, town, "FEDERAL CAPITAL TERRITORY"

    for lga, aliases in area_council_aliases.items():
        for alias in sorted(aliases, key=len, reverse=True):
            if alias in normalized or normalized in alias:
                return lga, _ncc_location_alias_to_town(alias), "FEDERAL CAPITAL TERRITORY"

    return "", town, ""


def _ncc_location_alias_to_town(alias: str) -> str:
    overrides = {
        "lifecamp": "Life Camp",
        "wuse ii": "Wuse II",
    }
    return overrides.get(alias, alias.title())


def _ticket_ncc_location(ticket: Ticket) -> tuple[str, str, str]:
    subscriber = ticket.subscriber
    location_sources = []
    if subscriber is not None:
        location_sources.extend(
            [
                subscriber.service_city,
                subscriber.service_address_line2,
                subscriber.service_address_line1,
            ]
        )
    customer = ticket.customer
    if customer is not None:
        location_sources.extend(
            [
                customer.city,
                customer.address_line2,
                customer.address_line1,
            ]
        )

    for source in location_sources:
        lga, town, state = _map_ncc_location(source)
        if lga and state:
            return lga, town, state
    return "", _clean_text(next((source for source in location_sources if source), "")), ""


_NCC_GENERIC_RESOLUTION_NOTE_MARKERS = (
    "kindly treat",
    "whats the update",
    "what's the update",
    "please treat",
    "assigned",
    "escalated",
    "resolution sent to the customer for confirmation",
)
_NCC_NOTE_LEADING_GROUP_MENTION_RE = re.compile(r"^@\s*[^()\n\r]{1,120}\([^)]*\)\s*", re.IGNORECASE)
_NCC_NOTE_LEADING_ROUTING_MENTION_RE = re.compile(r"^@\s*[^@\n\r,;:.]{1,80}\s*[,;:.+-]\s*", re.IGNORECASE)
_NCC_NOTE_GROUP_MENTION_RE = re.compile(r"@\s*[^@,;:.\n\r]{1,120}\([^)]*\)", re.IGNORECASE)
_NCC_NOTE_PERSON_MENTION_RE = re.compile(r"@\s*[A-Za-z][A-Za-z0-9.'_-]*(?:\s+[A-Z][A-Za-z.'_-]*)?")


def _ncc_clean_note_text(value: object) -> str:
    note = _clean_text(value)
    note = _NCC_NOTE_GROUP_MENTION_RE.sub(" ", note)
    note = _NCC_NOTE_PERSON_MENTION_RE.sub(" ", note)
    note = re.sub(r"\s+([,;:.])", r"\1", note)
    note = re.sub(r"(?:\s*[,;]\s*){2,}", ", ", note)
    previous = None
    while note and note != previous:
        previous = note
        note = _NCC_NOTE_LEADING_GROUP_MENTION_RE.sub("", note)
        note = _NCC_NOTE_LEADING_ROUTING_MENTION_RE.sub("", note).strip(" ,;:-.")
    return _ncc_clean_long_text(note.strip(" ,;:-."))


def _ncc_meaningful_note(value: object) -> str:
    note = _ncc_clean_note_text(value)
    if not note:
        return ""
    lowered = note.lower()
    if any(marker in lowered for marker in _NCC_GENERIC_RESOLUTION_NOTE_MARKERS):
        return ""
    return note


def _ticket_notes(ticket: Ticket) -> tuple[str, str, str]:
    latest_internal: TicketComment | None = None
    latest_meaningful_internal = ""
    latest_meaningful_any = ""

    comments = sorted(ticket.comments or [], key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC))
    for comment in comments:
        meaningful_body = _ncc_meaningful_note(comment.body)
        if meaningful_body:
            latest_meaningful_any = meaningful_body
            if comment.is_internal:
                latest_meaningful_internal = meaningful_body
        if comment.is_internal:
            latest_internal = comment

    metadata = ticket.metadata_ if isinstance(ticket.metadata_, dict) else {}
    resolution_note = ""
    for key in ("resolution_note", "resolution_notes", "resolution_details", "resolution_summary", "closure_note"):
        resolution_note = _ncc_meaningful_note(metadata.get(key))
        if resolution_note:
            break
    if not resolution_note:
        resolution_note = latest_meaningful_internal or latest_meaningful_any
    user_note = _ncc_clean_note_text(latest_internal.body) if latest_internal else ""
    user_note_dt = _display_timestamp(latest_internal.created_at) if latest_internal else ""
    return resolution_note, user_note, user_note_dt


def _ticket_phone_type(ticket: Ticket) -> str:
    metadata = ticket.metadata_ if isinstance(ticket.metadata_, dict) else {}
    for key in (
        "phone_type",
        "phone type",
        "device_make_model",
        "device make model",
        "device_model",
        "device model",
        "phone_model",
        "phone model",
        "device",
    ):
        value = metadata.get(key)
        if value:
            return _clean_text(value)
    return ""


def _default_ncc_date_values() -> tuple[str, str]:
    end_date = datetime.now(UTC).date()
    start_date = end_date - timedelta(days=7)
    return start_date.isoformat(), end_date.isoformat()


def _parse_ncc_window(start_date: str | None, end_date: str | None) -> tuple[datetime, datetime, str, str]:
    default_start, default_end = _default_ncc_date_values()
    start_value = (start_date or default_start).strip() or default_start
    end_value = (end_date or default_end).strip() or default_end

    try:
        start_dt = datetime.fromisoformat(start_value).replace(tzinfo=UTC)
    except ValueError:
        start_value = default_start
        start_dt = datetime.fromisoformat(start_value).replace(tzinfo=UTC)

    try:
        end_dt = datetime.fromisoformat(end_value).replace(tzinfo=UTC)
    except ValueError:
        end_value = default_end
        end_dt = datetime.fromisoformat(end_value).replace(tzinfo=UTC)

    end_dt = end_dt.replace(hour=23, minute=59, second=59)
    if end_dt < start_dt:
        end_value = start_value
        end_dt = start_dt.replace(hour=23, minute=59, second=59)

    return start_dt, end_dt, start_value, end_value


def _build_ncc_records(db: Session, start_dt: datetime, end_dt: datetime) -> list[dict[str, str]]:
    tickets = (
        db.scalars(
            select(Ticket)
            .options(
                joinedload(Ticket.customer),
                joinedload(Ticket.created_by),
                joinedload(Ticket.subscriber).joinedload(Subscriber.person),
                joinedload(Ticket.subscriber).joinedload(Subscriber.organization),
                joinedload(Ticket.comments).joinedload(TicketComment.author),
                selectinload(Ticket.assignees),
            )
            .where(Ticket.created_at >= start_dt, Ticket.created_at <= end_dt)
            .order_by(Ticket.created_at.asc())
        )
        .unique()
        .all()
    )

    ticket_ids = [ticket.id for ticket in tickets]
    conversation_subjects: dict[UUID, str] = {}
    if ticket_ids:
        conversations = db.scalars(
            select(Conversation).where(Conversation.ticket_id.in_(ticket_ids)).order_by(Conversation.created_at.desc())
        ).all()
        for conversation in conversations:
            if conversation.ticket_id and conversation.subject and conversation.ticket_id not in conversation_subjects:
                conversation_subjects[conversation.ticket_id] = conversation.subject.strip()

    subscriber_person_match_labels: set[str] = set()
    raw_person_match_labels: set[str] = set()
    person_email_match_labels: set[str] = set()
    for ticket in tickets:
        primary_person = _ticket_primary_person(ticket)
        if primary_person is not None:
            raw_label = _raw_person_match_label(primary_person.display_name)
            if raw_label:
                raw_person_match_labels.add(raw_label)
            label = _normalized_person_match_label(primary_person.display_name)
            if label:
                subscriber_person_match_labels.add(label)
            raw_label = _raw_person_match_label(f"{primary_person.first_name} {primary_person.last_name}")
            if raw_label:
                raw_person_match_labels.add(raw_label)
            label = _normalized_person_match_label(f"{primary_person.first_name} {primary_person.last_name}")
            if label:
                subscriber_person_match_labels.add(label)
            person_email_match_labels.update(_person_email_match_labels(primary_person))
        subscriber_person_match_labels.update(_subscriber_person_match_labels(ticket.subscriber))
        for raw_value in (
            getattr(ticket.subscriber, "display_name", "") if ticket.subscriber else "",
            getattr(ticket.subscriber, "subscriber_number", "") if ticket.subscriber else "",
            getattr(ticket.subscriber, "external_id", "") if ticket.subscriber else "",
            ticket.subscriber.organization.name if ticket.subscriber and ticket.subscriber.organization else "",
        ):
            raw_label = _raw_person_match_label(raw_value)
            if raw_label:
                raw_person_match_labels.add(raw_label)

    fallback_people_by_label: dict[str, list[Person]] = {}
    person_display_lookup_labels = subscriber_person_match_labels | raw_person_match_labels
    if person_display_lookup_labels:
        fallback_people = db.scalars(
            select(Person).where(func.lower(Person.display_name).in_(person_display_lookup_labels))
        ).all()
        for fallback_person in fallback_people:
            label = _normalized_person_match_label(fallback_person.display_name)
            if label:
                fallback_people_by_label.setdefault(label, []).append(fallback_person)

    fallback_people_by_email: dict[str, list[Person]] = {}
    if person_email_match_labels:
        fallback_email_people = db.scalars(
            select(Person).where(func.lower(Person.email).in_(person_email_match_labels))
        ).all()
        for fallback_person in fallback_email_people:
            email = _ncc_clean_email(fallback_person.email)
            if email:
                fallback_people_by_email.setdefault(email, []).append(fallback_person)

    people: list[Person] = []
    for ticket in tickets:
        person = _ticket_ncc_person(ticket, fallback_people_by_label)
        if person is not None:
            people.append(person)

    person_ids = {person.id for person in people}
    channels_by_person: dict[UUID, list[PersonChannel]] = {}
    if person_ids:
        person_channels = db.scalars(
            select(PersonChannel)
            .where(PersonChannel.person_id.in_(person_ids))
            .order_by(PersonChannel.created_at.asc())
        ).all()
        for channel in person_channels:
            channels_by_person.setdefault(channel.person_id, []).append(channel)

    records: list[dict[str, str]] = []
    for ticket in tickets:
        status_value = str(getattr(ticket.status, "value", ticket.status) or "").strip().lower()
        ticket_type = _clean_text(ticket.ticket_type)
        if status_value == "canceled":
            continue
        if "core link disconnection" in ticket_type.lower():
            continue
        if _looks_like_technical_ticket_name_source(ticket.title) and _normalized_ncc_category_key(ticket.title) in {
            "test",
            "this is a test",
        }:
            continue

        person = _ticket_ncc_person(ticket, fallback_people_by_label)
        first_name, last_name = _ticket_name_parts(ticket, person)
        if not first_name and not last_name:
            continue
        person_channels = channels_by_person.get(person.id, []) if person is not None else []
        msisdn = _ticket_msisdn(person, person_channels) or _ticket_msisdn_from_exact_person_matches(
            ticket,
            person,
            fallback_people_by_label,
            fallback_people_by_email,
        )
        resolution_note, user_note, user_note_dt = _ticket_notes(ticket)
        lga, town, state = _ticket_ncc_location(ticket)
        subject_text = conversation_subjects.get(ticket.id, "") or ticket.title
        ncc_category = _ncc_category_value(ticket_type, subject=subject_text, description=ticket.description)
        ncc_subcategory = _ncc_subcategory_value(
            ncc_category,
            ticket_type,
            subject=subject_text,
            description=ticket.description,
        )

        record = _clean_ncc_record(
            {
                "MSISDN": msisdn,
                "First Name": first_name,
                "Last Name": last_name,
                "Email": _ticket_email(person, person_channels),
                "Age": _calculate_age(person.date_of_birth if person else None, ticket.created_at),
                "Gender": _display_enum(person.gender)
                if person and getattr(person.gender, "value", "unknown") != "unknown"
                else "N/A",
                "created date time": _display_timestamp(ticket.created_at),
                "Subject": _title_case_report_value(subject_text),
                "Category": ncc_category,
                "category code (auto)": _ncc_category_code_value(ncc_category),
                "sub category code": ncc_subcategory,
                "Description (auto)": _ncc_description_for_subcategory(ncc_subcategory),
                "Ticket ID": _ncc_ticket_id(ticket),
                "Complaint type": _ncc_complaint_type_value(ticket),
                "Status": _ncc_status_value(ticket),
                "Resolved date": _display_timestamp(ticket.resolved_at or ticket.closed_at),
                "Resolved within SLA": _ncc_resolved_within_sla_value(ticket),
                "Resolution Note": _clean_text(resolution_note),
                "User Note": _clean_text(user_note),
                "user notes datetime": user_note_dt,
                "Language": "English",
                "Ticket source": _ncc_ticket_source_value(ticket.channel),
                "alt phone number": _ticket_alt_phone(person, person_channels),
                "created by": _title_case_report_value(_person_name(ticket.created_by)) or "Dotmac CRM",
                "State": state,
                "LGA": lga,
                "Town": town,
                "Phone Type": _ticket_phone_type(ticket),
                "_ticket_url": f"/admin/support/tickets/{ticket.number or ticket.id}",
                "_status_variant": _ncc_status_variant(ticket),
            }
        )
        if not record["First Name"] and not record["Last Name"]:
            continue
        if _ncc_name_contains_test(record["First Name"]) or _ncc_name_contains_test(record["Last Name"]):
            continue
        records.append(record)
    return records


def _ncc_export_rows(records: list[dict[str, str]]) -> list[dict[str, str]]:
    export_rows: list[dict[str, str]] = []
    for record in records:
        export_rows.append({key: value for key, value in record.items() if not key.startswith("_")})
    return export_rows


def _filter_ncc_records(records: list[dict[str, str]], query: str | None) -> list[dict[str, str]]:
    normalized_query = _clean_text(query).lower()
    if not normalized_query:
        return records
    return [
        record
        for record in records
        if normalized_query in " ".join(str(record.get(column, "")) for column in _NCC_COLUMNS).lower()
    ]


@router.get("/operations")
def operations_report_alias():
    return RedirectResponse(url="/admin/operations/work-orders", status_code=302)


@router.get(
    "/quarterly",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:operations", "reports"))],
)
def quarterly_report(
    request: Request,
    db: Session = Depends(get_db),
):
    user = get_current_user(request)
    load_error = ""
    report: dict[str, object] = {
        "sources": {
            "customer_workbook": "Dotmac Customer internet usage.xlsx",
            "plan_workbook": "Internet plan usage.xlsx",
        }
    }
    try:
        report = build_quarterly_report()
    except FileNotFoundError as exc:
        logger.warning("quarterly_report_missing_source path=%s", exc.filename)
        load_error = "source_missing"

    return templates.TemplateResponse(
        "admin/reports/quarterly_report.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "quarterly-report",
            "active_menu": "reports",
            "report": report,
            "load_error": load_error,
        },
    )


@router.get(
    "/ncc",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission("reports:operations", "reports"))],
)
def ncc_reports_page(
    request: Request,
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    q: str | None = Query(None),
):
    from app.services import ncc_report_email as ncc_report_email_service

    user = get_current_user(request)
    start_dt, end_dt, start_value, end_value = _parse_ncc_window(start_date, end_date)
    search_query = _clean_text(q)
    all_records = _build_ncc_records(db, start_dt, end_dt)
    records = _filter_ncc_records(all_records, search_query)
    ncc_email_settings = ncc_report_email_service.get_settings_snapshot(db)
    export_params = {"start_date": start_value, "end_date": end_value}
    if search_query:
        export_params["q"] = search_query
    page_params = {"start_date": start_value, "end_date": end_value}
    if search_query:
        page_params["q"] = search_query

    return templates.TemplateResponse(
        "admin/reports/ncc_reports.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "ncc-reports",
            "active_menu": "reports",
            "columns": _NCC_COLUMNS,
            "records": records,
            "total_records": len(all_records),
            "window_start": start_value,
            "window_end": end_value,
            "search_query": search_query,
            "ncc_export_url": f"/admin/reports/ncc/export?{urlencode(export_params)}",
            "ncc_current_url": f"/admin/reports/ncc?{urlencode(page_params)}",
            "ncc_email_settings": ncc_email_settings,
        },
    )


@router.post(
    "/ncc/email-settings",
    dependencies=[Depends(require_any_permission("reports:operations", "reports"))],
)
def ncc_reports_save_email_settings(
    enabled: str | None = Form(None),
    recipient_email: str = Form(""),
    cc: str = Form(""),
    bcc: str = Form(""),
    from_name: str = Form(""),
    subject: str = Form("Weekly NCC Report"),
    body_template: str = Form(""),
    local_time: str = Form("08:00"),
    timezone: str = Form("Africa/Lagos"),
    send_day: str = Form("monday"),
    lookback_days: int = Form(7),
    next_url: str = Form("/admin/reports/ncc"),
    db: Session = Depends(get_db),
):
    from app.services import ncc_report_email as ncc_report_email_service

    if not next_url.startswith("/admin/reports/ncc"):
        next_url = "/admin/reports/ncc"

    try:
        ncc_report_email_service.save_email_settings(
            db,
            enabled=str(enabled or "").strip().lower() in {"1", "true", "yes", "on"},
            recipient_email=recipient_email,
            cc=cc,
            bcc=bcc,
            from_name=from_name,
            subject=subject,
            body_template=body_template,
            local_time=local_time,
            timezone=timezone,
            send_day=send_day,
            lookback_days=lookback_days,
        )
    except Exception as exc:
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    return _toast_redirect(
        next_url,
        message="NCC report email automation settings saved. The scheduler checks every 5 minutes and sends once per week on the selected day after the selected time.",
        toast_type="success",
        status_code=303,
    )


@router.get(
    "/ncc/export",
    response_class=StreamingResponse,
    dependencies=[Depends(require_any_permission("reports:operations", "reports"))],
)
def ncc_reports_export(
    db: Session = Depends(get_db),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    q: str | None = Query(None),
):
    start_dt, end_dt, _start_value, _end_value = _parse_ncc_window(start_date, end_date)
    records = _ncc_export_rows(_filter_ncc_records(_build_ncc_records(db, start_dt, end_dt), q))
    workbook = _build_ncc_workbook(records, _NCC_COLUMNS)
    return _xlsx_response(workbook, _ncc_export_filename(start_dt))


@router.get("/operations-sla-violations", response_class=HTMLResponse)
def operations_sla_violations_report(
    request: Request,
    db: Session = Depends(get_db),
    data_type: str = Query("ticket"),
    region: str | None = Query(None),
    ticket_status: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    user = get_current_user(request)
    _valid_types = {"ticket", "project", "project_task"}
    selected_type: Literal["ticket", "project", "project_task"] = (
        data_type if data_type in _valid_types else "ticket"  # type: ignore[assignment]
    )
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    report = operations_sla_reports_service.operations_sla_violations_report
    region_options = report.region_options(db, selected_type)
    selected_region = region if region in region_options else None
    selected_ticket_status = None
    if selected_type == "ticket" and ticket_status:
        try:
            selected_ticket_status = TicketStatus(ticket_status)
        except ValueError:
            selected_ticket_status = None
    export_query = urlencode(
        {
            "data_type": selected_type,
            "region": selected_region or "",
            "ticket_status": selected_ticket_status.value if selected_ticket_status else "",
            "days": str(days),
            "start_date": start_date or "",
            "end_date": end_date or "",
        }
    )

    summary = report.summary(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        ticket_status=selected_ticket_status,
        open_only=True,
    )
    region_chart = report.by_region(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        ticket_status=selected_ticket_status,
        open_only=True,
    )
    trend_chart = report.trend_daily(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        ticket_status=selected_ticket_status,
        open_only=True,
    )
    records = report.list_records(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        ticket_status=selected_ticket_status,
        open_only=True,
    )

    data_type_options = [
        {"value": "ticket", "label": "Tickets"},
        {"value": "project", "label": "Projects"},
        {"value": "project_task", "label": "Project Tasks"},
    ]
    ticket_status_options = [
        {"value": status.value, "label": status.value.replace("_", " ").title()}
        for status in TicketStatus
        if status != TicketStatus.closed
    ]

    return templates.TemplateResponse(
        "admin/reports/operations_sla_violations.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "active_menu": "reports",
            "active_page": "operations-sla-violations",
            "sidebar_stats": get_sidebar_stats(db),
            "data_type_options": data_type_options,
            "selected_data_type": selected_type,
            "region_options": region_options,
            "selected_region": selected_region or "",
            "ticket_status_options": ticket_status_options,
            "selected_ticket_status": selected_ticket_status.value if selected_ticket_status else "",
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "summary": summary,
            "region_chart": region_chart,
            "trend_chart": trend_chart,
            "records": records,
            "export_query": export_query,
        },
    )


@router.get("/operations-sla-violations/export")
def operations_sla_violations_export(
    db: Session = Depends(get_db),
    data_type: str = Query("ticket"),
    region: str | None = Query(None),
    ticket_status: str | None = Query(None),
    days: int = Query(30, ge=1, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    _valid_types = {"ticket", "project", "project_task"}
    selected_type: Literal["ticket", "project", "project_task"] = (
        data_type if data_type in _valid_types else "ticket"  # type: ignore[assignment]
    )
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    report = operations_sla_reports_service.operations_sla_violations_report
    region_options = report.region_options(db, selected_type)
    selected_region = region if region in region_options else None
    selected_ticket_status = None
    if selected_type == "ticket" and ticket_status:
        try:
            selected_ticket_status = TicketStatus(ticket_status)
        except ValueError:
            selected_ticket_status = None
    records = report.list_records(
        db,
        entity_type=selected_type,
        region=selected_region,
        start_at=start_dt,
        end_at=end_dt,
        ticket_status=selected_ticket_status,
        open_only=True,
        limit=10000,
    )
    export_data = [
        {
            "ID": record.get("id", ""),
            "Title": record.get("title", ""),
            "Project": record.get("project", "") or "",
            "Region": record.get("region", ""),
            "SLA Type": record.get("sla_type", ""),
            "Status": str(record.get("ticket_status") or record.get("status") or "").replace("_", " ").title(),
            "Breach Duration": record.get("breach_duration", ""),
        }
        for record in records
    ]
    filename = f"operations_sla_violations_{selected_type}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_response(export_data, filename)


# Legacy redirects point to new subscriber overview
@router.get("/subscribers")
def subscribers_report_redirect():
    """Legacy subscriber report - redirect to overview."""
    return RedirectResponse(url="/admin/reports/subscribers/overview", status_code=302)


@router.get("/churn")
def churn_report_redirect():
    """Legacy churn report - redirect to churned subscribers."""
    return RedirectResponse(url="/admin/reports/subscribers/churned", status_code=302)


# =============================================================================
# Chat Queue & Classification Report
# =============================================================================


@router.get("/queue", response_class=HTMLResponse)
def queue_classification_report(
    request: Request,
    db: Session = Depends(get_db),
    period_days: int = Query(7, ge=1, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Queue-wait statistics and issue-classification breakdown for chat conversations."""
    from app.services.crm import reports as crm_reports

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(period_days, start_date, end_date)

    queue = crm_reports.queue_wait_metrics(db, start_dt, end_dt)
    classification = crm_reports.issue_classification_breakdown(db, start_dt, end_dt)

    return templates.TemplateResponse(
        "admin/reports/queue_classification.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "queue-report",
            "active_menu": "reports",
            "queue": queue,
            "classification": classification,
            "period_days": period_days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


# =============================================================================
# Network Infrastructure Report (real data)
# =============================================================================


@router.get("/network", response_class=HTMLResponse)
def network_report(
    request: Request,
    db: Session = Depends(get_db),
    period_days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Network infrastructure report with real OLT/ONT/fiber data."""
    from app.services import network_reports as nr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(period_days, start_date, end_date)

    kpis = nr.get_network_kpis(db)
    olt_capacity = nr.get_olt_capacity(db)
    fiber_strand_status = nr.get_fiber_strand_status(db)
    ont_trend = nr.get_ont_activation_trend(db, start_dt, end_dt)
    olt_table = nr.get_olt_table(db)
    fdh_table = nr.get_fdh_utilization(db)
    fiber_inventory = nr.get_fiber_inventory(db)
    recent_ont = nr.get_recent_ont_activity(db)

    return templates.TemplateResponse(
        "admin/reports/network.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "network-report",
            "active_menu": "reports",
            "kpis": kpis,
            "olt_capacity": olt_capacity,
            "fiber_strand_status": fiber_strand_status,
            "ont_trend": ont_trend,
            "olt_table": olt_table,
            "fdh_table": fdh_table,
            "fiber_inventory": fiber_inventory,
            "recent_ont": recent_ont,
            "period_days": period_days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/network/export")
def network_report_export(
    db: Session = Depends(get_db),
):
    """Export network infrastructure report as CSV."""
    from app.services import network_reports as nr

    export_data = nr.get_network_export_data(db)
    filename = f"network_infrastructure_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Overview Report
# =============================================================================


@router.get("/subscribers/overview", response_class=HTMLResponse)
def subscriber_overview(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    status: str | None = Query(None),
    region: str | None = Query(None),
):
    """Subscriber overview report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    region_value = region if isinstance(region, str) else None
    status_value = status if isinstance(status, str) else None
    selected_region = region_value if region_value in region_options else None
    valid_statuses = {status.value: status for status in SubscriberStatus}
    selected_status = valid_statuses.get((status_value or "").strip().lower())
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)

    kpis = sr.overview_kpis(db, start_dt, end_dt, subscriber_ids=subscriber_ids)
    growth_trend = sr.overview_growth_trend(db, start_dt, end_dt, subscriber_ids=subscriber_ids)
    status_dist = sr.overview_status_distribution(db, subscriber_ids=subscriber_ids)
    plan_dist = sr.overview_plan_distribution(db, subscriber_ids=subscriber_ids)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt, subscriber_ids=subscriber_ids)

    return templates.TemplateResponse(
        "admin/reports/subscriber_overview.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-overview",
            "active_menu": "reports",
            "kpis": kpis,
            "growth_trend": growth_trend,
            "status_dist": status_dist,
            "plan_dist": plan_dist,
            "regional": regional,
            "filter_opts": filter_opts,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "selected_status": selected_status.value if selected_status else "",
            "selected_region": selected_region or "",
        },
    )


@router.get("/subscribers/overview/export")
def subscriber_overview_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    status: str | None = Query(None),
    region: str | None = Query(None),
):
    """Export subscriber overview as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    region_value = region if isinstance(region, str) else None
    status_value = status if isinstance(status, str) else None
    selected_region = region_value if region_value in region_options else None
    valid_statuses = {subscriber_status.value: subscriber_status for subscriber_status in SubscriberStatus}
    selected_status = valid_statuses.get((status_value or "").strip().lower())
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)
    regional = sr.overview_regional_breakdown(db, start_dt, end_dt, subscriber_ids=subscriber_ids)

    export_data = [
        {
            "Region": r["region"],
            "Active": r["active"],
            "Suspended": r["suspended"],
            "Terminated": r["terminated"],
            "New in Period": r["new_in_period"],
            "Tickets": r["ticket_count"],
        }
        for r in regional
    ]
    filename = f"subscriber_overview_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Online Last 24h Report
# =============================================================================


@router.get(
    "/subscribers/online-last-24h",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_READ_PERMISSIONS))],
)
def subscriber_online_last_24h(
    request: Request,
    db: Session = Depends(get_db),
    status: str | None = Query(None),
    region: str | None = Query(None),
    search: str | None = Query(None),
    ticket_status: str | None = Query("all"),
    notification_state: str | None = Query("all"),
    activity_segment: str | None = Query("active_last24_not_online"),
    base_station: list[str] = Query(default=[]),
):
    """Subscribers with online/session activity in the last 24 hours."""
    from app.services import subscriber_notifications as subscriber_notifications_service
    from app.services import subscriber_offline_outreach as subscriber_offline_outreach_service
    from app.services import subscriber_reports as sr
    from app.services.crm.web_campaigns import outreach_channel_target_options

    user = get_current_user(request)
    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    status_value = (status or "").strip().lower()
    selected_region = region if isinstance(region, str) and region in region_options else None
    selected_status = next((item for item in SubscriberStatus if item.value == status_value), None)
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)

    selected_ticket_status = (ticket_status or "all").strip().lower()
    valid_ticket_values = {item["value"] for item in _ONLINE_LAST_24H_TICKET_STATUS_OPTIONS}
    if selected_ticket_status not in valid_ticket_values:
        selected_ticket_status = "all"
    selected_notification_state = (notification_state or "all").strip().lower()
    valid_notification_values = {item["value"] for item in _ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS}
    if selected_notification_state not in valid_notification_values:
        selected_notification_state = "all"
    selected_activity_segment = (activity_segment or "active_last24_not_online").strip().lower()
    valid_activity_segments = {item["value"] for item in _ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS}
    if selected_activity_segment not in valid_activity_segments:
        selected_activity_segment = "active_last24_not_online"
    search_value = (search or "").strip()

    cache_key = _online_last_24h_cache_key(
        status=selected_status.value if selected_status else "",
        region=selected_region or "",
        search=search_value,
        ticket_status=selected_ticket_status,
        notification_state=selected_notification_state,
        activity_segment=selected_activity_segment,
        subscriber_ids=subscriber_ids,
    )
    online_customers, cache_hit = _online_last_24h_cached_rows(
        cache_key,
        lambda: subscriber_notifications_service.enrich_notification_rows(
            subscriber_offline_outreach_service.enrich_rows_with_station_status(
                db,
                sr.online_customers_last_24h_rows(
                    db,
                    subscriber_ids=subscriber_ids,
                    search=search_value,
                    ticket_status=selected_ticket_status,
                    notification_state=selected_notification_state,
                    activity_segment=selected_activity_segment,
                    limit=None,
                ),
            )
            if hasattr(db, "execute")
            else sr.online_customers_last_24h_rows(
                db,
                subscriber_ids=subscriber_ids,
                search=search_value,
                ticket_status=selected_ticket_status,
                notification_state=selected_notification_state,
                activity_segment=selected_activity_segment,
                limit=None,
            ),
            db,
        ),
    )
    if cache_hit:
        logger.info("online_last_24h_rows_cache_hit rows=%s", len(online_customers))
    base_station_options = _online_last_24h_base_station_options(online_customers)
    selected_base_stations = [
        value for value in _normalize_online_last_24h_base_station_values(base_station) if value in base_station_options
    ]
    online_customers = _filter_online_last_24h_base_stations(online_customers, selected_base_stations)
    online_customers = _filter_online_last_24h_notification_state(online_customers, selected_notification_state)
    online_customers = _sort_online_last_24h_rows(online_customers)
    has_db_session = hasattr(db, "execute")
    outreach_settings = (
        subscriber_offline_outreach_service.get_outreach_settings_snapshot(db)
        if has_db_session
        else {
            "enabled": False,
            "interval_seconds": 0,
            "local_time": "10:00",
            "timezone": "Africa/Lagos",
            "channel_target_id": "",
            "cooldown_hours": 0,
            "template_name": "",
            "template_language": "",
            "template_body": "",
            "template_parameter_values": {},
            "template_parameter_indexes": [],
            "template_payload": None,
        }
    )
    outreach_channel_targets = outreach_channel_target_options(db) if has_db_session else {}

    return templates.TemplateResponse(
        "admin/reports/subscriber_online_last_24h.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-online-last-24h",
            "active_menu": "reports",
            "online_customers": online_customers,
            "summary_total": len(online_customers),
            "summary_no_ticket": sum(1 for row in online_customers if not row.get("ticket_status")),
            "ticket_status_kpis": _online_last_24h_ticket_status_cards(online_customers),
            "filter_opts": filter_opts,
            "selected_status": selected_status.value if selected_status else "",
            "selected_region": selected_region or "",
            "search": search_value,
            "selected_ticket_status": selected_ticket_status,
            "ticket_status_options": _ONLINE_LAST_24H_TICKET_STATUS_OPTIONS,
            "selected_notification_state": selected_notification_state,
            "notification_state_options": _ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS,
            "selected_activity_segment": selected_activity_segment,
            "activity_segment_options": _ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS,
            "base_station_options": base_station_options,
            "selected_base_stations": selected_base_stations,
            "selected_base_station_query": "".join(f"&base_station={quote(value)}" for value in selected_base_stations),
            "outreach_channel_targets": outreach_channel_targets,
            "outreach_settings": outreach_settings,
            "current_query": request.url.path + (f"?{request.url.query}" if request.url.query else ""),
        },
    )


@router.post(
    "/subscribers/online-last-24h/outreach/settings",
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS))],
)
def subscriber_online_last_24h_save_outreach_settings(
    request: Request,
    outreach_local_time: str = Form("10:00"),
    outreach_timezone: str = Form("Africa/Lagos"),
    outreach_channel_target_id: str = Form(""),
    outreach_whatsapp_template_name: str = Form(""),
    outreach_whatsapp_template_language: str = Form(""),
    outreach_whatsapp_template_parameters: str = Form("{}"),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_offline_outreach as subscriber_offline_outreach_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    try:
        subscriber_offline_outreach_service.save_outreach_settings(
            db,
            local_time=outreach_local_time,
            timezone=outreach_timezone,
            channel_target_id=outreach_channel_target_id,
            whatsapp_template_name=outreach_whatsapp_template_name,
            whatsapp_template_language=outreach_whatsapp_template_language,
            whatsapp_template_parameters=outreach_whatsapp_template_parameters,
        )
    except Exception as exc:
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    return _toast_redirect(
        next_url,
        message="Offline outreach settings saved. The scheduler will check every 5 minutes and run once per day after the selected time.",
        toast_type="success",
        status_code=303,
    )


@router.get(
    "/subscribers/online-last-24h/context/{subscriber_id}",
    response_class=JSONResponse,
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_READ_PERMISSIONS))],
)
def subscriber_online_last_24h_notify_context(
    subscriber_id: UUID,
    last_seen_at: str | None = Query(None),
    last_activity: str | None = Query(None),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    payload = subscriber_notifications_service.notification_context_for_subscriber(
        db,
        subscriber_id=subscriber_id,
        last_seen_text=last_seen_at,
        last_activity=last_activity,
    )
    return JSONResponse(payload)


@router.post(
    "/subscribers/online-last-24h/templates",
    response_class=JSONResponse,
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS))],
)
def subscriber_online_last_24h_save_template(
    template_key: str = Form(...),
    email_subject: str = Form(...),
    email_body: str = Form(...),
    sms_body: str = Form(...),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    saved = subscriber_notifications_service.save_template_bundle(
        db,
        template_key=template_key,
        email_subject=email_subject,
        email_body=email_body,
        sms_body=sms_body,
    )
    return JSONResponse({"ok": True, "template": saved})


@router.post(
    "/subscribers/online-last-24h/notify",
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS))],
)
def subscriber_online_last_24h_notify(
    request: Request,
    subscriber_id: UUID = Form(...),
    channel: str = Form(...),
    email_subject: str | None = Form(None),
    email_body: str | None = Form(None),
    sms_body: str | None = Form(None),
    scheduled_local_at: str | None = Form(None),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    user = get_current_user(request)
    raw_user_id = user.get("id")
    raw_person_id = user.get("person_id")

    try:
        subscriber_notifications_service.queue_subscriber_notification(
            db,
            subscriber_id=subscriber_id,
            channel_value=channel,
            email_subject=email_subject,
            email_body=email_body,
            sms_body=sms_body,
            scheduled_local_text=scheduled_local_at,
            sent_by_user_id=UUID(str(raw_user_id)) if raw_user_id else None,
            sent_by_person_id=UUID(str(raw_person_id)) if raw_person_id else None,
        )
    except Exception as exc:
        db.rollback()
        if isinstance(exc, Response):
            return exc
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    channel_label = channel.strip().lower()
    if channel_label == "both":
        message = "Email and WhatsApp notifications saved in test queue. No customer message was sent."
    elif channel_label == "whatsapp":
        message = "WhatsApp notification saved in test queue. No customer message was sent."
    else:
        message = "Email notification saved in test queue. No customer message was sent."
    return _toast_redirect(next_url, message=message)


@router.post(
    "/subscribers/online-last-24h/notify/bulk",
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS))],
)
def subscriber_online_last_24h_bulk_notify(
    request: Request,
    subscriber_ids: str = Form(...),
    channel: str = Form(...),
    email_subject: str | None = Form(None),
    email_body: str | None = Form(None),
    sms_body: str | None = Form(None),
    scheduled_local_at: str | None = Form(None),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    parsed_ids: list[UUID] = []
    for raw_id in subscriber_ids.split(","):
        try:
            parsed_ids.append(UUID(raw_id.strip()))
        except (TypeError, ValueError):
            continue
    if not parsed_ids:
        return _toast_redirect(next_url, message="Select at least one CRM-linked customer.", toast_type="error")

    user = get_current_user(request)
    raw_user_id = user.get("id")
    raw_person_id = user.get("person_id")
    result = subscriber_notifications_service.queue_bulk_subscriber_notifications(
        db,
        subscriber_ids=parsed_ids,
        channel_value=channel,
        email_subject=email_subject,
        email_body=email_body,
        sms_body=sms_body,
        scheduled_local_text=scheduled_local_at,
        sent_by_user_id=UUID(str(raw_user_id)) if raw_user_id else None,
        sent_by_person_id=UUID(str(raw_person_id)) if raw_person_id else None,
    )
    queued = int(result.get("queued", 0))
    skipped = int(result.get("skipped", 0))
    selected = int(result.get("selected", 0))
    toast_type = "success" if queued else "error"
    message = f"Bulk notification queued {queued} draft(s) for {selected} selected customer(s)."
    if skipped:
        message = (
            f"{message} Skipped {skipped} customer(s) due to missing contact details or recent duplicate notifications."
        )
    return _toast_redirect(next_url, message=message, toast_type=toast_type)


@router.post(
    "/subscribers/online-last-24h/outreach",
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS))],
)
def subscriber_online_last_24h_create_outreach(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form("Online Last 24H Outreach"),
    channel: str = Form("whatsapp"),
    channel_target_id: str = Form(""),
    subscriber_id: list[str] = Form(default=[]),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
):
    from app.services.crm.web_campaigns import create_online_last_24h_outreach_campaign

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    selected_subscriber_ids: list[str] = []
    for raw_id in subscriber_id:
        try:
            selected_subscriber_ids.append(str(UUID(str(raw_id).strip())))
        except (TypeError, ValueError):
            continue
    if not selected_subscriber_ids:
        return _toast_redirect(next_url, message="Select at least one CRM-linked customer.", toast_type="error")
    allowed_target_ids = _online_last_24h_allowed_target_ids(db, channel)
    if str(channel_target_id or "").strip() not in allowed_target_ids:
        return _toast_redirect(
            next_url,
            message="Select an approved Send From target for this channel.",
            toast_type="error",
            status_code=303,
        )

    user = get_current_user(request)
    try:
        campaign = create_online_last_24h_outreach_campaign(
            db,
            name=name,
            channel=channel,
            channel_target_id=channel_target_id,
            subscriber_ids=selected_subscriber_ids,
            created_by_id=str(user.get("person_id") or "") or None,
            source_filters={
                "query": request.headers.get("referer", ""),
                "selected_count": len(selected_subscriber_ids),
                "source_report": "online_last_24h",
            },
        )
    except Exception as exc:
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    return RedirectResponse(url=f"/admin/crm/campaigns/{campaign.id}", status_code=303)


@router.post(
    "/subscribers/online-last-24h/notify/test-send",
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_WRITE_PERMISSIONS))],
)
def subscriber_online_last_24h_test_send(
    request: Request,
    subscriber_id: UUID = Form(...),
    next_url: str = Form("/admin/reports/subscribers/online-last-24h"),
    db: Session = Depends(get_db),
):
    from app.services import subscriber_notifications as subscriber_notifications_service

    if not next_url.startswith("/admin/reports/subscribers/online-last-24h"):
        next_url = "/admin/reports/subscribers/online-last-24h"

    user = get_current_user(request)
    raw_person_id = user.get("person_id")
    try:
        result = subscriber_notifications_service.approve_and_send_test_notifications(
            db,
            subscriber_id=subscriber_id,
            approved_by_person_id=UUID(str(raw_person_id)) if raw_person_id else None,
        )
    except Exception as exc:
        db.rollback()
        detail = getattr(exc, "detail", None) or str(exc)
        return _toast_redirect(next_url, message=str(detail), toast_type="error", status_code=303)

    sent = int(result.get("sent", 0))
    failed = int(result.get("failed", 0))
    toast_type = "success" if sent and not failed else "error"
    return _toast_redirect(
        next_url,
        message=f"Approve & Send submitted for test account: {sent} sent to outreach delivery, {failed} failed.",
        toast_type=toast_type,
    )


@router.get(
    "/subscribers/online-last-24h/export",
    dependencies=[Depends(require_any_permission(*REPORTS_ONLINE_LAST_24H_READ_PERMISSIONS))],
)
def subscriber_online_last_24h_export(
    db: Session = Depends(get_db),
    status: str | None = Query(None),
    region: str | None = Query(None),
    search: str | None = Query(None),
    ticket_status: str | None = Query("all"),
    notification_state: str | None = Query("all"),
    activity_segment: str | None = Query("active_last24_not_online"),
    base_station: list[str] = Query(default=[]),
):
    """Export last-24h online subscribers report."""
    from app.services import subscriber_notifications as subscriber_notifications_service
    from app.services import subscriber_offline_outreach as subscriber_offline_outreach_service
    from app.services import subscriber_reports as sr

    filter_opts = sr.overview_filter_options(db)
    region_options = filter_opts.get("regions", [])
    status_value = (status or "").strip().lower()
    selected_region = region if isinstance(region, str) and region in region_options else None
    selected_status = next((item for item in SubscriberStatus if item.value == status_value), None)
    subscriber_ids = sr.overview_filtered_subscriber_ids(db, status=selected_status, region=selected_region)
    selected_ticket_status = (ticket_status or "all").strip().lower()
    valid_ticket_values = {item["value"] for item in _ONLINE_LAST_24H_TICKET_STATUS_OPTIONS}
    if selected_ticket_status not in valid_ticket_values:
        selected_ticket_status = "all"
    selected_notification_state = (notification_state or "all").strip().lower()
    valid_notification_values = {item["value"] for item in _ONLINE_LAST_24H_NOTIFICATION_STATE_OPTIONS}
    if selected_notification_state not in valid_notification_values:
        selected_notification_state = "all"
    selected_activity_segment = (activity_segment or "active_last24_not_online").strip().lower()
    valid_activity_segments = {item["value"] for item in _ONLINE_LAST_24H_ACTIVITY_SEGMENT_OPTIONS}
    if selected_activity_segment not in valid_activity_segments:
        selected_activity_segment = "active_last24_not_online"

    search_value = (search or "").strip()
    cache_key = _online_last_24h_cache_key(
        status=selected_status.value if selected_status else "",
        region=selected_region or "",
        search=search_value,
        ticket_status=selected_ticket_status,
        notification_state=selected_notification_state,
        activity_segment=selected_activity_segment,
        subscriber_ids=subscriber_ids,
    )
    online_customers, _cache_hit = _online_last_24h_cached_rows(
        cache_key,
        lambda: subscriber_notifications_service.enrich_notification_rows(
            subscriber_offline_outreach_service.enrich_rows_with_station_status(
                db,
                sr.online_customers_last_24h_rows(
                    db,
                    subscriber_ids=subscriber_ids,
                    search=search_value,
                    ticket_status=selected_ticket_status,
                    notification_state=selected_notification_state,
                    activity_segment=selected_activity_segment,
                    limit=None,
                ),
            )
            if hasattr(db, "execute")
            else sr.online_customers_last_24h_rows(
                db,
                subscriber_ids=subscriber_ids,
                search=search_value,
                ticket_status=selected_ticket_status,
                notification_state=selected_notification_state,
                activity_segment=selected_activity_segment,
                limit=None,
            ),
            db,
        ),
    )
    base_station_options = _online_last_24h_base_station_options(online_customers)
    selected_base_stations = [
        value for value in _normalize_online_last_24h_base_station_values(base_station) if value in base_station_options
    ]
    online_customers = _filter_online_last_24h_base_stations(online_customers, selected_base_stations)
    online_customers = _filter_online_last_24h_notification_state(online_customers, selected_notification_state)
    online_customers = _sort_online_last_24h_rows(online_customers)

    export_rows = [
        {
            "Name": row.get("name", ""),
            "Subscriber Number": row.get("subscriber_number", ""),
            "Status": row.get("status", ""),
            "Region": row.get("region", ""),
            "Email": row.get("email", ""),
            "Phone": row.get("phone", ""),
            "Last Seen At": row.get("last_seen_at", ""),
            "Last Activity": row.get("last_activity", ""),
            "Base Station": row.get("base_station", ""),
            "Base Station Status": row.get("station_status", ""),
            "Currently Online": "Yes" if row.get("currently_online") else "No",
            "Ticket Status": row.get("ticket_status", ""),
        }
        for row in online_customers
    ]
    filename_prefix = (
        "active_last24_not_currently_online"
        if selected_activity_segment == "active_last24_not_online"
        else "online_customers_last_24h"
    )
    filename = f"{filename_prefix}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"
    return _csv_response(export_rows, filename)


# =============================================================================
# Subscriber Lifecycle Report
# =============================================================================


@router.get("/subscribers/lifecycle", response_class=HTMLResponse)
def subscriber_lifecycle(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    sort_by: str = Query("total_paid"),
):
    """Subscriber lifecycle and churn report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)

    kpis = sr.lifecycle_kpis(db, start_dt, end_dt)
    funnel = sr.lifecycle_funnel(db)
    churn_trend = sr.lifecycle_churn_trend(db)
    conversion_by_source = sr.lifecycle_conversion_by_source(db, start_dt, end_dt)
    retention_cohorts = sr.lifecycle_retention_cohorts(db, start_dt, end_dt)
    time_to_convert_distribution = sr.lifecycle_time_to_convert_distribution(db, start_dt, end_dt)
    plan_migration_flow = sr.lifecycle_plan_migration_flow(db, start_dt, end_dt)
    plan_distribution = sr.overview_plan_distribution(db, limit=8)
    recent_churns = sr.lifecycle_recent_churns(db)
    recent_churn_summary = sr.lifecycle_recent_churn_summary(db)
    longest_tenure = sr.lifecycle_longest_tenure(db)
    top_subscribers_by_value = sr.lifecycle_top_subscribers_by_value(db)
    top_subscribers_title = "Top Subscribers By Value (All Time)"
    top_subscribers_description = "Sorted by total paid across all subscriber histories."
    if sort_by == "tenure_months":
        top_subscribers_by_value = sorted(
            top_subscribers_by_value,
            key=lambda row: (-(row.get("tenure_months") or 0), -(row.get("total_paid") or 0), row.get("name") or ""),
        )
        top_subscribers_title = "By Tenure"
        top_subscribers_description = "Sorted by tenure, with total paid as tie-breaker."
    elif sort_by == "plan_type":
        top_subscribers_by_value = sorted(
            top_subscribers_by_value,
            key=lambda row: (
                (row.get("plan") or "").lower(),
                -(row.get("total_paid") or 0),
                -(row.get("tenure_months") or 0),
                row.get("name") or "",
            ),
        )
        top_subscribers_title = "Plan Type"
        top_subscribers_description = "Sorted alphabetically by plan type, with revenue and tenure as tie-breakers."
    else:
        sort_by = "total_paid"

    return templates.TemplateResponse(
        "admin/reports/subscriber_lifecycle.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-lifecycle",
            "active_menu": "reports",
            "kpis": kpis,
            "funnel": funnel,
            "churn_trend": churn_trend,
            "conversion_by_source": conversion_by_source,
            "retention_cohorts": retention_cohorts,
            "time_to_convert_distribution": time_to_convert_distribution,
            "plan_migration_flow": plan_migration_flow,
            "plan_distribution": plan_distribution,
            "recent_churns": recent_churns,
            "recent_churn_summary": recent_churn_summary,
            "longest_tenure": longest_tenure,
            "top_subscribers_by_value": top_subscribers_by_value,
            "top_subscribers_title": top_subscribers_title,
            "top_subscribers_description": top_subscribers_description,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "sort_by": sort_by,
        },
    )


@router.get("/subscribers/lifecycle/export")
def subscriber_lifecycle_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export subscriber lifecycle data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    recent_churns = sr.lifecycle_recent_churns(db, limit=100)

    export_data = [
        {
            "Name": c["name"],
            "Subscriber #": c["subscriber_number"],
            "Plan": c["plan"],
            "Region": c["region"],
            "Activated": c["activated_at"],
            "Terminated": c["terminated_at"],
            "Tenure (days)": c["tenure_days"],
        }
        for c in recent_churns
    ]
    filename = f"subscriber_lifecycle_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Churned Subscribers Report
# =============================================================================


@router.get("/subscribers/churned", response_class=HTMLResponse)
def churned_subscribers(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(0, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    behavioral_days: int = Query(60, ge=30, le=180),
):
    """Standard churned subscribers dashboard with KPIs, trend, and churn detail tables."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    kpis = sr.churned_subscribers_kpis(db, start_dt, end_dt, behavioral_days=behavioral_days)
    churn_trend = sr.churned_subscribers_trend(db, start_dt, end_dt, behavioral_days=behavioral_days)
    churn_reasons = sr.churned_subscribers_reason_breakdown(db, start_dt, end_dt)
    churned_rows = sr.churned_subscribers_rows(db, start_dt, end_dt, limit=100, behavioral_days=behavioral_days)
    churned_count = kpis.get("churned_count")
    if churned_count is None:
        churned_count = kpis.get("terminated_in_period")
    if churned_count is None:
        churned_count = len(churned_rows)
    kpis["churned_count"] = int(churned_count or 0)

    tracked_count = int(kpis.get("retention_tracked_count") or kpis.get("total_active_subscribers_start") or 0)
    kpis["churn_rate"] = round((kpis["churned_count"] / tracked_count) * 100, 1) if tracked_count > 0 else 0.0

    return templates.TemplateResponse(
        "admin/reports/churned_subscribers.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-churned",
            "active_menu": "reports",
            "kpis": kpis,
            "churn_trend": churn_trend,
            "churn_reasons": churn_reasons,
            "churned_rows": churned_rows,
            "distinct_churned_subscribers_count": kpis["churned_count"],
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "behavioral_days": behavioral_days,
        },
    )


@router.get("/subscribers/churned/export")
def churned_subscribers_export(
    db: Session = Depends(get_db),
    days: int = Query(0, ge=0, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    behavioral_days: int = Query(60, ge=30, le=180),
):
    """Export churned subscriber rows as CSV for selected range."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _resolve_lifecycle_date_range(db, days, start_date, end_date)
    churned_rows = sr.churned_subscribers_rows(db, start_dt, end_dt, limit=1000, behavioral_days=behavioral_days)

    export_data = [
        {
            "Name": row["name"],
            "Subscriber #": row["subscriber_number"],
            "Plan": row["plan"],
            "Region": row["region"],
            "Activated": row["activated_at"],
            "Terminated": row["terminated_at"],
            "Tenure (days)": row["tenure_days"],
        }
        for row in churned_rows
    ]
    filename = f"subscriber_churned_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Billing Risk Report
# =============================================================================


@router.get(
    "/subscribers/billing-risk",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission(*REPORTS_BILLING_RISK_READ_PERMISSIONS))],
)
def subscriber_billing_risk(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    overdue_invoice_days: int = Query(30, ge=1, le=180),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    bucket: str | None = Query("all"),
    search: str | None = Query(None),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    mrr_sort: str | None = Query(None),
):
    """Billing risk dashboard for blocked, overdue, and otherwise at-risk subscribers."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)

    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    mrr_sort_value = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (mrr_sort_value if mrr_sort_value is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments, query_segment or segment
    )

    churn_rows = sr.get_churn_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        source="selfcare_live",
        limit=500,
        enrich_visible_rows=False,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    normalized_search = (search if isinstance(search, str) else "").strip().lower()
    if normalized_search:
        churn_rows = [
            row
            for row in churn_rows
            if normalized_search
            in " ".join(
                [
                    str(row.get("name") or ""),
                    str(row.get("subscriber_id") or ""),
                    str(row.get("phone") or ""),
                    str(row.get("city") or ""),
                    str(row.get("street") or ""),
                    str(row.get("area") or ""),
                    str(row.get("plan") or ""),
                ]
            ).lower()
        ]
    normalized_bucket = (bucket if isinstance(bucket, str) else "all").strip().lower()
    if normalized_bucket != "all":

        def _matches_bucket(row: dict) -> bool:
            value = row.get("blocked_for_days")
            if value is None:
                return False
            days = int(value)
            if normalized_bucket == "0-7":
                return 0 <= days <= 7
            if normalized_bucket == "8-30":
                return 8 <= days <= 30
            if normalized_bucket == "31-60":
                return 31 <= days <= 60
            if normalized_bucket == "61+":
                return days >= 61
            return True

        churn_rows = [row for row in churn_rows if _matches_bucket(row)]
    if normalized_mrr_sort == "desc":
        churn_rows.sort(key=lambda row: (-float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    elif normalized_mrr_sort == "asc":
        churn_rows.sort(key=lambda row: (float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    overdue_invoices = sr.get_overdue_invoices_table(
        db,
        min_days_past_due=overdue_invoice_days,
        limit=250,
    )
    kpis = sr.churn_risk_summary(churn_rows, overdue_invoices)
    segment_breakdown = sr.churn_risk_segment_breakdown(churn_rows)
    aging_buckets = sr.churn_risk_aging_buckets(churn_rows, due_soon_days=due_soon_days)

    export_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    retention_tracker_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    refresh_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "segment": segment or "",
            "segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    segment_all_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
        },
        doseq=True,
    )
    segment_due_soon_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
            "segment": "overdue",
        },
        doseq=True,
    )
    segment_suspended_query = urlencode(
        {
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": str(high_balance_only).lower(),
            "days_past_due": query_days_past_due or days_past_due or "",
            "bucket": bucket or "all",
            "search": search or "",
            "mrr_sort": normalized_mrr_sort,
            "segment": "suspended",
        },
        doseq=True,
    )
    return templates.TemplateResponse(
        "admin/reports/subscriber_billing_risk.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-billing-risk",
            "active_menu": "reports",
            "kpis": kpis,
            "segment_breakdown": segment_breakdown,
            "aging_buckets": aging_buckets,
            "churn_rows": churn_rows,
            "overdue_invoices": overdue_invoices,
            "due_soon_days": due_soon_days,
            "overdue_invoice_days": overdue_invoice_days,
            "high_balance_only": high_balance_only,
            "selected_segments": selected_segments,
            "days_past_due": query_days_past_due or days_past_due,
            "export_query": export_query,
            "retention_tracker_query": retention_tracker_query,
            "refresh_query": refresh_query,
            "segment_all_query": segment_all_query,
            "segment_due_soon_query": segment_due_soon_query,
            "segment_suspended_query": segment_suspended_query,
            "last_synced_at": _latest_subscriber_sync_at(db),
            "billing_risk_cache": {"row_count": len(churn_rows)},
            "csrf_token": get_csrf_token(request),
            "refresh_started": request.query_params.get("refresh_started") == "1",
            "refresh_error": request.query_params.get("refresh_error"),
            "live_bucket": bucket or "all",
            "live_search": search or "",
            "live_mrr_sort": normalized_mrr_sort,
            "enterprise_mrr_threshold": 70000,
        },
    )


@router.post("/subscribers/billing-risk/refresh")
def subscriber_billing_risk_refresh(
    request: Request,
    next_url: str = Form("/admin/reports/subscribers/billing-risk"),
    _permission: dict = Depends(require_any_permission(*REPORTS_BILLING_RISK_WRITE_PERMISSIONS)),
):
    if not next_url.startswith("/admin/reports/subscribers/billing-risk"):
        next_url = "/admin/reports/subscribers/billing-risk"

    try:
        sync_subscribers_from_selfcare.delay()
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_started", "1"), status_code=303)
    except Exception:
        logger.exception("Failed to enqueue Selfcare subscriber sync")
        return RedirectResponse(url=_append_query_flag(next_url, "refresh_error", "queue_unavailable"), status_code=303)


@router.get(
    "/subscribers/billing-risk/export",
    dependencies=[Depends(require_any_permission(*REPORTS_BILLING_RISK_READ_PERMISSIONS))],
)
def subscriber_billing_risk_export(
    request: Request,
    db: Session = Depends(get_db),
    due_soon_days: int = Query(7, ge=1, le=30),
    high_balance_only: bool = Query(False),
    segment: str | None = Query(None),
    segments: list[str] = Query(default=[]),
    days_past_due: str | None = Query(None),
    enterprise_only: bool = Query(False),
    customer_segment: str | None = Query(None),
    mrr_sort: str | None = Query(None),
):
    """Export billing risk rows as CSV."""
    from app.services import subscriber_reports as sr

    query_segments = request.query_params.getlist("segments")
    query_segment = request.query_params.get("segment")
    query_days_past_due = request.query_params.get("days_past_due")
    mrr_sort_value = request.query_params.get("mrr_sort")
    normalized_mrr_sort = (
        (mrr_sort_value if mrr_sort_value is not None else (mrr_sort if isinstance(mrr_sort, str) else ""))
        .strip()
        .lower()
    )
    selected_segments = _normalize_segment_filters(
        query_segments if query_segments else segments, query_segment or segment
    )

    churn_rows = sr.get_churn_table(
        db,
        due_soon_days=due_soon_days,
        high_balance_only=high_balance_only,
        segment=segment,
        segments=selected_segments,
        days_past_due=query_days_past_due or days_past_due,
        source="selfcare_live",
        limit=2000,
    )
    selected_labels = _segment_labels(selected_segments)
    if selected_labels:
        churn_rows = [row for row in churn_rows if str(row.get("risk_segment") or "") in selected_labels]
    if normalized_mrr_sort == "desc":
        churn_rows.sort(key=lambda row: (-float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    elif normalized_mrr_sort == "asc":
        churn_rows.sort(key=lambda row: (float(row.get("mrr_total") or 0), str(row.get("name") or "").casefold()))
    export_data = [
        {
            "Name": row["name"],
            "Email": row["email"],
            "Phone": row.get("phone", ""),
            "Subscriber Status": row["subscriber_status"],
            "Risk Segment": row["risk_segment"],
            "Next Bill Date": row["next_bill_date"],
            "Days To Due": row["days_to_due"],
            "Days Past Due": row.get("days_past_due", ""),
            "Balance": row["balance"],
            "Billing Cycle": row["billing_cycle"],
            "Last Transaction Date": row["last_transaction_date"],
            "Expires In": row["expires_in"],
            "Invoiced Until": row["invoiced_until"],
            "Days Since Last Payment": row.get("days_since_last_payment", ""),
            "Total Paid": row["total_paid"],
            "High Balance Risk": "Yes" if row["is_high_balance_risk"] else "No",
        }
        for row in churn_rows
    ]
    filename = f"subscriber_billing_risk_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Service Quality Report
# =============================================================================


@router.get("/subscribers/service-quality", response_class=HTMLResponse)
def subscriber_service_quality(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Subscriber service quality report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.service_quality_kpis(db, start_dt, end_dt)
    tickets_by_type = sr.service_quality_tickets_by_type(db, start_dt, end_dt)
    wo_by_type = sr.service_quality_wo_by_type(db, start_dt, end_dt)
    weekly_trend = sr.service_quality_weekly_trend(db, start_dt, end_dt)
    high_maintenance = sr.service_quality_high_maintenance(db, start_dt, end_dt)
    regional_quality = sr.service_quality_regional(db, start_dt, end_dt)

    return templates.TemplateResponse(
        "admin/reports/subscriber_service_quality.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-service-quality",
            "active_menu": "reports",
            "kpis": kpis,
            "tickets_by_type": tickets_by_type,
            "wo_by_type": wo_by_type,
            "weekly_trend": weekly_trend,
            "high_maintenance": high_maintenance,
            "regional_quality": regional_quality,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/subscribers/service-quality/export")
def subscriber_service_quality_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export service quality data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    high_maintenance = sr.service_quality_high_maintenance(db, start_dt, end_dt, limit=100)

    export_data = [
        {
            "Name": h["name"],
            "Subscriber #": h["subscriber_number"],
            "Region": h["region"],
            "Plan": h["plan"],
            "Tickets": h["tickets"],
            "Work Orders": h["work_orders"],
            "Projects": h["projects"],
            "Total Issues": h["total"],
        }
        for h in high_maintenance
    ]
    filename = f"service_quality_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Subscriber Revenue & Pipeline Report
# =============================================================================


@router.get("/subscribers/revenue", response_class=HTMLResponse)
def subscriber_revenue(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Subscriber revenue and pipeline report."""
    from app.services import subscriber_reports as sr

    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    kpis = sr.revenue_kpis(db, start_dt, end_dt)
    monthly_trend = sr.revenue_monthly_trend(db)
    payment_status = sr.revenue_payment_status(db, start_dt, end_dt)
    order_status = sr.revenue_order_status(db, start_dt, end_dt)
    top_subscribers = sr.revenue_top_subscribers(db, start_dt, end_dt)
    outstanding = sr.revenue_outstanding_balances(db)

    return templates.TemplateResponse(
        "admin/reports/subscriber_revenue.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "subscriber-revenue",
            "active_menu": "reports",
            "kpis": kpis,
            "monthly_trend": monthly_trend,
            "payment_status": payment_status,
            "order_status": order_status,
            "top_subscribers": top_subscribers,
            "outstanding": outstanding,
            "days": days,
            "start_date": start_date or "",
            "end_date": end_date or "",
        },
    )


@router.get("/subscribers/revenue/export")
def subscriber_revenue_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=365),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export revenue data as CSV."""
    from app.services import subscriber_reports as sr

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    top_subs = sr.revenue_top_subscribers(db, start_dt, end_dt, limit=100)

    export_data = [
        {
            "Name": s["name"],
            "Email": s["email"],
            "Total Revenue": s["total_revenue"],
            "Order Count": s["order_count"],
            "Avg Order Value": s["avg_value"],
            "Latest Order": s["latest_order"],
            "Status": s["status"],
        }
        for s in top_subs
    ]
    filename = f"subscriber_revenue_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Revenue & Service Report - Downtime & Credit Notes
# =============================================================================


@router.get(
    "/revenue-service",
    response_class=HTMLResponse,
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_report(
    request: Request,
    db: Session = Depends(get_db),
):
    """Revenue and service report for downtime extension credit exposure."""
    from app.services import revenue_service_report as revenue_service_report_service

    user = get_current_user(request)
    report_error = None
    summary = {
        "total_downtime_hours": 0,
        "incident_count": 0,
        "affected_customers_count": 0,
        "total_credit_exposure": 0,
        "average_uptime_percent": 100,
        "root_cause_totals": {},
        "top_affected_customers": [],
    }
    downtime_log: list[dict[str, Any]] = []
    try:
        report = revenue_service_report_service.build_report(db)
        summary = report["summary"]
        downtime_log = report["downtime_log"]
    except revenue_service_report_service.SelfcareReportError as exc:
        report_error = str(exc)
    except Exception:
        logger.exception("revenue_service_report_build_failed")
        report_error = (
            "The revenue & service report could not be generated right now "
            "(an upstream data source may be unavailable). Please try again later."
        )

    return templates.TemplateResponse(
        "admin/reports/revenue_service_report.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "revenue-service-report",
            "active_menu": "reports",
            "summary": summary,
            "downtime_log": downtime_log,
            "report_error": report_error,
        },
    )


@router.get(
    "/revenue-service/api/summary",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_summary(
    year: int | None = Query(None, ge=2020, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Return metric card data for the revenue and service report."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.build_summary(db, year=year, month=month))
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/log",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_log(
    year: int | None = Query(None, ge=2020, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Return all downtime incidents derived from extension transactions."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.build_downtime_log(db, year=year, month=month))
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/months",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_month_options(db: Session = Depends(get_db)):
    """Return available month filter options for the revenue and service report."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.build_month_options(db))
    except Exception as exc:
        logger.exception("revenue_service_month_options_failed")
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/compensation",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_compensation(
    search: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
):
    """Look up the latest extension compensation for a Splynx customer."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.lookup_compensation(db, search))
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/payment-classification",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_payment_classification(
    search: str = Query("", min_length=0),
    classification: str = Query("all"),
    year: int | None = Query(None, ge=2020, le=2100),
    month: int | None = Query(None, ge=1, le=12),
    db: Session = Depends(get_db),
):
    """Return Splynx customer payment behaviour classification."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(
            revenue_service_report_service.build_payment_classification(
                db,
                search=search,
                classification=classification,
                year=year,
                month=month,
            )
        )
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/uptime/search",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_uptime_search(
    q: str = Query("", min_length=0),
    db: Session = Depends(get_db),
):
    """Return customer matches for uptime analytics lookup."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse({"rows": revenue_service_report_service.search_uptime_customers(db, q)})
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/uptime/{customer_id}",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_uptime_profile(
    customer_id: str,
    month: str = Query(""),
    db: Session = Depends(get_db),
):
    """Return full customer uptime calculation for one month."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.build_customer_uptime_profile(db, customer_id, month))
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/uptime/{customer_id}/sessions",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_uptime_sessions(
    customer_id: str,
    month: str = Query(""),
    db: Session = Depends(get_db),
):
    """Return raw session records for customer uptime analytics."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.build_customer_uptime_sessions(db, customer_id, month))
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/uptime/{customer_id}/trend",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_uptime_trend(
    customer_id: str,
    db: Session = Depends(get_db),
):
    """Return last six months uptime trend for a customer."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.build_customer_uptime_trend(db, customer_id))
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


@router.get(
    "/revenue-service/api/uptime/{customer_id}/compensation",
    dependencies=[Depends(require_any_permission(*REPORTS_REVENUE_SERVICE_READ_PERMISSIONS))],
)
def revenue_service_uptime_compensation(
    customer_id: str,
    month: str = Query(""),
    db: Session = Depends(get_db),
):
    """Return compensation summary from customer uptime analytics."""
    from app.services import revenue_service_report as revenue_service_report_service

    try:
        return JSONResponse(revenue_service_report_service.build_customer_uptime_compensation(db, customer_id, month))
    except revenue_service_report_service.SelfcareReportError as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# =============================================================================
# Technician Performance Report
# =============================================================================


def _get_technician_stats(
    db: Session,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[dict[str, object]], int, dict[str, int], list[WorkOrder]]:
    """Get technician performance stats for a date range."""
    # Active technician profiles should appear even when they have no jobs in range.
    active_technician_person_ids = {
        row
        for row in db.scalars(select(TechnicianProfile.person_id).where(TechnicianProfile.is_active.is_(True))).all()
        if row is not None
    }

    total_rows = db.execute(
        select(WorkOrder.assigned_to_person_id, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.assigned_to_person_id.isnot(None),
            WorkOrder.created_at >= start_date,
            WorkOrder.created_at <= end_date,
        )
        .group_by(WorkOrder.assigned_to_person_id)
    ).all()
    completed_rows = db.execute(
        select(WorkOrder.assigned_to_person_id, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.assigned_to_person_id.isnot(None),
            WorkOrder.status == WorkOrderStatus.completed,
            WorkOrder.completed_at >= start_date,
            WorkOrder.completed_at <= end_date,
        )
        .group_by(WorkOrder.assigned_to_person_id)
    ).all()

    total_by_person = {person_id: count for person_id, count in total_rows if person_id is not None}
    completed_by_person = {person_id: count for person_id, count in completed_rows if person_id is not None}

    person_ids = set(active_technician_person_ids) | set(total_by_person.keys()) | set(completed_by_person.keys())
    people_by_id: dict = {}
    if person_ids:
        people = db.scalars(select(Person).where(Person.id.in_(person_ids), Person.is_active.is_(True))).all()
        people_by_id = {person.id: person for person in people}

    def _person_name(person: Person | None) -> str:
        if not person:
            return "Unknown"
        if person.display_name:
            return person.display_name
        return f"{person.first_name or ''} {person.last_name or ''}".strip() or "Unknown"

    technician_stats = []
    for person_id in person_ids:
        total_assigned = int(total_by_person.get(person_id, 0))
        completed = int(completed_by_person.get(person_id, 0))
        completion_rate = (completed / total_assigned * 100) if total_assigned > 0 else 0
        rating = min(5, max(1, int(completion_rate / 20))) if total_assigned > 0 else 3
        technician_stats.append(
            {
                "name": _person_name(people_by_id.get(person_id)),
                "total_jobs": total_assigned,
                "completed_jobs": completed,
                "avg_hours": 2.5 if completed > 0 else 0,  # Placeholder: use time tracking when available
                "rating": rating,
                "completion_rate": round(completion_rate, 1),
            }
        )

    technician_stats.sort(
        key=lambda x: (
            -(x["completed_jobs"] if isinstance(x["completed_jobs"], int) else 0),
            -(x["total_jobs"] if isinstance(x["total_jobs"], int) else 0),
            str(x.get("name", "")).lower(),
        )
    )
    total_jobs_completed = sum(completed_by_person.values())

    # Job type breakdown
    type_rows = db.execute(
        select(WorkOrder.work_type, func.count(WorkOrder.id))
        .where(
            WorkOrder.is_active.is_(True),
            WorkOrder.created_at >= start_date,
            WorkOrder.created_at <= end_date,
        )
        .group_by(WorkOrder.work_type)
    ).all()
    job_type_breakdown: dict[str, int] = {
        (work_type.value if work_type else "other"): count for work_type, count in type_rows
    }

    # Recent completions
    recent_completions = (
        db.scalars(
            select(WorkOrder)
            .options(joinedload(WorkOrder.assigned_to))
            .where(
                WorkOrder.is_active.is_(True),
                WorkOrder.status == WorkOrderStatus.completed,
                WorkOrder.completed_at >= start_date,
                WorkOrder.completed_at <= end_date,
            )
            .order_by(WorkOrder.completed_at.desc())
            .limit(5)
        )
        .unique()
        .all()
    )

    return technician_stats, total_jobs_completed, job_type_breakdown, list(recent_completions)


@router.get("/technician", response_class=HTMLResponse)
def technician_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Technician performance report."""
    user = get_current_user(request)

    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    technician_stats, total_jobs_completed, job_type_breakdown, recent_completions = _get_technician_stats(
        db, start_dt, end_dt
    )

    # Summary stats
    avg_completion_hours = 2.5  # Placeholder
    first_visit_rate = 85.0  # Placeholder

    return templates.TemplateResponse(
        "admin/reports/technician.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "total_technicians": len(technician_stats),
            "jobs_completed": total_jobs_completed,
            "avg_completion_hours": avg_completion_hours,
            "first_visit_rate": first_visit_rate,
            "technician_stats": technician_stats,
            "job_type_breakdown": job_type_breakdown,
            "recent_completions": recent_completions,
            "days": days,
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
        },
    )


@router.get("/technician/export")
def technician_report_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export technician performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    technician_stats, _, _, _ = _get_technician_stats(db, start_dt, end_dt)

    # Format for CSV
    export_data = []
    for i, tech in enumerate(technician_stats, 1):
        export_data.append(
            {
                "Rank": i,
                "Technician": tech["name"],
                "Total Jobs": tech["total_jobs"],
                "Completed Jobs": tech["completed_jobs"],
                "Completion Rate (%)": tech["completion_rate"],
                "Avg Hours": tech["avg_hours"],
                "Rating": tech["rating"],
            }
        )

    filename = f"technician_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Project Task People Performance Report
# =============================================================================


def _hours_between(start_at: datetime | None, end_at: datetime | None) -> float | None:
    if not start_at or not end_at:
        return None
    if start_at.tzinfo is None and end_at.tzinfo is not None:
        start_at = start_at.replace(tzinfo=end_at.tzinfo)
    elif start_at.tzinfo is not None and end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=start_at.tzinfo)
    return max((end_at - start_at).total_seconds() / 3600, 0.0)


def _datetime_after(left: datetime | None, right: datetime | None) -> bool:
    if not left or not right:
        return False
    if left.tzinfo is None and right.tzinfo is not None:
        left = left.replace(tzinfo=right.tzinfo)
    elif left.tzinfo is not None and right.tzinfo is None:
        right = right.replace(tzinfo=left.tzinfo)
    return left > right


def _project_task_person_name(person: Person | None) -> str:
    if not person:
        return "Unknown"
    if person.display_name:
        return person.display_name
    return f"{person.first_name or ''} {person.last_name or ''}".strip() or "Unknown"


def _task_assignee_ids(task: ProjectTask) -> list[UUID]:
    assignee_ids = [assignee.person_id for assignee in task.assignees if assignee.person_id]
    if assignee_ids:
        return list(dict.fromkeys(assignee_ids))
    if task.assigned_to_person_id:
        return [task.assigned_to_person_id]
    return []


def _new_project_task_person_accumulator(
    person_id: UUID,
    people_by_id: dict[UUID, Person],
) -> _ProjectTaskPersonAccumulator:
    return {
        "id": str(person_id),
        "name": _project_task_person_name(people_by_id.get(person_id)),
        "assigned_tasks": 0,
        "completed_tasks": 0,
        "open_tasks": 0,
        "blocked_tasks": 0,
        "overdue_tasks": 0,
        "on_time_tasks": 0,
        "cycle_hours_total": 0.0,
        "cycle_hours_count": 0,
        "effort_accuracy_total": 0.0,
        "effort_accuracy_count": 0,
    }


def _metric_int(row: dict[str, object], key: str) -> int:
    value = row.get(key, 0)
    return int(value) if isinstance(value, int | float | str) else 0


def _metric_float(row: dict[str, object], key: str) -> float:
    value = row.get(key, 0.0)
    return float(value) if isinstance(value, int | float | str) else 0.0


def _project_task_window_clause(start_date: datetime, end_date: datetime):
    """Select tasks that were active at any point in the requested window."""
    return and_(
        ProjectTask.created_at <= end_date,
        or_(ProjectTask.completed_at.is_(None), ProjectTask.completed_at >= start_date),
    )


def _get_project_task_people_performance(
    db: Session,
    start_date: datetime,
    end_date: datetime,
) -> tuple[list[dict[str, object]], dict[str, object], dict[str, int], list[ProjectTask]]:
    """Aggregate people performance from project task assignment activity."""
    tasks = (
        db.scalars(
            select(ProjectTask)
            .options(
                selectinload(ProjectTask.assignees),
                selectinload(ProjectTask.project),
            )
            .where(
                ProjectTask.is_active.is_(True),
                _project_task_window_clause(start_date, end_date),
            )
        )
        .unique()
        .all()
    )

    person_ids = {person_id for task in tasks for person_id in _task_assignee_ids(task)}
    if not person_ids:
        person_ids.update(
            row
            for row in db.scalars(
                select(ProjectTask.assigned_to_person_id)
                .where(
                    ProjectTask.is_active.is_(True),
                    ProjectTask.assigned_to_person_id.isnot(None),
                    ProjectTask.completed_at >= start_date,
                    ProjectTask.completed_at <= end_date,
                )
                .distinct()
            ).all()
            if row is not None
        )
        person_ids.update(
            row
            for row in db.scalars(
                select(ProjectTaskAssignee.person_id)
                .join(ProjectTask, ProjectTask.id == ProjectTaskAssignee.task_id)
                .where(
                    ProjectTask.is_active.is_(True),
                    ProjectTask.completed_at >= start_date,
                    ProjectTask.completed_at <= end_date,
                )
                .distinct()
            ).all()
            if row is not None
        )

    people_by_id: dict[UUID, Person] = {}
    if person_ids:
        people = db.scalars(select(Person).where(Person.id.in_(person_ids), Person.is_active.is_(True))).all()
        people_by_id = {person.id: person for person in people}

    stats_by_person: dict[UUID, _ProjectTaskPersonAccumulator] = {
        person_id: _new_project_task_person_accumulator(person_id, people_by_id) for person_id in person_ids
    }

    project_type_breakdown: dict[str, int] = {}
    now = datetime.now(UTC)

    for task in tasks:
        project_type = task.project.project_type.value if task.project and task.project.project_type else "unspecified"
        project_type_breakdown[project_type] = project_type_breakdown.get(project_type, 0) + 1

        assignee_ids = _task_assignee_ids(task)
        if not assignee_ids:
            continue

        is_done = bool(
            task.status == TaskStatus.done
            and task.completed_at
            and not _datetime_after(start_date, task.completed_at)
            and not _datetime_after(task.completed_at, end_date)
        )
        is_blocked = task.status == TaskStatus.blocked
        completed_or_window_end = task.completed_at or now
        if _datetime_after(completed_or_window_end, end_date):
            completed_or_window_end = end_date
        is_overdue = bool(task.due_at and _datetime_after(completed_or_window_end, task.due_at) and not is_done)
        is_on_time = bool(
            is_done and task.due_at and task.completed_at and not _datetime_after(task.completed_at, task.due_at)
        )
        cycle_hours = _hours_between(task.start_at or task.created_at, task.completed_at) if is_done else None
        effort_accuracy = None
        if cycle_hours is not None and task.effort_hours and task.effort_hours > 0:
            effort_accuracy = max(0.0, 1 - abs(cycle_hours - float(task.effort_hours)) / float(task.effort_hours)) * 100

        for person_id in assignee_ids:
            row = stats_by_person.setdefault(
                person_id,
                _new_project_task_person_accumulator(person_id, people_by_id),
            )
            row["assigned_tasks"] += 1
            if is_done:
                row["completed_tasks"] += 1
            else:
                row["open_tasks"] += 1
            if is_blocked:
                row["blocked_tasks"] += 1
            if is_overdue:
                row["overdue_tasks"] += 1
            if is_on_time:
                row["on_time_tasks"] += 1
            if cycle_hours is not None:
                row["cycle_hours_total"] += cycle_hours
                row["cycle_hours_count"] += 1
            if effort_accuracy is not None:
                row["effort_accuracy_total"] += effort_accuracy
                row["effort_accuracy_count"] += 1

    rows: list[dict[str, object]] = []
    for row in stats_by_person.values():
        assigned = int(row["assigned_tasks"])
        completed = int(row["completed_tasks"])
        blocked = int(row["blocked_tasks"])
        overdue = int(row["overdue_tasks"])
        completion_rate = (completed / assigned * 100) if assigned else 0.0
        on_time_rate = (int(row["on_time_tasks"]) / completed * 100) if completed else 0.0
        blocked_rate = (blocked / assigned * 100) if assigned else 0.0
        overdue_rate = (overdue / assigned * 100) if assigned else 0.0
        avg_cycle_hours = (
            float(row["cycle_hours_total"]) / int(row["cycle_hours_count"]) if int(row["cycle_hours_count"]) else 0.0
        )
        effort_accuracy = (
            float(row["effort_accuracy_total"]) / int(row["effort_accuracy_count"])
            if int(row["effort_accuracy_count"])
            else 0.0
        )
        health_score = max(0.0, 100.0 - blocked_rate - overdue_rate)
        performance_score = (
            (completion_rate * 0.4) + (on_time_rate * 0.35) + (health_score * 0.15) + (effort_accuracy * 0.10)
        )
        rows.append(
            {
                "id": row["id"],
                "name": row["name"],
                "assigned_tasks": assigned,
                "completed_tasks": completed,
                "open_tasks": int(row["open_tasks"]),
                "blocked_tasks": blocked,
                "overdue_tasks": overdue,
                "completion_rate": round(completion_rate, 1),
                "on_time_rate": round(on_time_rate, 1),
                "avg_cycle_hours": round(avg_cycle_hours, 1),
                "effort_accuracy": round(effort_accuracy, 1),
                "performance_score": round(performance_score, 1),
                "rating": min(5, max(1, round(performance_score / 20))) if assigned else 3,
            }
        )

    rows.sort(
        key=lambda item: (
            -_metric_float(item, "performance_score"),
            -_metric_int(item, "completed_tasks"),
            str(item.get("name", "")).lower(),
        )
    )

    completed_rows = [row for row in rows if _metric_int(row, "completed_tasks") > 0]
    total_assigned = sum(_metric_int(row, "assigned_tasks") for row in rows)
    total_completed = sum(_metric_int(row, "completed_tasks") for row in rows)
    total_overdue = sum(_metric_int(row, "overdue_tasks") for row in rows)
    weighted_completion = (total_completed / total_assigned * 100) if total_assigned else 0.0
    weighted_on_time = (
        sum(_metric_int(row, "completed_tasks") * _metric_float(row, "on_time_rate") for row in rows) / total_completed
        if total_completed
        else 0.0
    )
    avg_cycle_hours = (
        sum(_metric_int(row, "completed_tasks") * _metric_float(row, "avg_cycle_hours") for row in completed_rows)
        / total_completed
        if total_completed
        else 0.0
    )
    summary: dict[str, object] = {
        "people_count": len(rows),
        "tasks_assigned": total_assigned,
        "tasks_completed": total_completed,
        "tasks_overdue": total_overdue,
        "completion_rate": round(weighted_completion, 1),
        "on_time_rate": round(weighted_on_time, 1),
        "avg_cycle_hours": round(avg_cycle_hours, 1),
    }

    recent_completions = (
        db.scalars(
            select(ProjectTask)
            .options(
                joinedload(ProjectTask.assigned_to),
                selectinload(ProjectTask.assignees).selectinload(ProjectTaskAssignee.person),
                selectinload(ProjectTask.project),
            )
            .where(
                ProjectTask.is_active.is_(True),
                ProjectTask.status == TaskStatus.done,
                ProjectTask.completed_at >= start_date,
                ProjectTask.completed_at <= end_date,
            )
            .order_by(ProjectTask.completed_at.desc())
            .limit(5)
        )
        .unique()
        .all()
    )

    return rows, summary, project_type_breakdown, list(recent_completions)


@router.get("/project-task-performance", response_class=HTMLResponse)
def project_task_people_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """People performance report based on assigned project tasks."""
    user = get_current_user(request)
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    people_stats, summary, project_type_breakdown, recent_completions = _get_project_task_people_performance(
        db, start_dt, end_dt
    )

    return templates.TemplateResponse(
        "admin/reports/project_task_performance.html",
        {
            "request": request,
            "user": user,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "project-task-performance",
            "active_menu": "reports",
            "people_stats": people_stats,
            "summary": summary,
            "project_type_breakdown": project_type_breakdown,
            "recent_completions": recent_completions,
            "days": days,
            "custom_range": bool(start_date and end_date),
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": end_dt.strftime("%Y-%m-%d"),
        },
    )


@router.get("/project-task-performance/export")
def project_task_people_performance_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
):
    """Export project task people performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)
    people_stats, _, _, _ = _get_project_task_people_performance(db, start_dt, end_dt)

    export_data = []
    for index, person in enumerate(people_stats, 1):
        export_data.append(
            {
                "Rank": index,
                "Person": person["name"],
                "Assigned Tasks": person["assigned_tasks"],
                "Completed Tasks": person["completed_tasks"],
                "Open Tasks": person["open_tasks"],
                "Blocked Tasks": person["blocked_tasks"],
                "Overdue Tasks": person["overdue_tasks"],
                "Completion Rate (%)": person["completion_rate"],
                "On-Time Rate (%)": person["on_time_rate"],
                "Avg Cycle Hours": person["avg_cycle_hours"],
                "Effort Accuracy (%)": person["effort_accuracy"],
                "Performance Score": person["performance_score"],
            }
        )

    filename = f"project_task_people_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# CRM Performance Report
# =============================================================================


@router.get("/crm-performance", response_class=HTMLResponse)
def crm_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    agent_id: str | None = Query(None),
    team_id: str | None = Query(None),
    channel_type: str | None = Query(None),
):
    """CRM agent/team performance report."""
    from app.models.crm.enums import ChannelType

    user = get_current_user(request)
    now = datetime.now(UTC)
    start_date = now - timedelta(days=days)

    # Get inbox KPIs
    inbox_stats = crm_reports_service.inbox_kpis(
        db=db,
        start_at=start_date,
        end_at=now,
        channel_type=channel_type,
        agent_id=agent_id,
        team_id=team_id,
    )

    # Get per-agent performance metrics
    agent_stats = crm_reports_service.agent_performance_metrics(
        db=db,
        start_at=start_date,
        end_at=now,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Get conversation trend data
    trend_data = crm_reports_service.conversation_trend(
        db=db,
        start_at=start_date,
        end_at=now,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Summary stats
    total_conversations = sum(agent["total_conversations"] for agent in agent_stats)
    resolved_conversations = sum(agent["resolved_conversations"] for agent in agent_stats)
    resolution_rate = resolved_conversations / total_conversations * 100 if total_conversations > 0 else 0

    # Weighted average FRT across agents (weight by total conversations with valid FRT)
    total_team_response_minutes = sum(
        (a["avg_first_response_minutes"] or 0) * a["total_conversations"]
        for a in agent_stats
        if a["avg_first_response_minutes"] is not None
    )
    total_convos_with_frt = sum(
        a["total_conversations"] for a in agent_stats if a["avg_first_response_minutes"] is not None
    )
    avg_frt = total_team_response_minutes / total_convos_with_frt if total_convos_with_frt > 0 else None

    # Weighted average resolution time across agents (weight by resolved conversations)
    total_resolution_minutes = sum(
        (a["avg_resolution_minutes"] or 0) * a["resolved_conversations"]
        for a in agent_stats
        if a["avg_resolution_minutes"] is not None
    )
    avg_resolution_time = total_resolution_minutes / resolved_conversations if resolved_conversations > 0 else None

    # Get teams and agents for filter dropdowns
    teams = crm_team_service.Teams.list(
        db=db,
        is_active=True,
        order_by="name",
        order_dir="asc",
        limit=200,
        offset=0,
    )
    agents = crm_team_service.Agents.list(
        db=db,
        person_id=None,
        is_active=True,
        order_by="created_at",
        order_dir="desc",
        limit=200,
        offset=0,
    )
    agent_labels = crm_team_service.get_agent_labels(db, agents)

    # Channel type breakdown (ensure key channels appear even with zero data)
    channel_breakdown = inbox_stats.get("messages", {}).get("by_channel", {})
    channel_labels: dict[str, str] = {}
    email_inbox_breakdown = inbox_stats.get("messages", {}).get("by_email_inbox", {}) or {}

    if email_inbox_breakdown:
        channel_breakdown.pop(str(ChannelType.email), None)
        for inbox_id, data in email_inbox_breakdown.items():
            inbox_key = f"email:{inbox_id}"
            channel_breakdown[inbox_key] = data.get("count", 0)
            inbox_label = data.get("label") or "Unknown Inbox"
            channel_labels[inbox_key] = f"Email - {inbox_label}"

    for channel in (ChannelType.whatsapp, ChannelType.facebook_messenger, ChannelType.instagram_dm):
        channel_key = str(channel)
        if channel_key not in channel_breakdown:
            channel_breakdown[channel_key] = 0

    return templates.TemplateResponse(
        "admin/reports/crm_performance.html",
        {
            "request": request,
            "user": user,
            "active_page": "crm-performance",
            "active_menu": "reports",
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            # Summary metrics
            "total_conversations": total_conversations,
            "resolved_conversations": resolved_conversations,
            "resolution_rate": resolution_rate,
            "avg_frt_minutes": avg_frt,
            "avg_resolution_minutes": avg_resolution_time,
            "total_messages": inbox_stats.get("messages", {}).get("total", 0),
            "inbound_messages": inbox_stats.get("messages", {}).get("inbound", 0),
            "outbound_messages": inbox_stats.get("messages", {}).get("outbound", 0),
            # Agent breakdown
            "agent_stats": agent_stats,
            # Trend data for charts
            "trend_data": trend_data,
            # Channel breakdown
            "channel_breakdown": channel_breakdown,
            "channel_labels": channel_labels,
            # Filters
            "days": days,
            "selected_agent_id": agent_id,
            "selected_team_id": team_id,
            "selected_channel_type": channel_type,
            # Dropdown options
            "teams": teams,
            "agents": agents,
            "agent_labels": agent_labels,
            "channel_types": [t.value for t in ChannelType],
        },
    )


@router.get("/crm-performance/export")
def crm_performance_report_export(
    db: Session = Depends(get_db),
    days: int = Query(30, ge=7, le=90),
    start_date: str | None = Query(None),
    end_date: str | None = Query(None),
    agent_id: str | None = Query(None),
    team_id: str | None = Query(None),
    channel_type: str | None = Query(None),
):
    """Export CRM performance report as CSV."""
    start_dt, end_dt = _parse_date_range(days, start_date, end_date)

    # Get per-agent performance metrics
    agent_stats = crm_reports_service.agent_performance_metrics(
        db=db,
        start_at=start_dt,
        end_at=end_dt,
        agent_id=agent_id,
        team_id=team_id,
        channel_type=channel_type,
    )

    # Format for CSV
    export_data = []
    for i, agent in enumerate(agent_stats, 1):
        resolution_rate = (
            agent["resolved_conversations"] / agent["total_conversations"] * 100
            if agent["total_conversations"] > 0
            else 0
        )
        export_data.append(
            {
                "Rank": i,
                "Agent": agent["name"],
                "Active Hours": agent.get("active_hours_display") or "",
                "Total Conversations": agent["total_conversations"],
                "Resolved": agent["resolved_conversations"],
                "Resolution Rate (%)": round(resolution_rate, 1),
                "Avg First Response (min)": round(agent["avg_first_response_minutes"], 1)
                if agent["avg_first_response_minutes"]
                else "",
                "Avg Resolution Time (min)": round(agent["avg_resolution_minutes"], 1)
                if agent["avg_resolution_minutes"]
                else "",
            }
        )

    filename = f"crm_performance_{start_dt.strftime('%Y%m%d')}_{end_dt.strftime('%Y%m%d')}.csv"
    return _csv_response(export_data, filename)


# =============================================================================
# Agent Performance Report (Weekly Trends)
# =============================================================================


@router.get("/agent-performance", response_class=HTMLResponse)
def agent_performance_report(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(7, ge=7, le=90),
):
    """Weekly agent performance report with trend comparisons."""
    user = get_current_user(request)
    now = datetime.now(UTC)
    current_start = now - timedelta(days=days)
    previous_start = current_start - timedelta(days=days)
    previous_end = current_start

    current_metrics = crm_reports_service.agent_weekly_performance(
        db,
        start_at=current_start,
        end_at=now,
    )
    previous_metrics = crm_reports_service.agent_weekly_performance(
        db,
        start_at=previous_start,
        end_at=previous_end,
    )

    prev_map = {m["agent_id"]: m for m in previous_metrics}

    all_resolved = [m["resolved_count"] for m in current_metrics]
    team_median_resolved = sorted(all_resolved)[len(all_resolved) // 2] if all_resolved else 0

    for m in current_metrics:
        prev = prev_map.get(m["agent_id"], {})
        m["prev_resolved_count"] = prev.get("resolved_count", 0)
        m["prev_median_response_seconds"] = prev.get("median_response_seconds")
        m["prev_median_resolution_seconds"] = prev.get("median_resolution_seconds")
        m["prev_open_backlog"] = prev.get("open_backlog", 0)
        m["prev_csat_avg"] = prev.get("csat_avg")
        m["prev_sla_breach_count"] = prev.get("sla_breach_count", 0)
        m["below_median"] = m["resolved_count"] < team_median_resolved

    return templates.TemplateResponse(
        "admin/reports/agent_performance.html",
        {
            "request": request,
            "current_user": user,
            "sidebar_stats": get_sidebar_stats(db),
            "active_page": "agent-performance",
            "active_menu": "reports",
            "days": days,
            "agents": current_metrics,
            "team_median_resolved": team_median_resolved,
        },
    )
