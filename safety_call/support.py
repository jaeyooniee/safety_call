"""Support catalogue and simplified eligibility rules.

These are illustrative summaries of public UK rules, written to show the approach.
They are guidance, not advice: the organisation that runs each scheme decides.
Kept behind one function so a full rules engine (e.g. PolicyEngine UK) could
replace it later.
"""

from __future__ import annotations

from dataclasses import dataclass

from .household import Household
from .life_events import LifeEvent
from .simulator import energy_plan_amount

ILLUSTRATIVE = "Illustrative summary of a public rule; the organisation decides."


@dataclass(frozen=True)
class SupportResult:
    id: str
    name: str
    status: str  # "eligible", "may_be_eligible", "can_request", "not_eligible"
    summary: str
    rule: str
    monthly_effect: float | None = None  # pounds a month, when we can say
    action: str | None = None  # simulator action this maps to
    for_event: str | None = None

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def _council_tax_spd(persona, hh, **_):
    ct = persona["council_tax"]
    bill = hh.bill("council_tax")
    rule = "If you are the only adult (18 or over) living in your home, you get 25% off your Council Tax."
    if bill is None:
        return None
    if persona["adults_in_home"] != 1:
        return SupportResult(
            "council_tax_single_person_discount",
            "Council Tax single person discount",
            "not_eligible",
            "Another adult lives in the home, so the single person discount does not apply.",
            rule,
        )
    if ct["discount_applied"]:
        return None
    saving = round(bill.amount * 0.25, 2)
    return SupportResult(
        "council_tax_single_person_discount",
        "Council Tax single person discount",
        "eligible",
        f"Only one adult lives in the home and no discount is applied: £{saving:.2f} less each month.",
        rule,
        monthly_effect=saving,
        action="single_person_discount",
    )


def _energy_plan(hh, **_):
    bill = hh.bill("energy")
    if bill is None:
        return None
    plan = energy_plan_amount(bill.amount)
    return SupportResult(
        "energy_payment_plan",
        "Affordable energy payment plan",
        "can_request",
        f"Ask to pay £{plan:.0f} a month instead of £{bill.amount:.0f} for 3 months; "
        "the difference is repaid later.",
        "Energy suppliers must take your ability to pay into account when agreeing a payment plan (Ofgem rules).",
        monthly_effect=round(bill.amount - plan, 2),
        action="energy_payment_plan",
    )


def _universal_credit(persona, **_):
    if persona["receives_universal_credit"]:
        return None
    return SupportResult(
        "universal_credit",
        "Universal Credit",
        "may_be_eligible",
        "Low or falling earnings with a child and rent to pay may qualify. "
        "The amount needs a full check by an adviser or benefits calculator.",
        "Universal Credit tops up low or changing earnings; amounts depend on full circumstances.",
    )


def _simple(id_, name, status, summary, rule):
    return lambda **_: SupportResult(id_, name, status, summary, rule)


def _healthy_start(persona, **_):
    young = any(c["age"] < 4 for c in persona["children"]) or persona.get("pregnant")
    rule = "Healthy Start helps with food and milk if you are pregnant or have a child under 4 and get certain benefits."
    if not young:
        return SupportResult(
            "healthy_start", "Healthy Start", "not_eligible",
            "No child under 4 and no pregnancy recorded.", rule,
        )
    return SupportResult(
        "healthy_start", "Healthy Start", "may_be_eligible",
        "A young child or pregnancy is recorded; eligibility also depends on benefits.", rule,
    )


CATALOGUE = {
    "council_tax_single_person_discount": _council_tax_spd,
    "energy_payment_plan": _energy_plan,
    "universal_credit": _universal_credit,
    "new_style_jsa": _simple(
        "new_style_jsa", "New Style Jobseeker's Allowance", "may_be_eligible",
        "May apply after losing work, depending on recent National Insurance contributions.",
        "New Style JSA depends on National Insurance paid in recent tax years, not on savings.",
    ),
    "council_tax_reduction": _simple(
        "council_tax_reduction", "Council Tax Reduction", "may_be_eligible",
        "Councils reduce the bill for people on low incomes; the council decides.",
        "Council Tax Reduction is means-tested and run by each local council.",
    ),
    "statutory_sick_pay": _simple(
        "statutory_sick_pay", "Statutory Sick Pay", "may_be_eligible",
        "If off work sick for 4 or more days in a row, the employer may have to pay SSP.",
        "Employees off sick for 4+ days in a row can get Statutory Sick Pay from their employer.",
    ),
    "pip": _simple(
        "pip", "Personal Independence Payment", "may_be_eligible",
        "If a long-term condition affects daily life, PIP may help; an assessment decides.",
        "PIP helps with extra costs of a long-term physical or mental health condition or disability.",
    ),
    "prescription_prepayment": _simple(
        "prescription_prepayment", "Prescription Prepayment Certificate", "can_request",
        "Caps prescription costs in England if you need several items.",
        "A Prescription Prepayment Certificate covers all NHS prescriptions for a fixed price.",
    ),
    "child_benefit_claim": _simple(
        "child_benefit_claim", "Child Benefit for a new baby", "may_be_eligible",
        "Claim soon after the birth; it can be backdated up to 3 months.",
        "Child Benefit is paid for each child; claims can be backdated up to 3 months.",
    ),
    "healthy_start": _healthy_start,
    "sure_start_maternity_grant": _simple(
        "sure_start_maternity_grant", "Sure Start Maternity Grant", "may_be_eligible",
        "A one-off payment for a first baby if certain benefits are received.",
        "A one-off grant in England and Wales for a first child, for people on certain benefits.",
    ),
    "breathing_space": _simple(
        "breathing_space", "Breathing Space", "can_request",
        "A debt adviser can apply for 60 days' protection from most creditor action, interest and charges.",
        "Breathing Space (England and Wales) is applied for through a debt adviser.",
    ),
    "debt_advice_handover": _simple(
        "debt_advice_handover", "Free debt advice (warm handover)", "can_request",
        "Pass the case file to a free debt adviser so the customer does not repeat their story.",
        "Free, regulated debt advice is available from charities and councils.",
    ),
}


def check(persona: dict, household: Household, events: list[LifeEvent]) -> list[SupportResult]:
    """The support bundle for the detected life events, in order, without duplicates."""
    seen: set[str] = set()
    results: list[SupportResult] = []
    for event in events:
        for item in event.bundle:
            if item in seen:
                continue
            seen.add(item)
            result = CATALOGUE[item](persona=persona, hh=household)
            if result is not None:
                results.append(
                    SupportResult(**{**result.to_dict(), "for_event": event.key})
                )
    return results
