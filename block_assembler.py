"""
block_assembler.py — 검토의견서 조립 모듈 (구조적 형량오류 방지)

핵심 원칙:
  - 법률 사실(형량·조문·개정일)은 legal_blocks.json에서 가져와 직접 삽입
  - LLM(Gemini)은 사규 교차분석·실무 권고만 생성
  - LLM이 법률 사실을 "생성"하는 경로가 존재하지 않음

기존 파이프라인과의 차이:

  [기존 - 9번 반복 실패]
  질문 → Gemini가 법률분석 "생성" → Gatekeeper 검증 시도 → 출력
         ^^^^^^^^^^^^^^^^^^^^^^^^
         여기서 형량 오염 발생 (20년이 7년을 덮어씀)

  [개선 - 이 모듈]
  질문 → 쟁점분류 → DB에서 블록 조회 → Gemini는 사규 연계만 생성 → 블록+사규 조합 → 출력
                     ^^^^^^^^^^^^^^^^    ^^^^^^^^^^^^^^^^^^^^^^^^
                     형량은 DB가 보장     LLM은 형량을 건드리지 않음
"""

import json
from pathlib import Path
from datetime import datetime


# ============================================================
# 1. 블록 DB 로드
# ============================================================

def load_legal_blocks(path: str = "legal_blocks.json") -> dict:
    """법률 분석 블록 DB를 로드한다."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# 2. 쟁점 분류 (Stage 0)
# ============================================================

def classify_issues(query: str, db: dict) -> list[str]:
    """
    질문에서 쟁점 키워드를 분류한다.

    현재는 규칙 기반으로 구현. 쟁점 유형이 늘어나면
    Gemini에게 분류만 시킬 수 있음 (형량 생성 없이 키 선택만).

    반환값: DB의 토픽 키 목록 (예: ["모조품"])
    """
    # 규칙 기반 매칭 (확실하고 빠름)
    keyword_map = {
        "모조품": ["모조품", "위조", "짝퉁", "가품", "위조상품", "모조", "fake", "counterfeit"],
        # 향후 확장 예시:
        # "병행수입": ["병행수입", "병행", "진정상품", "grey market"],
        # "표시광고": ["표시광고", "허위광고", "과대광고", "부당광고"],
    }

    matched_topics = []
    query_lower = query.lower()

    for topic_key, keywords in keyword_map.items():
        if any(kw in query_lower for kw in keywords):
            if topic_key in db:
                matched_topics.append(topic_key)

    return matched_topics


# ============================================================
# 3. 블록 조회 (Stage 1) — LLM 사용 안 함
# ============================================================

def fetch_legal_blocks(topic_key: str, db: dict) -> dict:
    """
    DB에서 해당 토픽의 법률 분석 블록을 조회한다.

    이 함수의 출력은 LLM을 거치지 않고
    최종 문서에 직접 삽입된다.
    """
    topic = db.get(topic_key)
    if not topic:
        raise ValueError(f"DB에 '{topic_key}' 토픽이 없습니다.")

    return {
        "label": topic["label"],
        "summary": topic["summary"],
        "risk_level_legend": topic.get("risk_level_legend", ""),
        "issues": topic["issues"],
    }


# ============================================================
# 4. 사규 연계 프롬프트 생성 (Stage 2 준비)
# ============================================================

def build_gemini_prompt(topic_key: str, blocks: dict, 사규_texts: list[str]) -> str:
    """
    Gemini에게 보낼 프롬프트를 생성한다.

    핵심: Gemini에게 법률 분석을 쓰라고 하지 않는다.
    각 쟁점의 ID와 제목만 알려주고,
    해당 쟁점에 맞는 사규 조항·사규 관점·실무 권고만 생성하게 한다.
    """

    issue_list = ""
    for i, issue in enumerate(blocks["issues"], 1):
        issue_list += f"""
쟁점 {i}: {issue['title']}
  - ID: {issue['id']}
  - 적용 법령: {issue['applicable_laws']}
"""

    prompt = f"""당신은 면세점 컴플라이언스 실무 어시스턴트입니다.

아래 사규 원문을 참고하여, 각 쟁점에 대한 [사규 연계 분석]과 [실무 권고]만 작성하십시오.

## 절대 금지 사항
- 법령의 형량, 조문 내용, 개정일을 절대 작성하지 마십시오.
- 법률 분석은 이미 별도로 준비되어 있으므로, 당신은 사규 연계와 실무 권고만 담당합니다.
- "~년 이하의 징역", "~원 이하의 벌금" 등 형량 관련 숫자를 포함하지 마십시오.

## 쟁점 목록
{issue_list}

## 사규 원문
{chr(10).join(사규_texts)}

## 출력 형식 (JSON)
각 쟁점 ID별로 아래 형식으로 출력하십시오:

```json
{{
  "{blocks['issues'][0]['id']}": {{
    "applicable_saryu": "적용 사규 조항명과 조문번호",
    "saryu_analysis": "사규 관점에서의 분석 (2~3문장)",
    "recommendation": "종합 실무 권고 (2~3문장)"
  }},
  ...
}}
```
"""
    return prompt


# ============================================================
# 5. Gemini 응답 파싱
# ============================================================

def parse_gemini_response(response_text: str) -> dict:
    """Gemini의 JSON 응답을 파싱한다."""
    import re
    # ```json ... ``` 블록 추출
    json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
    if json_match:
        return json.loads(json_match.group(1))

    # 순수 JSON인 경우
    cleaned = response_text.strip()
    if cleaned.startswith('{'):
        return json.loads(cleaned)

    raise ValueError("Gemini 응답에서 JSON을 파싱할 수 없습니다.")


# ============================================================
# 6. 최종 문서 조립 (핵심)
# ============================================================

def assemble_document(
    blocks: dict,
    gemini_results: dict,
    query: str = "",
) -> str:
    """
    법률 블록(DB)과 사규 연계(Gemini)를 조합하여 검토의견서를 생성한다.

    *** 이 함수가 핵심입니다 ***

    - blocks["issues"][i]["legal_analysis"]  → DB에서 온 텍스트, 그대로 삽입
    - gemini_results[issue_id]               → Gemini가 생성한 사규 분석
    - 두 텍스트는 독립적으로 문서에 배치되며, LLM이 법률 블록을 수정하지 않음
    """

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    doc = f"""**🤝 공정거래 실무 어시스턴트 v3.0 — 검토 의견서**

MD·협력사 실무 Q&A & 계약·법령 Self-Check AI

작성일: {now} | AI 검토 초안

**⚠️ 본 문서는 AI가 생성한 검토 초안입니다. 법적 효력이 없으며, 반드시 사내변호사의 최종 확인을 거쳐야 합니다.**

**🔴 중대 위험 발견 (진행 보류 권고)**

**📋 {blocks['summary']}**

> 위험도 등급 기준: {blocks['risk_level_legend']}

## 쟁점별 교차 분석

"""

    for i, issue in enumerate(blocks["issues"], 1):
        issue_id = issue["id"]
        gemini = gemini_results.get(issue_id, {})

        doc += f"""### {issue['risk_level']} 쟁점 {i}: {issue['title']} [{issue['risk_label']}]

**📌 검토 대상 원문:**

> {query or blocks['label']}

**⚖️ 적용 법령:** {issue['applicable_laws']}

**🔍 법령 분석:**
{issue['legal_analysis']}

**🏛️ 적용 사규:** {gemini.get('applicable_saryu', '(사규 분석 대기중)')}

**🔍 사규 관점:** {gemini.get('saryu_analysis', '(사규 분석 대기중)')}

**💡 종합 실무 권고:** {gemini.get('recommendation', '(실무 권고 대기중)')}

"""

    doc += """## MD Action Plan

즉시: 모든 의심 상품 판매중단 및 재고조사 → 24시간: 법무담당부서 긴급대응팀 구성, 관련 증거자료 보전 → 1주: 공급업체 전면 재심사, 정품 인증 시스템 강화방안 수립 → 1개월: 보세판매장고시 준수 체계 재구축, 직원 교육 프로그램 시행

본 검토의견서는 AI가 생성한 초안이며, 법적 효력이 없습니다.
반드시 사내변호사의 최종 검토를 거치기 바랍니다.
"""

    return doc


# ============================================================
# 7. 무결성 검증 (Stage 3 보조)
# ============================================================

def verify_block_integrity(final_text: str, blocks: dict) -> list[str]:
    """
    최종 문서에 DB 블록의 법률 분석이 변형 없이 포함되었는지 검증한다.

    이 검증은 LLM이 아니라 Python 문자열 대조로 수행하므로 100% 정확하다.
    """
    errors = []
    for issue in blocks["issues"]:
        # 핵심 형량 키워드가 최종 문서에 존재하는지 확인
        if issue["legal_analysis"] not in final_text:
            errors.append(
                f"[무결성 오류] {issue['title']}의 법률 분석 블록이 "
                f"최종 문서에서 변형되었거나 누락되었습니다."
            )
    return errors


# ============================================================
# 8. 전체 파이프라인 실행
# ============================================================

def run_pipeline(
    query: str,
    사규_texts: list[str],
    gemini_call_fn=None,  # Gemini API 호출 함수 (외부 주입)
    db_path: str = "legal_blocks.json",
) -> dict:
    """
    전체 파이프라인을 실행한다.

    Args:
        query: 사용자 질문 (예: "면세점에서 모조품 판매 행위에 대해 법률 검토해줘")
        사규_texts: 사규 원문 텍스트 목록
        gemini_call_fn: Gemini API 호출 함수 (prompt를 받아 response_text 반환)
        db_path: 법률 블록 DB 경로

    Returns:
        {
            "document": 완성된 검토의견서 텍스트,
            "integrity_errors": 무결성 오류 목록 (정상이면 빈 리스트),
            "topic_keys": 분류된 쟁점 키 목록,
            "token_usage": {
                "gemini_input": Stage2 프롬프트 토큰 수 (추정),
                "note": "법률 분석은 DB 삽입이므로 LLM 토큰 미사용"
            }
        }
    """

    # Stage 0: 블록 DB 로드 + 쟁점 분류
    db = load_legal_blocks(db_path)
    topic_keys = classify_issues(query, db)

    if not topic_keys:
        return {
            "document": "해당 질문에 대한 법률 분석 블록이 DB에 없습니다.",
            "integrity_errors": ["쟁점 분류 실패"],
            "topic_keys": [],
            "token_usage": {"gemini_input": 0, "note": ""},
        }

    # Stage 1: DB에서 블록 조회 (LLM 사용 안 함)
    all_blocks = fetch_legal_blocks(topic_keys[0], db)

    # Stage 2: Gemini에게 사규 연계만 요청
    prompt = build_gemini_prompt(topic_keys[0], all_blocks, 사규_texts)

    if gemini_call_fn:
        response_text = gemini_call_fn(prompt)
        gemini_results = parse_gemini_response(response_text)
    else:
        # gemini_call_fn이 없으면 빈 결과 (테스트용)
        gemini_results = {}

    # Stage 3: 문서 조립 (법률 블록은 DB 텍스트 그대로 삽입)
    document = assemble_document(all_blocks, gemini_results, query)

    # Stage 4: 무결성 검증 (Python 문자열 대조)
    integrity_errors = verify_block_integrity(document, all_blocks)

    return {
        "document": document,
        "integrity_errors": integrity_errors,
        "topic_keys": topic_keys,
        "token_usage": {
            "gemini_input": len(prompt) // 4,  # 대략적 토큰 추정
            "note": "법률 분석은 DB 직접 삽입이므로 LLM 토큰 미사용",
        },
    }


# ============================================================
# 테스트 실행
# ============================================================

if __name__ == "__main__":
    # Gemini 없이 DB 블록만으로 테스트
    result = run_pipeline(
        query="면세점에서 모조품 판매 행위에 대해 법률 검토해줘",
        사규_texts=["(사규 텍스트가 여기에 들어감)"],
        gemini_call_fn=None,  # Gemini 없이 테스트
    )

    print("=" * 60)
    print("쟁점 분류:", result["topic_keys"])
    print("무결성 오류:", result["integrity_errors"] or "없음")
    print("토큰 사용:", result["token_usage"])
    print("=" * 60)
    print(result["document"][:3000])
    print("... (이하 생략)")
