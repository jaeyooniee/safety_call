"""Settings, all overridable with environment variables."""

from __future__ import annotations

import os

PRODUCT_NAME = "Safety Call"

# Claude API
MODEL = os.environ.get("SAFETY_CALL_MODEL", "claude-opus-5-5")
# A phone call needs quick replies: low effort keeps thinking short.
EFFORT = os.environ.get("SAFETY_CALL_EFFORT", "low")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_AGENT_STEPS = 6  # model calls per customer turn (tool loop)

# Forecast
HORIZON_DAYS = 90
PATHS = 2000
SEED = 7
TRIGGER_PROBABILITY = 0.5  # place a help call when 30-day shortfall risk is at least this

# Reverse authentication: the phrase the customer chose at enrolment (demo default).
DEFAULT_SAFE_PHRASE = "blue kettle on Sunday"


def offline_mode() -> bool:
    """Scripted agent instead of the Claude API.

    SAFETY_CALL_OFFLINE=1 forces it on, =0 forces it off. Without the variable, the
    live agent is used only when ANTHROPIC_API_KEY is set.
    """
    flag = os.environ.get("SAFETY_CALL_OFFLINE")
    if flag is not None:
        return flag.strip().lower() not in ("0", "false", "no", "off", "")
    return not os.environ.get("ANTHROPIC_API_KEY")
