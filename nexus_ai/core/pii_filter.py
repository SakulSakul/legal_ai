"""PII 마스킹 — DB/AI 호출 직전 전처리 단계에서 호출한다.

- 실명, 주민번호, 휴대폰번호, 이메일, 부서명 후보 패턴을 [익명] 으로 치환.
- 한국어 이름은 false-positive 가 많아 보수적으로 처리(2~4자 한글 + 직책 어휘 동반 시).
- 회사 부서명 사전(`departments.txt`)이 secrets/환경에 주어지면 우선 매칭.
"""

from __future__ import annotations

import re
from typing import Iterable

ANON = "[익명]"

_RE_EMAIL    = re.compile(r"[\w\.\-+]+@[\w\.\-]+\.[A-Za-z]{2,}")
_RE_RRN      = re.compile(r"\b\d{6}-\d{7}\b")
_RE_PHONE    = re.compile(r"\b01[016-9]-?\d{3,4}-?\d{4}\b")
_RE_PHONE2   = re.compile(r"\b0\d{1,2}-\d{3,4}-\d{4}\b")

# 한국어 인명 + 직책/호칭 — 보수적
_HONORIFICS = (
    "님|씨|과장|차장|부장|팀장|실장|본부장|상무|전무|부사장|사장|대표|"
    "대리|사원|주임|선임|책임|수석|이사"
)
_RE_NAME_HONOR = re.compile(rf"([가-힣]{{2,4}})\s*({_HONORIFICS})")

# 부서명 후보 — 일반 패턴 (00팀 / 00실 / 00본부 / 00사업부 / 00센터)
_RE_DEPT     = re.compile(r"[가-힣A-Za-z0-9]{2,15}(팀|실|본부|사업부|센터|그룹)")


def mask_pii(text: str, extra_terms: Iterable[str] = ()) -> str:
    if not text:
        return text
    out = text
    out = _RE_EMAIL.sub(ANON, out)
    out = _RE_RRN.sub(ANON, out)
    out = _RE_PHONE.sub(ANON, out)
    out = _RE_PHONE2.sub(ANON, out)
    out = _RE_NAME_HONOR.sub(lambda m: f"{ANON} {m.group(2)}", out)
    out = _RE_DEPT.sub(ANON, out)

    for term in extra_terms:
        term = (term or "").strip()
        if len(term) >= 2:
            out = re.sub(re.escape(term), ANON, out)
    return out
