"""
metrics.py — 측정 지표 함수

legal_ai 블록삽입 아키텍처에 맞춘 4축 중 결정론적으로 측정 가능한
2축(쟁점 분류, 사규 retrieval)의 지표를 제공한다.
LLM 의존 축(답변 적합성·형량 정확성)은 step3 이후 추가.
"""
from typing import List, Set


def recall_at_k(retrieved: List[str], gold: List[str], k: int) -> float:
    """상위 k개 안에 들어온 gold 비율."""
    if not gold:
        return 1.0
    topk = set(retrieved[:k])
    hit = sum(1 for g in gold if g in topk)
    return hit / len(gold)


def precision_at_k(retrieved: List[str], gold: List[str], k: int) -> float:
    if k == 0:
        return 0.0
    topk = retrieved[:k]
    if not topk:
        return 0.0
    gold_set = set(gold)
    hit = sum(1 for r in topk if r in gold_set)
    return hit / len(topk)


def mrr(retrieved: List[str], gold: List[str]) -> float:
    """첫 정답이 등장하는 순위의 역수(Mean Reciprocal Rank, 단일 쿼리)."""
    gold_set = set(gold)
    for i, r in enumerate(retrieved, 1):
        if r in gold_set:
            return 1.0 / i
    return 0.0


def surfaced_recall(surfaced: Set[str], gold: List[str]) -> float:
    """
    블랙박스 recall — 최종 retrieval 출력 문자열에 gold 조항이
    '실제로 포함되었는가'. LLM이 보는 컨텍스트 기준의 정직한 지표.
    (max_chars 예산·truncation까지 반영됨)
    """
    if not gold:
        return 1.0
    hit = sum(1 for g in gold if g in surfaced)
    return hit / len(gold)


def classification_correct(predicted: List[str], expected) -> bool:
    """
    분류 정답 여부.
      expected=None  → 아무 토픽도 매칭 안 돼야 정답 (false-positive 방지)
      expected=str   → 해당 토픽이 매칭 결과에 포함돼야 정답
    """
    if expected is None:
        return len(predicted) == 0
    return expected in predicted


def mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0
