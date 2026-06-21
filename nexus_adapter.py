"""
nexus_adapter.py — DF 콤파스 nexus_* 읽기 전용 어댑터 (내부문서 후보 소스).

목적: 내부문서(사규·계약) 후보를 nexus_chunks ⋈ nexus_documents에서 가져와 grounding_util
후보 형식으로 반환. 인용 grounding(grounding_util)이 그대로 nexus를 소스로 쓰게 한다.

원칙:
  - nexus_*는 DF 콤파스 소유 → **읽기 전용**(SELECT만, 쓰기 0). DB View 생성도 안 함(앱측 쿼리+매핑만).
    #38: service_role 권한으로도 못 쓰게 코드 가드(NexusWriteForbidden)로 봉인 — nexus_ prefix write는 raise.
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


# ── nexus write 가드 (#38) ────────────────────────────────────
# legal_ai는 service_role로 연결 → 권한상 nexus_*에 쓸 수 있음. DB는 안 건드리기로 했으니
# (DF 콤파스 깰 위험) 코드 레벨 트립와이어로 봉인. 규칙은 prefix 기준 —
# nexus_documents·nexus_chunks·향후 nexus_* 자동 커버. write는 raise, read(.select 체인)는 무변경.
class NexusWriteForbidden(PermissionError):
    """nexus_*는 DF 콤파스 소유 — legal_ai에서 read-only. write 시도는 코드로 봉인."""


# 쓰기성 메서드(insert/update/delete/upsert). 메서드명 문자열 set — 정적 read-only 검사에 안 걸림.
_WRITE_METHODS = frozenset({"insert", "update", "delete", "upsert"})


class _ReadOnlyTable:
    """nexus_* 테이블 프록시 — read 체인(select/eq/overlaps/limit/execute…)은 그대로 위임,
    write 메서드는 NexusWriteForbidden. 체인 반환값도 다시 감싸 가드 유지(execute 응답만 raw)."""

    def __init__(self, inner, name):
        self._inner = inner
        self._name = name

    def __getattr__(self, attr):
        if attr in _WRITE_METHODS:
            raise NexusWriteForbidden(
                f"nexus_* is read-only in legal_ai (table={self._name}, op={attr})")
        target = getattr(self._inner, attr)
        if not callable(target):
            return target

        def _wrapped(*a, **k):
            res = target(*a, **k)
            # execute() 응답은 raw(데이터). 그 외 빌더 반환은 계속 가드.
            return res if attr == "execute" else _ReadOnlyTable(res, self._name)

        return _wrapped


def _read_only_table(client, name):
    """nexus_ prefix 테이블은 읽기 전용 프록시로 감싼다(write 시 raise). 비-nexus는 그대로."""
    table = client.table(name)
    return _ReadOnlyTable(table, name) if str(name).startswith("nexus_") else table


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


def fetch_nexus_candidates(query, client=None, categories=None, limit=None, top_k=8):
    """nexus에서 현행 내부문서 청크 후보를 읽어 grounding 후보로(읽기 전용). client 주입 가능(테스트).

    categories: 도메인 필터(list). None이면 '전체 코퍼스 폴백'(category 필터 없음) — silent miss 방지
      핵심: '모르면 공정거래 디폴트' 금지. 블록경로는 블록토픽→category 결정론으로 list를 주고,
      LLM경로(토픽 분류기 없음)는 None으로 좁히지 않는다(좁게 오라우팅 < 넓게).
    실패/RLS거부/빈 결과 → None(호출측 docs 폴백, 로깅). 절대 예외를 위로 던지지 않음."""
    if client is None:
        return None
    # 필터 시엔 좁아 적은 limit, 전체 폴백 시엔 코퍼스 전체 풀(클라 랭킹이 다 보도록 —
    # 임의 truncation에 의한 recall 미스 방지. 코퍼스 ~수백 청크, 한 쿼리로 감당).
    if limit is None:
        limit = 40 if categories else 1000
    try:
        q = _read_only_table(client, "nexus_chunks").select(_SELECT)
        if categories:
            q = q.overlaps("categories", list(categories))  # categories && {…} (union, 단일배제 아님)
        resp = q.limit(limit).execute()
        rows = getattr(resp, "data", None) or []
        cands = map_rows_to_candidates(rows)
        if not cands:
            logger.info(f"nexus 후보 0건(categories={categories or '전체'}/RLS) → docs 폴백")
            return None
        return rank_candidates(cands, query, top_k=top_k)
    except Exception as e:
        logger.warning(f"nexus 어댑터 조회 실패(→docs 폴백): {type(e).__name__}: {e}")
        return None
