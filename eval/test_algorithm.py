"""
test_algorithm.py — 하이브리드 배선 검증 (실모델 불필요, 항상 실행)

주입형 가짜 임베더로 classify_issues / _hybrid_retrieve의 배선을 결정론적으로
증명한다. '의미' 자체는 실모델(text-embedding-004)의 책임이고, 이 테스트는
플러밍(query·rep 임베딩 → 코사인 → 임계값 → 포함/배제, RRF 융합·조립)만 검증.

가짜 임베더: 작은 개념 사전으로 텍스트를 5차원 벡터에 매핑.
  dim0=위조개념, dim1=단가, dim2=판촉, dim3=친환경, dim4=base
디스크 캐시는 완전 격리(monkeypatch)해 실제 실행을 오염시키지 않는다.
"""
import os
import json

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

_LEX = {
    0: ["모조품", "위조", "짝퉁", "가품", "모조", "위조상품", "fake", "counterfeit",
        "가짜", "명품", "이미테이션", "정품", "레플리카", "카피", "진품", "불법상품",
        "장물", "밀수", "진정"],
    1: ["단가", "인하", "대금", "수수료"],
    2: ["판촉", "행사", "프로모션", "전가", "할인"],
    3: ["친환경", "그린워싱", "ESG", "실증", "환경"],
}


def _fake_backend(texts, model):
    out = []
    for t in texts:
        v = [0.0] * 5
        for dim, toks in _LEX.items():
            for tok in toks:
                if tok in t:
                    v[dim] += 1.0
        v[4] = 0.3  # 공통 base 성분
        out.append(v)
    return out


@pytest.fixture
def fake_emb(monkeypatch):
    import embedding_util as eu
    monkeypatch.setattr(eu, "_mem_cache", {})
    monkeypatch.setattr(eu, "_disk_loaded", True)        # 실제 디스크 캐시 로드 차단
    monkeypatch.setattr(eu, "_save_disk_cache", lambda: None)  # 디스크 기록 차단
    monkeypatch.setenv("CLASSIFY_EMB_THRESHOLD", "0.5")
    eu.set_backend(_fake_backend)
    yield
    eu.set_backend(None)


def _db():
    from block_assembler import load_legal_blocks
    return load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))


def _corpus_docs():
    with open(os.path.join(HERE, "saryu_corpus.json"), encoding="utf-8") as f:
        return json.load(f)["docs"]


# ── classify_issues 의미매칭 배선 ───────────────────────
def test_classify_synonym_matched_via_embedding(fake_emb):
    """키워드 미스 동의어 질의가 임베딩으로 모조품에 매칭된다."""
    from block_assembler import classify_issues
    q = "가짜 명품 가방을 정품인 것처럼 판매했어요"  # 키워드(모조품/위조/가품) 없음
    assert "모조품" in classify_issues(q, _db())


def test_classify_oblique_matched_via_embedding(fake_emb):
    from block_assembler import classify_issues
    q = "공급사가 진품이라고 보증했는데 알고 보니 아니었습니다"
    assert "모조품" in classify_issues(q, _db())


def test_classify_negative_not_false_positive(fake_emb):
    """negative(단가·판촉·그린워싱)는 임계값 아래 → 모조품 오분류 안 됨."""
    from block_assembler import classify_issues
    db = _db()
    for q in [
        "협력사 납품단가를 부당하게 인하하면 대규모유통업법 위반인가요",
        "판촉행사 비용을 협력사에 전가하면 문제가 되나요",
        "친환경이라고 광고했는데 실증 근거가 없으면 그린워싱인가요",
    ]:
        assert classify_issues(q, db) == [], f"false positive: {q}"


def test_classify_direct_still_keyword(fake_emb):
    """direct 키워드 질의는 임베딩과 무관하게 키워드로 매칭 (고신뢰)."""
    from block_assembler import classify_issues
    assert "모조품" in classify_issues("면세점 모조품 판매 검토", _db())


# ── _hybrid_retrieve RRF 배선 ───────────────────────────
def test_hybrid_surfaces_relevant_article_for_synonym(fake_emb):
    """동의어 질의에서 RRF가 위조 관련 조항을 상위로 끌어올린다."""
    import saryu_retriever as sr
    out = sr._hybrid_retrieve("레플리카 상품 판매가 법적으로 가능한지",
                              _corpus_docs(), max_chars=5000)
    assert out is not None
    # 위조개념 조항(제16조의2 손해배상 / 제17조 위해·불법상품)이 표면화돼야 함
    assert ("제16조의2" in out) or ("제17조" in out)


def test_hybrid_returns_none_without_backend(monkeypatch):
    """임베딩 백엔드 없으면 None → 키워드 폴백 유도 (graceful)."""
    import embedding_util as eu
    import saryu_retriever as sr
    monkeypatch.setattr(eu, "_mem_cache", {})
    monkeypatch.setattr(eu, "_disk_loaded", True)
    eu.set_backend(lambda texts, model: None)  # 백엔드 실패 시뮬레이션
    try:
        assert sr._hybrid_retrieve("레플리카 상품", _corpus_docs(), max_chars=5000) is None
    finally:
        eu.set_backend(None)


def test_output_format_preserved(fake_emb):
    """하이브리드 출력도 '[label] article (title)' 포맷 유지 (eval 파서 호환)."""
    import re
    import saryu_retriever as sr
    out = sr._hybrid_retrieve("가짜 명품 판매", _corpus_docs(), max_chars=5000)
    assert out is not None
    assert re.search(r"\[[^\]]+\] 제\d+조(?:의\d+)? \([^)]+\)", out), out[:200]
