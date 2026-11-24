"""The account on this call. find is the stand-in a later lookup replaces."""

from agents.verification.passcode import digits

KNOWN = {"5550102000": "Jordan"}


def find(spoken: str) -> str | None:
    """The name on the account for these digits, or none."""
    number = "".join(digits(spoken))
    if not number:
        return None
    return KNOWN.get(number)
