"""
negotiation_util.py — MD 협상 브리프(negotiation_brief)의 순수 로직 (streamlit 비의존 → 단위테스트 가능).

렌더(st.*)는 legal_ai.py가 맡고, 여기서는 LLM이 낸 negotiation_brief를 '신뢰성 가드' 관점에서
정규화·검증하는 순수 함수만 둔다. 두 불변식:
  P2 — 출처(source)는 retrieval 버킷(법령/사규/계약)에서만. 알 수 없는 출처는 '근거 출처 불명'으로
       강등(LLM의 출처 창작을 렌더에 노출하지 않음 = '투명성 연극' 방지).
  P3 — 예외는 자격요건(conditions)·입증서류(documents)와 세트. 그리고 '예외 없음(no_room)'과
       '예외 못 찾음(exception_uncertain)'을 구분(못 찾음 ≠ 없음).
"""

# 출처 → (아이콘, MD 평이 라벨, role). P2: 아래 세 버킷만 인정한다.
#   법령=방패(못 양보해도 되는 정당한 근거·룰북) / 계약=협상 테이블(내가 서명) / 사규=내부 유연성
SOURCE_ROLE = {
    "법령": {"icon": "🛡", "label": "양보 안 해도 되는 선 (법·룰북)",   "role": "shield"},
    "계약": {"icon": "📄", "label": "거래 성립 지점 (계약·협의)",       "role": "table"},
    "사규": {"icon": "📋", "label": "우리 사규 (내부 유연성)",         "role": "internal"},
}
UNKNOWN_SOURCE = {"icon": "❔", "label": "근거 출처 불명", "role": "unknown"}
VALID_SOURCES = set(SOURCE_ROLE)

# 구속력 라벨 — 못 바꿈 / 조건부 / 협의 가능
BINDING_LABEL = {"fixed": "못 바꿈", "conditional": "조건부", "negotiable": "협의 가능"}

# 예외 '없음'을 뜻하는 표기들 (LLM이 exception 슬롯에 넣는 공백/none 류)
_NULLISH = ("", "null", "none", "없음", "n/a", "na")


def source_meta(source):
    """출처 문자열 → 시각 메타. 미인정 출처는 UNKNOWN(강등)."""
    return SOURCE_ROLE.get(source, UNKNOWN_SOURCE)


def _has_exception(exception):
    return bool(exception) and str(exception).strip().lower() not in _NULLISH


def sanitize_leverage(item, allowed_sources=None):
    """단일 leverage 항목을 렌더 가능한 구조로 정규화 (P2/P3 가드).

    - source가 세 버킷(또는 교차검증용 allowed_sources)에 없으면 '근거 출처 불명'으로 강등.
      provenance_ok=False로 표시해 렌더가 가짜 출처 배지를 달지 않게 한다.
    - exception이 실질적이면 conditions(자격요건)·documents(서류)를 리스트로 보장.
    """
    if not isinstance(item, dict):
        return None
    src = item.get("source")
    ok = src in VALID_SOURCES
    if allowed_sources is not None:
        ok = ok and src in set(allowed_sources)
    meta = source_meta(src) if ok else UNKNOWN_SOURCE
    has_exc = _has_exception(item.get("exception"))
    binding = item.get("binding") if item.get("binding") in BINDING_LABEL else None
    return {
        "source": src if ok else None,
        "provenance_ok": ok,
        "icon": meta["icon"],
        "source_label": meta["label"],
        "role": meta["role"],
        "binding": binding,
        "binding_label": BINDING_LABEL.get(binding, ""),
        "point": (item.get("point") or "").strip(),
        "has_exception": has_exc,
        "exception": str(item.get("exception")).strip() if has_exc else None,
        "conditions": [str(c).strip() for c in (item.get("conditions") or []) if str(c).strip()] if has_exc else [],
        "documents": [str(d).strip() for d in (item.get("documents") or []) if str(d).strip()] if has_exc else [],
    }


def sanitize_brief(brief, allowed_sources=None):
    """negotiation_brief 전체를 렌더가 바로 쓸 정규화 구조로. 형식 불량이면 None.

    allowed_sources: 실제 retrieval 버킷 집합(작업4의 [출처:X])을 주면 교차검증,
    없으면 enum(법령/사규/계약) 검증만 수행.
    """
    if not isinstance(brief, dict):
        return None
    lev = [sanitize_leverage(it, allowed_sources) for it in (brief.get("leverage") or [])]
    lev = [x for x in lev if x and x["point"]]
    return {
        "bottom_line": (brief.get("bottom_line") or "").strip(),
        "leverage": lev,
        "no_room": [str(x).strip() for x in (brief.get("no_room") or []) if str(x).strip()],
        "exception_uncertain": [str(x).strip() for x in (brief.get("exception_uncertain") or []) if str(x).strip()],
        "escalation": (brief.get("escalation") or "").strip(),
    }


def has_exception_exposed(brief):
    """P3 가드: 브리프가 예외에 대해 '침묵'하지 않았는지.

    예외 경로(leverage.exception) / 예외 없음 확인(no_room) / 못 찾음(exception_uncertain) 중
    하나라도 명시돼 있으면 True. 과보수(금지만 알려주고 예외를 조용히 빠뜨림) 검출용 — 골든 테스트.
    """
    b = sanitize_brief(brief)
    if not b:
        return False
    if any(x["has_exception"] for x in b["leverage"]):
        return True
    return bool(b["no_room"] or b["exception_uncertain"])


def is_empty_brief(brief):
    """렌더가 '브리프 없음'(구버전 메시지 등)을 판단할 때 사용."""
    b = sanitize_brief(brief)
    if not b:
        return True
    return not (b["bottom_line"] or b["leverage"] or b["no_room"] or b["exception_uncertain"])


# ── issues → brief 결정론 조립 (모델이 negotiation_brief를 빼먹어도 형태 보장) ──
# 휴리스틱 신호 — 모델이 잘 채우는 issues(applicable_law/law_analysis/applicable_rule/…)에서
# 출처·예외·금지를 추출한다. 모델의 별도 brief emit에 의존하지 않는 게 핵심(형태 안정).
# 예외(🃏 card) 판정 — '강한' 신호만. 바른 '예외' 키워드는 '예외 없음/예외 요건 못 갖춤'에
# 오탐(B3: §11④ 상한이 🃏로 새던 원인) → 강신호 + 부정문맥 가드로 분리.
_EXC_STRONG = ("적용하지 아니", "적용되지 아니", "적용제외", "예외로 한다", "이 경우 제1항부터")
_EXC_NEG = ("갖추지 못", "충족하지", "미충족", "미달", "없으면", "없는 경우", "예외 없", "해당하지 아니")
_NO_ROOM_HINTS = ("초과하여서는 아니", "초과할 수 없", "원칙적 불가", "금지", "할 수 없", "상한")
_CONTRACT_HINTS = ("계약", "특약", "약정", "공동판촉", "거래기본", "표준계약")
_DOC_HINTS = ("요청", "입증", "서면", "동의", "공문", "기획안", "자료", "증빙")


def _short(s, n=90):
    """S2: 브리프는 짧게. 조문 전문은 [상세 근거] expander로, 브리프엔 한 줄 요약."""
    s = (s or "").replace("**", "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _first_line(text):
    for ln in (text or "").replace("**", "").split("\n"):
        ln = ln.strip().lstrip("-•·▶ ").strip()
        if ln:
            return ln
    return ""


def _line_with(text, hints):
    for ln in (text or "").replace("**", "").split("\n"):
        s = ln.strip().lstrip("-•·▶ ").strip()
        if s and any(h in s for h in hints):
            return s
    return ""


def _is_exception_issue(title, law_an):
    """B3: 이 이슈가 '예외(🃏)'인가. 제목이 예외/적용제외를 가리키거나, 본문에 강한 예외
    신호가 부정문맥 없이 있을 때만 True. (§11④ 상한처럼 '예외 요건 못 갖춤'은 예외 아님.)"""
    t = title or ""
    if "예외" in t or "적용제외" in t:
        return True
    for ln in (law_an or "").replace("**", "").split("\n"):
        if any(s in ln for s in _EXC_STRONG) and not any(n in ln for n in _EXC_NEG):
            return True
    return False


def _exc_detail(law_an, rec):
    """예외 경로 한 줄(짧게). 강신호 줄 우선, 부정문맥 제외."""
    for src in (law_an, rec):
        for ln in (src or "").replace("**", "").split("\n"):
            s = ln.strip().lstrip("-•·▶ ").strip()
            if any(x in s for x in _EXC_STRONG) and not any(n in s for n in _EXC_NEG):
                return _short(s)
    return ""


def assemble_brief(json_data):
    """issues로부터 결정론 brief 조립(S2 짧게·명사형). 매핑:
      applicable_law → 🛡 법령(금지·상한, fixed). 예외 이슈(⑤ 등) → 🃏 card(conditional).
      applicable_rule → 📄 계약(계약 신호) 또는 📋 사규. point는 '제목'(조문 전문 아님, B4 중복 방지).
      예외가 토픽에 하나도 없으면 fixed 금지조항을 no_room(🚫 못 움직임)으로 이동.
    """
    if not isinstance(json_data, dict):
        return None
    leverage = []
    for iss in (json_data.get("issues") or []):
        if not isinstance(iss, dict):
            continue
        title = (iss.get("title") or "").strip()
        law = (iss.get("applicable_law") or "").strip()
        law_an = iss.get("law_analysis") or ""
        rec = iss.get("recommendation") or ""
        rule = (iss.get("applicable_rule") or "").strip()
        rule_an = iss.get("rule_analysis") or ""
        # 짧은 point(S2/B4): 제목 우선(조문 전문·법령명 중복 방지). 없으면 law_analysis 첫 줄 축약.
        point = _short(title) or _short(_first_line(law_an))
        if law and law not in ("해당 없음", "없음") and point:
            has_exc = _is_exception_issue(title, law_an)
            lv = {
                "source": "법령", "role": "shield",
                "binding": "conditional" if has_exc else "fixed",
                "point": point,
            }
            if has_exc:
                conds = _line_with(rec, _DOC_HINTS) or _line_with(law_an, ("요청", "입증", "차별화", "자발"))
                lv["exception"] = _exc_detail(law_an, rec) or "예외 경로 있음(상세 근거 참조)"
                lv["conditions"] = [_short(conds)] if conds else []
                lv["documents"] = []
            leverage.append(lv)
        if rule and rule not in ("해당 없음", "없음"):
            is_contract = any(h in (rule + rule_an) for h in _CONTRACT_HINTS)
            leverage.append({
                "source": "계약" if is_contract else "사규",
                "role": "table" if is_contract else "internal",
                "binding": "negotiable",
                "point": _short(f"{rule} — {_first_line(rule_an)}" if rule_an else rule),
            })
    # 동일 레버리지 중복 제거(이슈마다 같은 사규/계약이 반복될 수 있음 — S2 간결).
    _seen, _dedup = set(), []
    for lv in leverage:
        k = (lv["source"], lv["point"])
        if k in _seen:
            continue
        _seen.add(k)
        _dedup.append(lv)
    leverage = _dedup
    # 금지·상한 조항은 🛡 fixed 레버리지로 유지(렌더가 분류별 판단근거로 표시). no_room은
    # 모델이 emit한 brief에서만(진짜 hard-no를 LLM이 명시한 경우) — 자동 이동 안 함.
    return {
        "bottom_line": _short(_first_line(json_data.get("verdict_reason")) or _first_line(json_data.get("summary"))),
        "leverage": leverage, "no_room": [],
        "exception_uncertain": [], "escalation": "",
    }


def build_brief(json_data):
    """렌더용 최종 brief. 모델이 emit한 negotiation_brief가 실하면 그걸(richer·provenance 정확),
    아니면 issues로 조립(형태 보장). 어느 경로든 sanitize 통과 → 정규화 일관. 빈 결과면 None."""
    if not isinstance(json_data, dict):
        return None
    model_brief = json_data.get("negotiation_brief")
    b = sanitize_brief(model_brief) if not is_empty_brief(model_brief) else sanitize_brief(assemble_brief(json_data))
    if not b or not (b["bottom_line"] or b["leverage"] or b["no_room"] or b["exception_uncertain"]):
        return None
    return b
