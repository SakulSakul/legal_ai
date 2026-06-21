"""
test_nexus_adapter.py — nexus 읽기 전용 어댑터 결정론 검증 (실 DB 불필요, mock client/rows).

핵심: nexus 행 → grounding 후보 매핑(현행본만·레코드 title), 키워드 랭킹, graceful 폴백(None).
실제 Supabase·RLS는 라이브 검증(여기선 mock).
"""
import nexus_adapter as NX


def _rows():
    return [
        {"id": "c1", "document_id": "d1", "article_no": "제5조", "text": "분담비율 50% 원칙",
         "nexus_documents": {"id": "d1", "title": "(공정거래) 협력회사 판촉비용 분담 지침", "superseded_by": None}},
        {"id": "c2", "document_id": "d1", "article_no": "제6조", "text": "예외 조건",
         "nexus_documents": {"id": "d1", "title": "(공정거래) 협력회사 판촉비용 분담 지침", "superseded_by": None}},
        {"id": "c9", "document_id": "d9", "article_no": "제1조", "text": "구버전",
         "nexus_documents": {"id": "d9", "title": "(구) 옛 지침", "superseded_by": "d1"}},  # 구버전
    ]


# ── 매핑: 현행본만, title은 레코드에서 ─────────────────────────
def test_map_rows_current_only_title_from_record():
    cands = NX.map_rows_to_candidates(_rows())
    ids = {c["id"] for c in cands}
    assert ids == {"c1", "c2"}                      # c9(superseded) 제외
    assert "c9" not in ids
    c1 = next(c for c in cands if c["id"] == "c1")
    assert c1["title"] == "(공정거래) 협력회사 판촉비용 분담 지침"  # 레코드 title(LLM 아님)
    assert c1["kind"] == "사규" and c1["article_no"] == "제5조"
    assert c1["document_id"] == "d1"


def test_map_rows_skips_recordless():
    rows = [{"id": "x", "nexus_documents": {"superseded_by": None}}]  # title 없음
    assert NX.map_rows_to_candidates(rows) == []


# ── 키워드 랭킹(FTS 대용) ─────────────────────────────────────
def test_rank_prefers_keyword_hits():
    cands = NX.map_rows_to_candidates(_rows())
    ranked = NX.rank_candidates(cands, "판촉비 분담비율 예외")
    assert ranked  # 비어있지 않음
    assert {c["id"] for c in ranked} <= {"c1", "c2"}


# ── fetch: mock client / graceful 폴백 ────────────────────────
class _FakeResp:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *a, **k):
        return self

    def contains(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return _FakeResp(self._data)


class _FakeClient:
    def __init__(self, data):
        self._data = data
        self.requested = None

    def table(self, name):
        self.requested = name
        return _FakeQuery(self._data)


def test_fetch_returns_candidates_from_nexus():
    client = _FakeClient(_rows())
    cands = NX.fetch_nexus_candidates("판촉비 분담", client=client)
    assert cands and {c["id"] for c in cands} <= {"c1", "c2"}
    assert client.requested == "nexus_chunks"  # 읽은 테이블


def test_fetch_none_when_no_client():
    assert NX.fetch_nexus_candidates("q", client=None) is None  # → docs 폴백


def test_fetch_none_on_empty_rows():
    assert NX.fetch_nexus_candidates("q", client=_FakeClient([])) is None  # 폴백


def test_fetch_graceful_on_exception():
    class _Boom:
        def table(self, *a, **k):
            raise RuntimeError("RLS denied")  # 권한 거부 시뮬
    assert NX.fetch_nexus_candidates("q", client=_Boom()) is None  # 예외 안 던지고 폴백


def test_candidates_are_grounding_compatible():
    """nexus 후보가 grounding_util과 호환(같은 형식) — ground_ids로 바로 검증/렌더 가능."""
    import grounding_util as G
    cands = NX.fetch_nexus_candidates("판촉비", client=_FakeClient(_rows()))
    grounded = G.ground_ids(["c1", "FAKE"], cands)   # 가짜 드롭
    assert [g["id"] for g in grounded] == ["c1"]
    assert G.grounded_titles(["c1"], cands) == ["(공정거래) 협력회사 판촉비용 분담 지침"]


# ── 읽기 전용 + 배선 가드 (회귀 잠금) ─────────────────────────
import os
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_nexus_adapter_is_read_only():
    """nexus_*는 DF 콤파스 소유 — 쓰기(insert/upsert/update/delete) 0."""
    src = open(os.path.join(_ROOT, "nexus_adapter.py"), encoding="utf-8").read()
    for write in (".insert(", ".upsert(", ".update(", ".delete("):
        assert write not in src, f"nexus 쓰기 금지 위반: {write}"
    assert "nexus_chunks" in src and "superseded_by" in src and "공정거래" in src


def test_legal_ai_wires_nexus_with_docs_fallback():
    """legal_ai가 _internal_candidates(nexus 우선→docs 폴백)로 후보를 얻는지."""
    src = open(os.path.join(_ROOT, "legal_ai.py"), encoding="utf-8").read()
    assert "_internal_candidates" in src
    assert "nexus_adapter.fetch_nexus_candidates" in src
    assert "make_candidates(st.session_state" in src  # docs 폴백 잔존


# ── Bug2: doc_kind → cat 결정론 매핑 ──────────────────────────
def test_doc_kind_maps_to_category():
    rows = [
        {"id": "r1", "nexus_documents": {"title": "판촉비 지침", "doc_kind": "rule", "superseded_by": None}},
        {"id": "k1", "nexus_documents": {"title": "특약매입 계약", "doc_kind": "contract", "superseded_by": None}},
        {"id": "a1", "nexus_documents": {"title": "공동판촉 약정", "doc_kind": "agreement", "superseded_by": None}},
    ]
    by_id = {c["id"]: c for c in NX.map_rows_to_candidates(rows)}
    assert by_id["r1"]["kind"] == "사규"   # rule → 사규
    assert by_id["k1"]["kind"] == "계약"   # contract → 계약
    assert by_id["a1"]["kind"] == "약정"   # agreement → 약정


# ── category 라우팅: 블록=필터 / LLM=전체 폴백 (silent miss 방지) ──
class _TrackQuery(_FakeQuery):
    def __init__(self, data, rec):
        super().__init__(data); self._rec = rec
    def overlaps(self, col, vals):
        self._rec["overlaps"] = (col, list(vals)); return self
    def contains(self, col, vals):
        self._rec["contains"] = (col, list(vals)); return self


class _TrackClient:
    def __init__(self, data):
        self._data = data; self.rec = {}
    def table(self, name):
        self.rec["table"] = name; return _TrackQuery(self._data, self.rec)


def test_categories_filter_uses_overlaps_union():
    """블록경로: categories 주면 overlaps(union) 필터 — 단일배제 아님."""
    c = _TrackClient(_rows())
    NX.fetch_nexus_candidates("판촉비", client=c, categories=["공정거래"])
    assert c.rec.get("overlaps") == ("categories", ["공정거래"])
    assert "contains" not in c.rec


def test_full_fallback_applies_no_category_filter():
    """LLM경로(categories=None): category 필터 미적용 = 전체 코퍼스 폴백(공정거래 디폴트 금지)."""
    c = _TrackClient(_rows())
    NX.fetch_nexus_candidates("개인정보 제3자 제공", client=c, categories=None)
    assert "overlaps" not in c.rec and "contains" not in c.rec  # 필터 0


# ── 배선 가드: 블록토픽 결정론, LLM 전체 폴백 ─────────────────
import os
_ROOT2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_block_topic_category_map_and_llm_full_fallback():
    src = open(os.path.join(_ROOT2, "legal_ai.py"), encoding="utf-8").read()
    assert "_BLOCK_TOPIC_CAT" in src
    assert '"판촉비분담": ["공정거래"]' in src           # 블록토픽 결정론
    assert "block_topic=matched_topics[0]" in src        # 블록경로만 토픽 전달
    assert "모르면 공정거래 디폴트' 금지" in src or "공정거래 디폴트 금지" in src
