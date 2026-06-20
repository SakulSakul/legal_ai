"""
staleness_util.py — 형량 staleness 비교 로직 (순수·결정론·key-free)

Step 5에서 구조화한 penalty 숫자를 '현재 법령 텍스트'와 대조해 낡았는지 판정한다.
이 모듈은 외부 의존(네트워크·키·UI)을 일절 갖지 않는다 — 현재 법령 텍스트는
호출부가 주입한 fetch 함수로 가져오고, 여기서는 문자열 비교만 한다(단위 테스트 가능).

판정 규칙 (모호하면 안전한 쪽):
  - penalty 숫자가 현재 법령 텍스트에 그대로 등장 → OK
  - 같은 종류의 '다른' 형량이 등장(바뀜) → STALE
  - 텍스트를 못 가져왔거나 형량 표현이 없어 모호 → NEEDS_REVIEW (false OK/STALE보다 안전)

이 모듈은 감지 전용이다. 어떤 파일도 수정하지 않는다.
"""
import re

OK = "OK"
STALE = "STALE"
NEEDS_REVIEW = "NEEDS_REVIEW"

_MIN_TEXT_LEN = 30
_FAIL_MARKERS = ("실패", "연결 실패", "조회 실패", "추출 실패", "미지정", "오류")


def _krw_forms(n: int) -> list:
    """KRW 정수 → 법령 텍스트에 등장할 만한 표기 후보."""
    forms = []
    ko = ""
    eok = n // 10**8
    if eok:
        ko += f"{eok}억"
    man = (n % 10**8) // 10**4
    if man:
        if man % 1000 == 0:
            ko += f"{man // 1000}천만"
        elif man % 100 == 0:
            ko += f"{man // 100}백만"
        else:
            ko += f"{man}만"
    if ko:
        forms.append(ko + "원")
    forms.append(f"{n:,}원")
    forms.append(f"{n:,}")
    return forms


def _check_imprisonment(years, text):
    if years is None:
        return None
    if any(form in text for form in (f"{years}년 이하", f"{years}년이하", f"{years}년 이상")):
        return {"field": "징역", "db_value": f"{years}년", "verdict": OK, "note": f"법령에 {years}년 형량 확인"}
    others = re.findall(r"(\d+)\s*년\s*이하의?\s*징역", text)
    if others:
        return {"field": "징역", "db_value": f"{years}년", "verdict": STALE,
                "note": f"법령 형량 {'/'.join(sorted(set(others)))}년 — DB({years}년)와 불일치"}
    return {"field": "징역", "db_value": f"{years}년", "verdict": NEEDS_REVIEW,
            "note": "법령 텍스트에서 징역 형량을 찾지 못함"}


def _check_fine(amount, text, label):
    if amount is None:
        return None
    forms = _krw_forms(amount)
    if any(f in text for f in forms):
        return {"field": label, "db_value": forms[0], "verdict": OK, "note": f"법령에 {forms[0]} 확인"}
    others = re.findall(r"([\d,]+\s*원|\d+(?:억|천만|백만|만)\s*원)\s*이하의?\s*벌금", text)
    if others:
        return {"field": label, "db_value": forms[0], "verdict": STALE,
                "note": f"법령 벌금 {others[0].strip()} — DB({forms[0]})와 불일치"}
    return {"field": label, "db_value": forms[0], "verdict": NEEDS_REVIEW,
            "note": "법령 텍스트에서 벌금 형량을 찾지 못함"}


def check_penalty_staleness(penalty: dict, law_text: str) -> dict:
    """
    penalty(구조화 형량)를 현재 법령 텍스트와 대조.
    Returns: {"verdict": OK|STALE|NEEDS_REVIEW, "fields": [...], "summary": str}
    """
    text = (law_text or "").strip()
    if len(text) < _MIN_TEXT_LEN or any(m in text for m in _FAIL_MARKERS):
        return {"verdict": NEEDS_REVIEW, "fields": [],
                "summary": "현재 법령 텍스트를 가져오지 못함 — 사람이 직접 확인 필요"}

    penalty = penalty or {}
    fields = []
    for r in (
        _check_imprisonment(penalty.get("imprisonment_max_years"), text),
        _check_fine(penalty.get("fine_max_krw"), text, "벌금"),
        _check_fine(penalty.get("corporate_fine_max_krw"), text, "양벌규정 벌금"),
    ):
        if r:
            fields.append(r)

    if not fields:
        return {"verdict": NEEDS_REVIEW, "fields": [],
                "summary": "구조화된 형사 형량 없음(행정제재 등) — 사람이 직접 확인 필요"}

    verdicts = [f["verdict"] for f in fields]
    if STALE in verdicts:
        overall = STALE
        summary = "법 개정 미반영 의심 — 구조화 형량이 현재 법령과 불일치"
    elif all(v == OK for v in verdicts):
        overall = OK
        summary = "구조화 형량이 현재 법령과 일치"
    else:
        overall = NEEDS_REVIEW
        summary = "일부 형량을 자동 확인하지 못함 — 사람이 직접 확인 필요"
    return {"verdict": overall, "fields": fields, "summary": summary}


def run_staleness_check(issues, fetch_fn) -> list:
    """
    각 issue의 penalty.law_ref로 fetch_fn(law_ref)->(success, text)를 호출해
    현재 법령을 가져온 뒤 staleness를 판정한다. 감지 전용 — 아무것도 수정하지 않는다.

    fetch_fn: (law_ref: str) -> (success: bool, text: str)  [주입 — 테스트는 mock]
    Returns: issue별 리포트 리스트.
    """
    report = []
    for iss in issues:
        penalty = iss.get("penalty") or {}
        law_ref = penalty.get("law_ref", "")
        text = ""
        if law_ref and fetch_fn is not None:
            try:
                ok, fetched = fetch_fn(law_ref)
                if ok:
                    text = fetched or ""
            except Exception:
                text = ""  # fetch 실패 → NEEDS_REVIEW로 귀결
        res = check_penalty_staleness(penalty, text)
        report.append({
            "issue_id": iss.get("id"),
            "title": iss.get("title", ""),
            "law_ref": law_ref,
            "penalty": penalty,
            "excerpt": text[:300],
            "verdict": res["verdict"],
            "fields": res["fields"],
            "summary": res["summary"],
        })
    return report
