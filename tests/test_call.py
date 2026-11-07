"""The call flow: reverse authentication, consent, corrections, and the live agent's requests."""

import json
from types import SimpleNamespace

from safety_call import config, demo
from safety_call.call import CallSession
from safety_call.case import World

PHRASE = config.DEFAULT_SAFE_PHRASE


def started(offline=True, client=None):
    call = CallSession(World.demo(), PHRASE, offline=offline, client=client)
    call.answer()
    call.approve()
    return call


def test_offline_demo_story_passes_all_checks():
    call = demo.run(offline=True, verbose=False)
    assert demo.check(call) == []


def test_call_opens_with_safe_phrase_before_the_agent():
    call = CallSession(World.demo(), PHRASE, offline=True)
    events = call.answer()
    assert events[0]["type"] == "system_say" and PHRASE in events[0]["text"]
    assert "159" in events[1]["text"]
    assert events[-1]["type"] == "approval_needed"
    # The agent says nothing until the customer approves in the app.
    assert not any(e["type"] == "agent_say" for e in events)
    assert not any(e["type"] == "agent_say" for e in call.say("hello"))


def test_nothing_is_sent_without_a_clear_yes():
    call = started()
    call.say("Yes, I can talk.")
    call.say("Yes, write them.")
    assert len(call.case.pending) == 2
    call.say("Hmm, what did you say?")
    assert len(call.case.pending) == 2 and not call.case.sent
    call.say("No, don't send them.")
    assert not call.case.sent
    assert {d.status for d in call.case.drafts.values()} == {"cancelled"}


def test_button_decision_sends():
    call = started()
    call.say("Yes.")
    call.say("Yes please.")
    call.decide(True)
    assert {d.kind for d in call.case.sent} == {"energy_payment_plan", "council_tax_single_person_discount"}


def test_customer_correction_mid_call():
    call = started()
    call.say("Yes, go on.")
    events = call.say("That's wrong, actually I live with my partner.")
    text = " ".join(e["text"] for e in events if e["type"] == "agent_say")
    assert "corrected" in text
    assert call.case.persona["adults_in_home"] == 2
    assert call.case.corrections[0]["field"] == "adults_in_home"
    call.say("Yes, write it.")
    kinds = {d.kind for d in call.case.drafts.values() if d.status == "awaiting_confirmation"}
    assert kinds == {"energy_payment_plan"}  # no Council Tax discount letter any more


def test_repeat_phrase_is_answered_by_the_system_not_the_agent():
    call = started()
    events = call.say("Sorry, can you say my safe phrase again?")
    assert [e["type"] for e in events] == ["customer_say", "system_say"]
    assert PHRASE in events[1]["text"]


# ---- live agent, against a fake Claude client ------------------------------------


class Block(SimpleNamespace):
    def to_dict(self):
        return dict(self.__dict__)


def response(stop_reason, *blocks):
    return SimpleNamespace(stop_reason=stop_reason, content=list(blocks))


class FakeClient:
    """Plays back scripted responses and records every request body."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(json.loads(json.dumps(request, default=lambda o: getattr(o, "__dict__", str(o)))))
        return self.script.pop(0)


def test_live_agent_requests_never_contain_the_safe_phrase_and_use_opus_settings():
    client = FakeClient(
        [
            response("tool_use", Block(type="thinking", thinking="", signature="sig"), Block(type="tool_use", id="t1", name="get_situation", input={})),
            response("end_turn", Block(type="text", text="Hello Amira. Our forecast gives a 58% chance of going below zero by 26 November.")),
            response("end_turn", Block(type="text", text="Thanks, that's noted.")),
        ]
    )
    call = started(offline=False, client=client)
    # The customer says the phrase out loud: it must be removed before the model sees it.
    call.say("Is my phrase blue kettle on Sunday?")
    assert len(client.requests) == 3 and call.requests_checked == 3
    for req in client.requests:
        blob = json.dumps(req).lower()
        assert "kettle" not in blob
        assert req["model"] == "claude-opus-5-5"
        assert "thinking" not in req  # cannot be disabled on Opus 5.5; effort is the control
        assert req["output_config"] == {"effort": config.EFFORT}
        assert req["tool_choice"] == {"type": "auto"}
        assert req["fallbacks"] == "default" and req["betas"] == [config.FALLBACK_BETA]
        assert all(t["strict"] for t in req["tools"])
    # History is append-only, and the thinking block is echoed back unchanged.
    second = client.requests[1]["messages"]
    assert second[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert second[2]["content"][0]["type"] == "tool_result"
    assert client.requests[2]["messages"][: len(second)] == second


def test_live_agent_figures_are_traced_and_credential_requests_blocked():
    client = FakeClient(
        [
            response("tool_use", Block(type="tool_use", id="t1", name="get_situation", input={})),
            response("end_turn", Block(type="text", text="There's a 58% chance by 26 November. What's your PIN?")),
        ]
    )
    call = CallSession(World.demo(), PHRASE, offline=False, client=client)
    call.answer()
    events = call.approve()
    assert any(e["type"] == "guard_block" for e in events)
    spoken = [e for e in events if e["type"] == "agent_say"][0]
    assert "PIN" in spoken["text"] and "never" in spoken["text"]  # the safe replacement


def test_live_agent_confirmation_note_is_a_system_message():
    client = FakeClient(
        [
            response("end_turn", Block(type="text", text="Hello.")),
            response(
                "tool_use",
                Block(type="tool_use", id="t1", name="draft_letter", input={"kind": "energy_payment_plan"}),
            ),
            response("tool_use", Block(type="tool_use", id="t2", name="request_send", input={"draft_ids": ["d1"]})),
            response("end_turn", Block(type="text", text="Shall I send it? Yes or no.")),
            response("end_turn", Block(type="text", text="It's sent.")),
        ]
    )
    call = started(offline=False, client=client)
    call.say("Please write to my energy company.")
    assert [d.status for d in call.case.drafts.values()] == ["awaiting_confirmation"]
    call.say("Yes, send it.")
    assert [d.status for d in call.case.drafts.values()] == ["sent"]
    last = client.requests[-1]["messages"]
    assert last[-2] == {"role": "user", "content": "Yes, send it."}
    assert last[-1]["role"] == "system" and "said yes" in last[-1]["content"]


def test_refusal_is_handled():
    client = FakeClient([response("refusal")])
    call = CallSession(World.demo(), PHRASE, offline=False, client=client)
    call.answer()
    events = call.approve()
    assert "159" in [e for e in events if e["type"] == "agent_say"][0]["text"]
