"""A help call, from ringing to goodbye. The code here, not the model, decides:

- the opening: the customer's safe phrase is spoken by this code, never by the model;
- what the model may see: the safe phrase is redacted from anything the customer says,
  and every request is checked before it leaves;
- what is spoken: every agent sentence passes the credential guard;
- what is sent: only drafts the customer said yes to, recorded here.
"""

from __future__ import annotations

import re
import uuid

from . import config, facts, guard
from .agent import KICKOFF, LiveAgent, OfflineAgent, yes_no
from .case import Case, World
from .firewall import POLICY
from .tools import Toolbox

REPEAT_PHRASE = re.compile(
    r"\b(repeat|again|say|tell|what('s| is| was))\b.*\bphrase\b|\bphrase\b.*\b(again|repeat)\b", re.I
)


def opening_lines(name: str, phrase: str) -> list[str]:
    return [
        f"Hello {name}. This is {config.PRODUCT_NAME} from your bank. "
        f"Your safe phrase is: {phrase}.",
        "We will never ask for your PIN, passwords, card details or any codes. If you're unsure "
        "this call is really us, hang up and call 159, or the number on the back of your card.",
    ]


APPROVE_PROMPT = "To continue, please approve this call in your banking app."


class CallSession:
    def __init__(self, world: World, safe_phrase: str, offline: bool | None = None, client=None):
        self.id = uuid.uuid4().hex[:10]
        self.case = Case(world)
        self.toolbox = Toolbox(self.case)
        self.safe_phrase = safe_phrase
        self.offline = config.offline_mode() if offline is None else offline
        self.status = "ringing"  # ringing, verifying, in_call, ended, declined
        self.requests_checked = 0
        self.transcript: list[dict] = []
        if self.offline:
            self.agent = OfflineAgent(self.toolbox)
        else:
            self.agent = LiveAgent(self.toolbox, client=client, before_send=self._check_request)
        self.case.log("trigger", "Help call placed: forecast risk over the threshold")

    # ---- guards around the model ----------------------------------------------

    def _check_request(self, request: dict) -> None:
        guard.assert_no_secret(request, self.safe_phrase)
        self.requests_checked += 1

    # ---- events for the UI -----------------------------------------------------

    def _ev(self, events: list, kind: str, **payload) -> None:
        event = {"type": kind, **payload}
        events.append(event)
        if kind in ("system_say", "agent_say", "customer_say"):
            self.transcript.append(event)

    # ---- call flow -------------------------------------------------------------

    def answer(self) -> list[dict]:
        events: list[dict] = []
        if self.status != "ringing":
            return events
        self.status = "verifying"
        for line in opening_lines(self.case.persona["name"], self.safe_phrase):
            self._ev(events, "system_say", text=line, has_safe_phrase="safe phrase is" in line)
        self._ev(events, "system_say", text=APPROVE_PROMPT)
        self._ev(events, "approval_needed")
        self.case.log("auth", "Customer answered. Bank proved itself first: safe phrase played")
        return events

    def decline(self) -> list[dict]:
        self.status = "declined"
        self.case.log("call", "Customer declined the call. Nothing changes on their account.")
        return [{"type": "call_ended", "reason": "declined"}]

    def approve(self) -> list[dict]:
        events: list[dict] = []
        if self.status != "verifying":
            return events
        self.status = "in_call"
        self.case.log("auth", "Customer approved the call in their app (stub)")
        return events + self._agent_turn(KICKOFF, [])

    def say(self, text: str) -> list[dict]:
        events: list[dict] = []
        text = text.strip()
        if not text:
            return events
        if self.status == "verifying":
            self._ev(events, "customer_say", text=text)
            self._ev(events, "system_say", text=APPROVE_PROMPT)
            return events
        if self.status != "in_call":
            return events
        self._ev(events, "customer_say", text=text)

        if REPEAT_PHRASE.search(text):
            self._ev(events, "system_say", text=f"Your safe phrase is: {self.safe_phrase}.", has_safe_phrase=True)
            self.case.log("auth", "Safe phrase repeated by the system (the agent never sees it)")
            return events

        clean, redacted = guard.redact_secret(text, self.safe_phrase)
        if redacted:
            self.case.log("auth", "Customer said their safe phrase; it was removed before the agent saw it")
        notes = self._settle_pending(clean, events)
        return events + self._agent_turn(clean, notes)

    def decide(self, send: bool) -> list[dict]:
        """The Yes / No buttons on screen, for when speaking isn't easy."""
        return self.say("Yes, send it." if send else "No, don't send it.")

    def hang_up(self) -> list[dict]:
        if self.status in ("ended", "declined"):
            return []
        self.status = "ended"
        for d in self.case.pending:
            d.status = "cancelled"
        self.case.log("call", "Call ended")
        return [{"type": "call_ended", "reason": "hung_up"}]

    # ---- consent: decided here, from the customer's own words --------------------

    def _settle_pending(self, text: str, events: list) -> list[str]:
        pending = self.case.pending
        if not pending:
            return []
        titles = "; ".join(f"{d.subject} to {d.recipient}" for d in pending)
        decision = yes_no(text)
        if decision == "yes":
            done = self.case.resolve_pending(True)
            self._ev(events, "sent", drafts=[d.id for d in done])
            return [f"System: the customer said yes. Sent (simulated): {titles}."]
        if decision == "no":
            done = self.case.resolve_pending(False)
            self._ev(events, "cancelled", drafts=[d.id for d in done])
            return [f"System: the customer said no. Nothing was sent: {titles}."]
        return [f"System: still waiting for a clear yes or no before sending: {titles}. Nothing has been sent."]

    # ---- one agent turn, filtered --------------------------------------------------

    def _agent_turn(self, text: str, notes: list[str]) -> list[dict]:
        events: list[dict] = []
        try:
            turn = self.agent.turn(text, notes)
        except guard.SecretLeak as e:
            self.case.log("guard", f"Blocked a request to the model: {e}")
            self._ev(events, "guard_block", reason=str(e), original=None)
            self._ev(events, "system_say", text="Sorry, something went wrong on our side. Please call the number on your card.")
            return events
        except Exception as e:  # network or API trouble: say so plainly, keep the call alive
            self.case.log("error", f"Agent unavailable: {type(e).__name__}")
            self._ev(events, "error", message=f"{type(e).__name__}: {e}")
            self._ev(
                events,
                "system_say",
                text="Sorry, the line is having trouble. Please try again, or call the number on your card.",
            )
            return events

        for call in turn.tool_calls:
            self._ev(events, "tool", name=call["name"], input=call["input"])
        for utterance in turn.utterances:
            verdict = guard.check_utterance(utterance)
            if not verdict.allowed:
                self.case.log("guard", f"Blocked agent speech: {verdict.reason}")
                self._ev(events, "guard_block", reason=verdict.reason, original=utterance)
            self._ev(
                events,
                "agent_say",
                text=verdict.text,
                figures=facts.trace(verdict.text, self.case.facts),
            )
        if self.case.pending:
            self._ev(events, "pending", drafts=[d.id for d in self.case.pending])
        if getattr(self.agent, "stage", None) == "ended":
            self.status = "ended"
            self._ev(events, "call_ended", reason="finished")
        return events

    # ---- for the UI --------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "mode": "offline" if self.offline else "live",
            "model": None if self.offline else config.MODEL,
            "requests_checked": self.requests_checked,
            "data_use_policy": POLICY,
            **self.case.to_dict(),
        }
