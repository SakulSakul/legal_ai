"""
test_kpd_mcp_client.py — K Public Data MCP 파서/해석 검증 (응답 mock·key-free)

네트워크·키·streamlit 일절 미사용. MCP 응답은 mock 텍스트로 주입.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import kpd_mcp as K  # noqa: E402


# ── 조문번호 → 6자리 ────────────────────────────────────
def test_article_to_6digit():
    assert K.article_to_6digit("제178조") == "017800"
    assert K.article_to_6digit("제235조") == "023500"
    assert K.article_to_6digit("제69조의5") == "006905"
    assert K.article_to_6digit("제16조의2") == "001602"
    assert K.article_to_6digit("조문아님") is None


# ── search_laws: 정확 매칭 ──────────────────────────────
_SEARCH_MULTI = json.dumps({"results": [
    {"law_name": "관세법", "law_id": 1556, "law_type": "법률", "effective_date": "2026-01-01"},
    {"law_name": "관세법 시행령", "law_id": 9001, "law_type": "대통령령", "effective_date": "2026-02-01"},
    {"law_name": "관세법 시행규칙", "law_id": 9002, "law_type": "총리령", "effective_date": "2026-04-01"},
]}, ensure_ascii=False)


def test_search_laws_exact_match_picks_correct_law_id():
    r = K.parse_search_laws(_SEARCH_MULTI, "관세법", law_type="법률")
    assert r["ok"] and r["law_id"] == 1556 and r["effective_date"] == "2026-01-01"


def test_search_laws_zero_match_fails():
    r = K.parse_search_laws(_SEARCH_MULTI, "상표법")
    assert not r["ok"] and r["law_id"] is None


def test_search_laws_ambiguous_multi_fails():
    raw = json.dumps({"results": [
        {"law_name": "관세법", "law_id": 1, "law_type": "법률"},
        {"law_name": "관세법", "law_id": 2, "law_type": "법률"},
    ]}, ensure_ascii=False)
    r = K.parse_search_laws(raw, "관세법", law_type="법률")
    assert not r["ok"], "동명 다건은 오선택 방지를 위해 실패해야 함"


def test_search_laws_bad_json_fails():
    r = K.parse_search_laws("not json at all", "관세법")
    assert not r["ok"]


# ── get_law_article_sub: 시행일 ─────────────────────────
def test_article_sub_effective_date():
    raw = json.dumps({"results": [{"article": "017800", "시행일자": "2026-01-01"}]}, ensure_ascii=False)
    r = K.parse_article_sub(raw)
    assert r["ok"] and r["effective_date"] == "2026-01-01"


def test_article_sub_text_fallback():
    r = K.parse_article_sub("제178조(반입정지 등과 특허의 취소) 시행일 2026.01.01")
    assert r["ok"] and r["effective_date"] == "2026-01-01"


def test_article_sub_no_date_fails():
    r = K.parse_article_sub(json.dumps({"results": [{"article": "017800"}]}))
    assert not r["ok"]


# ── search_admin_rules: 발령/시행일 ─────────────────────
def test_admin_rules_parse():
    raw = json.dumps({"admin_rules": [
        {"admrul_nm": "보세판매장 특허 및 운영에 관한 고시", "admrul_id": 5544, "시행일자": "2025-09-01"},
    ]}, ensure_ascii=False)
    r = K.parse_admin_rules(raw, exact_name="보세판매장 특허 및 운영에 관한 고시")
    assert r["ok"] and r["admrul_id"] == 5544 and r["effective_date"] == "2025-09-01"


def test_admin_rules_ambiguous_fails():
    raw = json.dumps({"admin_rules": [
        {"admrul_nm": "A고시", "admrul_id": 1}, {"admrul_nm": "B고시", "admrul_id": 2},
    ]}, ensure_ascii=False)
    r = K.parse_admin_rules(raw, exact_name="C고시")
    assert not r["ok"]


# ── 통합 흐름 (mock call_fn) ────────────────────────────
def test_resolve_law_effective_date_flow():
    calls = []

    def mock_call(action, **params):
        calls.append((action, params))
        if action == "search_laws":
            return _SEARCH_MULTI
        if action == "get_law_article_sub":
            assert params["law_id"] == 1556
            assert params["article"] == "017800"
            return json.dumps({"results": [{"시행일자": "2026-01-01"}]}, ensure_ascii=False)
        return ""

    r = K.resolve_law_effective_date(mock_call, "관세법", "제178조", law_type="법률")
    assert r["ok"] and r["effective_date"] == "2026-01-01" and r["law_id"] == 1556
    assert [c[0] for c in calls] == ["search_laws", "get_law_article_sub"]


def test_resolve_fails_on_ambiguous_law():
    def mock_call(action, **params):
        return json.dumps({"results": [
            {"law_name": "관세법", "law_id": 1, "law_type": "법률"},
            {"law_name": "관세법", "law_id": 2, "law_type": "법률"},
        ]}, ensure_ascii=False)
    r = K.resolve_law_effective_date(mock_call, "관세법", "제178조")
    assert not r["ok"] and r["effective_date"] is None, "오선택 대신 실패 신호여야 함"


# ── 격리: key/네트워크/streamlit-free ───────────────────
def test_kpd_mcp_is_pure():
    src = open(os.path.join(ROOT, "kpd_mcp.py"), encoding="utf-8").read()
    for forbidden in ("import requests", "import streamlit", "get_secret", "http", "railway"):
        assert forbidden not in src, f"kpd_mcp.py는 순수 파서여야 함: '{forbidden}' 발견"
