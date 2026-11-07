"""The voice agent: a live Claude agent, and a scripted offline agent with the same tools.

Both only speak; the call session (call.py) decides what happens to their words.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from . import config
from .tools import DEFINITIONS, Toolbox

SYSTEM_PROMPT = """\
You are Safety Call, a voice agent phoning a bank customer on the bank's behalf, before a \
likely money shortfall rather than after it. This is a hackathon prototype with synthetic \
data, but play the role sincerely: the person on the line may be worried.

How this call works
- Before you joined, the system played the customer's own safe phrase, so they know the call \
is really from their bank, and the customer approved the call in their banking app. You do not \
know the safe phrase and never need it. If they ask to hear it again, the system handles that.
- Everything you write is spoken aloud by text-to-speech to someone with low vision. Use short, \
plain sentences, at most about four per turn, and one question at a time. No lists, headings, \
symbols or markdown. Say money like "£118" and dates like "24 November".

Facts and figures
- Every number, date, amount, chance or eligibility result you say must come from a tool \
result in this call, used as the tool gave it. If no tool tells you something, say you don't \
know rather than estimating.
- Start by calling get_situation. Say why you are calling in plain words, name the life events \
the bank noticed as things you noticed (not facts you are sure of), and ask whether it is a good \
time to talk and whether you have understood their situation.
- Use check_support and compare_futures before suggesting actions. Describe two futures: what \
happens if nothing changes, and what changes if they act.
- This is guidance and practical help, not regulated financial advice. Eligibility results are \
illustrative checks; the organisation or an adviser decides. Don't tell the customer what they \
must do; offer options and let them choose.

When the customer says something is wrong
- Believe them. Call correct_information with the right field, then call get_situation or \
check_support again and explain what has changed. Never argue for the old figure.

Safety
- Never ask for, or invite, a PIN, password, passcode, card number, security code, one-time code, \
sort code or account number. If they start to share one, stop them kindly.
- If the customer is unsure the call is genuine, agree that checking is wise: tell them to hang \
up and call 159 or the number on the back of their card. Don't try to persuade them.
- You cannot move money, and nothing you say changes their credit: the bank does not use this \
forecast for lending, credit limits or credit scores. Say so if they worry about it.

Doing things for the customer
- You can draft letters and prepare a case file for a free debt adviser. Read the short summary \
of a draft before asking to send it.
- To send, call request_send with the draft ids, then ask one clear yes-or-no question. Nothing \
is sent until the system records their answer, and a system message will tell you what \
happened. Never say something was sent unless a system message confirmed it.
- The adviser case file shares personal information: explain what is in it and ask consent.

Ending
- When they have what they need, sum up what was done and what happens next in two or three \
sentences, remind them they can call the number on their card or 159, and say goodbye.
"""

KICKOFF = "[Call connected. The customer heard their safe phrase and approved the call. Begin.]"


@dataclass
class AgentTurn:
    utterances: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)  # {"name", "input", "result"}
    refused: bool = False


def _speakable(text: str) -> str:
    text = re.sub(r"[*_#`>]+", "", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def _echo_content(content: list) -> list[dict]:
    """Assistant content to append to history, unchanged except for the fallback rule.

    After a server-side fallback, model-internal blocks before the last fallback marker
    are not echoed back (Claude API refusal-fallback semantics).
    """
    blocks = [b.to_dict() if hasattr(b, "to_dict") else dict(b) for b in content]
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=None)
    if last is None:
        return blocks
    keep_types = {"text", "server_tool_use", "web_search_tool_result"}
    return [b for i, b in enumerate(blocks) if i > last or b.get("type") in keep_types]


class LiveAgent:
    """Claude Opus 5.5 with tool use. History is append-only (preserved thinking)."""

    def __init__(
        self,
        toolbox: Toolbox,
        client=None,
        before_send: Callable[[dict], None] | None = None,
        model: str = config.MODEL,
        effort: str = config.EFFORT,
    ):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.toolbox = toolbox
        self.before_send = before_send or (lambda request: None)
        self.model = model
        self.effort = effort
        self.messages: list[dict] = []
        self.tools = [{**d, "strict": True} for d in DEFINITIONS]

    def turn(self, user_text: str, notes: list[str] = ()) -> AgentTurn:
        out = AgentTurn()
        self.messages.append({"role": "user", "content": user_text})
        for note in notes:
            # Operator notes (e.g. "the customer said yes; sent") go in as system messages.
            self.messages.append({"role": "system", "content": note})
        for _ in range(config.MAX_AGENT_STEPS):
            request = {
                "model": self.model,
                "max_tokens": 16000,
                "system": SYSTEM_PROMPT,
                "tools": self.tools,
                "tool_choice": {"type": "auto"},
                "output_config": {"effort": self.effort},
                "messages": self.messages,
                "betas": [config.FALLBACK_BETA],
                "fallbacks": "default",
            }
            self.before_send(request)
            response = self.client.beta.messages.create(**request)
            self.messages.append({"role": "assistant", "content": _echo_content(response.content)})
            texts = [b.text for b in response.content if getattr(b, "type", None) == "text" and b.text.strip()]

            if response.stop_reason == "refusal":
                out.refused = True
                out.utterances.append(
                    "Sorry, I can't help with that on this call. You can call the number on the "
                    "back of your card, or 159, to speak to someone."
                )
                return out
            out.utterances.extend(_speakable(t) for t in texts)
            if response.stop_reason != "tool_use":
                return out

            results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                try:
                    result = self.toolbox.run(block.name, dict(block.input))
                    is_error = "error" in result
                except Exception as e:  # a tool failure is reported to the model, not raised
                    result, is_error = {"error": str(e)}, True
                out.tool_calls.append({"name": block.name, "input": dict(block.input), "result": result})
                shown = {k: v for k, v in result.items() if k != "facts"}
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(shown, default=str),
                        **({"is_error": True} if is_error else {}),
                    }
                )
            self.messages.append({"role": "user", "content": results})
        out.utterances.append("Sorry, that took me too long. Could you say that again?")
        return out


# ---------------------------------------------------------------------------------
# Offline agent: a scripted conversation over the same tools, for demos without Wi-Fi.
# ---------------------------------------------------------------------------------

YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|please do|do it|send|fine|alright|right|correct)\b", re.I)
NO = re.compile(r"\b(no|nope|don'?t|do not|not now|stop|cancel|wait|hold on|later)\b", re.I)
WRONG = re.compile(r"\b(wrong|not right|not true|isn'?t right|incorrect|mistake|actually)\b", re.I)
PARTNER = re.compile(r"\b(partner|husband|wife|boyfriend|girlfriend|mum|mother|dad|father|sister|brother|lodger|flatmate)\b", re.I)
HOURS_BACK = re.compile(r"\b(hours|shifts)\b.*\b(back|normal|returned)\b", re.I)


def yes_no(text: str) -> str:
    """'yes', 'no' or 'unclear'. A refusal word with a yes word is unclear."""
    no, yes = bool(NO.search(text)), bool(YES.search(text))
    if no and re.search(r"\b(yes|yeah|yep|sure|ok|okay)\b", text, re.I):
        return "unclear"
    if no:
        return "no"
    return "yes" if yes else "unclear"


def _pct(p: float) -> str:
    return f"{round(p * 100)}%"


def _day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%B}"


class OfflineAgent:
    """Deterministic script for the demo story. It still uses the tools for every figure."""

    def __init__(self, toolbox: Toolbox):
        self.toolbox = toolbox
        self.stage = "intro"

    def _call(self, out: AgentTurn, name: str, **args) -> dict:
        result = self.toolbox.run(name, args)
        out.tool_calls.append({"name": name, "input": args, "result": result})
        return result

    def turn(self, user_text: str, notes: list[str] = ()) -> AgentTurn:
        out = AgentTurn()
        say = out.utterances.append
        case = self.toolbox.case
        answer = yes_no(user_text)

        # "That's wrong": correct first, wherever we are in the script.
        if self.stage not in ("intro", "ended") and WRONG.search(user_text):
            if PARTNER.search(user_text):
                self._call(out, "correct_information", field="adults_in_home", value="2")
                sup = self._call(out, "check_support")
                fut = self._call(out, "compare_futures", actions=["energy_payment_plan"])
                say(
                    "Thank you for telling me, I've corrected that. With another adult at home, "
                    "the Council Tax single person discount doesn't apply, so I've withdrawn that "
                    "letter. With the energy plan alone, the chance of going below zero within 30 "
                    f"days is {_pct(fut['with_actions']['chance_below_zero_within_30_days'])}. "
                    "Shall I write the energy letter?"
                )
                self.stage = "offer_letters_energy_only"
                return out
            if HOURS_BACK.search(user_text):
                self._call(out, "correct_information", field="hours_back_to_normal", value="yes")
                s = self._call(out, "get_situation")
                say(
                    "That's good news, and I've updated it. With your usual hours, the chance of "
                    "going below zero in the next 30 days is "
                    f"{_pct(s['forecast']['chance_below_zero_within_30_days'])}. "
                    "Would you still like help with anything today?"
                )
                self.stage = "offer_letters"
                return out
            say("Thank you. What did I get wrong? You can tell me in your own words.")
            return out

        if self.stage == "intro":
            s = self._call(out, "get_situation")
            ev = {e["key"]: e for e in s["life_events"]}
            bills = {b["name"]: b for b in s["bills_next_30_days"]}
            f = s["forecast"]
            hours = ""
            if "reduced_hours" in ev:
                hh = case.household.wages
                hours = (
                    f"It looks like your hours have been cut: your last few wages averaged "
                    f"£{hh.recent_weekly:.0f} a week, instead of about £{hh.usual_weekly:.0f}. "
                )
            energy, ct = bills.get("Energy (BrightSpark)"), bills.get("Council Tax")
            say(
                f"Hello {s['customer']['name']}, thanks for picking up. I'm calling before anything "
                f"goes wrong, not after. {hours}On {_day(energy['due'])} your energy bill of "
                f"£{energy['amount']:.0f} and your Council Tax of £{ct['amount']:.0f} both go out. "
                f"Our forecast gives a {_pct(f['chance_below_zero_by_riskiest_day'])} chance your "
                f"balance goes below zero by {_day(f['riskiest_day'])}. Have I got that right, "
                "and is now a good time to talk?"
            )
            self.stage = "permission"
            return out

        if self.stage == "permission":
            if answer == "no":
                say(
                    "No problem. Nothing has changed on your account. You can call the number on "
                    "your card or 159 whenever suits you. Goodbye for now."
                )
                self.stage = "ended"
                return out
            return self._offer(out, ["single_person_discount", "energy_payment_plan"])

        if self.stage in ("offer_letters", "offer_letters_energy_only"):
            if answer == "no":
                say("Okay, I won't write anything.")
                return self._offer_handover(out)
            kinds = ["energy_payment_plan"]
            if self.stage == "offer_letters" and any(
                s.id == "council_tax_single_person_discount" and s.status == "eligible" for s in case.support()
            ):
                kinds.append("council_tax_single_person_discount")
            drafts = [self._call(out, "draft_letter", kind=k) for k in kinds]
            drafts = [d for d in drafts if "draft_id" in d]
            self._call(out, "request_send", draft_ids=[d["draft_id"] for d in drafts])
            say(
                " ".join(f"Letter {i + 1}: {d['summary_to_read_aloud']}" for i, d in enumerate(drafts))
                + (" Shall I send both now? Please say yes or no." if len(drafts) > 1 else " Shall I send it now? Please say yes or no.")
            )
            self.stage = "await_letters"
            return out

        if self.stage == "await_letters":
            if case.pending:
                say("Sorry, I need a clear yes or no before I send anything. Shall I send them?")
                return out
            if case.sent:
                say("Done, they're sent. You'll get replies by phone or in large print.")
            else:
                say("Okay, nothing has been sent.")
            return self._offer_handover(out)

        if self.stage == "offer_handover":
            if answer == "no":
                return self._wrap_up(out)
            h = self._call(out, "prepare_adviser_handover")
            self._call(out, "request_send", draft_ids=[h["draft_id"]])
            say(
                f"I've prepared a case file. {h['summary_to_read_aloud']} It shares your personal "
                "information with the adviser. Is it okay to share it? Yes or no."
            )
            self.stage = "await_handover"
            return out

        if self.stage == "await_handover":
            if case.pending:
                say("Sorry, I need a clear yes or no. Can I share your case file with the adviser?")
                return out
            shared = any(d.kind == "adviser_handover" for d in case.sent)
            say("Thank you, it's shared. In this demo the adviser's call is simulated." if shared else "Okay, I won't share it.")
            return self._wrap_up(out)

        say(
            "Thanks for your time. If you need us, call the number on the back of your card, or "
            "159. Take care, goodbye."
        )
        self.stage = "ended"
        return out

    def _offer(self, out: AgentTurn, actions: list[str]) -> AgentTurn:
        sup = self._call(out, "check_support")
        items = {s["id"]: s for s in sup["support"]}
        fut = self._call(out, "compare_futures", actions=actions)
        b, a = fut["do_nothing"], fut["with_actions"]
        parts = [
            f"If nothing changes, there's a {_pct(b['chance_below_zero_by_riskiest_day'])} chance "
            f"you go below zero by {_day(fut['riskiest_day'])}.",
            "Two things could help.",
        ]
        spd = items.get("council_tax_single_person_discount")
        if spd and spd["status"] == "eligible":
            parts.append(
                f"You live with your child only, so you can get 25% off Council Tax: "
                f"£{spd['monthly_effect']:.2f} less a month."
            )
        plan = items.get("energy_payment_plan")
        if plan:
            parts.append(f"And your energy supplier must consider what you can afford. {plan['summary']}")
        parts.append(
            f"With both, that chance drops to {_pct(a['chance_below_zero_by_riskiest_day'])}. "
            "This is guidance, and the council and supplier decide. Shall I write both letters for you?"
        )
        out.utterances.append(" ".join(parts))
        self.stage = "offer_letters"
        return out

    def _offer_handover(self, out: AgentTurn) -> AgentTurn:
        debt = self.toolbox.case.persona["debts"][0]
        out.utterances.append(
            f"One more thing. Your {debt['creditor']} account has {debt['missed_payments']} missed "
            f"payments and £{debt['balance']:.0f} owing. A free debt adviser can check Universal "
            "Credit and Breathing Space with you. Shall I pass them your case, so you don't have "
            "to explain everything again?"
        )
        self.stage = "offer_handover"
        return out

    def _wrap_up(self, out: AgentTurn) -> AgentTurn:
        case = self.toolbox.case
        after = case.after or case.forecast()
        letters_to = [d.recipient for d in case.sent if d.kind != "adviser_handover"]
        done = f"we sent your letters to {' and '.join(letters_to)}" if letters_to else "we didn't send any letters"
        if any(d.kind == "adviser_handover" for d in case.sent):
            done += ", and shared your case file with a debt adviser"
        out.utterances.append(
            f"To sum up: {done}. With what we've done, the chance of going below "
            f"zero in the next 30 days is {_pct(after.prob_shortfall_within(30))}. This forecast is "
            "only used to help you, never for lending or your credit score. If you need us, call "
            "the number on your card or 159. Take care, goodbye."
        )
        self.stage = "ended"
        return out
