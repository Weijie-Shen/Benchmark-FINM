"""Question loading + structural validation.

Public entry: `load_questions(path)` — load from a single JSON file OR a
directory of `*.json` files.

A question must have `id`, `question`, `answer`. If `answer_type == "open"`,
the `answer` field is a structured rubric dict — see `_validate_rubric` for
the schema constraints.
"""
from __future__ import annotations

import json
from pathlib import Path


REQUIRED_QUESTION_FIELDS = ("id", "question", "answer")
_POINT_EPS = 1e-6   # tolerance for the float-sum check on rubric points


def load_questions(path: Path) -> list[dict]:
    """Load questions from a single JSON file OR a directory of `*.json` files.

    Directory mode concatenates every `*.json` in the directory and prints a
    per-file summary. File mode loads just that file (useful when iterating
    on one category).
    """
    if path.is_dir():
        files = sorted(path.glob("*.json"))
        if not files:
            raise FileNotFoundError(f"No JSON files in directory {path}")
        qs: list = []
        summary = []
        for f in files:
            with open(f) as fh:
                chunk = json.load(fh)
            if not isinstance(chunk, list):
                raise ValueError(f"{f}: top-level must be a JSON array")
            qs.extend(chunk)
            summary.append(f"{f.name}={len(chunk)}")
        _validate(qs)
        print(f"[runner] loaded {len(qs)} questions from {len(files)} files "
              f"({', '.join(summary)})")
        return qs

    with open(path) as f:
        qs = json.load(f)
    _validate(qs)
    return qs


def _validate(qs) -> None:
    """Empty list is allowed (so an empty per-category file loads cleanly
    until populated). Open-type questions must carry a well-formed rubric."""
    if not isinstance(qs, list):
        raise ValueError("questions JSON top-level must be an array")
    seen = set()
    for i, q in enumerate(qs):
        missing = [k for k in REQUIRED_QUESTION_FIELDS if k not in q]
        if missing:
            raise ValueError(f"question #{i}: missing fields {missing}")
        if q["id"] in seen:
            raise ValueError(f"duplicate question id: {q['id']}")
        seen.add(q["id"])
        if q.get("answer_type") == "open":
            _validate_rubric(q["answer"], q["id"])


def _validate_rubric(rubric, qid: str) -> None:
    """A rubric must have `total_points` and `categories`; points must add
    up category-by-category, and criterion ids must be unique within the
    question. Points may be floats (0.5 increments are OK)."""
    if not isinstance(rubric, dict):
        raise ValueError(f"{qid}: open-type answer must be a JSON object "
                         f"(rubric), got {type(rubric).__name__}")
    if "total_points" not in rubric or "categories" not in rubric:
        raise ValueError(f"{qid}: rubric missing 'total_points' or 'categories'")

    total = float(rubric["total_points"])
    cat_sum = 0.0
    seen_crit = set()
    for cat in rubric["categories"]:
        for k in ("name", "max_points", "criteria"):
            if k not in cat:
                raise ValueError(f"{qid}: category missing '{k}'")
        cat_max = float(cat["max_points"])
        cat_sum += cat_max
        crit_sum = 0.0
        for cr in cat["criteria"]:
            for k in ("id", "name", "points", "description"):
                if k not in cr:
                    raise ValueError(f"{qid}: criterion missing '{k}'")
            if cr["id"] in seen_crit:
                raise ValueError(f"{qid}: duplicate criterion id '{cr['id']}'")
            seen_crit.add(cr["id"])
            crit_sum += float(cr["points"])
        if abs(crit_sum - cat_max) > _POINT_EPS:
            raise ValueError(
                f"{qid}: category '{cat['name']}' max_points={cat_max} but "
                f"criteria sum to {crit_sum}")
    if abs(cat_sum - total) > _POINT_EPS:
        raise ValueError(
            f"{qid}: total_points={total} but categories sum to {cat_sum}")
