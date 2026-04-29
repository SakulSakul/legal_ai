"""Gemini 임베딩 래퍼 — 검색/적재 양쪽에서 단일 진입점 사용.

운영 시점 최신 안정 모델은 `NEXUS_EMBED_MODEL` 환경변수로 추상화한다.
출력 차원은 `NEXUS_EMBED_DIM` 와 일치해야 하며 DB 스키마(vector(768))도 동일.
"""

from __future__ import annotations

from typing import Iterable

from .config import settings


def _client():
    from google import genai
    return genai.Client(api_key=settings().gemini_api_key)


def embed_one(text: str, *, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
    s = settings()
    res = _client().models.embed_content(
        model=s.embed_model,
        contents=text,
        config={"task_type": task_type, "output_dimensionality": s.embed_dim},
    )
    return list(res.embeddings[0].values)


def embed_many(texts: Iterable[str], *, task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    s = settings()
    cli = _client()
    out: list[list[float]] = []
    for t in texts:
        res = cli.models.embed_content(
            model=s.embed_model,
            contents=t,
            config={"task_type": task_type, "output_dimensionality": s.embed_dim},
        )
        out.append(list(res.embeddings[0].values))
    return out
