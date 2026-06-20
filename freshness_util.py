"""
freshness_util.py — 블록 의존성 freshness 비교 로직 (순수·key-free)

블록의 법적 근거(의존 법령·고시)가 현행인지 '조문 단위'로 판정한다.
streamlit·키·MCP·네트워크를 일절 import 하지 않는다 — 현행 시행일(live)은 호출부가
주입한 resolve 함수로 가져오고, 여기서는 저장값 vs live 비교만 한다(완전 결정론).

모델: 블록 FRESH ⟺ 모든 의존 조문이 검증 시점 이후 미개정(의존성 AND).
fail-safe: 모호·미조회는 NEEDS_REVIEW, 의존성 미보강 블록은 UNCOVERED(절대 FRESH 아님).
"""
# 블록 판정
FRESH = "FRESH"
STALE = "STALE"
NEEDS_REVIEW = "NEEDS_REVIEW"
UNCOVERED = "UNCOVERED"
# 조문(타겟) 판정
OK = "OK"


def iter_targets(dependencies):
    """의존성을 검증 단위(조문 또는 행정규칙 rule)로 펼친다.
    yield {dep, type, ref, no, stored}. 고시처럼 조문별 날짜가 없고
    rule_effective_date만 있으면 rule 단위(no=None)로 본다."""
    for dep in dependencies or []:
        name = dep.get("name")
        dtype = dep.get("type")
        ref = dep.get("ref")
        articles = dep.get("articles") or []
        rule_eff = dep.get("rule_effective_date")
        has_article_dates = any(isinstance(a, dict) and a.get("effective_date") for a in articles)
        if rule_eff and not has_article_dates:
            yield {"dep": name, "type": dtype, "ref": ref, "no": None, "stored": rule_eff}
        else:
            for a in articles:
                if isinstance(a, dict):
                    yield {"dep": name, "type": dtype, "ref": ref,
                           "no": a.get("no"), "stored": a.get("effective_date")}


def _key(t):
    return (t["dep"], t["no"])


def check_target(stored, live):
    """저장 시행일 vs 현행(live) 시행일. ISO 문자열 비교(= 시간순).
      live 없음/저장 없음 → NEEDS_REVIEW
      같음 → OK / live가 더 최근 → STALE(개정 감지) / 저장이 미래(이상치) → NEEDS_REVIEW
    """
    if not stored or not live:
        return NEEDS_REVIEW
    if live == stored:
        return OK
    if live > stored:
        return STALE
    return NEEDS_REVIEW


def check_block_freshness(block, live_dates):
    """
    block(dependencies 포함) + live_dates{(dep,no): ISO|None} → 판정.
    Returns {verdict, targets:[{dep,no,type,ref,stored,live,verdict}], reason}.
    """
    deps = block.get("dependencies")
    if not deps:
        return {"verdict": UNCOVERED, "targets": [],
                "reason": "의존성 미검증 — 온보딩 필요(절대 현행으로 간주 금지)"}

    targets = []
    for t in iter_targets(deps):
        live = live_dates.get(_key(t))
        targets.append({**t, "live": live, "verdict": check_target(t["stored"], live)})

    verds = [t["verdict"] for t in targets]
    if STALE in verds:
        overall = STALE
    elif not targets:
        overall = UNCOVERED
    elif all(v == OK for v in verds):
        overall = FRESH
    else:
        overall = NEEDS_REVIEW
    return {"verdict": overall, "targets": targets, "reason": _summarize(overall, targets)}


def _summarize(overall, targets):
    if overall == FRESH:
        return "모든 의존 조문이 현행 시행일과 일치"
    bad = [t for t in targets if t["verdict"] != OK]
    parts = []
    for t in bad:
        unit = f"{t['dep']} {t['no']}" if t["no"] else t["dep"]
        if t["verdict"] == STALE:
            parts.append(f"{unit}: 저장 {t['stored']} → 현행 {t['live']} (개정 감지)")
        else:
            parts.append(f"{unit}: 현행 시행일 확인 불가(저장 {t['stored']})")
    return " · ".join(parts) if parts else "판정 불가"


def sweep(blocks, resolve_fn):
    """
    blocks(issue dict 목록)를 순회하며 freshness 판정.
    resolve_fn(dep_name, article_no, dep_type, ref) -> 현행 ISO 시행일 또는 None (주입).
    의존성 없는 블록은 resolve 호출 없이 UNCOVERED. 감지 전용 — 아무것도 수정 안 함.
    """
    report = []
    for block in blocks:
        deps = block.get("dependencies")
        if not deps:
            report.append({"block": block.get("id"), "title": block.get("title", ""),
                           "verdict": UNCOVERED, "targets": [],
                           "reason": "의존성 미검증 — §8 온보딩 필요"})
            continue
        live = {}
        for t in iter_targets(deps):
            try:
                live[_key(t)] = resolve_fn(t["dep"], t["no"], t.get("type"), t.get("ref"))
            except Exception:
                live[_key(t)] = None
        res = check_block_freshness(block, live)
        report.append({"block": block.get("id"), "title": block.get("title", ""), **res})
    return report
