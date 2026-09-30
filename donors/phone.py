"""Nigerian mobile number helpers, shared by the forms and the model."""

import re

# 0 or 234, then a network prefix starting 7/8/9 with 0/1 next, then 8 digits.
_NG_MOBILE = re.compile(r"^(?:234|0)([789][01]\d{8})$")


def normalize_ng_phone(raw):
    """Return the number in local format (08031234567), or None if invalid.

    Accepts 08031234567, 0803 123 4567, 2348031234567, +234 803 123 4567
    and +234 (0)803 123 4567. The local format is what gets stored, and it
    is also the login username, so the same person always ends up with the
    same value however they type it.
    """
    digits = re.sub(r"\D", "", raw or "")

    # "+234 (0)803..." -> drop the redundant trunk zero.
    if digits.startswith("2340"):
        digits = "234" + digits[4:]

    match = _NG_MOBILE.match(digits)

    if not match:
        return None

    return "0" + match.group(1)