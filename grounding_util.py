"""
grounding_util.py — 내부문서(사규·계약·약정) 인용 grounding (streamlit 비의존, 순수 → 단위테스트).

불변식(환각 구조적 차단): 인용은 '검색된 소스 ID' 없이는 못 나간다 + 본문은 소스 레코드에서만 렌더.
  - LLM은 후보집합(닫힌 집합)의 id로 '선택'만 한다. 인용 문서명·본문을 직접 생성하지 않는다.
  - 후보집합에 없는 id·자유텍스트로 지어낸 문서명 = 드롭(탐지가 아니라 '구조적 배제').
  - 화면의 문서명·본문은 레코드(title/snippet)에서 찍는다. LLM 텍스트에서 안 뽑는다.

소스 비의존: 후보가 legal_ai `docs` 테이블이든 DF 콤파스 `nexus_*`든 동일 레이어를 통과한다
(Phase 1a=docs.id 와이어링 / Phase 1b=nexus 이전 — 이 모듈은 그대로).
"""

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


def make_candidates(records, cats=INTERNAL_CATS):
    """레코드 목록 → 후보집합(닫힌 집합). cats로 도메인 필터. id 중복 제거."""
    out, seen = [], set()
    for rec in (records or []):
        if cats is not None and _rec_cat(rec) not in cats:
            continue
        c = make_candidate(rec)
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


def apply_grounding(json_data, candidates):
    """LLM 출력(json_data)의 내부문서 인용을 grounding(멱등). 각 issue:
      1) cited_source_ids가 있으면 → 후보 레코드 title로 applicable_rule을 '레코드에서' 재작성.
      2) 없고 기존 applicable_rule이 후보 title과 정확히 일치하면 유지(멤버십 통과).
      3) 둘 다 아니면(후보 밖 자유텍스트 = 환각) → '해당 없음'(날조 0, 안전한 실패).
    grounded_sources(렌더/감사용 {id,kind,title})도 부착. 법 인용(applicable_law)은 미수정(법 쪽 무회귀)."""
    if not isinstance(json_data, dict):
        return json_data
    for iss in (json_data.get("issues") or []):
        if not isinstance(iss, dict):
            continue
        cited = iss.get("cited_source_ids") or []
        recs = ground_ids(cited, candidates)
        if recs:
            iss["grounded_sources"] = [{"id": r["id"], "kind": r["kind"], "title": r["title"]} for r in recs]
            iss["applicable_rule"] = " / ".join(f"「{r['title']}」" for r in recs)
            continue
        iss["grounded_sources"] = []
        cur = (iss.get("applicable_rule") or "").strip().strip("「」")
        if cur and not is_hallucinated_name(cur, candidates):
            continue  # 자유텍스트지만 후보 title과 정확 일치 → 멤버십 통과
        iss["applicable_rule"] = "해당 없음"  # 후보 밖 = 환각 → 구조적 배제
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
