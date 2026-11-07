"""Letters and case files, filled from tool data only (the model never writes the facts)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .simulator import ENERGY_PLAN_MONTHS, energy_plan_amount


@dataclass
class Draft:
    id: str
    kind: str  # "energy_payment_plan", "council_tax_single_person_discount", "adviser_handover"
    recipient: str
    subject: str
    body: str
    summary: str  # one or two sentences the agent reads aloud
    status: str = "draft"  # "draft", "awaiting_confirmation", "sent", "cancelled", "superseded"
    shares_personal_data: bool = False
    facts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _sign_off(persona: dict) -> str:
    return (
        f"I have a visual impairment. Please contact me by "
        f"{persona['contact_preference'].lower()}.\n\nYours faithfully,\n{persona['full_name']}\n"
        f"{persona['address']}"
    )


def energy_payment_plan(draft_id: str, persona: dict, hh, today: date) -> Draft:
    bill = hh.bill("energy")
    plan = energy_plan_amount(bill.amount)
    w = hh.wages
    supplier = persona["energy"]["supplier"]
    body = (
        f"To: {supplier}, Customer Support\n"
        f"Account reference: {persona['energy']['account_ref']}\n"
        f"Date: {today:%d %B %Y}\n\n"
        f"Dear {supplier},\n\n"
        f"Request for an affordable payment plan\n\n"
        f"My working hours have been cut. Since {w.cut_since:%d %B %Y} my weekly wages have "
        f"averaged £{w.recent_weekly:.2f}, down from about £{w.usual_weekly:.2f}. "
        f"I am writing before I miss a payment.\n\n"
        f"I would like to lower my monthly Direct Debit from £{bill.amount:.2f} to £{plan:.2f} "
        f"for the next {ENERGY_PLAN_MONTHS} months. I understand the difference will build up as "
        f"a balance on my account, and I would like to agree how to repay it once my hours "
        f"return. Please take my ability to pay into account, as Ofgem's rules require.\n\n"
        + _sign_off(persona)
    )
    return Draft(
        id=draft_id,
        kind="energy_payment_plan",
        recipient=supplier,
        subject="Request for an affordable payment plan",
        body=body,
        summary=(
            f"It asks {supplier} to lower your Direct Debit from £{bill.amount:.0f} to "
            f"£{plan:.0f} a month for {ENERGY_PLAN_MONTHS} months, because your hours were cut, "
            f"and to agree repayment later."
        ),
    )


def council_tax_spd(draft_id: str, persona: dict, hh, today: date) -> Draft:
    ct = persona["council_tax"]
    bill = hh.bill("council_tax")
    saving = round(bill.amount * 0.25, 2)
    body = (
        f"To: {ct['council']}, Council Tax\n"
        f"Account reference: {ct['account_ref']}\n"
        f"Date: {today:%d %B %Y}\n\n"
        f"Dear {ct['council']},\n\n"
        f"Application for single person discount\n\n"
        f"I am the only adult living at my address; I live with my child, who is "
        f"{persona['children'][0]['age']}. My bill does not include the 25% single person "
        f"discount. Please apply it, recalculate my instalments (currently £{bill.amount:.2f} "
        f"a month), and backdate it to when I became the only adult, if you can.\n\n"
        + _sign_off(persona)
    )
    return Draft(
        id=draft_id,
        kind="council_tax_single_person_discount",
        recipient=ct["council"],
        subject="Application for single person discount",
        body=body,
        summary=(
            f"It tells {ct['council']} you are the only adult at home and asks for the 25% "
            f"discount, about £{saving:.2f} off each monthly instalment, backdated if possible."
        ),
    )


def adviser_handover(draft_id: str, persona: dict, hh, events, support, forecast, sent) -> Draft:
    debt = persona["debts"][0]
    risk = forecast.prob_shortfall_within(30)
    lines = [
        "Case file for a free debt adviser (shared with the customer's consent)",
        "",
        f"Customer: {persona['full_name']}, {persona['age']}, {persona['city']}",
        f"Household: {persona['adults_in_home']} adult, child aged "
        + ", ".join(str(c["age"]) for c in persona["children"]),
        f"Work: {persona['work']}",
        f"Access needs: {persona['access_needs']}. Prefers {persona['contact_preference'].lower()}.",
        "",
        "What has happened:",
        *[f"- {e.name}: {'; '.join(e.evidence)}" for e in events],
        "",
        f"Forecast: {risk:.0%} chance the balance goes below zero in the next 30 days "
        "(bank stress test, synthetic data).",
        "",
        f"Debt: {debt['creditor']}, £{debt['balance']:.2f} owed, £{debt['monthly_payment']:.2f} a "
        f"month, {debt['missed_payments']} missed payments.",
        "",
        "Already done on this call:",
        *([f"- Sent: {d.subject} to {d.recipient}" for d in sent] or ["- Nothing sent yet"]),
        "",
        "Please check:",
        *[f"- {s.name}: {s.summary}" for s in support if s.status in ("may_be_eligible", "can_request")],
        "",
        "The customer should not need to repeat any of this.",
    ]
    return Draft(
        id=draft_id,
        kind="adviser_handover",
        recipient="Free debt advice partner (simulated)",
        subject="Case file for debt advice",
        body="\n".join(lines),
        summary=(
            f"It includes your situation, the forecast, your {debt['creditor']} debt of "
            f"£{debt['balance']:.0f} with {debt['missed_payments']} missed payments, what we did "
            f"today, and what to check, like Universal Credit and Breathing Space."
        ),
        shares_personal_data=True,
    )
