"""
nexus_adapter.py — DF 콤파스 nexus_* 읽기 전용 어댑터 (내부문서 후보 소스).

목적: 내부문서(사규·계약) 후보를 nexus_chunks ⋈ nexus_documents에서 가져와 grounding_util
후보 형식으로 반환. 인용 grounding(grounding_util)이 그대로 nexus를 소스로 쓰게 한다.

원칙:
  - nexus_*는 DF 콤파스 소유 → **읽기 전용**(SELECT만, 쓰기 0). DB View 생성도 안 함(앱측 쿼리+매핑만).
  - 현행본만: nexus_documents.superseded_by IS NULL (내부문서 freshness 공짜 — 외부 리졸버 불필요).
  - 사규 = nexus_chunks.categories @> {공정거래}.
  - 임베딩 컬럼 미확인 → FTS 대용 키워드 랭킹 기본(+ synonym 확장 훅). 임베딩 확인되면 하이브리드로.
  - graceful: RLS 거부·스키마 차이·미연결 = None 반환 → 호출측이 docs로 폴백(단일소스 전환은 RLS 확인 후).
    폴백은 로깅(조용히 docs로만 떨어지는 걸 진단 가능하게).

매핑/랭킹 로직은 순수(테스트 가능). 쿼리는 client 주입(mock 가능).
"""
import logging
import re

import grounding_util

logger = logging.getLogger(__name__)

SAJU_CATEGORY = "공정거래"
# nexus_chunks ⋈ nexus_documents (PostgREST 임베디드 리소스). 읽기 전용 컬럼만.
_SELECT = "id,document_id,article_no,text,nexus_documents(id,title,doc_kind,superseded_by)"
# doc_kind → grounding cat 결정론 매핑(Bug2: 분류를 LLM 임의 아닌 doc_kind로).
#   rule=사규 / contract=계약 / agreement=약정. 미상은 사규(공정거래 셋은 전부 rule).
_DOCKIND_CAT = {"rule": "saryu", "contract": "contract", "agreement": "yakjeong"}


def map_rows_to_candidates(rows):
    """nexus_chunks 행(+document 조인) → grounding_util 후보 [{id,cat,kind,title,snippet,...}].
    title/본문은 레코드(nexus_documents.title / chunk.text)에서 — LLM 텍스트 0. 순수.
    현행본(superseded_by NULL/빈값)만 통과. document_id·article_no를 부가."""
    out, seen = [], set()
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        doc = r.get("nexus_documents") or {}
        if doc.get("superseded_by") not in (None, "", "null"):
            continue  # 구버전 — 현행 아님(freshness)
        _cat = _DOCKIND_CAT.get((doc.get("doc_kind") or "").strip().lower(), "saryu")
        rec = {"id": r.get("id"), "cat": _cat,
               "label": doc.get("title") or r.get("title"),
               "text": r.get("text") or ""}
        c = grounding_util.make_candidate(rec)
        if not c or c["id"] in seen:
            continue
        seen.add(c["id"])
        c["article_no"] = (r.get("article_no") or "").strip()
        c["document_id"] = str(r.get("document_id") or doc.get("id") or "")
        out.append(c)
    return out


def rank_candidates(candidates, query, synonyms=None, top_k=8):
    """FTS 대용 키워드 랭킹(순수). query 토큰(+synonym 확장)이 title/article_no/snippet에
    많이 맞는 순. 임베딩 없을 때 기본. 점수 동률은 입력 순서 유지(안정)."""
    toks = [t for t in re.split(r"[\s,./]+", (query or "")) if len(t) >= 2]
    for syn_list in (synonyms or {}).values() if isinstance(synonyms, dict) else []:
        toks += [s for s in syn_list if len(s) >= 2]
    toks = list(dict.fromkeys(toks))  # 중복 제거

    def score(c):
        hay = f"{c.get('title','')} {c.get('article_no','')} {c.get('snippet','')}"
        return sum(hay.count(t) for t in toks)

    ranked = sorted(range(len(candidates)), key=lambda i: (-score(candidates[i]), i))
    return [candidates[i] for i in ranked[:top_k]]


def fetch_nexus_candidates(query, client=None, category=SAJU_CATEGORY, limit=40, top_k=8):
    """nexus에서 현행 사규 청크 후보를 읽어 grounding 후보로(읽기 전용). client 주입 가능(테스트).
    실패/RLS거부/빈 결과 → None(호출측 docs 폴백, 로깅). 절대 예외를 위로 던지지 않음."""
    if client is None:
        return None
    try:
        resp = (client.table("nexus_chunks")
                .select(_SELECT)
                .contains("categories", [category])
                .limit(limit)
                .execute())
        rows = getattr(resp, "data", None) or []
        cands = map_rows_to_candidates(rows)
        if not cands:
            logger.info("nexus 후보 0건(현행 공정거래 청크 없음/RLS) → docs 폴백")
            return None
        return rank_candidates(cands, query, top_k=top_k)
    except Exception as e:
        logger.warning(f"nexus 어댑터 조회 실패(→docs 폴백): {type(e).__name__}: {e}")
        return None
