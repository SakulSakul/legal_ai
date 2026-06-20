"""
kpd_mcp.py — K Public Data MCP(legal_research) 응답 파서 + 시행일 해석 (순수·key-free)

Freshness sweep이 법령·조문·행정규칙의 '현행 시행일'을 읽기 위한 파싱 로직.
네트워크·키·streamlit을 일절 import 하지 않는다 — 실제 MCP 호출은 호출부가 주입한
call 함수로 하고, 여기서는 응답 텍스트 파싱만 한다(완전 결정론, mock 단위 테스트).

fail-safe: 불명확·미발견·다건이면 ok=False(추측 금지) → sweep이 NEEDS_REVIEW 처리.
"""
import json
import re

_DATE = re.compile(r"(\d{4})[-.]?\s*(\d{2})[-.]?\s*(\d{2})")


def article_to_6digit(article: str):
    """'제178조' → '017800', '제69조의5' → '006905'. 파싱 실패 시 None.
    규칙: 본조번호*100 + 의-가지번호(없으면 0), 6자리 zero-pad."""
    m = re.search(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", article or "")
    if not m:
        return None
    main = int(m.group(1))
    sub = int(m.group(2)) if m.group(2) else 0
    return f"{main * 100 + sub:06d}"


def _norm_date(s):
    m = _DATE.search(s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _as_items(raw):
    """raw 텍스트를 JSON으로 파싱해 항목 리스트로 정규화. 실패 시 None."""
    try:
        data = json.loads(raw)
    except Exception:
        return None
    if isinstance(data, dict):
        for k in ("results", "laws", "data", "items", "list", "admin_rules"):
            if isinstance(data.get(k), list):
                return data[k]
        return [data]
    if isinstance(data, list):
        return data
    return None


def _get(item, *keys):
    for k in keys:
        if isinstance(item, dict) and item.get(k) not in (None, ""):
            return item[k]
    return None


def parse_search_laws(raw, exact_name, law_type=None):
    """search_laws 응답에서 '정확 매칭' 1건의 law_id·시행일 추출.
    동명 다건(관세법/관세법 시행령 …) 또는 0건이면 ok=False(오선택 방지)."""
    items = _as_items(raw)
    if items is None:
        return {"ok": False, "law_id": None, "effective_date": None, "reason": "응답 JSON 파싱 실패"}
    matches = []
    for it in items:
        name = _get(it, "law_name", "법령명", "lawNm", "name")
        if name is None or str(name).strip() != str(exact_name).strip():
            continue
        if law_type:
            t = _get(it, "law_type", "법령구분", "종류", "type")
            if t and str(t).strip() != str(law_type).strip():
                continue
        matches.append(it)
    if len(matches) != 1:
        return {"ok": False, "law_id": None, "effective_date": None,
                "reason": f"정확 매칭 {len(matches)}건 (1건이어야 함)"}
    it = matches[0]
    law_id = _get(it, "law_id", "법령ID", "lawId", "id")
    eff = _norm_date(str(_get(it, "effective_date", "시행일자", "시행일", "enforce_date") or ""))
    return {"ok": law_id is not None, "law_id": law_id, "effective_date": eff,
            "reason": "" if law_id is not None else "law_id 없음"}


def parse_article_sub(raw):
    """get_law_article_sub 응답에서 조문 시행일자 추출."""
    items = _as_items(raw)
    eff = None
    if items:
        eff = _norm_date(str(_get(items[0], "effective_date", "시행일자", "시행일", "enforce_date") or ""))
    if eff is None:
        eff = _norm_date(raw)  # 텍스트 폴백
    if eff is None:
        return {"ok": False, "effective_date": None, "reason": "시행일 파싱 실패"}
    return {"ok": True, "effective_date": eff, "reason": ""}


def parse_admin_rules(raw, exact_name=None):
    """search_admin_rules 응답에서 admrul_id·발령/시행일 추출(정확 매칭 1건)."""
    items = _as_items(raw)
    if items is None:
        return {"ok": False, "admrul_id": None, "effective_date": None, "reason": "응답 JSON 파싱 실패"}
    cand = items
    if exact_name:
        cand = [it for it in items
                if str(_get(it, "admrul_nm", "행정규칙명", "rule_name", "name") or "").strip() == str(exact_name).strip()]
    if len(cand) != 1:
        return {"ok": False, "admrul_id": None, "effective_date": None, "reason": f"매칭 {len(cand)}건 (1건이어야 함)"}
    it = cand[0]
    aid = _get(it, "admrul_id", "행정규칙ID", "id")
    eff = _norm_date(str(_get(it, "effective_date", "시행일자", "시행일", "발령일자", "발령일") or ""))
    return {"ok": (aid is not None or eff is not None), "admrul_id": aid, "effective_date": eff,
            "reason": "" if (aid or eff) else "id/시행일 없음"}


def resolve_law_effective_date(call_fn, law_name, article=None, law_type="법률"):
    """
    (법령명[, 조문])의 현행 시행일을 해석. call_fn(action, **params)->raw text 주입.
    불명확·미발견 시 ok=False (sweep이 NEEDS_REVIEW 처리). 추측하지 않는다.
    """
    raw1 = call_fn("search_laws", query=law_name, search_type="law_name")
    s = parse_search_laws(raw1 or "", law_name, law_type)
    if not s["ok"]:
        return {"ok": False, "effective_date": None, "law_id": None, "reason": f"법령 검색 실패: {s['reason']}"}
    if not article:
        return {"ok": s["effective_date"] is not None, "effective_date": s["effective_date"],
                "law_id": s["law_id"], "reason": "" if s["effective_date"] else "법령 시행일 없음"}
    six = article_to_6digit(article)
    if six is None:
        return {"ok": False, "effective_date": None, "law_id": s["law_id"], "reason": f"조문번호 변환 실패: {article}"}
    raw2 = call_fn("get_law_article_sub", law_id=s["law_id"], article=six)
    a = parse_article_sub(raw2 or "")
    return {"ok": a["ok"], "effective_date": a["effective_date"], "law_id": s["law_id"], "reason": a["reason"]}
