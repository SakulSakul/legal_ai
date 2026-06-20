#!/usr/bin/env python3
"""
tune_threshold.py — classify 임베딩 임계값 보정 (GEMINI_API_KEY 필요)

§3.2 핵심: negative(단가·판촉·그린워싱)가 임계값 아래로 떨어지면서
synonym·oblique는 위로 올라오는 '안전 창(window)'을 찾는다.

각 임계값에 대해:
  - synonym/oblique 분류정확도 (높을수록 좋음)
  - negative false-positive 건수 (반드시 0)
를 측정하고, FP=0을 지키면서 synonym+oblique를 최대화하는 값을 추천한다.

실행: GEMINI_API_KEY=... python eval/tune_threshold.py
권장 임계값을 CLASSIFY_EMB_THRESHOLD env로 설정 후 `make eval` 재확인.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run_baseline as RB                                       # noqa: E402
from block_assembler import classify_issues, load_legal_blocks  # noqa: E402
import embedding_util                                           # noqa: E402
import metrics as M                                             # noqa: E402


class C:
    OK = "\033[92m"; FAIL = "\033[91m"; BOLD = "\033[1m"; DIM = "\033[2m"; END = "\033[0m"


def main():
    if embedding_util.embed_one("키 점검") is None:
        print(f"{C.FAIL}GEMINI_API_KEY 미설정 — 임계값 튜닝 불가. 키 설정 후 재실행.{C.END}")
        sys.exit(2)

    testset = RB.load_json("testset.json")
    db = load_legal_blocks(os.path.join(ROOT, "legal_blocks.json"))
    cases = testset["cases"]

    thresholds = [round(0.40 + 0.02 * i, 2) for i in range(26)]  # 0.40 ~ 0.90
    print(f"\n{C.BOLD}임계값 스윕 (synonym·oblique↑ / negative FP=0 유지){C.END}\n")
    print(f"  {'thr':>5}  {'synonym':>8}  {'oblique':>8}  {'neg FP':>7}  {'direct':>7}")

    best = None
    for thr in thresholds:
        os.environ["CLASSIFY_EMB_THRESHOLD"] = str(thr)
        by = {"synonym": [], "oblique": [], "negative": [], "direct": []}
        neg_fp = 0
        for c in cases:
            pred = classify_issues(c["query"], db)
            ok = M.classification_correct(pred, c["expected_topic"])
            by[c["level"]].append(1.0 if ok else 0.0)
            if c["level"] == "negative" and len(pred) > 0:
                neg_fp += 1
        syn = M.mean(by["synonym"]); obl = M.mean(by["oblique"])
        dr = M.mean(by["direct"])
        flag = ""
        if neg_fp == 0:
            score = syn + obl
            if best is None or score > best[1]:
                best = (thr, score, syn, obl)
                flag = f"  {C.OK}◀ 후보{C.END}"
        col = C.OK if neg_fp == 0 else C.FAIL
        print(f"  {thr:>5.2f}  {syn*100:>7.1f}%  {obl*100:>7.1f}%  "
              f"{col}{neg_fp:>6}{C.END}  {dr*100:>6.1f}%{flag}")

    print()
    if best:
        thr, _, syn, obl = best
        print(f"{C.BOLD}{C.OK}추천 임계값: CLASSIFY_EMB_THRESHOLD={thr}{C.END}")
        print(f"  → synonym {syn*100:.0f}% / oblique {obl*100:.0f}% / negative FP 0")
        print(f"{C.DIM}  설정 후: CLASSIFY_EMB_THRESHOLD={thr} make eval 로 게이트 재확인{C.END}\n")
    else:
        print(f"{C.FAIL}FP=0을 만족하는 임계값이 없음 — 토픽 대표텍스트/코퍼스 점검 필요{C.END}\n")


if __name__ == "__main__":
    main()
