"""
test_output.py — Step 4 출력 재설계 결정론 검증 (streamlit 불필요)

triage 데이터 로직(triage_util)과 dispatch 경로 판정을 검증한다.
렌더 자체(st.*)는 streamlit 런타임이 필요해 단위테스트 불가 → 구조는
소스 수준에서 가드(영웅이 상세보다 먼저, 신뢰배지 경로 존재).
"""
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import triage_util as T  # noqa: E402
from block_assembler import classify_issues, load_legal_blocks  # noqa: E402


def _db():
    return load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))


# ── 신뢰배지 정합성 (핵심 안전 테스트) ──────────────────
def test_evidence_grade_db_for_topic_match():
    """키워드 모조품(결정론) 매칭 → db_guaranteed."""
    mt = classify_issues("면세점에서 모조품 판매 검토해줘", _db())
    assert mt, "키워드 모조품이 매칭돼야 함"
    assert T.evidence_grade_for(mt) == "db_guaranteed"


def test_evidence_grade_llm_for_out_of_scope():
    """명백한 범위밖(타도메인) → 매칭 없음 → llm_draft."""
    mt = classify_issues("구내식당 점심 메뉴 추천해줘", _db())
    assert mt == [], "범위밖 질의는 토픽 매칭이 없어야 함"
    assert T.evidence_grade_for(mt) == "llm_draft"


# ── severity / escalation 파생 ──────────────────────────
def test_severity_and_escalation_rejected():
    jd = {"verdict": "rejected", "summary": "x", "verdict_reason": "y", "issues": []}
    T.enrich_triage_fields(jd)
    assert jd["severity"]["level"] == "stop"
    assert jd["severity"]["icon"] == "🔴"
    assert jd["escalation"], "🔴는 에스컬레이션 가이드가 있어야 함"


def test_severity_and_escalation_conditional():
    jd = {"verdict": "conditional"}
    T.enrich_triage_fields(jd)
    assert jd["severity"]["level"] == "caution"
    assert jd["escalation"], "🟡도 에스컬레이션 필요"


def test_no_escalation_for_approved():
    jd = {"verdict": "approved"}
    T.enrich_triage_fields(jd)
    assert jd["severity"]["level"] == "go"
    assert jd["escalation"] == "", "🟢는 에스컬레이션 없음"


def test_evidence_grade_defaults_llm():
    jd = {"verdict": "conditional"}
    T.enrich_triage_fields(jd)
    assert jd["evidence_grade"] == "llm_draft"


def test_enrich_preserves_db_grade():
    """블록경로가 심은 db_guaranteed는 보존(round-trip)."""
    jd = {"verdict": "rejected", "evidence_grade": "db_guaranteed"}
    T.enrich_triage_fields(jd)
    assert jd["evidence_grade"] == "db_guaranteed"


def test_enrich_idempotent():
    jd = {"verdict": "rejected"}
    T.enrich_triage_fields(jd)
    snap = dict(jd)
    T.enrich_triage_fields(jd)
    assert jd == snap, "멱등이어야 함(렌더·docx·로깅 다중 호출)"


# ── json_data 계약 보존 (불변식 가드) ───────────────────
def test_json_data_contract_preserved():
    jd = {
        "summary": "s", "verdict": "rejected", "verdict_reason": "r",
        "issues": [{"a": 1}], "action_plan": "ap", "cited_laws": [],
        "alternative_clause": None, "cited_precedents": [],
    }
    before = set(jd.keys())
    T.enrich_triage_fields(jd)
    assert before <= set(jd.keys()), "기존 필드는 보존되어야 함"
    assert jd["summary"] == "s" and jd["issues"] == [{"a": 1}]
    # 추가 필드만 늘었는지
    assert set(jd.keys()) - before == {"evidence_grade", "severity", "escalation", "freshness"}


# ── verdict-as-hero 구조 가드 (소스 수준) ───────────────
def test_render_hierarchy_hero_before_detail():
    src = open(os.path.join(ROOT, "legal_ai.py"), encoding="utf-8").read()
    assert src.count("render_verdict_hero(") >= 3, "정의 + 2개 렌더 사이트에서 영웅 카드 사용"
    assert "상세 근거" in src, "강등된 '상세 근거(법무 검토용)' 섹션 헤더 존재"
    assert "evidence_grade" in src, "신뢰배지 경로 존재"


# ── 블록 무결성 회귀 가드 ───────────────────────────────
def test_block_integrity_still_holds():
    from block_assembler import run_pipeline
    res = run_pipeline(
        query="면세점에서 모조품 판매 검토해줘",
        사규_texts=["(테스트 사규)"],
        gemini_call_fn=None,
        db_path=os.path.join(ROOT, "legal_blocks.json"),
    )
    assert res["integrity_errors"] == []


# ── 신선도 배지 (리스크와 직교 축) ──────────────────────
def test_freshness_forced_uncovered_for_llm_draft():
    """llm_draft는 항상 UNCOVERED — 거짓 '최신' 표기 방지 (§2)."""
    jd = {"verdict": "conditional", "evidence_grade": "llm_draft", "freshness": "FRESH"}
    T.enrich_triage_fields(jd)
    assert jd["freshness"] == "UNCOVERED", "llm_draft인데 FRESH로 새면 안 됨"


def test_freshness_db_guaranteed_defaults_needs_review():
    """db_guaranteed는 주입값 없으면 NEEDS_REVIEW — 거짓 FRESH 금지."""
    jd = {"verdict": "rejected", "evidence_grade": "db_guaranteed"}
    T.enrich_triage_fields(jd)
    assert jd["freshness"] == "NEEDS_REVIEW"


def test_freshness_db_guaranteed_keeps_injected_valid():
    jd = {"verdict": "rejected", "evidence_grade": "db_guaranteed", "freshness": "FRESH"}
    T.enrich_triage_fields(jd)
    assert jd["freshness"] == "FRESH"  # 판정모듈 주입값 유지


def test_freshness_badges_distinct_from_severity_yellow():
    """신선도 배지 아이콘이 리스크 🟡과 겹치지 않아야(§1)."""
    icons = {b["icon"] for b in T.FRESHNESS_BADGES.values()}
    assert icons == {"🟢", "🔵", "🟠", "⚪"}
    assert "🟡" not in icons, "리스크 🟡과 충돌"
    assert set(T.FRESHNESS_BADGES) == {"FRESH", "NEEDS_REVIEW", "STALE", "UNCOVERED"}


def test_freshness_badge_for_fallback():
    assert T.freshness_badge_for("STALE")["icon"] == "🟠"
    assert T.freshness_badge_for("nonsense") == T.FRESHNESS_BADGES["UNCOVERED"]


# ── 톤 린트: 정적 카피에 공포·단정 표현 금지 (§3/§8) ────
_FORBIDDEN = ("위험합니다", "절대 하지 마세요", "절대 하지마세요", "하면 안 됩니다", "하면 안됩니다")


def test_static_copy_has_no_fear_phrases():
    """우리가 만드는 배지/카피/footer 정적 문자열에 공포·단정 표현이 없어야 함.
    (LLM 생성 슬롯·DB 법령 텍스트는 대상 아님 — 시스템 카피만.)"""
    blobs = []
    for d in (T.FRESHNESS_BADGES, T.EVIDENCE_BADGES, T.SEVERITY_MAP):
        for v in d.values():
            blobs.append(v.get("text", "") + v.get("label", ""))
    blobs.extend(T.FRESHNESS_COPY.values())
    blobs.append(T.ESCALATION_TEXT)
    joined = " ".join(blobs)
    hits = [w for w in _FORBIDDEN if w in joined]
    assert not hits, f"정적 카피에 금지 표현: {hits}"
