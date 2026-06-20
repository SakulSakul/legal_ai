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
