"""Deterministic, citation-backed eligibility rules (CLAUDE.md ADR-2).

The LLM never decides eligibility. These rules encode the curated policy documents, and
every reason cites a section id (chunk id) of the policy corpus.
"""

from __future__ import annotations

from src.agent.state import EligibilitySlots, EligibilityVerdict, Occupation, SchemeName

SECC_MONTHLY_INCOME_LIMIT_INR = 10_000  # pm_jay:4.1
PM_JAY_SENIOR_AGE = 70  # pm_jay:3.5


class _Builder:
    def __init__(self) -> None:
        self.reasons: list[str] = []
        self.next_steps: list[str] = []
        self.citations: list[str] = []

    def reason(self, text: str, *cites: str) -> None:
        self.reasons.append(text)
        self.citations.extend(c for c in cites if c not in self.citations)

    def step(self, text: str, *cites: str) -> None:
        self.next_steps.append(text)
        self.citations.extend(c for c in cites if c not in self.citations)


def evaluate(slots: EligibilitySlots) -> EligibilityVerdict:
    if slots.scheme_name == SchemeName.PM_KISAN:
        return _pm_kisan(slots)
    if slots.scheme_name == SchemeName.PM_JAY:
        return _pm_jay(slots)
    raise ValueError("scheme_name is required for an eligibility verdict")


def _pm_kisan(s: EligibilitySlots) -> EligibilityVerdict:
    b = _Builder()
    excluded = False
    needs_check = False

    if s.occupation == Occupation.GOVERNMENT_EMPLOYEE:
        excluded = True
        b.reason(
            "Families with a serving or retired government employee are excluded "
            "(Multi Tasking Staff, Class IV and Group D employees are not excluded).",
            "pm_kisan:4.4",
        )
    if s.occupation == Occupation.REGISTERED_PROFESSIONAL:
        excluded = True
        b.reason(
            "Registered practising professionals (doctors, engineers, lawyers, CAs, architects) are excluded.",
            "pm_kisan:4.7",
        )
    if s.occupation == Occupation.CONSTITUTIONAL_OR_ELECTED_OFFICE:
        excluded = True
        b.reason(
            "Present or former holders of constitutional posts, ministers, MPs, MLAs, "
            "mayors and district panchayat chairpersons are excluded.",
            "pm_kisan:4.2",
            "pm_kisan:4.3",
        )
    if s.occupation == Occupation.GOVERNMENT_PENSIONER:
        needs_check = True
        b.reason(
            "Retired pensioners are excluded if their monthly pension is ₹10,000 or more "
            "(MTS / Class IV / Group D retirees are not excluded).",
            "pm_kisan:4.5",
        )
    if s.is_income_tax_payer:
        excluded = True
        b.reason(
            "A family in which any member paid income tax in the last assessment year is excluded.",
            "pm_kisan:4.6",
        )
    if s.land_holding_hectares is not None and s.land_holding_hectares <= 0:
        excluded = True
        b.reason(
            "PM-KISAN requires cultivable land recorded in the family's name; landless "
            "and tenant cultivators are not covered.",
            "pm_kisan:3.1",
            "pm_kisan:3.4",
        )

    if excluded:
        verdict = "NOT_ELIGIBLE"
    elif needs_check:
        verdict = "NEEDS_VERIFICATION"
    else:
        verdict = "ELIGIBLE"
        b.reason(
            f"You own {s.land_holding_hectares:g} ha of cultivable land; all landholding "
            "farmer families are covered irrespective of landholding size, and no "
            "exclusion criterion applies to you.",
            "pm_kisan:3.1",
            "pm_kisan:3.2",
        )
        b.reason("The benefit is ₹6,000 per year in three installments of ₹2,000.", "pm_kisan:2.1")

    if verdict != "NOT_ELIGIBLE":
        b.step(
            "Your name must appear in the land records as on 1 February 2019 "
            "(except land inherited on the owner's death).",
            "pm_kisan:3.3",
        )
        if s.has_aadhaar_linked_bank is False:
            b.step(
                "Seed Aadhaar with your bank account and enable DBT (or open an India Post "
                "Payments Bank account) — installments are not released otherwise.",
                "pm_kisan:6.2",
            )
        b.step(
            "Register at a CSC, through your patwari / agriculture office, or via "
            "'Farmers Corner' on pmkisan.gov.in, and complete eKYC.",
            "pm_kisan:5.1",
            "pm_kisan:6.1",
        )
    return EligibilityVerdict(
        scheme_name=SchemeName.PM_KISAN,
        verdict=verdict,
        reasons=b.reasons,
        next_steps=b.next_steps,
        citations=b.citations,
    )


def _pm_jay(s: EligibilitySlots) -> EligibilityVerdict:
    b = _Builder()
    verify_step = (
        "Check your name in the beneficiary database on beneficiary.nha.gov.in, the Ayushman "
        "app, or by calling 14555, then complete Aadhaar eKYC for a free Ayushman card.",
        "pm_jay:5.1",
        "pm_jay:5.2",
    )

    if s.age_years is not None and s.age_years >= PM_JAY_SENIOR_AGE:
        b.reason(
            "All citizens aged 70 and above are covered irrespective of income, with an "
            "Ayushman Vay Vandana card and up to ₹5 lakh cover per year.",
            "pm_jay:3.5",
        )
        b.step("Apply for the Ayushman Vay Vandana card with Aadhaar eKYC.", "pm_jay:5.2")
        return _jay(b, "ELIGIBLE")

    if s.occupation == Occupation.FRONTLINE_HEALTH_WORKER:
        b.reason("ASHA and Anganwadi workers / helpers and their families are covered.", "pm_jay:3.6")
        b.step(*verify_step)
        return _jay(b, "ELIGIBLE")

    excluded = False
    if s.is_income_tax_payer:
        excluded = True
        b.reason("Households paying income tax are excluded.", "pm_jay:4.1", "pm_jay:4.2")
    if s.occupation == Occupation.GOVERNMENT_EMPLOYEE:
        excluded = True
        b.reason(
            "Households with a member in regular government employment are excluded; "
            "government employees are generally covered by schemes such as CGHS.",
            "pm_jay:4.2",
        )
    income_limit = SECC_MONTHLY_INCOME_LIMIT_INR * 12
    if s.annual_income_inr is not None and s.annual_income_inr > income_limit:
        excluded = True
        b.reason(
            f"Households with a member earning more than ₹{SECC_MONTHLY_INCOME_LIMIT_INR:,} "
            f"per month (₹{income_limit:,} per year) are excluded under the SECC criteria.",
            "pm_jay:4.1",
        )
    if excluded:
        b.step(
            "Your State may have expanded coverage with its own funds — check with the State Health Agency.",
            "pm_jay:3.7",
        )
        return _jay(b, "NOT_ELIGIBLE")

    if s.occupation in (Occupation.INFORMAL_URBAN_WORKER, Occupation.AGRICULTURAL_LABOURER):
        b.reason(
            "Your occupation matches PM-JAY's SECC criteria (urban occupational categories or "
            "landless manual casual labour) and no exclusion applies, but eligibility is "
            "confirmed only by the beneficiary database.",
            "pm_jay:3.3",
            "pm_jay:3.4",
            "pm_jay:3.1",
        )
        b.step(*verify_step)
        return _jay(b, "LIKELY_ELIGIBLE")

    b.reason(
        "No exclusion criterion applies, but PM-JAY is entitlement-based: only families in "
        "the SECC 2011 / updated beneficiary database are covered.",
        "pm_jay:3.1",
        "pm_jay:4.3",
    )
    b.step(*verify_step)
    return _jay(b, "NEEDS_VERIFICATION")


def _jay(b: _Builder, verdict: str) -> EligibilityVerdict:
    return EligibilityVerdict(
        scheme_name=SchemeName.PM_JAY,
        verdict=verdict,  # type: ignore[arg-type]
        reasons=b.reasons,
        next_steps=b.next_steps,
        citations=b.citations,
    )
