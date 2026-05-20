"""Grader.

Answer extraction is done by an LLM judge upstream (see clients.judge_extract).
This module only compares the extracted answer to the expected one.

Grading rules:
  - 'number'  : parse both sides (decimals, fractions, percentages, latex \\frac),
                compare with absolute tolerance
  - 'string'  : expected appears as a whole word in extracted (case-insensitive)
  - 'choice'  : first letter A-E in extracted matches expected
"""
from __future__ import annotations

import re
from typing import Optional


# --- number parsing ---------------------------------------------------------

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_FRAC_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*/\s*(-?\d+(?:\.\d+)?)\s*$")
_LATEX_FRAC_RE = re.compile(r"\\frac\s*\{\s*(-?\d+(?:\.\d+)?)\s*\}\s*\{\s*(-?\d+(?:\.\d+)?)\s*\}")


def _to_float(s: str) -> Optional[float]:
    """Parse a numeric answer in any of: plain decimal, fraction a/b,
    percentage 16.67%, or latex \\frac{a}{b}. Returns None if unparseable."""
    if s is None:
        return None
    s = s.strip().strip(".,;:`*").strip()
    if not s or s.upper() == "N/A":
        return None

    # Latex \frac{a}{b} -> "a/b" so the fraction parser below handles it.
    m = _LATEX_FRAC_RE.search(s)
    if m:
        s = _LATEX_FRAC_RE.sub(f"{m.group(1)}/{m.group(2)}", s)

    # Strip latex / currency junk.
    s = s.replace("\\$", "").replace("$", "").replace(",", "")

    # Percentage suffix -> remember and strip.
    is_pct = s.rstrip().endswith("%")
    if is_pct:
        s = s.rstrip()[:-1].rstrip()

    # Plain fraction "a/b"
    m = _FRAC_RE.match(s)
    if m:
        try:
            val = float(m.group(1)) / float(m.group(2))
        except (ValueError, ZeroDivisionError):
            return None
        return val / 100 if is_pct else val

    # Fall back to first number anywhere in the string.
    m = _NUM_RE.search(s)
    if m:
        try:
            val = float(m.group(0))
        except ValueError:
            return None
        return val / 100 if is_pct else val

    return None


# --- per-type grading -------------------------------------------------------

def _grade_number(extracted: str, expected: str, tolerance: float) -> bool:
    got, want = _to_float(extracted), _to_float(expected)
    if got is None or want is None:
        return False
    return abs(got - want) <= tolerance


def _grade_string(extracted: str, expected: str) -> bool:
    """Word-boundary, case-insensitive match. 'yes' matches 'Yes, they are independent'."""
    return bool(re.search(rf"\b{re.escape(expected.strip())}\b", extracted, re.IGNORECASE))


def _grade_choice(extracted: str, expected: str) -> bool:
    m = re.search(r"[A-Ea-e]", extracted)
    if not m:
        return False
    return m.group(0).upper() == expected.strip().upper()


# --- public API -------------------------------------------------------------

def grade(extracted: str, expected: str, answer_type: str = "number",
          tolerance: float = 1e-4) -> bool:
    """Compare a (judge-extracted) answer to the expected answer."""
    if extracted is None or extracted.strip() == "" or extracted.strip().upper() == "N/A":
        return False
    if answer_type == "number":
        return _grade_number(extracted, expected, tolerance)
    if answer_type == "choice":
        return _grade_choice(extracted, expected)
    return _grade_string(extracted, expected)
