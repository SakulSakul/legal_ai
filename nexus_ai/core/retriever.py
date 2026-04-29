"""하이브리드 검색 (RPC: nexus_hybrid_search) 래퍼."""

from __future__ import annotations

from typing import Any

from .config import settings
from .embedder import embed_one


def hybrid_search(
    supabase: Any,
    *,
    question: str,
    categories: list[str] | None,
    top_k: int | None = None,
) -> list[dict]:
    s = settings()
    emb = embed_one(question, task_type="RETRIEVAL_QUERY")
    payload = {
        "query_text": question,
        "query_embed": emb,
        "filter_categories": categories or None,
        "top_k": top_k or s.top_k,
        "fanout": max(20, (top_k or s.top_k) * 10),
        "rrf_k": 60,
        "fallback_to_common": True,
    }
    res = supabase.rpc("nexus_hybrid_search", payload).execute()
    return res.data or []
