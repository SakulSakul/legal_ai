"""
triage_util.py — MD triage 출력 보강 로직 (streamlit 비의존 → 단위테스트 가능)

Step 4 출력 재설계의 '데이터' 책임만 담당한다. 렌더(st.*)는 legal_ai.py가 맡고,
여기서는 json_data에 triage 필드를 채우는 순수 함수만 둔다.

추가 필드 (json_data 계약 — 기존 필드는 보존, 아래만 추가):
  - evidence_grade: "db_guaranteed"(블록삽입 경로) | "llm_draft"(LLM 생성 경로)
  - severity: {level, icon, label, color, bg}  ← verdict에서 파생
  - escalation: 에스컬레이션 가이드 문자열 (🔴/🟡일 때만 비어있지 않음)
"""

# verdict(기존 값) → 평이한 3단계 심각도 (주니어도 즉시 이해)
SEVERITY_MAP = {
    "rejected":    {"level": "stop",    "icon": "🔴", "label": "진행 금지",         "color": "#C62828", "bg": "#FCE4EC"},
    "conditional": {"level": "caution", "icon": "🟡", "label": "법무 확인 후 진행", "color": "#F57F17", "bg": "#FFF8E1"},
    "approved":    {"level": "go",      "icon": "🟢", "label": "진행 가능",         "color": "#2E7D32", "bg": "#E8F5E9"},
}
DEFAULT_SEVERITY = {"level": "unknown", "icon": "⚪", "label": "판단 보류", "color": "#616161", "bg": "#F5F5F5"}

# 신뢰 경계 배지 — 주니어 과신 방지 핵심 안전장치
EVIDENCE_BADGES = {
    "db_guaranteed": {"text": "🔒 사규·법령 DB 근거 (검증된 분석)", "color": "#1565C0", "bg": "#E3F2FD"},
    "llm_draft":     {"text": "⚠️ AI 초안 · 반드시 법무 검증 필요", "color": "#9C6500", "bg": "#FFF8E1"},
}

ESCALATION_TEXT = "→ 진행 전 [법무팀/팀 선임]에 확인 필수"


def evidence_grade_for(matched_topics) -> str:
    """
    dispatch가 어느 경로를 탔는지로 근거 등급 결정.
      - 토픽 매칭(블록삽입, 형량 DB 보장) → "db_guaranteed"
      - 미매칭(LLM 생성)                 → "llm_draft"
    새 분류 로직 없음 — 기존 matched_topics 유무만 본다.
    """
    return "db_guaranteed" if matched_topics else "llm_draft"


def severity_for(verdict: str) -> dict:
    return SEVERITY_MAP.get(verdict, DEFAULT_SEVERITY)


def enrich_triage_fields(jd: dict) -> dict:
    """
    json_data에 triage 필드를 멱등(idempotent) 보강. 기존 필드는 건드리지 않는다.
    여러 번 호출돼도 안전(렌더·docx·로깅이 각각 호출).
    """
    if not isinstance(jd, dict):
        return jd
    jd.setdefault("evidence_grade", "llm_draft")
    sev = severity_for(jd.get("verdict", ""))
    jd["severity"] = sev
    if sev["level"] in ("stop", "caution"):
        jd["escalation"] = jd.get("escalation") or ESCALATION_TEXT
    else:
        jd["escalation"] = jd.get("escalation", "")
    return jd
