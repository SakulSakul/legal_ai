"""
test_negotiation_brief.py — MD 협상 브리프 재설계 결정론 검증 (streamlit 불필요).

두 층위:
  1) negotiation_util(순수) 단위 — provenance(P2)·예외(P3) 정규화 가드.
  2) 소스 레벨 가드 — gatekeeper 관련성 게이트(작업1)·프롬프트 협상 브리프 규칙(작업2)·
     provenance 버킷 태깅(작업4)·렌더 배선(작업3)이 회귀로 사라지지 않게 잠금.
     (라이브 LLM 출력은 CI에서 못 돌리므로, 예외-우선 '지시'와 '검출 헬퍼'를 잠근다.)
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import negotiation_util as N  # noqa: E402


def _src():
    return open(os.path.join(ROOT, "legal_ai.py"), encoding="utf-8").read()


# ── P2: provenance — 출처는 버킷에서만, 창작은 강등 ────────────
def test_valid_source_maps_to_role():
    for src, role in (("법령", "shield"), ("계약", "table"), ("사규", "internal")):
        m = N.source_meta(src)
        assert m["role"] == role


def test_unknown_source_downgraded():
    """LLM이 날조한 출처는 '근거 출처 불명'으로 강등 — 가짜 출처 배지 금지."""
    lv = N.sanitize_leverage({"source": "날조법전", "point": "가짜 근거"})
    assert lv["provenance_ok"] is False
    assert lv["source"] is None
    assert lv["source_label"] == "근거 출처 불명"


def test_allowed_sources_cross_check():
    """작업4 교차검증: 실제 retrieval 버킷에 없는 출처면 enum이 맞아도 강등."""
    lv = N.sanitize_leverage({"source": "계약", "point": "p"}, allowed_sources={"법령", "사규"})
    assert lv["provenance_ok"] is False


# ── P3: 예외 — 자격요건·서류 세트, '없음 vs 못찾음' ───────────
def test_exception_carries_conditions_and_documents():
    lv = N.sanitize_leverage({
        "source": "법령", "point": "50% 상한", "binding": "conditional",
        "exception": "자발+차별화 시 가능",
        "conditions": ["자발 요청 공문", ""], "documents": ["행사 기획안"],
    })
    assert lv["has_exception"] is True
    assert lv["conditions"] == ["자발 요청 공문"]   # 빈 항목 제거
    assert lv["documents"] == ["행사 기획안"]
    assert lv["binding_label"] == "조건부"


def test_nullish_exception_not_treated_as_exception():
    for bad in (None, "", "null", "없음", "N/A"):
        lv = N.sanitize_leverage({"source": "법령", "point": "p", "exception": bad})
        assert lv["has_exception"] is False
        assert lv["conditions"] == [] and lv["documents"] == []


def test_no_room_vs_exception_uncertain_distinct():
    """'예외 없음(확인)'과 '예외 못 찾음(미확인)'은 서로 다른 칸이어야(과보수/과허용 방지)."""
    b = N.sanitize_brief({
        "bottom_line": "결론",
        "no_room": ["법정 50% 상한"],
        "exception_uncertain": ["고시 추가 예외 가능성"],
    })
    assert b["no_room"] == ["법정 50% 상한"]
    assert b["exception_uncertain"] == ["고시 추가 예외 가능성"]
    assert b["no_room"] != b["exception_uncertain"]


# ── 예외-누락 = FAIL (골든 가드) ───────────────────────────────
def test_exception_exposed_when_present():
    """금지+예외 쌍: 예외 경로가 노출되면 통과."""
    brief = {"bottom_line": "조건부 가능", "leverage": [
        {"source": "법령", "point": "50% 상한", "exception": "자발+차별화 시 가능",
         "conditions": ["공문"], "documents": ["기획안"]},
    ]}
    assert N.has_exception_exposed(brief) is True


def test_exception_silence_is_detected_as_fail():
    """과보수: 금지만 나열하고 예외/없음확인/미확인 어디에도 언급 없으면 FAIL로 검출."""
    brief = {"bottom_line": "원칙 불가", "leverage": [
        {"source": "법령", "point": "50% 상한", "exception": None},
    ]}
    assert N.has_exception_exposed(brief) is False  # 예외 침묵 → 골든 FAIL 신호


def test_no_room_counts_as_exception_exposed():
    """예외가 진짜 없으면 no_room 명시로 '침묵 아님' 처리."""
    brief = {"leverage": [{"source": "법령", "point": "p", "exception": None}],
             "no_room": ["예외 없음 확인된 절대 상한"]}
    assert N.has_exception_exposed(brief) is True


def test_empty_brief_helpers():
    assert N.is_empty_brief(None) is True
    assert N.is_empty_brief({}) is True
    assert N.is_empty_brief({"bottom_line": "x"}) is False


# ── 소스 레벨 가드 (회귀 잠금) ─────────────────────────────────
def test_gatekeeper_relevance_gate_present():
    """작업1: 무조건 강제주입이 관련성 게이트(강등, 드롭 아님)로 바뀌었는지."""
    src = _src()
    assert "관련성 게이트" in src
    assert "demoted_laws" in src and "기타 참고 법령" in src
    # 옛 무조건 주입 문구는 사라져야
    assert "무조건 강제 주입" not in src


def test_provenance_buckets_tagged():
    """작업4: gatekeeper_text가 [출처:사규]/[출처:계약]/[출처:법령] 버킷을 기계 태깅하는지."""
    src = _src()
    for tag in ("[출처:사규]", "[출처:법령]"):
        assert tag in src, f"{tag} 버킷 태그 누락"
    assert "출처:{_src}" in src or "[출처:계약]" in src  # 계약은 cat→동적 태깅


def test_prompt_has_negotiation_brief_and_exception_rules():
    """작업2: 합성 프롬프트에 negotiation_brief 스키마 + P3 예외-우선 규칙이 있는지(잠금)."""
    src = _src()
    assert '"negotiation_brief"' in src
    assert "no_room" in src and "exception_uncertain" in src
    # P3 — 예외를 자격요건·서류와 세트로, '못 찾음 ≠ 없음'
    assert "예외 우선 노출" in src or "예외(단서" in src
    assert "출처를\n" in src or "추정·창작" in src  # P2 — 출처 창작 금지
    # 규칙0 완화 — 무조건 무효 문구 제거, 관련성 게이트 적용
    assert "빠뜨리면 출력물 전체가 무효" not in src


def test_render_negotiation_brief_wired():
    """작업3: hero 직후 render_negotiation_brief 호출 배선 + 강등 expander 유지."""
    src = _src()
    assert src.count("render_negotiation_brief(") >= 3  # 정의 + 2개 렌더 사이트
    assert "상세 근거" in src  # 상세 법령 근거는 강등 섹션으로 유지


# ── 예외 절 포함 retrieval (작업1 보강 — 60% 오답 근본 원인) ───
def test_gemini_stage1_collects_exception_clauses():
    """Gemini 수집 프롬프트가 예외 절(적용제외 항)을 '동반 수집'하도록 지시하는지.
    이게 없으면 제11조⑤가 검증 데이터에 안 들어와 #27 P3가 있어도 못 띄움(과보수 오답)."""
    src = _src()
    assert "예외 절 동반 수집" in src or "적용제외 항" in src
    # 제11조 ④ 상한 + ⑤ 적용제외(자발+차별화)를 함께 수집하라는 명시
    assert "제11조" in src and ("⑤" in src or "적용제외" in src)
    assert "자발" in src and "차별화" in src


def test_synthesis_article11_exception_anchor():
    """합성 프롬프트(P3)에 제11조 ④만 단정하고 ⑤를 빠뜨리지 말라는 구체 앵커가 있는지."""
    src = _src()
    assert "④만 단정" in src and "법률 오류" in src


# ── 작업A: issues→brief 결정론 조립 (모델이 brief 빼먹어도 형태 보장) ──
def _issue_60pct():
    return {"verdict": "conditional", "verdict_reason": "60% 분담 — 자발+차별화 시 협의 가능",
            "issues": [
                {"applicable_law": "대규모유통업법 제11조",
                 "law_analysis": "- 제11조④ 분담 50% 초과 금지(상한)\n- 제11조⑤ 예외: 자발적 요청+차별화 시 제1~4항 적용제외 → 60% 협의 가능",
                 "recommendation": "자발적 요청 공문·차별화 기획안 확보 시 협의 가능",
                 "applicable_rule": "「판촉비 지침」 제5조", "rule_analysis": "내부 50% 원칙"},
                {"applicable_rule": "특약매입계약 제12조④", "rule_analysis": "⑤ 요건 첨부"},
            ]}


def test_assemble_brief_maps_issues_to_lanes():
    b = N.assemble_brief(_issue_60pct())
    srcs = {lv["source"] for lv in b["leverage"]}
    assert "법령" in srcs and "사규" in srcs and "계약" in srcs  # 🛡/📋/📄
    assert b["bottom_line"]


def test_assemble_detects_exception_as_card():
    """⑤(적용제외·자발·차별화) 신호 → 법령 레버리지가 conditional + exception(=🃏 card)."""
    b = N.build_brief(_issue_60pct())  # sanitized — render가 받는 형태
    law = next(lv for lv in b["leverage"] if lv["source"] == "법령")
    assert law["has_exception"] is True
    assert law["binding"] == "conditional"
    assert law["conditions"]  # 자격요건 추출


def test_prohibition_is_fixed_shield_not_card():
    """예외 신호 없는 금지·상한 → 🛡 fixed shield(🃏 아님). '예외 요건 못 갖춤'은 거짓 예외로
    안 잡힘(B3 회귀가드 — §11④ 상한이 🃏로 새던 버그)."""
    jd = {"issues": [{"title": "50% 상한", "applicable_law": "X법 제1조",
                      "law_analysis": "분담비율은 50%를 초과하여서는 아니 된다. 별도의 예외 요건을 갖추지 못한 경우 위반."}]}
    b = N.build_brief(jd)
    law = [lv for lv in b["leverage"] if lv["source"] == "법령"]
    assert law and not any(lv["has_exception"] for lv in law)
    assert all(lv["binding"] == "fixed" for lv in law)


def test_build_brief_prefers_model_then_assembles():
    """모델 brief 있으면 그걸, 없으면 issues 조립. 둘 다 없으면 None(미렌더)."""
    model = {"negotiation_brief": {"bottom_line": "모델결론",
             "leverage": [{"source": "법령", "point": "p"}]}, "issues": []}
    assert N.build_brief(model)["bottom_line"] == "모델결론"
    assembled = N.build_brief(_issue_60pct())
    assert assembled and assembled["leverage"]
    assert N.build_brief({"verdict": "approved", "issues": []}) is None


# ── 작업B/C: relevance gate(라이브) · 1단계 제거 ────────────────
def test_no_step1_two_tier_remnants():
    """작업C: 1단계/2단계·'두 가지 수준' 잔재 0 (단일 목적 도구)."""
    src = _src()
    for bad in ("1단계", "2단계", "두 가지 수준"):
        assert bad not in src, f"'{bad}' 잔재"


def test_gemini_stage1_bonded_relevance_gated():
    """작업B: Gemini stage1이 보세판매장고시를 '모든 질의 무조건'이 아니라 관련성 게이트로."""
    src = _src()
    assert "모든 질의에서 law_findings에 반드시 포함" not in src
    assert "빠뜨리면 시스템 오류로 간주" not in src
    assert "관련성 게이트" in src  # 관련 질의에서만


# ── 답변 v2 회귀 가드 (라이브 피드백 — B1~B4·S1·S3) ───────────
def test_b1_step1_buttons_deleted():
    """B1: 1단계/'사내 기준 문의' 진입점 자체 삭제(relabel 아님), 버튼 2개."""
    src = _src()
    assert "사내 기준 문의" not in src and "사내 규정 문의" not in src
    assert "st.columns(2)" in src


def test_b2_action_plan_topic_separated():
    """B2: 모조품 액션플랜이 전 토픽에 누수되지 않게 토픽맵. 블록경로 하드코딩 제거."""
    src = _src()
    assert "_TOPIC_ACTION_PLAN" in src
    assert "판촉비분담" in src and "재고조사" in src  # 모조품 액션은 모조품 키에만
    # 블록 json_data가 action_plan을 토픽맵에서(고정 모조품 문자열 직접대입 아님)
    assert '"action_plan": action_plan' in src


def test_s1_verdict_derived_not_hardcoded():
    """S1: 블록경로 verdict가 파생(예외→conditional). 고정 rejected 아님."""
    src = _src()
    assert "_has_exc" in src and '_verdict = "conditional"' in src


def test_s3_four_block_render_present():
    """S3: 협상 브리프가 결론/판단근거/권장행동 + 분류 5종 구조."""
    src = _src()
    for blk in ("결론", "판단 근거", "권장 행동"):
        assert blk in src
    assert "약정·행정규칙" in src


def test_b4_law_name_no_dup():
    """B4: cited_laws law명 중복 버그(split[0]+전체) 제거 → 정규식 분리."""
    src = _src()
    assert '"article": issue["applicable_laws"],' not in src  # 옛 버그 라인 제거


def test_s2_brief_points_short_via_title():
    """S2: assemble가 조문 전문 아닌 '제목' 기반 짧은 point를 쓰는지(_short)."""
    jd = {"issues": [{"title": "50% 상한 (제11조④)",
                      "applicable_law": "대규모유통업법 제11조",
                      "law_analysis": "x" * 400}]}
    b = N.build_brief(jd)
    law = [lv for lv in b["leverage"] if lv["source"] == "법령"][0]
    assert law["point"] == "50% 상한 (제11조④)"  # 제목 사용, 400자 prose 아님


def test_s4_freshness_computed_not_hardcoded():
    """S4: 블록경로 freshness가 정적 'FRESH' 주입이 아니라 계산값(check_block_freshness)인지."""
    src = _src()
    assert '"freshness": "FRESH" if any' not in src  # 정적 거짓 단언 제거
    assert "_compute_block_freshness" in src
    assert "check_block_freshness" in src and "freshness_util" in src


def test_promo_action_plan_branch_placement():
    """A(P0): 권장행동 §11①②는 ⑤-미충족 가지에만. ⑤ 충족 시 ①②③④ 적용제외이므로
    충족 가지에 §11①②를 의무로 두면 인용 오류(라이브 60% 답 오배치 회귀가드)."""
    src = _src()
    # ⑤ 미충족 가지: §11①②③④ 적용 + 서면약정(§11①②)
    assert "[⑤ 미충족 시] §11①②③④ 적용" in src
    assert "서면약정·동시교부(§11①②)" in src
    # ⑤ 충족 가지: §11①②는 의무 아님(입증자료)
    assert "§11①② 의무 아님" in src
    # 옛 버그 문구(충족 시 서면약정 의무) 제거
    assert "2요건 충족 시 서면약정·동시교부(§11①②)" not in src
