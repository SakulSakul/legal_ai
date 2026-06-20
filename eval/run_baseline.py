#!/usr/bin/env python3
"""
run_baseline.py — legal_ai 현재 코드 baseline 측정

원칙:
  - 레포의 실제 함수를 import해서 측정한다 (재구현 금지 → drift 차단)
  - 모델은 현재 버전으로 고정 (controlled variable). 본 스크립트는
    결정론적 2축(쟁점 분류 / 사규 retrieval)만 측정하므로 LLM·API 불필요.
  - 결과를 JSON으로 저장해 변경 전후 델타를 추적한다 (self-healing 추적 자산).

실행: python eval/run_baseline.py
"""
import os
import sys
import json
import datetime

# 레포 루트를 path에 추가 → 실제 모듈 import
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from block_assembler import classify_issues, load_legal_blocks          # noqa: E402
from saryu_retriever import (                                            # noqa: E402
    chunk_by_article, score_chunk, extract_keywords, retrieve_relevant_saryu,
)
import metrics as M                                                      # noqa: E402


# ── 컬러 ────────────────────────────────────────────────
class C:
    OK = "\033[92m"; WARN = "\033[93m"; FAIL = "\033[91m"
    BOLD = "\033[1m"; DIM = "\033[2m"; CYAN = "\033[96m"; END = "\033[0m"


def load_json(name):
    with open(os.path.join(HERE, name), "r", encoding="utf-8") as f:
        return json.load(f)


def rank_chunks(query, docs):
    """retriever 내부 로직을 복제해 점수순 chunk id 랭킹 반환 (진단용 Recall@K)."""
    kws = extract_keywords(query)
    scored = []
    for doc in docs:
        if doc.get("cat") in ("saryu", "contract", "yakjeong"):
            for ch in chunk_by_article(doc.get("text", ""), doc.get("label", "")):
                if not ch["article"]:
                    continue
                cid = f"{ch['label']}|{ch['article']}"
                scored.append((cid, score_chunk(ch, kws)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [cid for cid, sc in scored]


def surfaced_articles(output_text, gold):
    """retrieve_relevant_saryu 출력 문자열에 gold 조항이 실제로 포함됐는지."""
    found = set()
    for g in gold:
        label, article = g.split("|", 1)
        marker = f"[{label}] {article}"
        if marker in output_text:
            found.add(g)
    return found


def main():
    testset = load_json("testset.json")
    corpus = load_json("saryu_corpus.json")
    docs = corpus["docs"]
    cases = testset["cases"]

    rows = []
    for c in cases:
        q = c["query"]
        gold = c["gold_saryu_articles"]

        # 1) 쟁점 분류 (실제 classify_issues)
        db = load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))
        predicted = classify_issues(q, db)
        cls_ok = M.classification_correct(predicted, c["expected_topic"])

        # 2) 사규 retrieval — 블랙박스(LLM이 보는 컨텍스트)
        out = retrieve_relevant_saryu(q, docs, max_chars=5000)
        surf = surfaced_articles(out, gold)
        s_recall = M.surfaced_recall(surf, gold)

        # 3) 사규 retrieval — 랭킹 진단
        ranked = rank_chunks(q, docs)
        r3 = M.recall_at_k(ranked, gold, 3)
        r5 = M.recall_at_k(ranked, gold, 5)
        mrr_v = M.mrr(ranked, gold)

        rows.append({
            "id": c["id"], "level": c["level"], "query": q,
            "expected_topic": c["expected_topic"], "predicted_topic": predicted,
            "cls_ok": cls_ok,
            "surfaced_recall": round(s_recall, 3),
            "recall@3": round(r3, 3), "recall@5": round(r5, 3),
            "mrr": round(mrr_v, 3),
            "missed_articles": [g for g in gold if g not in surf],
        })

    # ── 집계 ───────────────────────────────────────────
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
    overall = agg()
    by_level = {lv: agg(lv) for lv in levels}

    # ── 출력 ───────────────────────────────────────────
    def bar(v):
        n = int(round(v * 20))
        col = C.OK if v >= 0.8 else (C.WARN if v >= 0.5 else C.FAIL)
        return f"{col}{'█'*n}{C.DIM}{'░'*(20-n)}{C.END}"

    print(f"\n{C.BOLD}{C.CYAN}{'='*64}")
    print(f"  legal_ai BASELINE  —  모델 고정(현재 버전), 결정론적 2축")
    print(f"{'='*64}{C.END}\n")

    print(f"{C.BOLD}난이도별 (쟁점분류 / 사규검색){C.END}")
    print(f"  {'level':<10}{'n':>3}  {'분류정확도':<14}{'검색Recall(체감)':<18}{'Recall@5':<12}{'MRR'}")
    for lv in levels:
        a = by_level[lv]
        if not a:
            continue
        print(f"  {lv:<10}{a['n']:>3}  "
              f"{a['cls_acc']*100:>5.1f}% {bar(a['cls_acc'])}  "
              f"{a['surfaced_recall']*100:>5.1f}% {bar(a['surfaced_recall'])}  "
              f"{a['recall@5']*100:>5.1f}%   {a['mrr']:.2f}")
    print(f"  {C.BOLD}{'OVERALL':<10}{overall['n']:>3}  "
          f"{overall['cls_acc']*100:>5.1f}%        "
          f"{overall['surfaced_recall']*100:>5.1f}%           "
          f"{overall['recall@5']*100:>5.1f}%   {overall['mrr']:.2f}{C.END}")

    # 약점 쿼리 (weak docs 아날로그)
    weak = [r for r in rows if (not r["cls_ok"] and r["expected_topic"]) or r["surfaced_recall"] < 0.5]
    print(f"\n{C.BOLD}{C.FAIL}약점 쿼리 ({len(weak)}건) — 우선 개선 대상{C.END}")
    for r in weak:
        cls = "✅" if r["cls_ok"] else "❌분류실패"
        print(f"  {C.DIM}[{r['id']}/{r['level']}]{C.END} {r['query'][:34]}…  "
              f"분류:{cls} 검색:{r['surfaced_recall']*100:.0f}%")
        if r["missed_articles"]:
            print(f"        {C.DIM}↳ 누락: {', '.join(r['missed_articles'])}{C.END}")

    # ── 저장 ───────────────────────────────────────────
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    result = {
        "timestamp": ts,
        "model": "FIXED (baseline control)",
        "overall": overall, "by_level": by_level, "rows": rows,
    }
    out_path = os.path.join(HERE, f"results_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n{C.DIM}결과 저장: eval/results_{ts}.json (델타 추적용){C.END}\n")


if __name__ == "__main__":
    main()
