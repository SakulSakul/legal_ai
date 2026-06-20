"""
test_penalty_schema.py — Step 5 형량 구조화 스키마 (키 불필요·결정론)

검증:
  - 7개 issue 전부 penalty 필드 + 필수 키
  - penalty 숫자 ↔ prose 정합성 (verify_block_integrity 강화 경로)
  - assemble_document 출력 바이트 불변 (penalty가 출력에 새지 않음) ← 가장 중요
  - json_data 계약/기존 필드 보존
API·시크릿 일절 사용하지 않는다.
"""
import os
import copy

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import block_assembler as BA  # noqa: E402

PENALTY_KEYS = {
    "imprisonment_max_years", "fine_max_krw",
    "corporate_fine_max_krw", "amended_date", "law_ref",
}


def _issues():
    return BA.load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))["모조품"]["issues"]


# ── 스키마 존재 ─────────────────────────────────────────
def test_all_seven_issues_have_penalty():
    issues = _issues()
    assert len(issues) == 7, f"issue 수 {len(issues)} ≠ 7"
    for iss in issues:
        assert "penalty" in iss, f"{iss['id']}: penalty 누락"
        assert PENALTY_KEYS <= set(iss["penalty"].keys()), f"{iss['id']}: 필수 키 누락"


def test_penalty_value_types():
    for iss in _issues():
        p = iss["penalty"]
        for k in ("imprisonment_max_years", "fine_max_krw", "corporate_fine_max_krw"):
            assert p[k] is None or isinstance(p[k], int), f"{iss['id']}.{k}"
        assert p["law_ref"], f"{iss['id']}: law_ref 비어있음"


# ── 정합성 (penalty 숫자 ↔ prose) ───────────────────────
def test_penalty_consistent_with_prose():
    for iss in _issues():
        errs = BA._penalty_prose_errors(iss)
        assert errs == [], f"{iss['id']}: {errs}"


def test_integrity_includes_penalty_consistency():
    db = BA.load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))
    blocks = BA.fetch_legal_blocks("모조품", db)
    doc = BA.assemble_document(blocks, {}, query="면세점 모조품 판매 검토")
    assert BA.verify_block_integrity(doc, blocks) == []


def test_penalty_detects_inconsistency():
    """일부러 prose에 없는 숫자를 넣으면 정합성 오류가 잡혀야 함 (가드의 가드)."""
    iss = copy.deepcopy(_issues()[0])
    iss["penalty"]["imprisonment_max_years"] = 999
    errs = BA._penalty_prose_errors(iss)
    assert errs, "prose에 없는 형량(999년)을 못 잡으면 검증 무의미"


# ── 출력 불변 (회귀 0) — 가장 중요 ──────────────────────
def test_assemble_output_byte_identical_without_penalty():
    """penalty 유무로 assemble_document 출력이 바이트 동일해야 한다.
    (penalty_stripped == origin/main 상태이므로 '출력 불변 vs 이전'과 동치.)"""
    db = BA.load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))
    blocks = BA.fetch_legal_blocks("모조품", db)
    q = "면세점 모조품 판매 검토"
    out_with = BA.assemble_document(blocks, {}, query=q)

    stripped = copy.deepcopy(blocks)
    for iss in stripped["issues"]:
        iss.pop("penalty", None)
    out_without = BA.assemble_document(stripped, {}, query=q)

    assert out_with == out_without, "penalty가 출력 텍스트에 새어나옴 (출력 불변 위반)"


# ── 헬퍼 ────────────────────────────────────────────────
def test_get_penalty_helper():
    iss = _issues()[0]
    p = BA.get_penalty(iss)
    assert p is not None and p["imprisonment_max_years"] == 7
    assert BA.get_penalty({"id": "x"}) is None  # penalty 없으면 None


# ── 불변식: 기존 issue 필드 보존 ────────────────────────
def test_existing_issue_fields_preserved():
    for iss in _issues():
        for k in ("id", "title", "risk_level", "risk_label", "applicable_laws", "legal_analysis"):
            assert k in iss and iss[k], f"{iss.get('id')}: 기존 필드 {k} 손상"


# ── 감시값 (구조화가 prose와 맞는지 핵심 케이스) ────────
def test_known_penalty_values():
    by = {iss["id"]: iss["penalty"] for iss in _issues()}
    t = by["trademark_230"]
    assert (t["imprisonment_max_years"], t["fine_max_krw"], t["corporate_fine_max_krw"]) == (7, 100000000, 300000000)
    assert t["amended_date"] == "2025-07-22"
    f = by["criminal_fraud_347"]
    assert (f["imprisonment_max_years"], f["fine_max_krw"]) == (20, 50000000)
    assert f["amended_date"] == "2025-12-23"
    assert by["unfair_competition_18"]["fine_max_krw"] == 30000000
    # 형사처벌 없는 issue는 null
    assert by["consumer_protection"]["imprisonment_max_years"] is None
