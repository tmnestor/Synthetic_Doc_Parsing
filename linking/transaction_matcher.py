"""Transaction matching utilities for receipt/invoice to bank statement linking.

Pure functions: parse_amount, normalize_date, description_score.
Cherry-picked from common/transaction_matcher.py.
"""

import re
from datetime import date
from difflib import SequenceMatcher


def parse_amount(value: str) -> float | None:
    """Parse a monetary amount string to float.

    Handles: "$1,234.56", "67.32", "-$50.00", "NOT_FOUND", "".

    Args:
        value: Amount string.

    Returns:
        Float amount, or None if unparseable.
    """
    if not value or value == "NOT_FOUND":
        return None
    cleaned = value.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


_DATE_PATTERNS = [
    (re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"), "dd/mm/yyyy"),
    (re.compile(r"^(\d{2})/(\d{2})/(\d{2})$"), "dd/mm/yy"),
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"), "yyyy-mm-dd"),
    (re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$"), "dd mon yyyy"),
    (re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2})$"), "dd mon yy"),
]

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def normalize_date(value: str) -> date | None:
    """Parse a date string in various Australian formats to a date object.

    Supported formats: DD/MM/YYYY, DD/MM/YY, DD Mon YYYY, DD Mon YY, YYYY-MM-DD.

    Args:
        value: Date string.

    Returns:
        date object, or None if unparseable.
    """
    if not value or value == "NOT_FOUND":
        return None

    value = value.strip()

    for pattern, fmt in _DATE_PATTERNS:
        m = pattern.match(value)
        if not m:
            continue

        try:
            if fmt == "dd/mm/yyyy":
                return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
            if fmt == "dd/mm/yy":
                year = int(m.group(3))
                year = year + 2000 if year < 100 else year
                return date(year, int(m.group(2)), int(m.group(1)))
            if fmt == "yyyy-mm-dd":
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if fmt == "dd mon yyyy":
                month = _MONTHS.get(m.group(2).lower())
                if month:
                    return date(int(m.group(3)), month, int(m.group(1)))
            if fmt == "dd mon yy":
                month = _MONTHS.get(m.group(2).lower())
                if month:
                    year = int(m.group(3))
                    year = year + 2000 if year < 100 else year
                    return date(year, month, int(m.group(1)))
        except ValueError:
            continue

    return None


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip, compress whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def description_score(a: str, b: str) -> float:
    """Score description similarity between 0.0 and 1.0.

    Uses SequenceMatcher on normalized text.

    Args:
        a: First description.
        b: Second description.

    Returns:
        Similarity score between 0.0 and 1.0.
    """
    na = _normalize_text(a)
    nb = _normalize_text(b)
    return SequenceMatcher(None, na, nb).ratio()
