"""Blood-type compatibility rules for red blood cell transfusion.

This module is the single source of truth for ABO/Rh compatibility.
Both the search view and the test suite import from here, so the rules
are never duplicated.

Direction of the mapping is: recipient -> donor blood types the
recipient can safely receive from.
"""

# All 8 blood types, in display order.
ALL_BLOOD_TYPES = [
    "A+",
    "A-",
    "B+",
    "B-",
    "AB+",
    "AB-",
    "O+",
    "O-",
]

# Recipient blood type -> compatible donor blood types.
COMPATIBILITY = {
    # A+ plasma carries anti-B; Rh+ recipient may receive Rh+ or Rh-.
    "A+": ["A+", "A-", "O+", "O-"],
    # A- has anti-B and anti-Rh, so Rh- donors only.
    "A-": ["A-", "O-"],
    "B+": ["B+", "B-", "O+", "O-"],
    "B-": ["B-", "O-"],
    # AB+ has no antibodies: universal recipient.
    "AB+": ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"],
    # AB- has no anti-A/anti-B, but does have anti-Rh.
    "AB-": ["AB-", "A-", "B-", "O-"],
    "O+": ["O+", "O-"],
    # O- has anti-A, anti-B and anti-Rh: universal donor, Rh- only.
    "O-": ["O-"],
}


def compatible_donor_types(recipient_type):
    """Return the donor blood types compatible with ``recipient_type``.

    Unknown or empty input returns an empty list, so an unmapped value
    can never silently widen a search to every donor.
    """
    if not recipient_type:
        return []

    return COMPATIBILITY.get(recipient_type, [])
