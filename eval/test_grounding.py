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


# ── #39 2B: 큰 docs 런타임 섹션추출 (판촉 조 가시화) ──────────
def _big_contract():
    """특약매입 계약서 재현 — 판촉비용 분담금 조항이 긴 본문 뒤에 묻힘(snippet[:240] 밖)."""
    text = ("특약매입 거래계약서 제1조 [목적] 기본조건을 정한다. "
            + "본 계약은 신의성실로 이행한다. " * 60   # 앞부분 채워 판촉 조항 묻기(>800자 = 분할 대상)
            + "제11조 [판촉비용 분담금] 을은 판촉비용 분담금을 상품대금에서 공제 부담한다. "
            + "제20조 [기타] 관계법령에 따른다.")
    return {"id": "k1", "cat": "contract", "label": "특약매입 계약서", "text": text}


def test_split_articles_by_jo():
    secs = G._split_articles("제1조 목적 제2조의2 정의 제11조 [판촉비용 분담금] 부담")
    heads = [h for h, _ in secs]
    assert heads == ["제1조", "제2조의2", "제11조"]
    assert "판촉비용 분담금" in dict(secs)["제11조"]


def test_split_articles_none_when_no_marker():
    assert G._split_articles("조 없는 평문 텍스트") == []


def test_expand_small_doc_unchanged():
    """작은 docs(공동판촉 약정서 등)는 분할 없이 1건(원 레코드 id)."""
    small = {"id": "y1", "cat": "yakjeong", "label": "공동 판촉행사 약정서",
             "text": "제1조 [비용분담] 구매자는 50% 이상 부담."}
    out = G.expand_doc_sections(small)
    assert len(out) == 1 and out[0]["id"] == "y1"


def test_expand_large_doc_surfaces_promo_article():
    """큰 계약서 → 부모(실 id) + 제N조 후보. 판촉 조가 짧고 on-point한 독립 후보로."""
    out = G.expand_doc_sections(_big_contract())
    ids = {c["id"] for c in out}
    assert "k1" in ids                       # 부모(전문) 후보 유지(인용 호환)
    assert "k1::제11조" in ids                # 판촉 조 합성 후보
    promo = next(c for c in out if c["id"] == "k1::제11조")
    assert "판촉비용 분담금" in promo["snippet"]   # 묻혔던 조항이 가시화
    assert promo["title"] == "특약매입 계약서"     # 렌더는 부모 문서명(조 단위 노출 아님)
    assert promo["kind"] == "계약"


def test_expand_large_buried_promo_invisible_without_split():
    """가드: 분할 없으면(현행 snippet[:240]) 판촉 조항이 LLM 시야 밖이었음 — 회귀 잠금."""
    base = G.make_candidate(_big_contract())
    assert "판촉비용 분담금" not in base["snippet"]   # 버그 재현(묻힘)


def test_make_candidates_expand_large_flag():
    cands = G.make_candidates([_big_contract()], cats=("contract",), expand_large=True)
    assert any("판촉비용 분담금" in c["snippet"] for c in cands)
    # 기본(expand_large=False)은 분할 안 함(무회귀)
    plain = G.make_candidates([_big_contract()], cats=("contract",))
    assert len(plain) == 1 and not any("판촉비용 분담금" in c["snippet"] for c in plain)


def test_apply_grounding_dedups_same_doc_articles():
    """같은 문서의 여러 조가 인용돼도 계약 칸에 문서명 1회만(title dedup)."""
    # 두 조 모두 판촉/분담 → 둘 다 랭킹 생존(max_sections=2)하도록 구성.
    two_promo = {"id": "k1", "cat": "contract", "label": "특약매입 계약서",
                 "text": "특약매입 거래계약서 " + "전문 일반조항 텍스트입니다. " * 120
                         + "제11조 [판촉행사] 판촉비용 분담비율 50%. "
                         + "제12조 [판촉비용] 판촉 분담 정산 방법. "}
    cands = G.make_candidates([two_promo], cats=("contract",), expand_large=True, query="판촉 분담")
    ids = {c["id"] for c in cands}
    assert "k1::제11조" in ids and "k1::제12조" in ids        # 둘 다 생존
    jd = {"issues": [{"cited_source_ids": ["k1::제11조", "k1::제12조"]}]}
    G.apply_grounding(jd, cands)
    assert jd["issues"][0]["applicable_rule"] == "「특약매입 계약서」"   # 「..」/「..」 중복 아님


# ── #40: 조 청크 flooding 차단 (단일 약정서 crowd-out 방지) ────
def _flood_contract():
    """다수 조를 가진 대형 계약서 — 판촉 조 1개 + 무관 조 다수(노이즈)."""
    body = "특약매입 거래계약서 "
    for n in (1, 2, 3, 5, 7, 9, 13, 15, 17, 19, 20):
        body += f"제{n}조 [일반조항{n}] 일반적인 거래 조건을 정한다. " * 3
    body += "제11조 [판촉행사] 공급자 판촉비용 분담비율은 50%를 초과할 수 없다. "
    return {"id": "kf", "cat": "contract", "label": "특약매입 계약서", "text": body}


def test_section_flood_capped_to_max():
    """대형 계약서가 전체 조로 후보를 잠식하지 않게 — base + 상위 max_sections개만."""
    out = G.expand_doc_sections(_flood_contract(), query="판촉비 분담")
    secs = [c for c in out if "::" in c["id"]]
    assert len(secs) <= 2                       # 조 청크 상한(flooding 차단)
    assert len(out) <= 3                         # base + ≤2
    assert any("제11조" in c["id"] for c in secs)  # 판촉 조는 살아남음


def test_noise_articles_dropped_only_onpoint_kept():
    """질의 무관 조(일반조항)는 후보에서 제외 — on-point(판촉)만."""
    out = G.expand_doc_sections(_flood_contract(), query="판촉비 분담")
    sec_ids = [c["id"] for c in out if "::" in c["id"]]
    assert all("일반조항" not in c["snippet"] or "판촉" in c["snippet"] for c in out if "::" in c["id"])
    assert not any(("제2조" in s or "제7조" in s or "제20조" in s) for s in sec_ids)  # 노이즈 드롭


def test_yakjeong_survives_contract_flood():
    """핵심 회귀: 큰 계약서 + 단일 약정서 → 약정서가 계약 조 청크에 밀리지 않음(#40)."""
    docs = [_flood_contract(),
            {"id": "yg", "cat": "yakjeong", "label": "공동 판촉행사 약정서",
             "text": "제1조 공동 판촉행사 비용분담 구매자 50% 이상 부담."}]
    cands = G.make_candidates(docs, cats=("contract", "yakjeong"), expand_large=True, query="판촉비 분담")
    assert any(c["id"] == "yg" for c in cands)                       # 약정서 생존
    contract_n = sum(1 for c in cands if c["kind"] == "계약")
    assert contract_n <= 3                                            # 계약 청크 제한(13 → ≤3)


def test_rank_sections_orders_by_query_relevance():
    secs = G._split_articles("제1조 일반조항 제11조 판촉비용 분담 50% 제20조 기타사항")
    ranked = G._rank_sections(secs, "판촉 분담")
    assert ranked and "판촉" in ranked[0][1]      # 판촉 조가 1순위
    heads = [h for h, _ in ranked]
    assert "제1조" not in heads and "제20조" not in heads  # 무관 조 제외(score 0)


def test_prompt_block_has_bucket_representation_nudge():
    cands = G.make_candidates(_docs())
    block = G.candidates_prompt_block(cands)
    assert "유형(사규·계약·약정)별로 대표" in block   # 2B 버킷 대표 nudge
    assert "관련성 우선" in block                      # 무관 억지 인용 금지


# ── #41: relevance 게이트 버킷 대표 (약정 칸 결정론) ──────────
def _bucket_docs():
    return [
        {"id": "kc", "cat": "contract", "label": "특약매입 계약서",
         "text": "특약매입 거래계약서 " + "일반조항. " * 80
                 + "제11조 [판촉행사] 공급자 판촉비용 분담비율 50% 초과 불가."},
        {"id": "yg", "cat": "yakjeong", "label": "공동 판촉행사 약정서",
         "text": "제1조 공동 판촉행사 비용분담 구매자 50% 이상 부담."},
        {"id": "ym", "cat": "yakjeong", "label": "매장이동 약정서",
         "text": "제1조 매장 위치 이동. 해당 없음."},
        {"id": "yi", "cat": "yakjeong", "label": "인테리어 설치 약정서",
         "text": "제1조 인테리어 비용 협력사 부담."},
    ]


def test_bucket_reps_gate_keeps_onpoint_drops_irrelevant():
    """관련 버킷 대표(공동판촉)는 통과, 무관(매장이동·인테리어)은 게이트에서 제외."""
    cands = G.make_candidates(_bucket_docs(), cats=("contract", "yakjeong"),
                              expand_large=True, query="특약매입 판촉비 60%")
    reps = G.relevant_bucket_reps(cands, "특약매입 판촉비 60%")
    assert reps["계약"]["title"] == "특약매입 계약서"
    assert reps["약정"]["title"] == "공동 판촉행사 약정서"   # 관련 약정만
    # 무관 약정(매장이동·인테리어)은 top-1으로 안 뽑힘
    assert reps["약정"]["title"] not in ("매장이동 약정서", "인테리어 설치 약정서")


def test_bucket_reps_empty_when_no_relevant():
    """질의와 무관하면 그 버킷 대표 없음 — 억지 주입 금지(닫힌집합 드롭 정신)."""
    docs = [{"id": "ym", "cat": "yakjeong", "label": "매장이동 약정서", "text": "매장 위치 이동."}]
    cands = G.make_candidates(docs, cats=("yakjeong",), expand_large=True, query="개인정보 제3자 제공")
    reps = G.relevant_bucket_reps(cands, "개인정보 제3자 제공")
    assert "약정" not in reps          # 무관 → 미주입


def test_bucket_reps_cross_topic_no_overinjection():
    """#41 교차주제 가드: 비-판촉 질의(모조품)엔 공동판촉 약정서 과주입 금지.
    부스트(판촉/분담)가 무조건이면 공동판촉이 모든 질의에 score>0 → #37 병렬 약정으로 오주입."""
    cands = G.make_candidates(_bucket_docs(), cats=("contract", "yakjeong"),
                              expand_large=True, query="모조품 짝퉁 상표권 침해")
    reps = G.relevant_bucket_reps(cands, "모조품 짝퉁 상표권 침해")
    assert "약정" not in reps        # 판촉 약정이 모조품 질의에 안 끌려옴
    assert "계약" not in reps        # 특약매입 계약도 모조품과 무관 → 미주입


def test_bucket_reps_boost_is_query_conditional():
    """부스트는 질의가 그 도메인일 때만 게이트를 돕는다(무조건 아님)."""
    docs = [{"id": "yg", "cat": "yakjeong", "label": "공동 판촉행사 약정서",
             "text": "제1조 공동 판촉행사 비용분담 50%."}]
    on = G.make_candidates(docs, cats=("yakjeong",), expand_large=True, query="판촉비 분담")
    off = G.make_candidates(docs, cats=("yakjeong",), expand_large=True, query="개인정보 제3자 제공")
    assert G.relevant_bucket_reps(on, "판촉비 분담").get("약정", {}).get("title") == "공동 판촉행사 약정서"
    assert "약정" not in G.relevant_bucket_reps(off, "개인정보 제3자 제공")   # 부스트 비활성 → 미주입


# ── #42: 패널 버킷 = 그 문서 고유 원문 스니펫 (가짜 보강 제거) ──
def test_bucket_reps_carry_verbatim_snippet():
    """rep snippet = 후보 원문 text(닫힌집합 그대로) — LLM 재작성 0, 한 글자도 안 바뀜."""
    cands = G.make_candidates(_bucket_docs(), cats=("contract", "yakjeong"),
                              expand_large=True, query="특약매입 판촉비 60%")
    reps = G.relevant_bucket_reps(cands, "특약매입 판촉비 60%")
    by_id = {c["id"]: c for c in cands}
    for r in reps.values():
        assert "snippet" in r
        assert r["snippet"] == by_id[r["id"]]["snippet"]   # 원본과 동일(환각 0)


def test_bucket_reps_prefer_clause_section_over_preamble():
    """계약 대표 = 판촉 조항 보유 조 청크(전문/머리말 아님) — snippet 가중 스코어(#42)."""
    contract = {"id": "kc", "cat": "contract", "label": "특약매입 계약서",
                "text": "특약매입 거래계약서 " + "전문 일반조항 텍스트. " * 110
                        + "제12조 [판촉비용 분담] 공급자 판촉비용 분담비율 50% 초과 불가."}
    cands = G.make_candidates([contract], cats=("contract",), expand_large=True, query="판촉비 분담")
    rep = G.relevant_bucket_reps(cands, "판촉비 분담")["계약"]
    assert "판촉비용 분담" in rep["snippet"]          # 조항 스니펫
    assert "전문 일반조항" not in rep["snippet"]       # 전문(preamble) 아님


def test_bucket_reps_distinct_content_per_bucket():
    """버킷마다 서로 다른 고유 원문 — 같은 문장 복붙(가짜 다중 보강) 아님."""
    docs = [
        {"id": "kc", "cat": "contract", "label": "특약매입 계약서",
         "text": "특약매입 거래계약서 " + "전문. " * 80 + "제12조 공급자 판촉비용 분담 50% 초과 불가."},
        {"id": "yg", "cat": "yakjeong", "label": "공동 판촉행사 약정서",
         "text": "제1조 공동 판촉행사 비용은 구매자가 50% 이상 부담."},
        {"id": "sg", "cat": "saryu", "label": "판촉비용 분담 지침",
         "text": "제5조 판촉비용 분담은 협력사에 50% 초과 전가 불가."},
    ]
    cands = G.make_candidates(docs, cats=("contract", "yakjeong", "saryu"),
                              expand_large=True, query="판촉비 분담 50%")
    reps = G.relevant_bucket_reps(cands, "판촉비 분담 50%")
    snippets = [r["snippet"] for r in reps.values()]
    assert len(snippets) == len(set(snippets))         # 모두 상이(복붙 아님)
    assert "구매자" in reps["약정"]["snippet"]          # 약정 고유(구매자 50%)
    assert "협력사" in reps["사규"]["snippet"]          # 사규 고유


def test_bucket_reps_top1_per_bucket():
    """버킷당 최대 1건(top-1) — 같은 버킷 여러 후보여도 최상위만."""
    cands = G.make_candidates(_bucket_docs(), cats=("contract", "yakjeong"),
                              expand_large=True, query="판촉 분담")
    reps = G.relevant_bucket_reps(cands, "판촉 분담")
    assert set(reps.keys()) <= {"계약", "약정", "사규"}
    assert isinstance(reps.get("약정"), dict)   # 단일 dict(top-1), 리스트 아님


# ── #41: 합성 docs id 본문 누출 scrub ─────────────────────────
def _synth_cands():
    return G.make_candidates([
        {"id": "특약매입.docx_contract_1773706789.495567", "cat": "contract",
         "label": "특약매입 계약서", "text": "특약매입 거래계약서 " + "조항. " * 90
                  + "제12조 [판촉비용] 판촉 분담 정산."},
        {"id": "공동판촉.docx_yakjeong_1773706790.1", "cat": "yakjeong",
         "label": "공동 판촉행사 약정서", "text": "제1조 공동 판촉 비용분담 50%."},
    ], cats=("contract", "yakjeong"), expand_large=True, query="판촉 분담")


def test_scrub_synthetic_docs_id_to_title():
    """라이브 누출 재현: [파일명.docx_cat_ts::제N조] → 「title」(UUID 아님도 잡힘)."""
    cands = _synth_cands()
    leak = ("쟁점은 [특약매입.docx_contract_1773706789.495567::제12조, "
            "공동판촉.docx_yakjeong_1773706790.1] 참조")
    out = G.scrub_uuids(leak, cands)
    assert ".docx_" not in out and "::제" not in out      # 합성 id 0건
    assert "「특약매입 계약서」" in out and "「공동 판촉행사 약정서」" in out


def test_scrub_synthetic_section_id_falls_back_to_parent():
    """후보에 없는 조 청크 id여도 부모 base id로 「title」 매핑(::제N조 stray 차단)."""
    cands = _synth_cands()
    out = G.scrub_uuids("근거 특약매입.docx_contract_1773706789.495567::제99조 참조", cands)
    assert "::제99조" not in out and ".docx_" not in out
    assert "「특약매입 계약서」" in out


def test_scrub_synthetic_stray_removed():
    """후보에 전혀 없는 합성 id는 제거(매핑 불가 stray)."""
    out = G.scrub_uuids("근거 미상.docx_contract_999.0 참조", [])
    assert ".docx_" not in out and "참조" in out


def test_scrub_plain_text_unchanged():
    """id 없는 평문은 무변경(무회귀)."""
    assert G.scrub_uuids("제11조 판촉비용 분담비율 50%", []) == "제11조 판촉비용 분담비율 50%"


# ── 배선 회귀 잠금 (소스 가드) ─────────────────────────────────
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_legal_ai_wires_bucket_reps_backstop():
    """#41/#42: 약정 칸 결정론 — reps 계산 + 패널이 그 문서 원문 스니펫을 결정론 표시."""
    src = open(os.path.join(_ROOT, "legal_ai.py"), encoding="utf-8").read()
    assert "relevant_bucket_reps" in src                  # 게이트 대표 계산
    assert "_doc_bucket_reps" in src                      # jd에 저장 + 패널
    assert 'jd.get("_doc_bucket_reps")' in src
    # #42: 패널이 rep의 원문 snippet을 렌더(LLM 공통 분석 복사 아님)
    assert 'rep.get("snippet")' in src


def test_legal_ai_wires_expand_and_label_groups():
    """#39: docs 후보가 expand_large로 조 분할 + detail 라벨이 #33 kind 결정론 분리."""
    src = open(os.path.join(_ROOT, "legal_ai.py"), encoding="utf-8").read()
    assert "expand_large=True" in src                       # 2B 섹션추출 배선
    assert "expand_large=True, query=query" in src          # #40: 조 랭킹용 query 전달(flooding 차단)
    assert "_issue_doc_groups" in src                       # 라벨 결정론 헬퍼
    assert 'grounded_sources' in src
    # detail 라벨이 kind→type 결정론 매핑을 씀(휴리스틱 단독 아님)
    assert '"계약": ("📄 적용 계약서"' in src and '"약정": ("📝 적용 약정서"' in src


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


# ── Bug1: UUID 누출 백스톱 (scrub) ────────────────────────────
_UID = "854249ee-d519-4efa-9fe2-587deb57aed7"


def test_scrub_uuid_known_to_title():
    """후보 id면 「title」로 치환."""
    cands = [{"id": _UID, "cat": "saryu", "kind": "사규", "title": "협력회사 판촉비용 분담 지침", "snippet": ""}]
    cands = [G.make_candidate({"id": _UID, "cat": "saryu", "label": "협력회사 판촉비용 분담 지침"})]
    out = G.scrub_uuids(f"제5항 및 사내 지침인 {_UID}에 따르면 분담", cands)
    assert _UID not in out
    assert "「협력회사 판촉비용 분담 지침」" in out


def test_scrub_uuid_stray_removed():
    """후보에 없는 stray UUID는 제거(군더더기 정리)."""
    out = G.scrub_uuids(f"제5항 및 {_UID}에 따르면", [])
    assert _UID not in out
    assert "에 따르면" in out  # 문장 살아있음


def test_apply_grounding_scrubs_prose_uuid():
    """apply_grounding이 issue prose의 UUID를 정리(렌더 직전)."""
    cands = [G.make_candidate({"id": _UID, "cat": "saryu", "label": "판촉비 지침"})]
    jd = {"issues": [{"cited_source_ids": [_UID],
                      "law_analysis": f"제11조 제5항 및 {_UID}에 따르면 협의 가능"}]}
    G.apply_grounding(jd, cands)
    assert _UID not in jd["issues"][0]["law_analysis"]
    assert "「판촉비 지침」" in jd["issues"][0]["law_analysis"]
