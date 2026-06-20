"""
test_kpd_mcp_client.py — K Public Data MCP 파서/해석 검증 (실제 마크다운 mock·key-free)

PR #19 라이브 스모크로 확정된 실제 응답 형식(UTF-8 마크다운)으로 픽스처 구성.
네트워크·키·streamlit 일절 미사용. 인코딩 회귀 가드 포함.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import kpd_mcp as K  # noqa: E402


# ── 실제 라이브 마크다운 (UTF-8 복원본) ─────────────────
MD_SEARCH = """## 법령 검색 결과

검색어: "관세법"
총 12건 (1페이지)

1. [280363] 관세법
   법률 | 소관: 재정경제부 | 시행: 20260401 | 일부개정
2. [285897] 관세법 시행령
   대통령령 | 소관: 재정경제부 | 시행: 20260508 | 일부개정
3. [284979] 관세법 시행규칙
   재정경제부령 | 소관: 재정경제부 | 시행: 20260401 | 일부개정
"""

MD_ARTICLE = """## 관세법

- **법종구분**: 법률
- **소관부처**: 재정경제부
- **시행일자**: 20260101

### 제178조 (반입정지 등과 특허의 취소)
"""

MD_ADMIN = """## 행정규칙 검색 결과

검색어: "보세판매장 특허 및 운영에 관한 고시"
총 1건 (1페이지)

1. [2100000263374] 보세판매장 특허 및 운영에 관한 고시
   종류: 고시 | 발령: 20250901 | 일부개정 | 현행
"""


# ── 조문번호 → 6자리 ────────────────────────────────────
def test_article_to_6digit():
    assert K.article_to_6digit("제178조") == "017800"
    assert K.article_to_6digit("제235조") == "023500"
    assert K.article_to_6digit("제69조의5") == "006905"
    assert K.article_to_6digit("제16조의2") == "001602"
    assert K.article_to_6digit("조문아님") is None


# ── search_laws: 마크다운 정확 매칭 ─────────────────────
def test_search_laws_exact_match_picks_correct_law_id():
    r = K.parse_search_laws(MD_SEARCH, "관세법", law_type="법률")
    assert r["ok"] and r["law_id"] == 280363 and r["effective_date"] == "2026-04-01"


def test_search_laws_excludes_siheng(law_type=None):
    """'관세법'이 '관세법 시행령/시행규칙'과 구분되어 1건만 선택."""
    r = K.parse_search_laws(MD_SEARCH, "관세법")
    assert r["ok"] and r["law_id"] == 280363


def test_search_laws_zero_match_fails():
    r = K.parse_search_laws(MD_SEARCH, "상표법")
    assert not r["ok"] and r["law_id"] is None


def test_search_laws_ambiguous_multi_fails():
    md = """## 법령 검색 결과

1. [1] 관세법
   법률 | 소관: x | 시행: 20260401 | 일부개정
2. [2] 관세법
   법률 | 소관: y | 시행: 20250101 | 일부개정
"""
    r = K.parse_search_laws(md, "관세법", law_type="법률")
    assert not r["ok"], "동명 다건은 오선택 방지를 위해 실패"


# ── get_law_article_sub: 라벨 우선 ──────────────────────
def test_article_sub_label_priority():
    r = K.parse_article_sub(MD_ARTICLE)
    assert r["ok"] and r["effective_date"] == "2026-01-01"


def test_article_sub_no_date_fails():
    r = K.parse_article_sub("### 제178조 (반입정지 등과 특허의 취소)\n본문에 날짜 없음")
    assert not r["ok"]


# ── search_admin_rules: 발령일 ──────────────────────────
def test_admin_rules_parse():
    r = K.parse_admin_rules(MD_ADMIN, exact_name="보세판매장 특허 및 운영에 관한 고시")
    assert r["ok"] and r["admrul_id"] == 2100000263374 and r["effective_date"] == "2025-09-01"


def test_admin_rules_zero_match_fails():
    r = K.parse_admin_rules(MD_ADMIN, exact_name="존재하지 않는 고시")
    assert not r["ok"]


# ── 통합 흐름 (mock call_fn, 마크다운) ──────────────────
def test_resolve_law_effective_date_flow():
    calls = []

    def mock_call(action, **params):
        calls.append((action, params))
        if action == "search_laws":
            return MD_SEARCH
        if action == "get_law_article_sub":
            assert params["law_id"] == 280363
            assert params["article"] == "017800"
            return MD_ARTICLE
        return ""

    r = K.resolve_law_effective_date(mock_call, "관세법", "제178조", law_type="법률")
    assert r["ok"] and r["effective_date"] == "2026-01-01" and r["law_id"] == 280363
    assert [c[0] for c in calls] == ["search_laws", "get_law_article_sub"]


def test_resolve_fails_on_ambiguous_law():
    md = "1. [1] 관세법\n   법률 | 시행: 20260401 |\n2. [2] 관세법\n   법률 | 시행: 20250101 |\n"
    r = K.resolve_law_effective_date(lambda a, **p: md, "관세법", "제178조")
    assert not r["ok"] and r["effective_date"] is None


# ── 인코딩 회귀 가드 (mojibake) ─────────────────────────
def test_encoding_mojibake_breaks_match_and_utf8_fixes():
    """클라이언트가 Latin-1로 잘못 읽으면 '관세법'→mojibake → 정확매칭 실패.
    UTF-8로 복원하면 다시 성공(= KPublicDataClient의 resp.encoding='utf-8'가 하는 일)."""
    assert K.parse_search_laws(MD_SEARCH, "관세법", "법률")["ok"]
    mojibake = MD_SEARCH.encode("utf-8").decode("latin-1")
    assert not K.parse_search_laws(mojibake, "관세법", "법률")["ok"], "깨진 인코딩이면 한글 매칭 실패"
    recovered = mojibake.encode("latin-1").decode("utf-8")
    assert K.parse_search_laws(recovered, "관세법", "법률")["ok"], "UTF-8 복원 후 정상 매칭"


# ── 격리: key/네트워크/streamlit-free ───────────────────
def test_kpd_mcp_is_pure():
    src = open(os.path.join(ROOT, "kpd_mcp.py"), encoding="utf-8").read()
    for forbidden in ("import requests", "import streamlit", "get_secret", "railway", "http"):
        assert forbidden not in src, f"kpd_mcp.py는 순수 파서여야 함: '{forbidden}' 발견"
