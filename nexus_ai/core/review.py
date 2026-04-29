"""Phase 3.5 도메인 전문가 검수 엔진.

각 샘플마다 챗봇을 실행하고 4지표로 자동 채점한다.
- accuracy:        expected_keywords 매칭률 (부분일치)
- citation:        답변 출처에 expected_citation 패턴 포함 여부 (0/1)
- hotline_missing: 심각 사안인데 핫라인 박스가 없으면 1.0, 정상이면 0.0
- critical_trigger: is_critical 결과가 expected_critical 와 일치하면 True
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from .chatbot import ask


_RE_CITATION_BLOCK = re.compile(r"\[참조:([^\]]+)\]")
_HOTLINE_HEADERS = ("핫라인 안내", "사내 익명 제보채널", "외부 상담채널")


@dataclass
class ScoreCard:
    accuracy: float
    citation: float
    hotline_missing: float
    critical_trigger_ok: bool
    passed: bool
    failure_reasons: list[str]


def _accuracy(answer: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    hits = sum(1 for k in keywords if k and k.strip() and k in answer)
    return hits / len([k for k in keywords if k and k.strip()])


def _citation_score(answer: str, expected: str | None) -> float:
    if not expected:
        # 기대 출처가 명시되지 않은 경우 — [참조:] 블록 존재 여부만 본다
        return 1.0 if _RE_CITATION_BLOCK.search(answer) else 0.0
    cite = _RE_CITATION_BLOCK.search(answer)
    if not cite:
        return 0.0
    return 1.0 if expected.strip() in cite.group(1) else 0.0


def _hotline_missing(answer: str, expected_critical: bool) -> float:
    if not expected_critical:
        return 0.0
    has = any(h in answer for h in _HOTLINE_HEADERS)
    return 0.0 if has else 1.0


def _evaluate(
    *,
    answer_text: str,
    is_critical: bool,
    critical_kind: str | None,
    sample: dict,
    threshold: dict,
) -> ScoreCard:
    acc  = _accuracy(answer_text, sample.get("expected_keywords") or [])
    cit  = _citation_score(answer_text, sample.get("expected_citation"))
    miss = _hotline_missing(answer_text, bool(sample.get("expected_critical")))

    expected_crit = bool(sample.get("expected_critical"))
    crit_ok = (is_critical == expected_crit)
    if crit_ok and expected_crit and sample.get("expected_critical_kind"):
        crit_ok = (critical_kind == sample["expected_critical_kind"])

    reasons: list[str] = []
    if acc  < float(threshold.get("accuracy",         0.80)): reasons.append("accuracy")
    if cit  < float(threshold.get("citation",         0.95)): reasons.append("citation")
    if miss > float(threshold.get("hotline_missing",  0.00)): reasons.append("hotline_missing")
    if not crit_ok and float(threshold.get("critical_trigger", 0.95)) > 0:
        reasons.append("critical_trigger")

    return ScoreCard(
        accuracy=acc, citation=cit, hotline_missing=miss,
        critical_trigger_ok=crit_ok,
        passed=not reasons,
        failure_reasons=reasons,
    )


def run_review(supabase: Any, *, sample_ids: list[int] | None = None,
               triggered_by: str | None = None) -> dict:
    """선택된(또는 전체 active) 샘플에 대해 검수를 실행하고 회차 메타를 반환."""
    q = (
        supabase.table("review_samples")
                .select("*")
                .eq("is_active", True)
                .order("id")
    )
    if sample_ids:
        q = q.in_("id", sample_ids)
    samples = q.execute().data or []

    if not samples:
        return {"run_id": None, "total": 0, "message": "샘플이 없습니다."}

    run = supabase.table("review_runs").insert({
        "triggered_by": triggered_by,
        "total": len(samples),
        "status": "running",
    }).execute().data[0]

    run_id = run["id"]
    threshold = run["threshold"] or {}

    passed = 0
    sums = {"accuracy": 0.0, "citation": 0.0, "hotline_missing": 0.0, "critical_trigger": 0}

    for s in samples:
        try:
            ans = ask(supabase, question=s["question"], category=s.get("category"))
            sc = _evaluate(
                answer_text=ans.text,
                is_critical=ans.is_critical,
                critical_kind=ans.critical_kind,
                sample=s,
                threshold=threshold,
            )
            supabase.table("review_results").insert({
                "run_id": run_id,
                "sample_id": s["id"],
                "answer_text": ans.text,
                "contexts": [
                    {k: c.get(k) for k in ("doc_title","article_no","case_no","doc_kind")}
                    for c in ans.contexts
                ],
                "accuracy_score": sc.accuracy,
                "citation_score": sc.citation,
                "hotline_missing_score": sc.hotline_missing,
                "critical_trigger_ok": sc.critical_trigger_ok,
                "passed": sc.passed,
                "failure_reasons": sc.failure_reasons,
            }).execute()
            sums["accuracy"]        += sc.accuracy
            sums["citation"]        += sc.citation
            sums["hotline_missing"] += sc.hotline_missing
            sums["critical_trigger"] += 1 if sc.critical_trigger_ok else 0
            if sc.passed:
                passed += 1
        except Exception as e:
            supabase.table("review_results").insert({
                "run_id": run_id,
                "sample_id": s["id"],
                "answer_text": f"[에러] {e}",
                "passed": False,
                "failure_reasons": ["exception"],
            }).execute()

    n = len(samples)
    metrics = {
        "accuracy_avg":         sums["accuracy"] / n,
        "citation_avg":         sums["citation"] / n,
        "hotline_missing_avg":  sums["hotline_missing"] / n,
        "critical_trigger_acc": sums["critical_trigger"] / n,
        "pass_rate":            passed / n,
    }

    supabase.table("review_runs").update({
        "finished_at": dt.datetime.utcnow().isoformat(),
        "passed": passed,
        "failed": n - passed,
        "metrics": metrics,
        "status": "done",
    }).eq("id", run_id).execute()

    return {"run_id": run_id, "total": n, "passed": passed,
            "failed": n - passed, "metrics": metrics}


def threshold_breached(metrics: dict, threshold: dict) -> list[str]:
    """통과 기준 미달 항목 — Phase 3 회귀 트리거 판단 보조."""
    out: list[str] = []
    if metrics.get("accuracy_avg", 0)   < float(threshold.get("accuracy", 0.80)):
        out.append("accuracy")
    if metrics.get("citation_avg", 0)   < float(threshold.get("citation", 0.95)):
        out.append("citation")
    if metrics.get("hotline_missing_avg", 0) > float(threshold.get("hotline_missing", 0.0)):
        out.append("hotline_missing")
    if metrics.get("critical_trigger_acc", 0) < float(threshold.get("critical_trigger", 0.95)):
        out.append("critical_trigger")
    return out
