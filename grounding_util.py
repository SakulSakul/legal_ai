"""
grounding_util.py — 내부문서(사규·계약·약정) 인용 grounding (streamlit 비의존, 순수 → 단위테스트).

불변식(환각 구조적 차단): 인용은 '검색된 소스 ID' 없이는 못 나간다 + 본문은 소스 레코드에서만 렌더.
  - LLM은 후보집합(닫힌 집합)의 id로 '선택'만 한다. 인용 문서명·본문을 직접 생성하지 않는다.
  - 후보집합에 없는 id·자유텍스트로 지어낸 문서명 = 드롭(탐지가 아니라 '구조적 배제').
  - 화면의 문서명·본문은 레코드(title/snippet)에서 찍는다. LLM 텍스트에서 안 뽑는다.

소스 비의존: 후보가 legal_ai `docs` 테이블이든 DF 콤파스 `nexus_*`든 동일 레이어를 통과한다
(Phase 1a=docs.id 와이어링 / Phase 1b=nexus 이전 — 이 모듈은 그대로).
"""
import re

_CAT_LABEL = {"saryu": "사규", "contract": "계약", "yakjeong": "약정"}
INTERNAL_CATS = ("saryu", "contract", "yakjeong")


def _rec_cat(rec):
    return rec.get("cat") or rec.get("doc_kind") or ""


def make_candidate(rec):
    """소스 레코드(docs row/nexus row) → 후보 {id, cat, kind, title, snippet}.
    안정 id·깨끗한 title은 레코드에서(LLM 아님). id나 title 없으면 None(grounding 불가 → 배제)."""
    if not isinstance(rec, dict):
        return None
    rid = rec.get("id")
    if rid is None or str(rid).strip() == "":
        return None
    title = (rec.get("title") or rec.get("label") or rec.get("source_filename") or "").strip()
    if not title:
        return None
    cat = _rec_cat(rec)
    text = (rec.get("text") or rec.get("snippet") or "").strip()
    return {
        "id": str(rid).strip(),
        "cat": cat,
        "kind": _CAT_LABEL.get(cat, "문서"),
        "title": title,
        "snippet": text[:240],
    }


# ── 큰 docs 런타임 섹션추출 (#39 2B) ──────────────────────────
# 문제: 큰 계약서(예 특약매입 5118자)는 판촉 조항이 긴 본문에 묻혀 snippet[:240]/프롬프트[:120]
# 밖으로 잘림 → LLM이 관련성을 못 봄(작은 약정서는 전체가 보여 인용됨). 크기 비대칭 버그.
# 해법: 큰 docs를 제N조 단위로 런타임 분할해 *판촉 관련 조*가 짧고 on-point한 독립 후보로 떠
#       작은 약정서와 동등 경쟁. RAG 재색인 아님 — in-memory split, docs 쓰기 0.
#       각 조 후보의 title은 부모 문서명(렌더는 「특약매입 계약서」로 — 조 단위 노출 아님).
_ARTICLE_MARK = re.compile(r"(제\s*\d+\s*조(?:의\s*\d+)?)")
_LARGE_DOC_CHARS = 800  # 이보다 길면 조 단위 분할(작은 약정서 ~수백자와 동등 경쟁 보장)


def _split_articles(text):
    """본문을 제N조 경계로 분할 → [(head, segment), …]. 마커 2개 미만이면 [](분할 불가)."""
    parts = _ARTICLE_MARK.split(text or "")  # [전문, '제1조', 본문1, '제2조', 본문2, …]
    if len(parts) < 3:
        return []
    sections, i = [], 1
    while i < len(parts) - 1:
        head = parts[i].strip()
        seg = (head + parts[i + 1]).strip()
        if seg:
            sections.append((head, seg))
        i += 2
    return sections


def expand_doc_sections(rec, large_chars=_LARGE_DOC_CHARS):
    """큰 docs 레코드를 제N조 단위 후보로 확장(런타임). 작거나 조 마커 없으면 원 레코드 1건.
    부모(전문) 후보는 실 id로 유지(인용 호환) + 각 조는 합성 id, title=부모 문서명."""
    base = make_candidate(rec)
    if not base:
        return []
    text = (rec.get("text") or rec.get("snippet") or "")
    if len(text) <= large_chars:
        return [base]
    sections = _split_articles(text)
    if not sections:
        return [base]
    out, seen = [base], {base["id"]}
    for head, seg in sections:
        sid = f"{base['id']}::{head.replace(' ', '')}"
        if sid in seen:
            continue
        seen.add(sid)
        out.append({"id": sid, "cat": base["cat"], "kind": base["kind"],
                    "title": base["title"], "snippet": seg[:240]})
    return out


def make_candidates(records, cats=INTERNAL_CATS, expand_large=False):
    """레코드 목록 → 후보집합(닫힌 집합). cats로 도메인 필터. id 중복 제거.
    expand_large=True면 큰 docs를 제N조 단위 후보로 확장(#39 2B — on-point 조 가시화)."""
    out, seen = [], set()
    for rec in (records or []):
        if cats is not None and _rec_cat(rec) not in cats:
            continue
        items = expand_doc_sections(rec) if expand_large else [make_candidate(rec)]
        for c in items:
            if c and c["id"] not in seen:
                seen.add(c["id"])
                out.append(c)
    return out


def candidates_prompt_block(candidates):
    """LLM 프롬프트용 후보 목록 텍스트 — '인용은 이 id로만, 새 문서명 창작 금지'.
    빈 후보집합이면 내부문서 인용 자체를 금지(정직한 '해당 없음')."""
    if not candidates:
        return ("[내부문서 후보 없음] — 내부문서(사규·계약·약정)를 인용하지 마라. "
                "관련 내부문서가 검색되지 않았으므로 cited_source_ids는 빈 배열, 문서명 창작 절대 금지.")
    lines = ["[내부문서 후보 — 인용은 반드시 아래 id로만. 목록에 없는 문서명을 새로 지어내지 마라]"]
    for c in candidates:
        lines.append(f"- id={c['id']} [{c['kind']}] {c['title']} :: {c['snippet'][:120]}")
    return "\n".join(lines)


def ground_ids(cited_ids, candidates):
    """LLM이 낸 cited_source_ids 중 '후보집합에 있는 id만' 통과(구조적 배제).
    반환: 후보 레코드 목록(title/snippet은 레코드에서). 후보 밖 id·중복 드롭. 유효 0이면 []."""
    by_id = {c["id"]: c for c in (candidates or [])}
    out, seen = [], set()
    for cid in (cited_ids or []):
        key = str(cid).strip()
        if key in by_id and key not in seen:
            seen.add(key)
            out.append(by_id[key])
    return out


def is_hallucinated_name(name, candidates):
    """자유텍스트 문서명이 후보 title 중 어디에도 정확히 없으면 환각(렌더 금지).
    기존 문자열 매칭 '허구 사규 감지'를 대체 — 후보 멤버십 기준."""
    n = (name or "").strip()
    if not n:
        return False
    return not any(n == c["title"] for c in (candidates or []))


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
# prose에서 UUID 누출을 정리할 텍스트 필드(문서명은 「title」로만 나와야 함)
_PROSE_FIELDS = ("law_analysis", "rule_analysis", "recommendation", "target_clause", "summary")


def scrub_uuids(text, candidates):
    """prose에서 raw UUID 백스톱(Bug1) — 후보 id면 「title」로 치환, 후보 밖 stray는 제거.
    LLM이 본문에서 문서를 id로 지칭한 누출을 렌더 직전 정리. id는 cited_source_ids에만 있어야 함."""
    if not text or "-" not in text:
        return text
    by_id = {c["id"].lower(): c for c in (candidates or [])}

    def _repl(m):
        c = by_id.get(m.group(0).lower())
        return f"「{c['title']}」" if c else ""

    out = _UUID_RE.sub(_repl, text)
    if out == text:
        return text
    # 치환/제거 후 군더더기 정리: 빈 꺾쇠, 연속 공백, 매달린 '및'/구두점 앞 공백
    out = out.replace("「」", "")
    out = re.sub(r"\s*및\s*(?=[,.)\]}」]|$)", "", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.)\]}])", r"\1", out)
    return out.strip()


def apply_grounding(json_data, candidates):
    """LLM 출력(json_data)의 내부문서 인용을 grounding(멱등). 각 issue:
      1) cited_source_ids가 있으면 → 후보 레코드 title로 applicable_rule을 '레코드에서' 재작성.
      2) 없고 기존 applicable_rule이 후보 title과 정확히 일치하면 유지(멤버십 통과).
      3) 둘 다 아니면(후보 밖 자유텍스트 = 환각) → '해당 없음'(날조 0, 안전한 실패).
    + prose의 UUID 누출 백스톱(scrub_uuids). grounded_sources({id,kind,title}) 부착.
    법 인용(applicable_law)은 미수정(법 쪽 무회귀)."""
    if not isinstance(json_data, dict):
        return json_data
    for iss in (json_data.get("issues") or []):
        if not isinstance(iss, dict):
            continue
        cited = iss.get("cited_source_ids") or []
        recs = ground_ids(cited, candidates)
        if recs:
            iss["grounded_sources"] = [{"id": r["id"], "kind": r["kind"], "title": r["title"]} for r in recs]
            # 같은 문서의 여러 조가 인용되면(2B 섹션추출) title 중복 → 표시용 dedup(순서 유지).
            _titles = list(dict.fromkeys(r["title"] for r in recs))
            iss["applicable_rule"] = " / ".join(f"「{t}」" for t in _titles)
        else:
            iss["grounded_sources"] = []
            cur = (iss.get("applicable_rule") or "").strip().strip("「」")
            if not (cur and not is_hallucinated_name(cur, candidates)):
                iss["applicable_rule"] = "해당 없음"  # 후보 밖 = 환각 → 구조적 배제
        # UUID 누출 백스톱 — prose 필드의 raw id를 「title」로/제거
        for f in _PROSE_FIELDS:
            if iss.get(f):
                iss[f] = scrub_uuids(iss[f], candidates)
    # 최상위 prose도 스크럽(요약·결론·액션에 id 누출 방지)
    for f in ("summary", "verdict_reason", "action_plan"):
        if json_data.get(f):
            json_data[f] = scrub_uuids(json_data[f], candidates)
    return json_data
    return json_data


def grounded_titles(cited_ids, candidates, kind=None):
    """렌더용: 유효 id → 레코드 title 목록(중복 없음). kind로 사규/계약/약정 필터.
    LLM 텍스트가 아니라 레코드 title만 반환 → '및 계약서' 같은 파편 구조적 불가."""
    recs = ground_ids(cited_ids, candidates)
    if kind:
        recs = [r for r in recs if r["kind"] == kind]
    titles, seen = [], set()
    for r in recs:
        if r["title"] not in seen:
            seen.add(r["title"])
            titles.append(r["title"])
    return titles
