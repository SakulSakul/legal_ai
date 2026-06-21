"""
kpd_mcp.py — K Public Data MCP(legal_research) 응답 파서 + 시행일 해석 (순수·key-free)

Freshness sweep이 법령·조문·행정규칙의 '현행 시행일'을 읽기 위한 파싱 로직.
네트워크·키·streamlit을 일절 import 하지 않는다 — 실제 MCP 호출은 호출부가 주입한
call 함수로 하고, 여기서는 응답 텍스트 파싱만 한다(완전 결정론, mock 단위 테스트).

라이브 확정 형식: 응답은 JSON이 아니라 '마크다운'(PR #19 스모크). 마크다운이 정상
경로이고, JSON은 혹시 모를 대비로 먼저 시도만 한다.

fail-safe: 불명확·미발견·다건이면 ok=False(추측 금지) → sweep이 NEEDS_REVIEW 처리.
"""
import json
import re

# "1. [280363] 관세법" 형태의 항목 시작 줄
_ITEM = re.compile(r"^\s*\d+\.\s*\[(\d+)\]\s*(.+?)\s*$")
_DATE = re.compile(r"(\d{4})[-.]?\s*(\d{2})[-.]?\s*(\d{2})")


def article_to_6digit(article: str):
    """'제178조' → '017800', '제69조의5' → '006905'. 파싱 실패 시 None."""
    m = re.search(r"제\s*(\d+)\s*조(?:\s*의\s*(\d+))?", article or "")
    if not m:
        return None
    main = int(m.group(1))
    sub = int(m.group(2)) if m.group(2) else 0
    return f"{main * 100 + sub:06d}"


def _norm_date(s):
    """'20260401' / '2026-04-01' / '2026.04.01' → '2026-04-01'."""
    m = _DATE.search(s or "")
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _try_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def _json_list(data):
    if isinstance(data, dict):
        for k in ("results", "laws", "data", "items", "list", "admin_rules"):
            if isinstance(data.get(k), list):
                return data[k]
        return [data]
    if isinstance(data, list):
        return data
    return []


def _get(item, *keys):
    for k in keys:
        if isinstance(item, dict) and item.get(k) not in (None, ""):
            return item[k]
    return None


def _md_items(raw):
    """마크다운 응답을 [{id, name, detail}]로 분해. detail = 항목 다음 비어있지 않은 줄."""
    lines = (raw or "").split("\n")
    n = len(lines)
    items = []
    i = 0
    while i < n:
        m = _ITEM.match(lines[i])
        if not m:
            i += 1
            continue
        lid, name = int(m.group(1)), m.group(2).strip()
        detail = ""
        j = i + 1
        while j < n and not lines[j].strip():
            j += 1
        if j < n and not _ITEM.match(lines[j]):
            detail = lines[j].strip()
        items.append({"id": lid, "name": name, "detail": detail})
        i += 1
    return items


def _date_from_detail(detail, labels=("시행", "발령")):
    for lab in labels:
        m = re.search(lab + r"\s*[:：]?\s*(\d{8})", detail or "")
        if m:
            return _norm_date(m.group(1))
    return None


def _type_from_detail(detail):
    """laws 상세줄 '법률 | 소관:...' → '법률'."""
    m = re.match(r"\s*([^|:\s]+)\s*\|", detail or "")
    return m.group(1) if m else None


def _law_items(raw):
    """법령 항목을 [{law_id, name, law_type, eff}]로 정규화 (JSON 우선, 마크다운 기본)."""
    data = _try_json(raw)
    if data is not None:
        out = []
        for x in _json_list(data):
            out.append({
                "law_id": _get(x, "law_id", "법령ID", "lawId", "id"),
                "name": str(_get(x, "law_name", "법령명", "lawNm", "name") or "").strip(),
                "law_type": _get(x, "law_type", "법령구분", "종류", "type"),
                "eff": _norm_date(str(_get(x, "effective_date", "시행일자", "시행일", "enforce_date") or "")),
            })
        return out
    return [{"law_id": it["id"], "name": it["name"],
             "law_type": _type_from_detail(it["detail"]),
             "eff": _date_from_detail(it["detail"], ("시행", "발령"))}
            for it in _md_items(raw)]


def _name_variants(name):
    """KPD 결과명 '정식명 (약칭)' → (정식명, 약칭|None). 약칭 접미사 분리.
    (예: '대규모유통업에서의 거래 공정화에 관한 법률 (대규모유통업법)')."""
    s = str(name or "").strip()
    m = re.match(r"^(.*?)\s*\(([^)]*)\)\s*$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return s, None


def parse_search_laws(raw, exact_name, law_type=None):
    """search_laws 응답에서 '정확 매칭' 1건의 law_id·시행일 추출.
    KPD 결과명의 '(약칭)' 접미사를 분리해 정식명·약칭 어느 쪽과도 정확 일치 허용
    (시행령 등 부분일치 오선택은 여전히 배제). 동명 다건/0건이면 ok=False."""
    target = str(exact_name).strip()
    matches = []
    for it in _law_items(raw):
        base, alias = _name_variants(it["name"])
        if target != base and target != alias:
            continue
        if law_type and it.get("law_type") and it["law_type"] != law_type:
            continue
        matches.append(it)
    if len(matches) != 1:
        return {"ok": False, "law_id": None, "effective_date": None,
                "reason": f"정확 매칭 {len(matches)}건 (1건이어야 함)"}
    it = matches[0]
    return {"ok": it["law_id"] is not None, "law_id": it["law_id"],
            "effective_date": it["eff"], "reason": "" if it["law_id"] is not None else "law_id 없음"}


def parse_article_sub(raw):
    """get_law_article_sub 응답에서 조문 시행일자 추출. '시행일자' 라벨 우선."""
    raw = raw or ""
    m = re.search(r"시행일자[^0-9]{0,6}(\d{8})", raw)
    eff = _norm_date(m.group(1)) if m else None
    if eff is None:
        data = _try_json(raw)
        if data is not None:
            for it in _json_list(data):
                eff = _norm_date(str(_get(it, "effective_date", "시행일자", "시행일", "enforce_date") or ""))
                if eff:
                    break
    if eff is None:
        eff = _norm_date(raw)  # 텍스트 폴백
    return {"ok": eff is not None, "effective_date": eff, "reason": "" if eff else "시행일 파싱 실패"}


def parse_admin_rules(raw, exact_name=None):
    """search_admin_rules 응답에서 admrul_id·발령/시행일 추출(정확 매칭 1건)."""
    data = _try_json(raw)
    if data is not None:
        items = [{"id": _get(x, "admrul_id", "행정규칙ID", "id"),
                  "name": str(_get(x, "admrul_nm", "행정규칙명", "rule_name", "name") or "").strip(),
                  "eff": _norm_date(str(_get(x, "effective_date", "시행일자", "시행일", "발령일자", "발령일") or ""))}
                 for x in _json_list(data)]
    else:
        items = [{"id": it["id"], "name": it["name"],
                  "eff": _date_from_detail(it["detail"], ("시행", "발령"))}
                 for it in _md_items(raw)]
    cand = items if not exact_name else [it for it in items if it["name"] == str(exact_name).strip()]
    if len(cand) != 1:
        return {"ok": False, "admrul_id": None, "effective_date": None, "reason": f"매칭 {len(cand)}건 (1건이어야 함)"}
    it = cand[0]
    ok = it["id"] is not None or it["eff"] is not None
    return {"ok": ok, "admrul_id": it["id"], "effective_date": it["eff"],
            "reason": "" if ok else "id/시행일 없음"}


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
