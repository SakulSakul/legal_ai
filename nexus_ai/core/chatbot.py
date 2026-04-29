"""챗봇 엔진 — 검색→Gemini 생성→후처리(출처/심각모드)→로깅.

운영 시점 최신 안정 모델은 NEXUS_CHAT_MODEL 환경변수로 추상화한다.
temperature/top_p 는 환각 제어를 위해 0/0.1 고정이 기본값이며,
필요한 경우에만 환경변수로 미세조정한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .config import settings, load_hotlines
from .critical_mode import CriticalDetection, detect, enforce_structure, load_keywords
from .pii_filter import mask_pii
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .retriever import hybrid_search


_RE_ACTION_BLOCK = re.compile(r"(?:^|\n)\s*\d+\.\s+(.+)")


@dataclass
class Answer:
    text: str
    is_critical: bool
    critical_kind: str | None
    contexts: list[dict]
    masked_question: str


def _gen(model: str, system: str, user: str, *, temperature: float, top_p: float) -> str:
    from google import genai
    from google.genai import types
    s = settings()
    cli = genai.Client(api_key=s.gemini_api_key)
    res = cli.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            top_p=top_p,
        ),
    )
    return (res.text or "").strip()


def _ensure_citation(answer: str, contexts: list[dict]) -> str:
    if "[참조:" in answer:
        return answer
    cites: list[str] = []
    for c in contexts:
        title = c.get("doc_title") or c.get("title") or "문서"
        if c.get("article_no"):
            cites.append(f"{title} {c['article_no']}")
        elif c.get("case_no"):
            cites.append(f"사례집 #{c['case_no']}")
        else:
            cites.append(title)
    if not cites:
        return answer + "\n\n[참조: 검색 결과 없음]"
    return answer + f"\n\n[참조: {', '.join(cites[:5])}]"


def _extract_action_items(answer: str) -> list[str]:
    return [m.group(1).strip() for m in _RE_ACTION_BLOCK.finditer(answer)][:3]


def ask(
    supabase: Any,
    *,
    question: str,
    category: str | None,
    extra_pii_terms: list[str] | None = None,
) -> Answer:
    s = settings()
    masked = mask_pii(question, extra_pii_terms or [])

    # 심각 사안 트리거 감지 (마스킹 전 원문 기준이 더 정확하므로 원문에도 검사)
    keywords = load_keywords(supabase)
    detection: CriticalDetection = detect(question, keywords)
    if not detection.triggered:
        detection = detect(masked, keywords)

    # 카테고리 필터: 단일 카테고리 선택 시 ['공통', 선택] 합집합으로 폭을 약간 넓힘.
    cats: list[str] | None
    if category and category != "전체":
        cats = list({"공통", category})
    else:
        cats = None

    # 심각 사안일 때는 안전 카테고리도 우선적으로 합집합에 포함
    if detection.triggered:
        if detection.kind == "safety":
            cats = list(set((cats or []) + ["안전", "공통"]))
        elif detection.kind == "harassment":
            cats = list(set((cats or []) + ["공통"]))

    contexts = hybrid_search(supabase, question=masked, categories=cats, top_k=s.top_k)

    user = build_user_prompt(masked, contexts)
    raw = _gen(s.chat_model, SYSTEM_PROMPT, user,
               temperature=s.temperature, top_p=s.top_p)
    raw = _ensure_citation(raw, contexts)

    if detection.triggered:
        actions = _extract_action_items(raw)
        hotlines = load_hotlines(supabase)
        final = enforce_structure(
            base_answer=raw,
            kind=detection.kind or "safety",
            action_items=actions,
            hotlines=hotlines,
        )
    else:
        final = raw

    # 질의 로그 (마스킹 후 본문만 저장, 원본은 즉시 폐기)
    try:
        supabase.table("query_logs").insert({
            "category":      category if category and category != "전체" else None,
            "query_masked":  masked,
            "is_critical":   detection.triggered,
            "critical_kind": detection.kind,
            "hit_chunk_ids": [c.get("chunk_id") for c in contexts if c.get("chunk_id")],
        }).execute()
    except Exception:
        pass

    return Answer(
        text=final,
        is_critical=detection.triggered,
        critical_kind=detection.kind,
        contexts=contexts,
        masked_question=masked,
    )
