"""Verification tools for account lookup and passcode check."""

from agents.verification import account, passcode

ACCOUNT_NAME = "account_name"
ACCOUNT_MISSES = "account_misses"
PASSCODE_ATTEMPTS = "passcode_attempts"


def _latest_user_text(flow_manager) -> str:
    messages = flow_manager.get_current_context()
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            return content
    return ""


def _account_miss(flow_manager) -> dict:
    misses = int(flow_manager.state.get(ACCOUNT_MISSES, 0)) + 1
    flow_manager.state[ACCOUNT_MISSES] = misses
    if misses >= 3:
        return {"status": "refused", "outcome": "refused"}
    return {"status": "not_found"}


async def find_account(flow_manager, number: str) -> dict:
    """Look up the account for the caller's phone number.
    Args:
        number: The phone number digits the caller said for the account.
    """
    heard = _latest_user_text(flow_manager)
    if passcode.digits(number) != passcode.digits(heard):
        return _account_miss(flow_manager)
    name = account.find(number)
    if name is None:
        return _account_miss(flow_manager)
    flow_manager.state[ACCOUNT_NAME] = name
    return {"status": "found", "name": name}


async def check_passcode(flow_manager, code: str) -> dict:
    """Check the verification code against what the caller said aloud.
    Args:
        code: The verification code digits the caller said.
    """
    if ACCOUNT_NAME not in flow_manager.state:
        return {"status": "not_ready"}
    heard = _latest_user_text(flow_manager)
    if passcode.attempt(code, heard):
        return {"status": "verified", "outcome": "verified"}
    attempts = int(flow_manager.state.get(PASSCODE_ATTEMPTS, 0)) + 1
    flow_manager.state[PASSCODE_ATTEMPTS] = attempts
    if attempts >= 3:
        return {"status": "refused", "outcome": "refused"}
    return {"status": "mismatch", "attempts": attempts}
