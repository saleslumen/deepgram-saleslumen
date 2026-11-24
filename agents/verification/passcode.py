"""The verification code. The read-back is the only caller."""

import re

CODE = ("1", "2", "3")
WORDS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def digits(text: str) -> tuple[str, ...]:
    """The code digits in the caller's words, in order."""
    found: list[str] = []
    for token in re.findall(r"[a-z]+|\d+", text.lower()):
        if token.isdigit():
            found.extend(token)
            continue
        digit = WORDS.get(token)
        if digit is not None:
            found.append(digit)
    return tuple(found)


def accepts(text: str) -> bool:
    """The spoken words are the code one, two, three."""
    return digits(text) == CODE


def attempt(spoken: str, heard: str) -> bool:
    """The tool argument and the caller's own words both match the code."""
    return accepts(spoken) and accepts(heard)
