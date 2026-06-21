"""
test_grounding.py — 내부문서 인용 grounding 결정론 검증 (streamlit·DB 불필요, mock 레코드).

핵심: 환각을 '탐지'가 아니라 '구조적으로 못 일어나게' — 인용은 후보집합 id로만,
본문은 레코드 title에서만. LLM이 가짜 id·가짜 문서명을 내도 드롭되는지 잠근다.
"""
import grounding_util as G


def _docs():
    return [
        {"id": "u1", "cat": "saryu", "label": "(공정거래) 협력회사 판촉비용 분담 지침", "text": "제5조 분담비율 50% 원칙..."},
        {"id": "u2", "cat": "contract", "label": "특약매입 표준계약서", "text": "제2조 법령준수..."},
        {"id": "u3", "cat": "yakjeong", "label": "공동판촉 약정서", "text": "제1조..."},
        {"id": "x9", "cat": "laws", "label": "관세법", "text": "..."},  # 내부문서 아님
    ]


# ── 후보집합 구성 ──────────────────────────────────────────────
def test_make_candidates_internal_only_with_ids():
    cands = G.make_candidates(_docs())
    ids = {c["id"] for c in cands}
    assert ids == {"u1", "u2", "u3"}  # laws(x9) 제외 — 내부문서 cats만
    c1 = next(c for c in cands if c["id"] == "u1")
    assert c1["kind"] == "사규" and c1["title"] == "(공정거래) 협력회사 판촉비용 분담 지침"


def test_make_candidate_drops_recordless_id_or_title():
    assert G.make_candidate({"cat": "saryu", "label": "x"}) is None       # id 없음
    assert G.make_candidate({"id": "u1", "cat": "saryu"}) is None          # title 없음
    assert G.make_candidate({"id": "u1", "cat": "saryu", "label": "지침"})["title"] == "지침"


# ── 구조적 배제 (★ 환각 가드) ─────────────────────────────────
def test_ground_ids_passes_only_candidate_ids():
    cands = G.make_candidates(_docs())
    grounded = G.ground_ids(["u1", "u2"], cands)
    assert [g["id"] for g in grounded] == ["u1", "u2"]


def test_ground_ids_drops_fabricated_id():
    """LLM이 후보에 없는 id를 내면 드롭(구조적 배제, 탐지 아님)."""
    cands = G.make_candidates(_docs())
    grounded = G.ground_ids(["u1", "FAKE-uuid", "u2", "u1"], cands)  # 가짜 + 중복
    assert [g["id"] for g in grounded] == ["u1", "u2"]               # 가짜·중복 제거


def test_ground_ids_empty_when_all_fabricated():
    """유효 id 0 → 빈 결과(정직한 '해당 없음', 날조 0)."""
    cands = G.make_candidates(_docs())
    assert G.ground_ids(["없는거1", "및 계약서"], cands) == []


def test_hallucinated_free_text_name_detected():
    """'및 계약서' 같은 자유텍스트 파편 = 후보 title에 없음 → 환각(렌더 금지)."""
    cands = G.make_candidates(_docs())
    assert G.is_hallucinated_name("및 계약서", cands) is True
    assert G.is_hallucinated_name("존재하지 않는 지침", cands) is True
    assert G.is_hallucinated_name("(공정거래) 협력회사 판촉비용 분담 지침", cands) is False  # 실재 title


# ── 렌더는 레코드 title에서만 (LLM 텍스트 0) ───────────────────
def test_grounded_titles_from_record_not_llm():
    """화면 문서명 = 레코드 title. LLM이 뭉갠 텍스트가 아니라 깨끗한 원본."""
    cands = G.make_candidates(_docs())
    titles = G.grounded_titles(["u1"], cands, kind="사규")
    assert titles == ["(공정거래) 협력회사 판촉비용 분담 지침"]   # 파편 아님, 1:1
    assert G.grounded_titles(["u2"], cands, kind="계약") == ["특약매입 표준계약서"]
    # 사규 id를 계약 kind로 거르면 안 뜸(분류 정확)
    assert G.grounded_titles(["u1"], cands, kind="계약") == []


def test_grounded_titles_empty_for_fabrication():
    cands = G.make_candidates(_docs())
    assert G.grounded_titles(["FAKE", "및 계약서"], cands) == []


# ── 빈 후보집합 → 인용 금지 프롬프트 ──────────────────────────
def test_empty_candidates_prompt_forbids_citation():
    block = G.candidates_prompt_block([])
    assert "후보 없음" in block and ("인용하지 마라" in block or "창작 절대 금지" in block)


def test_candidates_prompt_lists_ids():
    block = G.candidates_prompt_block(G.make_candidates(_docs()))
    assert "id=u1" in block and "id=u2" in block
    assert "창작" in block or "지어내지" in block  # 창작 금지 지시


# ── apply_grounding: 출력 전체에 구조적 배제 적용 ──────────────
def test_apply_grounding_rewrites_from_record():
    """cited_source_ids → 레코드 title로 applicable_rule 재작성(LLM 텍스트 폐기)."""
    cands = G.make_candidates(_docs())
    jd = {"issues": [{"title": "판촉비", "cited_source_ids": ["u1"],
                      "applicable_rule": "협력회사 판촉비용분담지침 및 계약서"}]}  # LLM 뭉갬
    G.apply_grounding(jd, cands)
    assert jd["issues"][0]["applicable_rule"] == "「(공정거래) 협력회사 판촉비용 분담 지침」"
    assert jd["issues"][0]["grounded_sources"][0]["id"] == "u1"


def test_apply_grounding_drops_fabricated_to_none():
    """cited 없고 자유텍스트가 후보 밖(환각) → '해당 없음'(날조 0)."""
    cands = G.make_candidates(_docs())
    jd = {"issues": [{"title": "x", "applicable_rule": "및 계약서"}]}  # 파편
    G.apply_grounding(jd, cands)
    assert jd["issues"][0]["applicable_rule"] == "해당 없음"


def test_apply_grounding_keeps_exact_member_freetext():
    """cited 없어도 applicable_rule이 후보 title과 정확 일치 → 유지(멤버십 통과)."""
    cands = G.make_candidates(_docs())
    jd = {"issues": [{"applicable_rule": "특약매입 표준계약서"}]}
    G.apply_grounding(jd, cands)
    assert jd["issues"][0]["applicable_rule"] == "특약매입 표준계약서"


def test_apply_grounding_does_not_touch_law():
    """법 인용(applicable_law)은 미수정 — 법 쪽 무회귀."""
    cands = G.make_candidates(_docs())
    jd = {"issues": [{"applicable_law": "대규모유통업법 제11조", "cited_source_ids": []}]}
    G.apply_grounding(jd, cands)
    assert jd["issues"][0]["applicable_law"] == "대규모유통업법 제11조"


def test_apply_grounding_idempotent():
    cands = G.make_candidates(_docs())
    jd = {"issues": [{"cited_source_ids": ["u1"]}]}
    G.apply_grounding(jd, cands)
    snap = jd["issues"][0]["applicable_rule"]
    G.apply_grounding(jd, cands)
    assert jd["issues"][0]["applicable_rule"] == snap


# ── 배선 회귀 잠금 (소스 가드) ─────────────────────────────────
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_legal_ai_wires_grounding():
    """legal_ai가 apply_grounding을 호출 + 합성 프롬프트에 후보 주입 + cited_source_ids 스키마."""
    src = open(os.path.join(_ROOT, "legal_ai.py"), encoding="utf-8").read()
    assert "grounding_util.apply_grounding" in src
    assert "make_candidates" in src and "candidates_prompt_block" in src
    assert "cited_source_ids" in src
    # 법 쪽 무회귀 — apply_grounding은 applicable_law 미수정(모듈 단위로 보장)


def test_block_gemini_prompt_has_id_grounding():
    """블록 gemini 프롬프트가 candidates 받으면 cited_source_ids로만 인용받는지."""
    src = open(os.path.join(_ROOT, "block_assembler.py"), encoding="utf-8").read()
    assert "candidates=None" in src
    assert "cited_source_ids" in src and "문서명" in src
