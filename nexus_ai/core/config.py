"""환경 설정 및 핫라인 문구 중앙 관리.

Streamlit secrets → 환경변수 → 기본값 순으로 조회한다.
배포 형태(Phase 5) 결정 전까지 코드 수정 없이 운영 변경이 가능하도록
모든 외부값은 본 모듈을 경유한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


CATEGORIES: tuple[str, ...] = (
    "공통", "CSR", "공정거래", "정보보안", "안전",
    "재무", "영업", "총무", "환경",
)


def get_secret(key: str, default: str = "") -> str:
    try:
        import streamlit as st  # type: ignore
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.environ.get(key, default)


@dataclass(frozen=True)
class Settings:
    supabase_url: str
    supabase_key: str
    gemini_api_key: str
    chat_model: str
    embed_model: str
    embed_dim: int
    top_k: int
    temperature: float
    top_p: float


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        supabase_url=get_secret("SUPABASE_URL"),
        supabase_key=get_secret("SUPABASE_KEY"),
        gemini_api_key=get_secret("GEMINI_API_KEY"),
        # 모델 버전은 운영 시점 최신 안정본으로 외부에서 갱신.
        chat_model=get_secret("NEXUS_CHAT_MODEL", "gemini-2.5-pro"),
        embed_model=get_secret("NEXUS_EMBED_MODEL", "gemini-embedding-001"),
        embed_dim=int(get_secret("NEXUS_EMBED_DIM", "768")),
        top_k=int(get_secret("NEXUS_TOP_K", "3")),
        temperature=float(get_secret("NEXUS_TEMPERATURE", "0")),
        top_p=float(get_secret("NEXUS_TOP_P", "0.1")),
    )


# ── Hotline / 안내 문구 (DB 우선, fallback default) ──────────
_DEFAULT_HOTLINES: dict[str, str] = {
    "internal_report_url": "https://example.invalid/report",
    "external_hotline":    "고용노동부 1350",
    "ethics_hotline_url":  "https://example.invalid/ethics",
    "hr_contact_text":     "신고·조사 절차 등 인사 행정 사항은 인사팀에 직접 문의하시기 바랍니다.",
    "hr_chatbot_url":      "",
}


def load_hotlines(supabase: Any | None = None) -> dict[str, str]:
    out = dict(_DEFAULT_HOTLINES)
    if supabase is None:
        return out
    try:
        rows = supabase.table("hotline_config").select("key,value").execute().data or []
        for r in rows:
            k = r.get("key"); v = r.get("value")
            if k and v is not None:
                out[k] = v
    except Exception:
        pass
    return out


def hr_routing_line(hotlines: dict[str, str]) -> str:
    """인사 챗봇 오픈 시 자연스러운 전환을 위한 단일 문구 빌더."""
    url = (hotlines.get("hr_chatbot_url") or "").strip()
    if url:
        return f"인사 챗봇으로 이동: {url}"
    return hotlines.get("hr_contact_text") or _DEFAULT_HOTLINES["hr_contact_text"]
