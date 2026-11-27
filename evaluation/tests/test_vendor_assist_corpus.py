"""고정 전체 평가셋 제약. 최종 corpus 검증 때 실행하며 dev-only 검사와 분리한다."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from evaluation.vendor_assist import make_dataset
from evaluation.vendor_assist.metrics import FIELDS, load_cases, validate_corpus

DATA = Path(__file__).resolve().parents[1] / "vendor_assist"


@pytest.fixture(scope="module")
def corpus():
    return load_cases(DATA / "dev.jsonl"), load_cases(DATA / "heldout.jsonl")


def test_corpus_has_144_distinct_cases_2_to_1_per_form(corpus):
    dev, heldout = corpus
    assert validate_corpus(dev, heldout) == {
        "total": 144,
        "dev": 96,
        "heldout": 48,
        "per_form": {"period": 36, "quote": 36, "dates": 36, "extra": 36},
    }
    for form in FIELDS:
        assert sum(x["form"] == form for x in dev) == 24
        assert sum(x["form"] == form for x in heldout) == 12
    assert len({case["text"] for case in [*dev, *heldout]}) == 144


def test_all_kinds_and_critical_corrections_covered(corpus):
    cases = [*corpus[0], *corpus[1]]
    kinds = Counter(case["kind"] for case in cases)
    assert len(kinds) == 12
    assert set(kinds.values()) == {12}
    tags = {tag for case in cases for tag in case["tags"]}
    assert {
        "amount_correction",
        "duration_correction",
        "vat_negation",
        "double_negation",
        "multi_date_exclusion",
        "previous_correction",
        "no_action",
        "clear",
    } <= tags
    assert all(
        case["human_verified"] is False and case["reviewed_by"] is None
        for case in cases
    )
    assert all(set(case["expected"]) == set(FIELDS[case["form"]]) for case in cases)


def test_previous_candidate_has_complete_original_fields_and_evidence(corpus):
    previous = [x for x in [*corpus[0], *corpus[1]] if x["previous_candidate"]]
    assert len(previous) >= 24
    for case in previous:
        first = case["previous_candidate"]
        assert len(first["fields"]) == len(FIELDS[case["form"]])
        for field in first["fields"]:
            if field["action"] == "set":
                assert field["evidence"] in first["text"]


def test_generator_is_deterministic_and_files_match():
    generated = make_dataset.build()
    for split in ("dev", "heldout"):
        expected = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in generated[split]
        )
        assert (DATA / f"{split}.jsonl").read_text(encoding="utf-8") == expected
