import pytest

from safety_call import guard
from safety_call.case import Case, World
from safety_call.firewall import BLOCKED_PURPOSES, PurposeRefused

BLOCK = [
    "To confirm it's you, what's your PIN?",
    "Could you read me the long number on your card?",
    "Please tell me the code we just texted you.",
    "Can you confirm your sort code and account number?",
    "I just need your online banking password to fix this.",
    "Can you tell me your PIN, not your password?",
    "Your card number is 4111 1111 1111 1111, is that right?",
    "Please give me your memorable word.",
]
ALLOW = [
    "We will never ask for your PIN, passwords, card details or any codes.",
    "Please don't share your PIN with anyone.",
    "Your energy bill of £118 goes out on 24 November.",
    "I won't need your password or any security code.",
    "Shall I send both letters now? Please say yes or no.",
]


@pytest.mark.parametrize("text", BLOCK)
def test_guard_blocks_credential_requests(text):
    verdict = guard.check_utterance(text)
    assert not verdict.allowed
    assert verdict.text == guard.SAFE_REPLACEMENT


@pytest.mark.parametrize("text", ALLOW)
def test_guard_allows_safe_speech(text):
    assert guard.check_utterance(text).allowed


def test_safe_phrase_is_redacted_from_customer_speech():
    text, changed = guard.redact_secret("Is it Blue  kettle, on sunday?", "blue kettle on Sunday")
    assert changed and "kettle" not in text.lower()


def test_secret_check_raises_on_any_request_containing_the_phrase():
    with pytest.raises(guard.SecretLeak):
        guard.assert_no_secret({"messages": [{"content": "my phrase is blue kettle on sunday"}]}, "blue kettle on Sunday")
    guard.assert_no_secret({"messages": [{"content": "blue sky"}]}, "blue kettle on Sunday")


@pytest.mark.parametrize("purpose", list(BLOCKED_PURPOSES))
def test_firewall_refuses_every_non_support_use(purpose):
    case = Case(World.demo())
    with pytest.raises(PurposeRefused):
        case.forecast(purpose)
    assert case.store.audit[-1] == {**case.store.audit[-1], "purpose": purpose, "allowed": False}


def test_firewall_allows_support():
    case = Case(World.demo())
    assert case.forecast("customer_support").prob_shortfall_within(30) > 0
