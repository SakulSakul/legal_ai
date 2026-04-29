"""심각 사안 응답 모드 (Critical Response Mode).

- DB 의 critical_keywords 사전을 기준으로 트리거 감지.
- 4단 응답 구조를 후처리에서 강제 삽입한다 (2·4번 항목 누락 시 필수 보강).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import hr_routing_line


@dataclass
class CriticalDetection:
    triggered: bool
    kind: str | None        # 'safety' | 'harassment' | None
    matched: list[str]


def load_keywords(supabase: Any) -> dict[str, list[str]]:
    """{'safety': [...], 'harassment': [...]} 반환. 실패 시 빈 dict."""
    try:
        rows = (
            supabase.table("critical_keywords")
                    .select("kind,keyword,is_active")
                    .eq("is_active", True)
                    .execute()
                    .data or []
        )
    except Exception:
        return {"safety": [], "harassment": []}
    out: dict[str, list[str]] = {"safety": [], "harassment": []}
    for r in rows:
        out.setdefault(r["kind"], []).append(r["keyword"])
    return out


def detect(text: str, keywords: dict[str, list[str]]) -> CriticalDetection:
    if not text:
        return CriticalDetection(False, None, [])
    # 우선순위: harassment > safety (인사 라우팅 안내 필요)
    for kind in ("harassment", "safety"):
        hits = [k for k in keywords.get(kind, []) if k and k in text]
        if hits:
            return CriticalDetection(True, kind, hits)
    return CriticalDetection(False, None, [])


def _scope_line(kind: str) -> str:
    if kind == "harassment":
        return ("본 답변은 사규 해석 관점입니다. "
                "신고·조사 절차 등 인사 행정 사항은 인사팀에 직접 문의하시기 바랍니다.")
    return ("본 답변은 사규/안전관리 기준 관점이며, "
            "실제 응급상황 시 아래 핫라인을 즉시 이용하시기 바랍니다.")


def _hotline_box(kind: str, hotlines: dict[str, str]) -> str:
    lines: list[str] = ["📞 **핫라인 안내**"]
    lines.append(f"- 사내 익명 제보채널: {hotlines.get('internal_report_url','')}")
    lines.append(f"- 외부 상담채널: {hotlines.get('external_hotline','')}")
    if kind == "harassment":
        lines.append(f"- 인사팀 문의: {hr_routing_line(hotlines)}")
    return "\n".join(lines)


def enforce_structure(
    *,
    base_answer: str,
    kind: str,
    action_items: list[str] | None,
    hotlines: dict[str, str],
) -> str:
    """4단 구조를 강제 보장하고, 누락 항목은 후처리 삽입."""
    parts: list[str] = []

    # 1) 사규 기반 답변
    parts.append("### 1. 사규 기반 답변")
    parts.append(base_answer.strip() or "관련 사규에서 확인되지 않습니다.")

    # 2) 범위 안내 (강제)
    parts.append("### 2. 답변 범위 안내")
    parts.append(_scope_line(kind))

    # 3) Action Item — 모델이 만든 항목이 있으면 우선 사용, 없으면 안전 기본값
    parts.append("### 3. 즉시 실행 가능한 행동 가이드")
    items = [a for a in (action_items or []) if a and a.strip()]
    if not items:
        if kind == "safety":
            items = [
                "현장 안전 확보 및 2차 피해 방지 (작업 중지/대피 우선)",
                "관리감독자·안전보건 담당자에게 즉시 보고",
                "필요한 경우 119 신고 및 사내 핫라인 이용",
            ]
        else:
            items = [
                "발생 일시·장소·관련자·구체적 행위를 시간순으로 메모",
                "가능한 증거(메시지·이메일·녹취 등)를 안전한 위치에 보관",
                "아래 핫라인 또는 인사팀에 익명/실명으로 상담 요청",
            ]
    for i, a in enumerate(items[:3], start=1):
        parts.append(f"{i}. {a}")

    # 4) 핫라인 안내 박스 (강제)
    parts.append("---")
    parts.append(_hotline_box(kind, hotlines))

    return "\n\n".join(parts)
