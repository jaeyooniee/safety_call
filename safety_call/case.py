"""One customer's case: what the bank knows, what was said, drafted, corrected and sent."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from . import config, data, household, life_events, support
from .firewall import FirewalledStore
from .letters import Draft
from .simulator import Forecast, simulate


@dataclass(frozen=True)
class World:
    """The bank's synthetic records for the demo customer."""

    persona: dict
    transactions: tuple
    failed_payments: tuple
    today: date

    @classmethod
    def demo(cls) -> "World":
        return cls(
            persona=data.PERSONA,
            transactions=tuple(data.generate_transactions()),
            failed_payments=tuple(data.FAILED_PAYMENTS),
            today=data.DEMO_TODAY,
        )


# What the customer can correct on the call, and how each value is read.
CORRECTABLE = {
    "adults_in_home": "How many adults (18+) live in the home",
    "hours_back_to_normal": "Whether working hours are back to normal",
    "receives_universal_credit": "Whether the customer already gets Universal Credit",
    "not_this_event": "A detected life event that is wrong (give its key)",
}


def _parse_bool(value: str) -> bool:
    v = value.strip().lower()
    if v in ("true", "yes", "y", "1"):
        return True
    if v in ("false", "no", "n", "0"):
        return False
    raise ValueError(f"expected yes or no, got {value!r}")


@dataclass
class Case:
    world: World
    persona: dict = field(init=False)
    timeline: list = field(default_factory=list)
    drafts: dict = field(default_factory=dict)
    facts: list = field(default_factory=list)
    corrections: list = field(default_factory=list)
    dismissed_events: set = field(default_factory=set)
    hours_back: bool = False
    chosen_actions: tuple = ()
    after: Forecast | None = None
    store: FirewalledStore = field(default_factory=FirewalledStore)

    def __post_init__(self):
        self.persona = copy.deepcopy(self.world.persona)
        self.refresh()

    # ---- what the bank infers --------------------------------------------------

    def refresh(self) -> None:
        txs = list(self.world.transactions)
        hh = household.build(txs, self.persona, self.world.today)
        if self.hours_back:
            w = hh.wages
            hh = replace(
                hh,
                wages=replace(
                    w, recent_weekly=w.usual_weekly, recent_sd=w.usual_sd,
                    recent_weeks=0, hours_cut=False, cut_since=None,
                ),
            )
        events = [
            e
            for e in life_events.detect(txs, hh, list(self.world.failed_payments), self.world.today)
            if e.key not in self.dismissed_events
        ]
        self.household = hh
        self.store.put("events", events)
        self.store.put("support", support.check(self.persona, hh, events))
        self.store.put("forecast", simulate(hh))
        if self.chosen_actions:
            self.after = simulate(hh, self.chosen_actions)

    def events(self, purpose: str = "customer_support"):
        return self.store.get("events", purpose)

    def support(self, purpose: str = "customer_support"):
        return self.store.get("support", purpose)

    def forecast(self, purpose: str = "customer_support") -> Forecast:
        return self.store.get("forecast", purpose)

    # ---- record keeping --------------------------------------------------------

    def log(self, kind: str, text: str) -> None:
        self.timeline.append({"at": datetime.now().strftime("%H:%M:%S"), "kind": kind, "text": text})

    def add_facts(self, facts: list[dict]) -> None:
        self.facts.extend(facts)

    def new_draft_id(self) -> str:
        return f"d{len(self.drafts) + 1}"

    def add_draft(self, draft: Draft) -> Draft:
        # A new draft of the same kind replaces an older unsent one.
        for old in self.drafts.values():
            if old.kind == draft.kind and old.status in ("draft", "awaiting_confirmation"):
                old.status = "superseded"
        self.drafts[draft.id] = draft
        self.log("draft", f"Drafted: {draft.subject} (to {draft.recipient})")
        return draft

    # ---- sending only after the customer says yes ------------------------------

    @property
    def pending(self) -> list[Draft]:
        return [d for d in self.drafts.values() if d.status == "awaiting_confirmation"]

    def request_send(self, ids: list[str]) -> list[Draft]:
        chosen = []
        for i in ids:
            draft = self.drafts.get(i)
            if draft is None or draft.status not in ("draft", "awaiting_confirmation"):
                raise ValueError(f"no unsent draft with id {i!r}")
            draft.status = "awaiting_confirmation"
            chosen.append(draft)
        self.log("consent", "Waiting for the customer's yes or no: " + ", ".join(d.subject for d in chosen))
        return chosen

    def resolve_pending(self, confirmed: bool) -> list[Draft]:
        done = self.pending
        for d in done:
            d.status = "sent" if confirmed else "cancelled"
        verb = "Customer said yes. Sent (simulated)" if confirmed else "Customer said no. Not sent"
        if done:
            self.log("sent" if confirmed else "cancelled", f"{verb}: " + ", ".join(d.subject for d in done))
        return done

    @property
    def sent(self) -> list[Draft]:
        return [d for d in self.drafts.values() if d.status == "sent"]

    # ---- "that's wrong": corrections from the customer -------------------------

    def correct(self, field_name: str, value: str) -> dict:
        if field_name not in CORRECTABLE:
            raise ValueError(f"cannot correct {field_name!r}")
        before = self._describe(field_name)
        if field_name == "adults_in_home":
            n = int(value)
            if not 1 <= n <= 8:
                raise ValueError("adults_in_home must be between 1 and 8")
            self.persona["adults_in_home"] = n
        elif field_name == "hours_back_to_normal":
            self.hours_back = _parse_bool(value)
        elif field_name == "receives_universal_credit":
            self.persona["receives_universal_credit"] = _parse_bool(value)
        elif field_name == "not_this_event":
            self.dismissed_events.add(value.strip())
        # Unsent drafts may rest on the old fact: withdraw them.
        withdrawn = []
        for d in self.drafts.values():
            if d.status in ("draft", "awaiting_confirmation"):
                d.status = "superseded"
                withdrawn.append(d.subject)
        if field_name == "adults_in_home" and "single_person_discount" in self.chosen_actions:
            self.chosen_actions = tuple(a for a in self.chosen_actions if a != "single_person_discount")
        self.refresh()
        after = self._describe(field_name)
        record = {"field": field_name, "before": before, "after": after, "withdrawn_drafts": withdrawn}
        self.corrections.append(record)
        self.log("correction", f"Customer corrected {CORRECTABLE[field_name].lower()}: {before} -> {after}")
        return record

    def _describe(self, field_name: str) -> str:
        if field_name == "adults_in_home":
            return str(self.persona["adults_in_home"])
        if field_name == "hours_back_to_normal":
            return "yes" if self.hours_back else "no"
        if field_name == "receives_universal_credit":
            return "yes" if self.persona["receives_universal_credit"] else "no"
        return ", ".join(sorted(self.dismissed_events)) or "none dismissed"

    # ---- for the UI ------------------------------------------------------------

    def to_dict(self) -> dict:
        view = "customer_view"
        return {
            "timeline": self.timeline,
            "drafts": [d.to_dict() for d in self.drafts.values()],
            "pending": [d.id for d in self.pending],
            "facts": self.facts,
            "corrections": self.corrections,
            "events": [e.to_dict() for e in self.events(view)],
            "support": [s.to_dict() for s in self.support(view)],
            "forecast": self.forecast(view).to_dict(),
            "after": self.after.to_dict() if self.after else None,
            "chosen_actions": list(self.chosen_actions),
            "data_use_audit": self.store.audit[-20:],
        }
