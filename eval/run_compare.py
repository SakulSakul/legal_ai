#!/usr/bin/env python3
"""
run_compare.py — 현재 코드 측정 → baseline_locked.json 대비 델타 + PASS/FAIL

원칙:
  - 스코어링 헬퍼(rank_chunks/surfaced_articles)는 run_baseline에서 import해
    재사용한다 → baseline과 current가 동일 척도로 측정됨(drift 차단).
  - 완료기준(지시서 §5)을 게이트로 인코딩. 전부 충족 시 exit 0, 하나라도
    미달/회귀 시 exit 1 → CI·Makefile이 그대로 회귀 차단에 사용.

실행: python eval/run_compare.py
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

# baseline과 동일한 스코어링 헬퍼를 공유 (단일 진실원 → drift 방지)
import run_baseline as RB                                                # noqa: E402
from block_assembler import classify_issues, load_legal_blocks          # noqa: E402
from saryu_retriever import retrieve_relevant_saryu, rank_chunk_ids      # noqa: E402
import metrics as M                                                     # noqa: E402


class C:
    OK = "\033[92m"; WARN = "\033[93m"; FAIL = "\033[91m"
    BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[96m"; END = "\033[0m"


def measure():
    """현재 레포 코드로 전 케이스 측정 → (rows, overall, by_level)."""
    testset = RB.load_json("testset.json")
    corpus = RB.load_json("saryu_corpus.json")
    docs = corpus["docs"]
    db = load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))

    rows = []
    for c in testset["cases"]:
        q = c["query"]
        gold = c["gold_saryu_articles"]

        predicted = classify_issues(q, db)
        cls_ok = M.classification_correct(predicted, c["expected_topic"])

        out = retrieve_relevant_saryu(q, docs, max_chars=5000)
        surf = RB.surfaced_articles(out, gold)
        s_recall = M.surfaced_recall(surf, gold)

        # 현재 시스템의 실제 랭킹(하이브리드 융합 순서)으로 Recall@5/MRR 측정.
        # baseline_locked는 키워드 순서(당시 시스템) — 각 버전의 실제 랭킹 기준 비교.
        ranked = rank_chunk_ids(q, docs)
        r5 = M.recall_at_k(ranked, gold, 5)
        mrr_v = M.mrr(ranked, gold)

        rows.append({
            "id": c["id"], "level": c["level"],
            "expected_topic": c["expected_topic"], "predicted_topic": predicted,
            "cls_ok": cls_ok, "surfaced_recall": s_recall,
            "recall@5": r5, "mrr": mrr_v,
        })

    def agg(level=None):
        sub = [r for r in rows if level is None or r["level"] == level]
        if not sub:
            return None
        return {
            "n": len(sub),
            "cls_acc": M.mean([1.0 if r["cls_ok"] else 0.0 for r in sub]),
            "surfaced_recall": M.mean([r["surfaced_recall"] for r in sub]),
            "recall@5": M.mean([r["recall@5"] for r in sub]),
            "mrr": M.mean([r["mrr"] for r in sub]),
        }

    levels = ["direct", "synonym", "oblique", "negative"]
    return rows, agg(), {lv: agg(lv) for lv in levels}


def structural_smoke():
    """verify_block_integrity가 여전히 통과하는지 (블록 무결성 스모크)."""
    try:
        from block_assembler import run_pipeline
        res = run_pipeline(
            query="면세점에서 모조품 판매 행위에 대해 법률 검토해줘",
            사규_texts=["(테스트 사규)"],
            gemini_call_fn=None,
            db_path=os.path.join(ROOT, "legal_blocks.json"),
        )
        return len(res["integrity_errors"]) == 0
    except Exception as e:
        print(f"{C.FAIL}structural_smoke 예외: {e}{C.END}")
        return False


def fmt_delta(base, cur):
    d = cur - base
    sign = "+" if d >= 0 else ""
    col = C.OK if d > 0.001 else (C.FAIL if d < -0.001 else C.DIM)
    return f"{base*100:>5.1f}% → {cur*100:>5.1f}% {col}({sign}{d*100:.1f}%p){C.END}"


def gate(name, ok, detail):
    tag = f"{C.OK}PASS{C.END}" if ok else f"{C.FAIL}FAIL{C.END}"
    print(f"  [{tag}] {name:<34} {detail}")
    return ok


def main():
    with open(os.path.join(HERE, "baseline_locked.json"), "r", encoding="utf-8") as f:
        base = json.load(f)
    b_over, b_lvl = base["overall"], base["by_level"]

    rows, over, lvl = measure()

    print(f"\n{C.BOLD}{C.CYAN}{'='*66}")
    print(f"  legal_ai COMPARE  —  baseline_locked 대비 델타")
    print(f"{'='*66}{C.END}\n")

    print(f"{C.BOLD}난이도별 분류정확도 (baseline → current){C.END}")
    for lv in ["direct", "synonym", "oblique", "negative"]:
        print(f"  {lv:<10} {fmt_delta(b_lvl[lv]['cls_acc'], lvl[lv]['cls_acc'])}")
    print(f"\n{C.BOLD}전체 지표 (baseline → current){C.END}")
    print(f"  {'분류정확도':<12} {fmt_delta(b_over['cls_acc'], over['cls_acc'])}")
    print(f"  {'검색Recall(체감)':<12} {fmt_delta(b_over['surfaced_recall'], over['surfaced_recall'])}")
    print(f"  {'Recall@5':<12} {fmt_delta(b_over['recall@5'], over['recall@5'])}")

    # ── 완료기준 게이트 (§5) ────────────────────────────
    print(f"\n{C.BOLD}완료기준 게이트 (지시서 §5){C.END}")
    gates = []
    print(f"{C.DIM}  — 개선 목표 —{C.END}")
    gates.append(gate("synonym 분류 ≥ 80%", lvl["synonym"]["cls_acc"] >= 0.80,
                      f"{lvl['synonym']['cls_acc']*100:.1f}%"))
    gates.append(gate("oblique 분류 ≥ 70%", lvl["oblique"]["cls_acc"] >= 0.70,
                      f"{lvl['oblique']['cls_acc']*100:.1f}%"))
    gates.append(gate("Recall@5 ≥ 70%", over["recall@5"] >= 0.70,
                      f"{over['recall@5']*100:.1f}%"))
    gates.append(gate("검색 체감recall ≥ 85%", over["surfaced_recall"] >= 0.85,
                      f"{over['surfaced_recall']*100:.1f}%"))
    print(f"{C.DIM}  — 회귀 금지 (controlled) —{C.END}")
    gates.append(gate("direct 분류 100% 유지", lvl["direct"]["cls_acc"] >= b_lvl["direct"]["cls_acc"],
                      f"{lvl['direct']['cls_acc']*100:.1f}% (base {b_lvl['direct']['cls_acc']*100:.1f}%)"))
    gates.append(gate("negative 분류 100% 유지 (FP=0)", lvl["negative"]["cls_acc"] >= b_lvl["negative"]["cls_acc"],
                      f"{lvl['negative']['cls_acc']*100:.1f}% (base {b_lvl['negative']['cls_acc']*100:.1f}%)"))
    print(f"{C.DIM}  — 구조 무결성 —{C.END}")
    gates.append(gate("verify_block_integrity 통과", structural_smoke(), "블록 무결성 스모크"))

    all_ok = all(gates)
    print()
    if all_ok:
        print(f"{C.BOLD}{C.OK}  ✅ ALL GATES PASS — PR 완료 기준 충족{C.END}\n")
        sys.exit(0)
    else:
        n_fail = sum(1 for g in gates if not g)
        print(f"{C.BOLD}{C.FAIL}  ❌ {n_fail} GATE(S) FAIL — 미충족 또는 회귀{C.END}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
