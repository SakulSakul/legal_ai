"""
test_regression.py — 회귀 가드 + 완료기준(§5) pytest 검증

한 방으로 green/red: `python -m pytest eval/`

분류:
  - 결정론(키 불필요): 회귀 가드(direct/negative 유지), 모델문자열 불변,
    시그니처 불변, 블록 무결성, baseline 존재.
  - 임베딩 의존(@pytest.mark.embedding, 키 없으면 skip): synonym/oblique 분류,
    Recall@5, 체감 recall 개선 게이트.
"""
import os
import re
import json
import inspect
import functools

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

def _has_key() -> bool:
    """env 또는 .streamlit/secrets.toml 어디든 키가 있으면 True."""
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
        return True
    try:
        import embedding_util
        return bool(embedding_util._get_api_key())
    except Exception:
        return False


HAS_KEY = _has_key()
needs_emb = pytest.mark.skipif(
    not HAS_KEY,
    reason="임베딩 키 없음 — 의미매칭 게이트 skip (결정론 테스트만 검증)",
)


# ── 측정 1회 캐시 ───────────────────────────────────────
@functools.lru_cache(maxsize=1)
def _measured():
    import run_compare
    rows, over, lvl = run_compare.measure()
    return rows, over, lvl


@functools.lru_cache(maxsize=1)
def _baseline():
    with open(os.path.join(HERE, "baseline_locked.json"), "r", encoding="utf-8") as f:
        return json.load(f)


# ── 결정론 테스트 (항상 실행) ───────────────────────────
def test_baseline_locked_exists():
    b = _baseline()
    assert b["overall"]["cls_acc"] == 0.5, "locked baseline 분류정확도가 50%가 아님 — 하네스 오염"
    assert abs(b["overall"]["recall@5"] - 0.476) < 0.01


def test_regression_direct_classification_held():
    """direct 분류 100% 유지 (controlled — 하락 불가)."""
    _, _, lvl = _measured()
    base = _baseline()["by_level"]["direct"]["cls_acc"]
    assert lvl["direct"]["cls_acc"] >= base, (
        f"direct 분류 회귀: {lvl['direct']['cls_acc']*100:.1f}% < baseline {base*100:.1f}%"
    )


def test_regression_negative_no_false_positive():
    """negative 분류 100% 유지 — 모조품 오분류(false positive) 0건."""
    _, _, lvl = _measured()
    base = _baseline()["by_level"]["negative"]["cls_acc"]
    assert lvl["negative"]["cls_acc"] >= base, (
        f"negative false-positive 발생: {lvl['negative']['cls_acc']*100:.1f}% < baseline {base*100:.1f}%"
    )


def test_model_strings_unchanged():
    """모델 문자열 가드 — 인가된 GA 모델 집합만 존재해야(우발적 변경·EOL 404 차단).
    2026-06: 속도 티어링 — 종합=Opus 유지 / 기계적 Claude=Haiku / Stage1 Gemini 3.5 Flash
    +폴백 3.1 Flash-Lite(전부 GA, preview 금지). 모델명은 상수 한 곳에서만."""
    src = open(os.path.join(ROOT, "legal_ai.py"), encoding="utf-8").read()
    found = set(re.findall(r"(?:claude|gemini)-[A-Za-z0-9.\-]+", src))
    expected = {
        "claude-opus-4-8",
        "claude-haiku-4-5-20251001",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    }
    assert found == expected, f"모델 문자열 변경 감지: {found ^ expected}"
    # 죽은/EOL 모델 잔재 0 + 하드코딩 일원화
    assert "claude-sonnet-4-20250514" not in src, "죽은 모델(404) 잔재"
    assert not any("preview" in m for m in found), "preview 모델 문자열 잔재 — GA만 허용(EOL 404 방지)"
    assert 'model="claude' not in src and 'model="gemini' not in src, "모델 인라인 하드코딩 잔재 — 상수 사용"


def test_retrieve_signature_unchanged():
    """retrieve_relevant_saryu(query, docs, max_chars=5000) 시그니처 불변 (§2.4)."""
    from saryu_retriever import retrieve_relevant_saryu
    sig = inspect.signature(retrieve_relevant_saryu)
    params = list(sig.parameters)
    assert params[:3] == ["query", "docs", "max_chars"], f"시그니처 변경: {params}"
    assert sig.parameters["max_chars"].default == 5000


def test_classify_signature_unchanged():
    """classify_issues(query, db) -> list 시그니처 불변 (§2.5)."""
    from block_assembler import classify_issues
    sig = inspect.signature(classify_issues)
    assert list(sig.parameters)[:2] == ["query", "db"], f"시그니처 변경: {sig}"


def test_block_integrity_holds():
    """verify_block_integrity 통과 유지 (§2.3) — DB 형량 블록 무손상 삽입."""
    from block_assembler import run_pipeline
    res = run_pipeline(
        query="면세점에서 모조품 판매 행위에 대해 법률 검토해줘",
        사규_texts=["(테스트 사규)"],
        gemini_call_fn=None,
        db_path=os.path.join(ROOT, "legal_blocks.json"),
    )
    assert res["integrity_errors"] == [], f"무결성 오류: {res['integrity_errors']}"


def test_embedding_graceful_fallback_without_key(monkeypatch):
    """임베딩 백엔드는 키 해석 불가 시 None 반환 → 앱이 죽지 않음 (§2.6).

    소스(env/secrets.toml/streamlit) 무관하게 검증하기 위해 _get_api_key를
    직접 무력화한다 (secrets.toml이 존재해도 테스트 전제가 깨지지 않음).
    """
    try:
        import embedding_util
    except ImportError:
        pytest.skip("embedding_util.py 아직 미작성")
    monkeypatch.setattr(embedding_util, "_get_api_key", lambda: "")
    monkeypatch.setattr(embedding_util, "_mem_cache", {})
    monkeypatch.setattr(embedding_util, "_disk_loaded", True)  # 디스크 캐시 히트 차단
    monkeypatch.setattr(embedding_util, "_backend", None)      # 기본 백엔드 사용
    out = embedding_util.embed(["임의의 미캐시 문장 " + "x" * 8])
    assert out is None, "키 없을 때 embed()는 None을 반환해 키워드 폴백을 유도해야 함"


# ── 임베딩 의존 게이트 (§5 개선 목표) ───────────────────
@needs_emb
@pytest.mark.embedding
def test_gate_synonym_classification():
    _, _, lvl = _measured()
    assert lvl["synonym"]["cls_acc"] >= 0.80, (
        f"synonym 분류 {lvl['synonym']['cls_acc']*100:.1f}% < 80%"
    )


@needs_emb
@pytest.mark.embedding
def test_gate_oblique_classification():
    _, _, lvl = _measured()
    assert lvl["oblique"]["cls_acc"] >= 0.70, (
        f"oblique 분류 {lvl['oblique']['cls_acc']*100:.1f}% < 70%"
    )


@needs_emb
@pytest.mark.embedding
def test_gate_recall_at_5():
    _, over, _ = _measured()
    assert over["recall@5"] >= 0.70, f"Recall@5 {over['recall@5']*100:.1f}% < 70%"


@needs_emb
@pytest.mark.embedding
def test_gate_surfaced_recall():
    _, over, _ = _measured()
    assert over["surfaced_recall"] >= 0.85, (
        f"체감 recall {over['surfaced_recall']*100:.1f}% < 85%"
    )
