"""Reverse-authentication safeguards, enforced in code rather than in the prompt.

1. The customer's safe phrase never reaches the language model: requests are
   checked before they are sent, and anything the customer says is redacted.
2. Every agent utterance is checked before it is spoken: a request for a PIN,
   password, card number, security code or bank details is blocked.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

CREDENTIAL = re.compile(
    r"\b("
    r"pin|pin number|password|passcode|pass code|passphrase for (your )?(online )?banking|"
    r"security code|cvv|cvc|card number|long number on (your|the) card|card details|"
    r"(one[- ]time|verification|security|authori[sz]ation|login|log-in) code|otp|"
    r"code (we|i) (just )?(sent|texted)|sort code|account number|bank details|"
    r"memorable (word|information|info|answer)|online banking (login|log-in|details)"
    r")\b",
    re.IGNORECASE,
)
REQUEST = re.compile(
    r"(\?|\b(tell|give|read|share|confirm|provide|type|enter|send|say|spell)\b|"
    r"\bwhat('s| is)\b|\bneed\b|\bcould you\b|\bcan you\b|\bplease\b)",
    re.IGNORECASE,
)
NEGATION = re.compile(r"\b(never|not|no one|nobody|don'?t|won'?t|do not|will not|shouldn'?t)\b|n't\b", re.IGNORECASE)
CARD_LIKE = re.compile(r"\b(?:\d[ -]?){12,19}\b")
SORT_CODE = re.compile(r"\b\d{2}-\d{2}-\d{2}\b")

SAFE_REPLACEMENT = (
    "Sorry, let me correct that. I will never ask for your PIN, passwords, card details "
    "or any codes, and you should never share them on a call."
)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    text: str  # what may be spoken
    reason: str | None = None


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def check_utterance(text: str) -> Verdict:
    """Block an agent utterance that asks for credentials or bank details."""
    for sentence in sentences(text):
        if CARD_LIKE.search(sentence) or SORT_CODE.search(sentence):
            return Verdict(False, SAFE_REPLACEMENT, "number that looks like card or bank details")
        if not REQUEST.search(sentence):
            continue
        for match in CREDENTIAL.finditer(sentence):
            # "never ask for your PIN" is fine; the negation has to come just before the term.
            before = sentence[max(0, match.start() - 45) : match.start()]
            if not NEGATION.search(before):
                return Verdict(False, SAFE_REPLACEMENT, f"asked for credentials ({match.group(0)})")
    return Verdict(True, text)


def _phrase_pattern(phrase: str) -> re.Pattern:
    words = re.findall(r"\w+", phrase.lower())
    return re.compile(r"\b" + r"\W+".join(map(re.escape, words)) + r"\b", re.IGNORECASE)


def redact_secret(text: str, secret: str) -> tuple[str, bool]:
    """Remove the safe phrase from something the customer said."""
    if not secret:
        return text, False
    redacted, n = _phrase_pattern(secret).subn("[safe phrase removed]", text)
    return redacted, n > 0


class SecretLeak(RuntimeError):
    pass


def assert_no_secret(payload: object, secret: str) -> None:
    """Raise if the safe phrase appears anywhere in a request about to leave the system."""
    if not secret:
        return
    blob = json.dumps(payload, default=lambda o: getattr(o, "__dict__", str(o)), ensure_ascii=False)
    if _phrase_pattern(secret).search(blob):
        raise SecretLeak("the safe phrase was about to be sent to the language model")
