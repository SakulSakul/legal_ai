"""
test_freshness.py — Stage 1 의존성 freshness 비교 검증 (key-free·mocked MCP)

순수 비교 로직(freshness_util)만 검증. live 시행일은 mock으로 주입(네트워크·키 없음).
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import freshness_util as F  # noqa: E402


# ── 조문 단위 판정 ──────────────────────────────────────
def test_target_ok_when_equal():
    assert F.check_target("2026-01-01", "2026-01-01") == F.OK


def test_target_stale_when_live_newer():
    assert F.check_target("2026-01-01", "2026-07-01") == F.STALE


def test_target_needs_review_when_live_missing():
    assert F.check_target("2026-01-01", None) == F.NEEDS_REVIEW


def test_target_needs_review_when_stored_in_future():
    """저장>live 이상치 → STALE로 단정하지 않고 NEEDS_REVIEW."""
    assert F.check_target("2026-07-01", "2026-01-01") == F.NEEDS_REVIEW


# ── 블록 AND 판정 ───────────────────────────────────────
def _bonded():
    return {
        "id": "bonded_store_notice", "title": "보세판매장고시 위반",
        "dependencies": [
            {"name": "관세법", "type": "법률", "articles": [
                {"no": "제178조", "effective_date": "2026-01-01"},
                {"no": "제235조", "effective_date": "2026-01-01"}]},
            {"name": "관세법 시행규칙", "type": "법규명령", "articles": [
                {"no": "제69조의5", "effective_date": "2026-04-01"}]},
            {"name": "보세판매장 특허 및 운영에 관한 고시", "type": "행정규칙",
             "ref": "관세청고시 2025-44호", "rule_effective_date": "2025-09-01",
             "articles": [{"no": "제52조"}]},
        ],
    }


_FRESH_LIVE = {
    ("관세법", "제178조"): "2026-01-01",
    ("관세법", "제235조"): "2026-01-01",
    ("관세법 시행규칙", "제69조의5"): "2026-04-01",
    ("보세판매장 특허 및 운영에 관한 고시", None): "2025-09-01",
}


def test_block_fresh_when_all_match():
    r = F.check_block_freshness(_bonded(), dict(_FRESH_LIVE))
    assert r["verdict"] == F.FRESH
    assert len(r["targets"]) == 4


def test_block_stale_when_any_article_amended():
    live = dict(_FRESH_LIVE)
    live[("관세법", "제178조")] = "2026-09-01"  # 개정 감지
    r = F.check_block_freshness(_bonded(), live)
    assert r["verdict"] == F.STALE
    bad = [t for t in r["targets"] if t["verdict"] == F.STALE]
    assert bad and bad[0]["no"] == "제178조" and bad[0]["stored"] == "2026-01-01" and bad[0]["live"] == "2026-09-01"


def test_block_needs_review_when_live_missing_no_stale():
    live = dict(_FRESH_LIVE)
    live[("관세법 시행규칙", "제69조의5")] = None  # 조회 실패
    r = F.check_block_freshness(_bonded(), live)
    assert r["verdict"] == F.NEEDS_REVIEW


def test_stale_outranks_needs_review():
    live = dict(_FRESH_LIVE)
    live[("관세법", "제178조")] = "2027-01-01"   # STALE
    live[("관세법 시행규칙", "제69조의5")] = None  # NEEDS_REVIEW
    assert F.check_block_freshness(_bonded(), live)["verdict"] == F.STALE


def test_uncovered_when_no_dependencies():
    r = F.check_block_freshness({"id": "x", "title": "t"}, {})
    assert r["verdict"] == F.UNCOVERED


# ── 고시 rule-level 타겟 ────────────────────────────────
def test_admin_rule_target_is_rule_level():
    targets = list(F.iter_targets(_bonded()["dependencies"]))
    rule = [t for t in targets if t["dep"].startswith("보세판매장")]
    assert len(rule) == 1 and rule[0]["no"] is None and rule[0]["stored"] == "2025-09-01"


# ── sweep (mock resolve_fn) ─────────────────────────────
def test_sweep_bonded_real_others_uncovered():
    blocks = [
        _bonded(),
        {"id": "trademark_230", "title": "상표법 위반"},   # dependencies 없음
        {"id": "criminal_fraud_347", "title": "형법 사기죄"},
    ]
    calls = []

    def resolve(dep, no, dtype, ref):
        calls.append((dep, no, dtype))
        return _FRESH_LIVE.get((dep, no))

    report = F.sweep(blocks, resolve)
    by = {r["block"]: r for r in report}
    assert by["bonded_store_notice"]["verdict"] == F.FRESH
    assert by["trademark_230"]["verdict"] == F.UNCOVERED
    assert by["criminal_fraud_347"]["verdict"] == F.UNCOVERED
    # UNCOVERED 블록은 resolve 호출 안 함(의존성 없음)
    assert all(c[0] in ("관세법", "관세법 시행규칙", "보세판매장 특허 및 운영에 관한 고시") for c in calls)


def test_sweep_never_marks_uncovered_as_fresh():
    report = F.sweep([{"id": "x", "title": "t"}], lambda *a: "2026-01-01")
    assert report[0]["verdict"] == F.UNCOVERED  # 거짓 안심 방지


# ── bonded 실제 스키마가 freshness_util 입력으로 파싱 ───
def test_real_bonded_schema_parses():
    db = json.load(open(os.path.join(ROOT, "legal_blocks.json"), encoding="utf-8"))
    bonded = next(i for i in db["모조품"]["issues"] if i["id"] == "bonded_store_notice")
    targets = list(F.iter_targets(bonded["dependencies"]))
    # 관세법 제178·235조 + 시행규칙 제69조의5 + 고시(rule) = 4개 타겟
    assert len(targets) == 4
    keys = {(t["dep"], t["no"]) for t in targets}
    assert ("관세법", "제178조") in keys and ("관세법 시행규칙", "제69조의5") in keys


# ── 격리 ────────────────────────────────────────────────
def test_freshness_util_is_pure():
    src = open(os.path.join(ROOT, "freshness_util.py"), encoding="utf-8").read()
    for forbidden in ("import requests", "import streamlit", "get_secret", "http", "call_mcp", "call_kpd"):
        assert forbidden not in src, f"freshness_util은 순수여야 함: '{forbidden}'"
