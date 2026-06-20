"""
embedding_util.py — 공용 임베딩 백엔드 (캐시 + graceful fallback)

인터페이스 (스왑 경계):
    embed(texts: list[str]) -> list[list[float]] | None
      · 성공: 각 텍스트의 임베딩 벡터 리스트 (text-embedding-004 = 768차원)
      · 실패/키없음: None → 호출부가 키워드 전용 경로로 graceful fallback

모델: gemini-embedding-001 (Gemini, 768d 요청).
  ※ 지시서 §3.1은 text-embedding-004를 지정했으나, 운영 API 키에서 해당 모델이
    404(미제공)였다. embedContent 지원 모델 조회 결과 gemini-embedding-001 계열만
    가용하여 이를 채택. output_dimensionality=768로 본래 의도(768d)는 유지.
    (embed() 인터페이스 불변 — 추후 모델 교체는 DEFAULT_MODEL 한 줄.)
캐시: 콘텐츠 sha256(모델명+차원 포함) 해시 키. 인메모리 dict + eval/.emb_cache 디스크 백업.
      매 질의마다 코퍼스 전체를 재임베딩하지 않는다 (캐시 히트).
인메모리 코사인 유사도 사용 (코퍼스 ~5만자 → pgvector 불필요).

── 스왑 경로 (코퍼스 확대 시) ──────────────────────────────────
embed() 인터페이스는 고정하고, _default_backend()만 교체하면 된다:
  (a) Supabase pgvector  — 대규모 코퍼스, 영속 인덱스
  (b) 로컬 BGE-M3        — 오프라인/비용절감, sentence-transformers
주입: set_backend(fn) 로 테스트/대체 백엔드를 꽂을 수 있다.
"""
import os
import json
import math
import hashlib
from typing import List, Optional, Callable

# ── 설정 ────────────────────────────────────────────────
DEFAULT_MODEL = "gemini-embedding-001"
OUTPUT_DIM = 768          # MRL 차원 축소 (<3072은 코사인 전 정규화 권장 → cosine()에서 처리)
TASK_TYPE = "SEMANTIC_SIMILARITY"
_CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "eval", ".emb_cache")
_CACHE_FILE = os.path.join(_CACHE_DIR, "embeddings.json")

# 인메모리 캐시: {hash: vector}
_mem_cache: dict = {}
_disk_loaded = False

# 주입 가능한 백엔드 (기본=Gemini). 테스트/스왑 시 set_backend로 교체.
_backend: Optional[Callable[[List[str], str], Optional[List[List[float]]]]] = None


# ── 키 조회 (streamlit 비의존, env 우선) ─────────────────
def _get_api_key() -> str:
    # 1) 환경변수 우선
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        v = os.environ.get(k)
        if v:
            return v
    # 2) .streamlit/secrets.toml 직접 읽기 (streamlit 런타임 없이도 동작 — 오프라인 eval/CI)
    try:
        import tomllib
        here = os.path.dirname(os.path.abspath(__file__))
        for path in (
            os.path.join(here, ".streamlit", "secrets.toml"),
            os.path.expanduser("~/.streamlit/secrets.toml"),
        ):
            if os.path.exists(path):
                with open(path, "rb") as f:
                    data = tomllib.load(f)
                for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
                    if data.get(k):
                        return data[k]
    except Exception:
        pass
    # 3) streamlit 런타임 secrets (앱 구동 중일 때)
    try:
        import streamlit as st
        for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            try:
                if st.secrets[k]:
                    return st.secrets[k]
            except Exception:
                pass
    except Exception:
        pass
    return ""


# ── 디스크 캐시 ──────────────────────────────────────────
def _load_disk_cache():
    global _disk_loaded
    if _disk_loaded:
        return
    _disk_loaded = True
    try:
        if os.path.exists(_CACHE_FILE):
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                _mem_cache.update(json.load(f))
    except Exception:
        pass


def _save_disk_cache():
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_mem_cache, f)
    except Exception:
        pass


def _key_for(text: str, model: str) -> str:
    return hashlib.sha256(
        f"{model}|d{OUTPUT_DIM}|{TASK_TYPE}\x00{text}".encode("utf-8")
    ).hexdigest()


# ── 기본 백엔드: Gemini text-embedding-004 ───────────────
def _default_backend(texts: List[str], model: str) -> Optional[List[List[float]]]:
    """Gemini 임베딩 호출. 실패/키없음 시 None (graceful fallback)."""
    key = _get_api_key()
    if not key:
        return None
    try:
        from google import genai
    except Exception:
        return None
    try:
        from google.genai import types
        cfg = types.EmbedContentConfig(
            output_dimensionality=OUTPUT_DIM,
            task_type=TASK_TYPE,
        )
        client = genai.Client(api_key=key)
        resp = client.models.embed_content(model=model, contents=texts, config=cfg)
        vecs = [list(e.values) for e in resp.embeddings]
        if len(vecs) != len(texts):
            return None
        return vecs
    except Exception:
        return None


def set_backend(fn: Optional[Callable[[List[str], str], Optional[List[List[float]]]]]):
    """백엔드 주입 (테스트/스왑용). None이면 기본 Gemini 백엔드로 복귀."""
    global _backend
    _backend = fn


# ── 공개 인터페이스 ──────────────────────────────────────
def embed(texts: List[str], model: str = DEFAULT_MODEL) -> Optional[List[List[float]]]:
    """
    텍스트 목록을 임베딩. 캐시 우선, 미스만 백엔드 호출.

    반환:
      · list[list[float]] — texts 순서에 맞춘 벡터 리스트
      · None — 백엔드 미가용(키없음/SDK없음/호출실패). 호출부는 키워드 폴백.
    """
    if not texts:
        return []
    _load_disk_cache()

    keys = [_key_for(t, model) for t in texts]
    missing_idx = [i for i, k in enumerate(keys) if k not in _mem_cache]

    if missing_idx:
        backend = _backend or _default_backend
        miss_texts = [texts[i] for i in missing_idx]
        vecs = backend(miss_texts, model)
        if vecs is None:
            # 미스를 못 채우면 전체 폴백 (혼합 반환 금지)
            return None
        for j, i in enumerate(missing_idx):
            _mem_cache[keys[i]] = vecs[j]
        _save_disk_cache()

    return [_mem_cache[k] for k in keys]


def embed_one(text: str, model: str = DEFAULT_MODEL) -> Optional[List[float]]:
    out = embed([text], model)
    return out[0] if out else None


# ── 코사인 유사도 ────────────────────────────────────────
def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
