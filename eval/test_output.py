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
    assert set(jd.keys()) - before == {"evidence_grade", "severity", "escalation"}


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
