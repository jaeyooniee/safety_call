"""Run the demo story end to end with typed input, and check each step happened.

    python -m safety_call.demo            # offline script (no API key needed)
    python -m safety_call.demo --live     # the same story against the Claude API
"""

from __future__ import annotations

import sys

from . import config
from .call import CallSession
from .case import World

CUSTOMER_LINES = [
    "Yes, that's right. My agency cut my shifts. I can talk now.",
    "Yes please, write both letters.",
    "Yes, send them.",
    "Yes, please pass it to an adviser.",
    "Yes, that's okay, share it.",
]


def run(offline: bool = True, verbose: bool = True, client=None) -> CallSession:
    call = CallSession(World.demo(), config.DEFAULT_SAFE_PHRASE, offline=offline, client=client)

    def show(events):
        for e in events:
            if not verbose:
                continue
            if e["type"] in ("system_say", "agent_say", "customer_say"):
                who = {"system_say": "SYSTEM", "agent_say": "AGENT", "customer_say": "AMIRA"}[e["type"]]
                print(f"{who:>7}: {e['text']}")
            elif e["type"] in ("tool", "sent", "cancelled", "guard_block", "error"):
                print(f"{'':>7}  [{e['type']}] {e.get('name') or e.get('drafts') or e.get('reason') or e.get('message')}")

    show(call.answer())
    show(call.approve())
    for line in CUSTOMER_LINES:
        if call.status != "in_call":
            break
        show(call.say(line))
    return call


def check(call: CallSession) -> list[str]:
    """What must be true after the story. Returns the failures (empty = all good)."""
    failures = []
    first = call.transcript[0]
    if not (first["type"] == "system_say" and call.safe_phrase in first["text"]):
        failures.append("the call did not open with the safe phrase spoken by the system")
    if any(call.safe_phrase.lower() in e["text"].lower() for e in call.transcript if e["type"] == "agent_say"):
        failures.append("the agent spoke the safe phrase")
    kinds = {d.kind: d.status for d in call.case.drafts.values()}
    for kind in ("energy_payment_plan", "council_tax_single_person_discount", "adviser_handover"):
        if kinds.get(kind) != "sent":
            failures.append(f"{kind} was not sent after the customer's yes")
    if not call.case.after or call.case.after.prob_shortfall_within(30) >= call.case.forecast().prob_shortfall_within(30):
        failures.append("the actions did not lower the 30-day risk")
    untraced = [
        f["figure"]
        for e in call.transcript
        if e["type"] == "agent_say"
        for f in e.get("figures", [])
        if f["fact"] is None
    ]
    if untraced:
        failures.append(f"figures without a source: {untraced}")
    return failures


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    live = "--live" in sys.argv
    call = run(offline=not live)
    problems = check(call)
    print()
    print("All demo checks passed." if not problems else "FAILED:\n- " + "\n- ".join(problems))
    sys.exit(1 if problems else 0)
