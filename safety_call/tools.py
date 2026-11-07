"""The agent's tools. All numbers come from here, each with a source.

There is deliberately no tool that moves money or reveals the safe phrase.
"""

from __future__ import annotations

from datetime import timedelta

from . import facts, letters
from .case import CORRECTABLE, Case
from .simulator import ACTIONS, energy_plan_amount, simulate

SRC_ACCOUNT = "Bank account records (synthetic)"
SRC_FORECAST = "Household stress test: 2,000 simulated futures over 90 days"
SRC_RULES = "Illustrative summary of a public rule; the organisation decides"


def _obj(properties: dict | None = None) -> dict:
    properties = properties or {}
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


DEFINITIONS = [
    {
        "name": "get_situation",
        "description": (
            "Why the bank is calling: the customer's name and household, balance today, the "
            "life events detected from their transactions (with evidence), bills due in the "
            "next 30 days, and the forecast risk of going below zero. Call this first."
        ),
        "input_schema": _obj(),
    },
    {
        "name": "check_support",
        "description": (
            "The support bundle prepared for the detected life events: each item with status "
            "(eligible, may_be_eligible, can_request, not_eligible), a plain summary, the rule, "
            "and the monthly effect when known. Results are illustrative; organisations decide."
        ),
        "input_schema": _obj(),
    },
    {
        "name": "compare_futures",
        "description": (
            "Compare 'do nothing' with taking actions, using the household stress test. Returns "
            "for both: chance of going below zero by the riskiest day and within 30 days, and "
            "the lowest typical balance. Actions: " + "; ".join(f"{k} = {v}" for k, v in ACTIONS.items())
        ),
        "input_schema": _obj(
            {"actions": {"type": "array", "items": {"type": "string", "enum": list(ACTIONS)}}}
        ),
    },
    {
        "name": "draft_letter",
        "description": (
            "Write a letter from the customer, filled from their records. Returns a draft id "
            "and a short summary to read aloud. Drafting does not send anything."
        ),
        "input_schema": _obj(
            {"kind": {"type": "string", "enum": ["energy_payment_plan", "council_tax_single_person_discount"]}}
        ),
    },
    {
        "name": "prepare_adviser_handover",
        "description": (
            "Prepare a case file for a free debt adviser so the customer never repeats their "
            "story. It contains personal information, so it needs the customer's consent to send."
        ),
        "input_schema": _obj(),
    },
    {
        "name": "request_send",
        "description": (
            "Ask to send drafts. This does NOT send: it marks them as waiting for the customer's "
            "answer. Then ask the customer one clear yes-or-no question. The system records "
            "their answer and tells you in a system message what happened."
        ),
        "input_schema": _obj({"draft_ids": {"type": "array", "items": {"type": "string"}}}),
    },
    {
        "name": "correct_information",
        "description": (
            "Use when the customer says something we have is wrong. Fields: "
            + "; ".join(f"{k} = {v}" for k, v in CORRECTABLE.items())
            + ". Values: a number for adults_in_home, yes/no for the yes/no fields, an event key "
            "for not_this_event. Recalculates the forecast and support, and withdraws unsent drafts."
        ),
        "input_schema": _obj(
            {
                "field": {"type": "string", "enum": list(CORRECTABLE)},
                "value": {"type": "string"},
            }
        ),
    },
]


class Toolbox:
    def __init__(self, case: Case):
        self.case = case

    def run(self, name: str, args: dict) -> dict:
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            raise ValueError(f"unknown tool {name!r}")
        result = handler(**args)
        self.case.add_facts(result.get("facts", []))
        return result

    # ---- tools -----------------------------------------------------------------

    def _get_situation(self) -> dict:
        c = self.case
        hh, p, fc = c.household, c.persona, c.forecast()
        peak_day, peak_p = fc.peak_risk()
        end = hh.today + timedelta(days=30)
        due = sorted(
            ((d, b) for b in hh.bills for d in b.due_dates(hh.today, end) if b.frequency == "monthly"),
            key=lambda x: x[0],
        )
        rent = next((b for b in hh.bills if b.frequency == "weekly"), None)
        events = c.events()
        fs = [
            facts.money("Balance today", hh.balance, SRC_ACCOUNT),
            facts.percent("Chance of going below zero by the riskiest day", peak_p, SRC_FORECAST),
            facts.day("Riskiest day in the next 30 days", peak_day, SRC_FORECAST),
            facts.percent("Chance of going below zero in the next 30 days", fc.prob_shortfall_within(30), SRC_FORECAST),
        ]
        if hh.wages.hours_cut:
            fs += [
                facts.money("Recent weekly wages (average)", hh.wages.recent_weekly, SRC_ACCOUNT),
                facts.money("Usual weekly wages (average)", hh.wages.usual_weekly, SRC_ACCOUNT),
            ]
        for d, b in due:
            fs += [facts.money(f"{b.name} due", b.amount, f"{b.source}"), facts.day(f"{b.name} due date", d, b.source)]
        if rent:
            fs.append(facts.money("Weekly rent", rent.amount, rent.source))
        for d in p["debts"]:
            fs.append(facts.money(f"{d['creditor']}: amount owed", d["balance"], "Creditor's Direct Debit and statement (synthetic)"))
        c.log("tool", "Read the situation and forecast")
        return {
            "customer": {
                "name": p["name"],
                "household": f"{p['adults_in_home']} adult(s), child aged "
                + ", ".join(str(ch["age"]) for ch in p["children"]),
                "access_needs": p["access_needs"],
            },
            "today": hh.today.isoformat(),
            "balance_today": hh.balance,
            "life_events": [e.to_dict() for e in events],
            "bills_next_30_days": [{"name": b.name, "amount": b.amount, "due": d.isoformat()} for d, b in due],
            "weekly_rent": rent.amount if rent else None,
            "debts": [
                {
                    "creditor": d["creditor"],
                    "owed": d["balance"],
                    "monthly_payment": d["monthly_payment"],
                    "missed_payments": d["missed_payments"],
                }
                for d in p["debts"]
            ],
            "forecast": {
                "riskiest_day": peak_day.isoformat(),
                "chance_below_zero_by_riskiest_day": round(peak_p, 2),
                "chance_below_zero_within_30_days": round(fc.prob_shortfall_within(30), 2),
                "biggest_risk": fc.drivers[0]["name"] if fc.drivers else None,
                "method": SRC_FORECAST,
            },
            "facts": fs,
        }

    def _check_support(self) -> dict:
        items = self.case.support()
        fs = [
            facts.money(f"{s.name}: monthly effect", s.monthly_effect, SRC_RULES)
            for s in items
            if s.monthly_effect
        ]
        fs += [facts.text(s.name, s.status.replace("_", " "), s.rule) for s in items]
        ids = {s.id for s in items}
        if "council_tax_single_person_discount" in ids:
            fs.append(facts.percent("Single person discount on Council Tax", 0.25, SRC_RULES))
        energy = self.case.household.bill("energy")
        if "energy_payment_plan" in ids and energy:
            fs.append(facts.money("Proposed energy Direct Debit", energy_plan_amount(energy.amount), SRC_RULES))
        self.case.log("tool", "Checked the support bundle for the detected life events")
        return {"support": [s.to_dict() for s in items], "note": SRC_RULES, "facts": fs}

    def _compare_futures(self, actions: list[str]) -> dict:
        c = self.case
        allowed = {s.action for s in c.support() if s.action and s.status in ("eligible", "can_request")}
        refused = [a for a in actions if a not in allowed]
        chosen = tuple(a for a in actions if a in allowed)
        base = c.forecast()
        after = simulate(c.household, chosen)
        c.chosen_actions, c.after = chosen, after
        day, _ = base.peak_risk()

        def summary(f):
            low_day, low = f.lowest_median()
            return {
                "chance_below_zero_by_riskiest_day": round(f.prob_shortfall_by(day), 2),
                "chance_below_zero_within_30_days": round(f.prob_shortfall_within(30), 2),
                "lowest_typical_balance": round(low, 2),
                "lowest_typical_balance_day": low_day.isoformat(),
            }

        b, a = summary(base), summary(after)
        fs = [
            facts.day("Riskiest day", day, SRC_FORECAST),
            facts.percent("Do nothing: chance below zero by the riskiest day", b["chance_below_zero_by_riskiest_day"], SRC_FORECAST),
            facts.percent("With actions: chance below zero by the riskiest day", a["chance_below_zero_by_riskiest_day"], SRC_FORECAST),
            facts.percent("Do nothing: chance below zero within 30 days", b["chance_below_zero_within_30_days"], SRC_FORECAST),
            facts.percent("With actions: chance below zero within 30 days", a["chance_below_zero_within_30_days"], SRC_FORECAST),
            facts.money("Do nothing: lowest typical balance", b["lowest_typical_balance"], SRC_FORECAST),
            facts.money("With actions: lowest typical balance", a["lowest_typical_balance"], SRC_FORECAST),
        ]
        c.log("forecast", f"Compared futures with: {', '.join(ACTIONS[x] for x in chosen) or 'no actions'}")
        out = {
            "riskiest_day": day.isoformat(),
            "do_nothing": b,
            "with_actions": a,
            "actions_applied": [ACTIONS[x] for x in chosen],
            "biggest_remaining_risk": after.drivers[0]["name"] if after.drivers else None,
            "facts": fs,
        }
        if refused:
            out["not_applied"] = {x: "not available for this customer" for x in refused}
        return out

    def _draft_letter(self, kind: str) -> dict:
        c = self.case
        support_ids = {s.id: s for s in c.support()}
        if kind not in support_ids or support_ids[kind].status not in ("eligible", "can_request"):
            return {"error": f"{kind} does not apply to this customer, so no letter was drafted."}
        make = {
            "energy_payment_plan": letters.energy_payment_plan,
            "council_tax_single_person_discount": letters.council_tax_spd,
        }[kind]
        draft = c.add_draft(make(c.new_draft_id(), c.persona, c.household, c.household.today))
        return {
            "draft_id": draft.id,
            "to": draft.recipient,
            "subject": draft.subject,
            "summary_to_read_aloud": draft.summary,
            "sent": False,
        }

    def _prepare_adviser_handover(self) -> dict:
        c = self.case
        draft = c.add_draft(
            letters.adviser_handover(
                c.new_draft_id(), c.persona, c.household, c.events(), c.support(), c.forecast(), c.sent
            )
        )
        return {
            "draft_id": draft.id,
            "to": draft.recipient,
            "summary_to_read_aloud": draft.summary,
            "shares_personal_information": True,
            "sent": False,
        }

    def _request_send(self, draft_ids: list[str]) -> dict:
        try:
            chosen = self.case.request_send(draft_ids)
        except ValueError as e:
            return {"error": str(e)}
        return {
            "status": "waiting_for_customer",
            "drafts": [{"id": d.id, "to": d.recipient, "subject": d.subject} for d in chosen],
            "next": "Ask one clear yes-or-no question. Nothing is sent until the system records a yes.",
        }

    def _correct_information(self, field: str, value: str) -> dict:
        try:
            record = self.case.correct(field, value)
        except ValueError as e:
            return {"error": str(e)}
        return {
            "corrected": record,
            "next": "Thank the customer, then call get_situation or check_support again before relying on earlier results.",
        }
