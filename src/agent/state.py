"""LangGraph state and strict Pydantic slot / payload schemas.

Pattern: "the LLM proposes, Pydantic disposes". LLM extraction returns loosely-typed
`*Update` objects; every value is then validated field-by-field against the strict
`*Slots` models before it is accepted into conversation state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, ValidationError, field_validator


class Intent(StrEnum):
    POLICY_QA = "POLICY_QA"
    ELIGIBILITY_CHECK = "ELIGIBILITY_CHECK"
    GRIEVANCE_FILE = "GRIEVANCE_FILE"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


FLOW_INTENTS = (Intent.ELIGIBILITY_CHECK, Intent.GRIEVANCE_FILE)


class SchemeName(StrEnum):
    PM_KISAN = "PM_KISAN"
    PM_JAY = "PM_JAY"


class Occupation(StrEnum):
    FARMER = "farmer"  # cultivator (land ownership is captured separately)
    AGRICULTURAL_LABOURER = "agricultural_labourer"
    INFORMAL_URBAN_WORKER = "informal_urban_worker"  # PM-JAY's 11 urban occupational groups
    FRONTLINE_HEALTH_WORKER = "frontline_health_worker"  # ASHA / Anganwadi worker or helper
    GOVERNMENT_EMPLOYEE = "government_employee"
    GOVERNMENT_PENSIONER = "government_pensioner"
    REGISTERED_PROFESSIONAL = "registered_professional"  # doctor, engineer, lawyer, CA, architect
    CONSTITUTIONAL_OR_ELECTED_OFFICE = "constitutional_or_elected_office"
    SALARIED_PRIVATE = "salaried_private"
    BUSINESS_OWNER = "business_owner"
    UNEMPLOYED_OR_HOMEMAKER = "unemployed_or_homemaker"
    OTHER = "other"


class GrievanceCategory(StrEnum):  # cpgrams:6.2
    DELAY_OR_NON_RECEIPT_OF_BENEFIT = "delay_or_non_receipt_of_benefit"
    WRONGFUL_REJECTION = "wrongful_rejection"
    CORRUPTION_OR_BRIBERY = "corruption_or_bribery"
    OFFICIAL_MISCONDUCT = "official_misconduct"
    DENIAL_OF_SERVICE_OR_TREATMENT = "denial_of_service_or_treatment"
    RECORD_OR_DOCUMENT_ERROR = "record_or_document_error"
    OTHER = "other"


class Urgency(StrEnum):  # cpgrams:6.3
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Canonical reference data for validation (the LLM normalises "UP", "Bengal", "दिल्ली").
STATES_AND_UTS = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab",
    "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand",
    "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh",
    "Lakshadweep", "Puducherry",
)  # fmt: skip
_STATE_LOOKUP = {s.lower(): s for s in STATES_AND_UTS}


def _canonical_state(value: str | None) -> str | None:
    if value is None:
        return None
    canonical = _STATE_LOOKUP.get(value.strip().lower())
    if canonical is None:
        raise ValueError(f"'{value}' is not an Indian State or Union Territory")
    return canonical


# --- Strict slot schemas --------------------------------------------------------------
class EligibilitySlots(BaseModel):
    scheme_name: SchemeName | None = None
    state: str | None = None
    occupation: Occupation | None = None
    land_holding_hectares: float | None = Field(default=None, ge=0, le=1000)
    annual_income_inr: int | None = Field(default=None, ge=0, le=1_000_000_000)
    is_income_tax_payer: bool | None = None
    has_aadhaar_linked_bank: bool | None = None
    age_years: int | None = Field(default=None, ge=0, le=120)  # ADR-3: PM-JAY 70+ rule

    _state = field_validator("state")(classmethod(lambda cls, v: _canonical_state(v)))


class GrievanceSlots(BaseModel):
    ministry_or_department: str | None = Field(default=None, min_length=3, max_length=200)
    state: str | None = None
    district: str | None = Field(default=None, min_length=2, max_length=100)
    scheme_reference: str | None = Field(default=None, max_length=200)  # optional
    incident_summary: str | None = Field(default=None, min_length=20, max_length=4000)
    grievance_category: GrievanceCategory | None = None
    urgency: Urgency | None = None

    _state = field_validator("state")(classmethod(lambda cls, v: _canonical_state(v)))


SLOT_MODELS: dict[Intent, type[BaseModel]] = {
    Intent.ELIGIBILITY_CHECK: EligibilitySlots,
    Intent.GRIEVANCE_FILE: GrievanceSlots,
}

REQUIRED_SLOTS: dict[str, list[str]] = {
    SchemeName.PM_KISAN: [
        "scheme_name", "state", "occupation", "land_holding_hectares",
        "is_income_tax_payer", "has_aadhaar_linked_bank",
    ],
    SchemeName.PM_JAY: [
        "scheme_name", "state", "age_years", "occupation", "annual_income_inr",
        "is_income_tax_payer",
    ],
    Intent.GRIEVANCE_FILE: [
        "ministry_or_department", "state", "district", "incident_summary",
        "grievance_category", "urgency",
    ],
}  # fmt: skip


def missing_slots(flow: Intent | str, slots: dict[str, Any]) -> list[str]:
    if flow == Intent.GRIEVANCE_FILE:
        required = REQUIRED_SLOTS[Intent.GRIEVANCE_FILE]
    elif slots.get("scheme_name") is None:
        return ["scheme_name"]
    else:
        required = REQUIRED_SLOTS[slots["scheme_name"]]
    return [name for name in required if slots.get(name) is None]


def merge_slots(
    model: type[BaseModel], current: dict[str, Any], updates: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate each proposed value on its own; accept valid ones, report invalid ones.

    Returns (merged_slots, errors) where errors maps field -> human-readable reason.
    """
    merged = dict(current)
    errors: dict[str, str] = {}
    for name, value in updates.items():
        if value is None or name not in model.model_fields:
            continue
        try:
            validated = model.model_validate({name: value})
        except ValidationError as exc:
            errors[name] = exc.errors()[0]["msg"].removeprefix("Value error, ")
            continue
        merged[name] = validated.model_dump(mode="json")[name]
    return merged, errors


# --- Final payload ----------------------------------------------------------------------
Verdict = Literal["ELIGIBLE", "LIKELY_ELIGIBLE", "NEEDS_VERIFICATION", "NOT_ELIGIBLE"]


class EligibilityVerdict(BaseModel):
    scheme_name: SchemeName
    verdict: Verdict
    reasons: list[str]
    next_steps: list[str] = Field(default_factory=list)
    citations: list[str]


class FinalActionPayload(BaseModel):
    schema_version: str = "1.0"
    payload_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    action_type: Literal["SCHEME_ELIGIBILITY_CHECK", "CPGRAMS_GRIEVANCE"]
    validated_slots: dict[str, Any]
    eligibility_verdict: EligibilityVerdict | None = None
    citations: list[str]
    redacted_transcript_summary: str
    pii_redacted: bool = True
    timestamp_utc: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# --- LangGraph state ----------------------------------------------------------------------
class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # redacted transcript only
    user_message: str  # current redacted user turn
    intent: str  # intent of the current turn
    active_flow: str | None  # ELIGIBILITY_CHECK | GRIEVANCE_FILE while slot-filling
    scheme_filter: str | None  # retrieval metadata filter (PM_KISAN | PM_JAY | CPGRAMS)
    search_query: str  # router-rewritten standalone English query (ADR-6)
    user_language: str  # router-detected language of the user turn
    eligibility_slots: dict[str, Any]
    grievance_slots: dict[str, Any]
    slot_errors: dict[str, str]
    pending_question: str | None
    retrieved: list[dict[str, Any]]
    citations: list[str]
    response: str  # English response for this turn (localised by the pipeline)
    final_payload: dict[str, Any] | None
