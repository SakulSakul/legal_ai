"""
test_bonded_store_fix.py — bonded_store_notice 인용 교정 검증 (key-free·결정론)

- JSON 유효성 / 현행 인용 존재 / 오류 인용 제거 / dependencies 스키마
- assemble_document 정상 + 무결성 통과
- controlled: bonded_store_notice 외 블록 바이트 불변
API·시크릿 일절 미사용.
"""
import os
import json
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import block_assembler as BA  # noqa: E402


def _db():
    return BA.load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))


def _bonded():
    return next(i for i in _db()["모조품"]["issues"] if i["id"] == "bonded_store_notice")


def _origin_main_topic():
    """origin/main 기준 비교용. CI 등 ref 미존재 환경에서는 skip
    (controlled diff 0은 PR git diff로 별도 검증됨)."""
    r = subprocess.run(
        ["git", "show", "origin/main:legal_blocks.json"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        pytest.skip("origin/main ref 미존재(CI 얕은 체크아웃) — git diff로 별도 검증")
    return json.loads(r.stdout)["모조품"]


def test_json_valid_seven_issues():
    assert len(_db()["모조품"]["issues"]) == 7


def test_current_citations_present():
    b = _bonded()
    text = b["applicable_laws"] + " " + b["legal_analysis"]
    for needle in ("제178조", "제235조", "2025-44", "제69조의5"):
        assert needle in text, f"현행 인용 누락: {needle}"


def test_erroneous_176_2_removed():
    """잘못된 취소 근거(관세법 제176조의2)가 이 블록에서 제거됨."""
    b = _bonded()
    assert "제176조의2" not in b["legal_analysis"], "176조의2가 legal_analysis에 잔존"
    assert "제176조의2" not in b["applicable_laws"], "176조의2가 applicable_laws에 잔존"


def test_dependencies_schema():
    deps = _bonded().get("dependencies")
    assert isinstance(deps, list) and len(deps) == 3, deps
    for d in deps:
        assert d.get("name"), f"name 누락: {d}"
        assert d.get("effective_date"), f"effective_date 누락: {d}"
    names = {d["name"] for d in deps}
    assert "관세법" in names


def test_penalty_law_ref_updated():
    assert "제178조" in _bonded()["penalty"]["law_ref"]


def test_assemble_and_integrity():
    db = _db()
    blocks = BA.fetch_legal_blocks("모조품", db)
    doc = BA.assemble_document(blocks, {}, query="면세점 모조품 판매 검토")
    assert "제178조" in doc and "제235조" in doc, "교정 텍스트가 렌더되지 않음"
    assert BA.verify_block_integrity(doc, blocks) == [], "무결성/정합성 오류"


def test_other_blocks_byte_identical():
    """controlled: bonded_store_notice 외 블록·메타 바이트 불변."""
    cur_topic = _db()["모조품"]
    old_topic = _origin_main_topic()
    cur_by = {i["id"]: i for i in cur_topic["issues"]}
    old_by = {i["id"]: i for i in old_topic["issues"]}
    assert set(cur_by) == set(old_by)
    for iid in cur_by:
        if iid == "bonded_store_notice":
            continue
        assert cur_by[iid] == old_by[iid], f"{iid} 변경됨 (controlled 위반)"
    for k in ("label", "summary", "risk_level_legend"):
        assert cur_topic.get(k) == old_topic.get(k), f"모조품.{k} 변경됨"
