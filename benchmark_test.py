#!/usr/bin/env python3
"""
벤치마크 테스트 — 법률 검토 AI 앱 v2.1
========================================
테스트 영역:
  1. DLP 자동 마스킹 (개인정보, 기업정보, 협력사명)
  2. 사규 리트리버 (키워드 추출, 조항 분할, 스코어링, 관련 조항 검색)
  3. 블록 어셈블러 (쟁점분류, DB 블록 조회, 문서 조립, 무결성 검증)
  4. JSON 파싱 (검토 응답 파싱, 다양한 형식 대응)
  5. 할루시네이션 방지 필터 (사규명 검증, B2B/B2C 필터, 필수 쟁점 보충)
  6. HTML 새니타이징 (LLM 출력물 정화)
  7. 법제처 API 모듈 (약칭 해석, 요청 생성)

실행: python benchmark_test.py
"""

import sys
import os
import json
import time
import re
import traceback

# ── 컬러 출력 ───────────────────────────────────────────
class C:
    OK   = "\033[92m"
    WARN = "\033[93m"
    FAIL = "\033[91m"
    BOLD = "\033[1m"
    DIM  = "\033[2m"
    END  = "\033[0m"
    CYAN = "\033[96m"

passed = 0
failed = 0
warned = 0
errors = []

def ok(test_name, detail=""):
    global passed
    passed += 1
    d = f" — {detail}" if detail else ""
    print(f"  {C.OK}✅ PASS{C.END}  {test_name}{C.DIM}{d}{C.END}")

def fail(test_name, detail=""):
    global failed
    failed += 1
    d = f" — {detail}" if detail else ""
    print(f"  {C.FAIL}❌ FAIL{C.END}  {test_name}{d}")
    errors.append(f"{test_name}: {detail}")

def warn(test_name, detail=""):
    global warned
    warned += 1
    d = f" — {detail}" if detail else ""
    print(f"  {C.WARN}⚠️  WARN{C.END}  {test_name}{d}")

def header(msg):
    print(f"\n{C.BOLD}{C.CYAN}{'═'*60}")
    print(f"  {msg}")
    print(f"{'═'*60}{C.END}")

def assert_test(name, condition, detail_pass="", detail_fail=""):
    if condition:
        ok(name, detail_pass)
    else:
        fail(name, detail_fail)


# ============================================================
#  TEST 1: DLP 자동 마스킹
# ============================================================
header("1/7  DLP 자동 마스킹 (개인정보 차단)")

# legal_ai.py에서 apply_auto_masking만 추출하여 테스트
# (streamlit import 피하기 위해 직접 구현)
def apply_auto_masking(text, target_partner=""):
    if not text:
        return text
    text = re.sub(r'(?<!\d)\d{6}[-\s]*[1-4]\d{6}(?!\d)', '█주민/외국인번호█', text)
    text = re.sub(r'(?<!\d)\d{6}-\d{7}(?!\d)', '█법인번호█', text)
    text = re.sub(r'(?<!\d)\d{3}-\d{2}-\d{5}(?!\d)', '█사업자번호█', text)
    text = re.sub(r'\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b', '█휴대전화█', text)
    text = re.sub(r'\b0[2-9][0-9]?[-\s]?\d{3,4}[-\s]?\d{4}\b', '█전화번호█', text)
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '█이메일█', text)
    text = re.sub(
        r'(계좌[^\d]{0,30})(\d{2,6}[-]?\d{2,6}[-]?\d{2,6})',
        lambda m: m.group(1) + '█계좌번호█',
        text
    )
    company_keywords = ['신세계디에프', '신세계면세점', '신세계 DF', 'Shinsegae DF', '신세계']
    for kw in company_keywords:
        text = text.replace(kw, '█당사(내부정보)█')
    if target_partner:
        partners = [p.strip() for p in target_partner.split(',') if p.strip()]
        for p in partners:
            text = text.replace(p, '█협력사█')
    return text

# 1-1: 주민등록번호
dlp_cases = [
    ("주민등록번호 마스킹", "주민번호는 900101-1234567입니다", "█주민/외국인번호█", "900101-1234567"),
    ("사업자등록번호 마스킹", "사업자번호 123-45-67890", "█사업자번호█", "123-45-67890"),
    ("법인등록번호 마스킹", "법인번호 123456-5234567 확인", "█법인번호█", "123456-5234567"),
    ("휴대전화 마스킹", "연락처: 010-1234-5678", "█휴대전화█", "010-1234-5678"),
    ("이메일 마스킹", "이메일 test@example.com 입니다", "█이메일█", "test@example.com"),
    ("계좌번호 마스킹", "계좌번호: 110-123-456789", "█계좌번호█", "110-123-456789"),
    ("기업명 마스킹", "신세계면세점 사규에 따르면", "█당사(내부정보)█", "신세계면세점"),
]

for name, input_text, expected_mask, original in dlp_cases:
    result = apply_auto_masking(input_text)
    assert_test(
        f"DLP: {name}",
        expected_mask in result and original not in result,
        f"'{original}' → '{expected_mask}'",
        f"마스킹 실패: '{result}'"
    )

# 1-2: 협력사명 마스킹
result = apply_auto_masking("루이비통 계약서 검토", target_partner="루이비통")
assert_test("DLP: 협력사명 마스킹", "█협력사█" in result and "루이비통" not in result)

# 1-3: 빈 문자열
assert_test("DLP: 빈 문자열 처리", apply_auto_masking("") == "")
assert_test("DLP: None 처리", apply_auto_masking(None) is None)

# 1-4: 오탐 방지 — 일반 숫자가 마스킹되지 않아야 함
no_mask = apply_auto_masking("2025년 매출 1조 2천억원")
assert_test("DLP: 일반 숫자 오탐 방지", "█" not in no_mask, f"마스킹 없음", f"오탐 발생: '{no_mask}'")


# ============================================================
#  TEST 2: 사규 리트리버
# ============================================================
header("2/7  사규 리트리버 (saryu_retriever.py)")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from saryu_retriever import chunk_by_article, extract_keywords, score_chunk, retrieve_relevant_saryu

    # 2-1: 키워드 추출
    kw = extract_keywords("면세점에서 모조품 판매 행위에 대해 법률 검토해줘")
    assert_test("키워드 추출: 모조품 질의", "모조품" in kw, f"키워드={kw}", f"'모조품' 미추출: {kw}")

    kw2 = extract_keywords("직매입 반품 절차가 어떻게 되나요?")
    assert_test("키워드 추출: 반품 질의", "반품" in kw2, f"키워드={kw2}")

    kw3 = extract_keywords("병행수입 관련 규정 확인")
    assert_test("키워드 추출: 병행수입", "병행수입" in kw3, f"키워드={kw3}")

    # 2-2: 조항 분할
    test_text = """제5조(관계법령 준수) 공급자는 상표법 등을 준수하여야 한다.
제16조의2(손해배상) 가품 납품 시 손해배상 책임을 진다.
제22조(계약해지) 다음 각 호의 사유 발생 시 계약을 해지할 수 있다."""

    chunks = chunk_by_article(test_text, "직매입계약서")
    assert_test("조항 분할: 3개 조문 감지", len(chunks) == 3, f"{len(chunks)}개 조문", f"기대 3개, 실제 {len(chunks)}개")

    if chunks:
        assert_test("조항 분할: 첫 조문 번호", chunks[0]["article"] == "제5조", chunks[0]["article"])
        assert_test("조항 분할: 라벨 보존", chunks[0]["label"] == "직매입계약서")

    # 2-3: 스코어링
    test_chunk = {"label": "계약서", "article": "제16조의2", "title": "손해배상", "text": "모조품 가품 납품시 손해배상"}
    score = score_chunk(test_chunk, ["모조품", "손해배상"])
    assert_test("스코어링: 키워드 매칭", score > 0, f"점수={score}")

    # 제목 매칭 보너스 확인
    score_title = score_chunk(test_chunk, ["손해배상"])
    score_body = score_chunk({"label": "", "article": "", "title": "", "text": "손해배상 관련 조항"}, ["손해배상"])
    assert_test("스코어링: 제목 가중치 (3x)", score_title > score_body, f"제목={score_title} > 본문={score_body}")

    # 2-4: 관련 사규 검색 통합
    test_docs = [
        {"cat": "contract", "label": "직매입계약서", "text": test_text},
        {"cat": "saryu", "label": "사내규정집", "text": "입점절차 - 모조품 가품 등 거래대상 제외\n퇴점절차: 법규위반 시 퇴점"},
    ]
    result = retrieve_relevant_saryu("모조품 판매 법률 검토", test_docs)
    assert_test("사규 검색: 관련 조항 추출", len(result) > 0 and ("모조품" in result or "가품" in result),
                f"결과 {len(result)}자", "관련 조항 미검출")

    # 2-5: max_chars 제한
    result_short = retrieve_relevant_saryu("모조품", test_docs, max_chars=100)
    assert_test("사규 검색: 문자 제한", len(result_short) <= 200, f"{len(result_short)}자")

    ok("사규 리트리버 모듈 임포트 성공")
except ImportError as e:
    fail(f"사규 리트리버 임포트 실패: {e}")
except Exception as e:
    fail(f"사규 리트리버 테스트 오류: {e}")


# ============================================================
#  TEST 3: 블록 어셈블러
# ============================================================
header("3/7  블록 어셈블러 (block_assembler.py)")

try:
    from block_assembler import (
        load_legal_blocks, classify_issues, fetch_legal_blocks,
        build_gemini_prompt, parse_gemini_response, assemble_document,
        verify_block_integrity, run_pipeline,
    )

    # 3-1: DB 로드
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legal_blocks.json")
    if os.path.exists(db_path):
        db = load_legal_blocks(db_path)
        assert_test("블록 DB 로드", "모조품" in db, f"토픽: {list(db.keys())}")

        # 3-2: 쟁점 분류
        topics = classify_issues("면세점에서 모조품 판매 행위에 대해 법률 검토해줘", db)
        assert_test("쟁점 분류: 모조품", "모조품" in topics, f"분류={topics}")

        topics_none = classify_issues("오늘 날씨 어때?", db)
        assert_test("쟁점 분류: 무관 질문", len(topics_none) == 0, "빈 결과 반환")

        topics_alias = classify_issues("위조 상품이 발견되었습니다", db)
        assert_test("쟁점 분류: 동의어(위조)", "모조품" in topics_alias, f"분류={topics_alias}")

        # 3-3: 블록 조회
        blocks = fetch_legal_blocks("모조품", db)
        assert_test("블록 조회: 모조품", len(blocks["issues"]) == 7, f"{len(blocks['issues'])}개 쟁점")
        assert_test("블록 조회: summary 존재", len(blocks["summary"]) > 50)

        # 3-4: 문서 조립 (Gemini 없이)
        doc = assemble_document(blocks, {}, "모조품 판매 검토")
        assert_test("문서 조립: 헤더 포함", "검토 의견서" in doc)
        assert_test("문서 조립: 법률 분석 포함", "상표법" in doc and "형법" in doc)
        assert_test("문서 조립: 형량 보존", "7년 이하" in doc and "20년 이하" in doc,
                     detail_fail="형량이 문서에서 누락됨")

        # 3-5: 무결성 검증
        integrity = verify_block_integrity(doc, blocks)
        assert_test("무결성 검증: 정상", len(integrity) == 0, "오류 없음",
                     f"무결성 오류 {len(integrity)}건: {integrity[:2]}")

        # 변형 시 감지 테스트
        tampered = doc.replace("7년 이하의 징역", "5년 이하의 징역")
        tampered_errors = verify_block_integrity(tampered, blocks)
        assert_test("무결성 검증: 변형 감지", len(tampered_errors) > 0, f"{len(tampered_errors)}건 감지")

        # 3-6: Gemini 응답 파싱
        test_resp1 = '```json\n{"test": "ok"}\n```'
        assert_test("Gemini 파싱: 코드블록", parse_gemini_response(test_resp1) == {"test": "ok"})

        test_resp2 = '{"test": "ok"}'
        assert_test("Gemini 파싱: 순수 JSON", parse_gemini_response(test_resp2) == {"test": "ok"})

        # 3-7: 전체 파이프라인 (Gemini 없이)
        result = run_pipeline("면세점에서 모조품 판매 행위에 대해 법률 검토해줘", ["(사규 텍스트)"], None, db_path)
        assert_test("파이프라인: 문서 생성", len(result["document"]) > 500, f"{len(result['document'])}자")
        assert_test("파이프라인: 무결성 통과", len(result["integrity_errors"]) == 0)
        assert_test("파이프라인: 토큰 절감", "DB 직접 삽입" in result["token_usage"]["note"])

    else:
        warn("legal_blocks.json 없음 — 블록 어셈블러 테스트 스킵")

except ImportError as e:
    fail(f"블록 어셈블러 임포트 실패: {e}")
except Exception as e:
    fail(f"블록 어셈블러 테스트 오류: {e}\n{traceback.format_exc()}")


# ============================================================
#  TEST 4: JSON 파싱 (parse_review_response)
# ============================================================
header("4/7  검토 응답 JSON 파싱")

def parse_review_response(response_text):
    """legal_ai.py의 parse_review_response와 동일 로직"""
    json_data = None
    detail_text = response_text

    json_match = re.search(r'```json\s*\n(.*?)\n```', response_text, re.DOTALL)
    if json_match:
        try:
            json_data = json.loads(json_match.group(1))
            detail_text = response_text[json_match.end():].strip()
        except json.JSONDecodeError:
            pass

    if not json_data:
        json_match2 = re.search(r'(\{[^{]*?"summary".*?"verdict".*?\})\s*$', response_text, re.DOTALL)
        if not json_match2:
            json_match2 = re.search(r'(\{[^{]*?"summary".*?"issues".*?\})', response_text, re.DOTALL)
        if json_match2:
            try:
                json_data = json.loads(json_match2.group(1))
                detail_text = response_text[json_match2.end():].strip()
            except json.JSONDecodeError:
                pass

    if not json_data and response_text.strip().startswith("{"):
        try:
            brace_count = 0
            json_end = 0
            for i, c in enumerate(response_text):
                if c == '{': brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        json_end = i + 1
                        break
            if json_end > 0:
                json_data = json.loads(response_text[:json_end])
                detail_text = response_text[json_end:].strip()
        except json.JSONDecodeError:
            pass

    return json_data, detail_text

# 4-1: 정상 코드블록
normal_json = '''```json
{
  "summary": "모조품 판매 위험",
  "verdict": "rejected",
  "issues": [{"issue_no": 1, "title": "상표법 위반"}]
}
```
추가 설명입니다.'''
jd, dt = parse_review_response(normal_json)
assert_test("JSON 파싱: 코드블록", jd is not None and jd["verdict"] == "rejected")
assert_test("JSON 파싱: detail 분리", "추가 설명" in dt)

# 4-2: 순수 JSON
raw_json = '{"summary": "테스트", "verdict": "approved", "issues": []}'
jd2, _ = parse_review_response(raw_json)
assert_test("JSON 파싱: 순수 JSON", jd2 is not None and jd2["verdict"] == "approved")

# 4-3: 마크다운만 (JSON 없음)
md_only = "이 계약서는 문제가 없습니다. 검토 완료."
jd3, dt3 = parse_review_response(md_only)
assert_test("JSON 파싱: 마크다운만", jd3 is None and dt3 == md_only)

# 4-4: JSON 뒤에 마크다운
mixed = '{"summary": "테스트", "verdict": "conditional", "issues": []}\n\n## 추가 분석\n상세 내용...'
jd4, dt4 = parse_review_response(mixed)
assert_test("JSON 파싱: JSON+마크다운", jd4 is not None and jd4["verdict"] == "conditional")


# ============================================================
#  TEST 5: 할루시네이션 방지 필터
# ============================================================
header("5/7  할루시네이션 방지 필터")

# 5-1: B2B/B2C 필터
def _filter_b2b_consumer_issues(json_data, user_query=""):
    if not json_data or "issues" not in json_data:
        return json_data
    b2b_keywords = ["직매입", "납품", "수수료", "입점", "퇴점", "특정매입", "위탁매입", "반품"]
    b2c_keywords = ["소비자", "고객", "클레임", "소비자보호", "소비자피해", "소비자분쟁"]
    has_b2b = any(kw in user_query for kw in b2b_keywords)
    has_b2c = any(kw in user_query for kw in b2c_keywords)
    if not has_b2b or has_b2c:
        return json_data
    original_count = len(json_data["issues"])
    json_data["issues"] = [
        iss for iss in json_data["issues"]
        if "소비자기본법" not in iss.get("applicable_law", "")
        and "소비자기본법" not in iss.get("title", "")
    ]
    for i, iss in enumerate(json_data["issues"], 1):
        iss["issue_no"] = i
    return json_data

b2b_data = {
    "issues": [
        {"issue_no": 1, "title": "대규모유통업법 위반", "applicable_law": "대규모유통업법"},
        {"issue_no": 2, "title": "소비자기본법 위반", "applicable_law": "소비자기본법"},
    ]
}
filtered = _filter_b2b_consumer_issues(b2b_data, "직매입 반품 절차 검토")
assert_test("B2B 필터: 소비자법 제거", len(filtered["issues"]) == 1, "소비자기본법 1건 제거")

b2c_data = {
    "issues": [
        {"issue_no": 1, "title": "소비자기본법 위반", "applicable_law": "소비자기본법"},
    ]
}
not_filtered = _filter_b2b_consumer_issues(b2c_data, "소비자 클레임 처리 검토")
assert_test("B2C 필터: 소비자법 유지", len(not_filtered["issues"]) == 1)

# 5-2: 사규명 검증
KNOWN_SARYU_NAMES = [
    "MD 협력회사 입점 및 퇴점 지침",
    "직매입거래 기본계약서",
    "특정매입거래 기본계약서",
    "임대차계약서",
    "사내규정집",
]

def _validate_saryu_names(json_data):
    if not json_data or "issues" not in json_data:
        return json_data
    for iss in json_data["issues"]:
        rule_text = iss.get("applicable_rule", "")
        if not rule_text or rule_text in ("해당 없음", "없음", "해당 규정 없음"):
            continue
        found_names = re.findall(r"「([^」]+)」", rule_text)
        if not found_names:
            suspicious_patterns = re.findall(r"([\w\s]+(?:지침|규정|계약서|내규|규정집|매뉴얼))", rule_text)
            found_names = [p.strip() for p in suspicious_patterns]
        if not found_names:
            continue
        is_valid = False
        for name in found_names:
            for known in KNOWN_SARYU_NAMES:
                if known in name or name in known:
                    is_valid = True
                    break
            if is_valid:
                break
        if not is_valid:
            iss["applicable_rule"] = "해당 없음"
            iss["rule_analysis"] = "검증 데이터에 해당 사규 존재하지 않음"
    return json_data

# 실재 사규
valid_saryu = {"issues": [{"applicable_rule": "「직매입거래 기본계약서」 제22조", "rule_analysis": "분석 내용"}]}
v = _validate_saryu_names(valid_saryu)
assert_test("사규 검증: 실재 사규 유지", v["issues"][0]["applicable_rule"] != "해당 없음")

# 허구 사규 (할루시네이션)
fake_saryu = {"issues": [{"applicable_rule": "「보세물품 관리지침」 제3조", "rule_analysis": "가짜 분석"}]}
f_result = _validate_saryu_names(fake_saryu)
assert_test("사규 검증: 허구 사규 차단", f_result["issues"][0]["applicable_rule"] == "해당 없음",
            "할루시네이션 차단됨", f"차단 실패: {f_result['issues'][0]['applicable_rule']}")

# 5-3: 사규 꺾쇠 자동 보정
def _wrap_saryu_brackets(text):
    if not text or text in ("해당 없음", "없음"):
        return text
    if "「" in text:
        return text
    for name in KNOWN_SARYU_NAMES:
        if name in text:
            text = text.replace(name, f"「{name}」")
    return text

assert_test("꺾쇠 보정: 자동 추가", "「직매입거래 기본계약서」" in _wrap_saryu_brackets("직매입거래 기본계약서 제5조"))
assert_test("꺾쇠 보정: 이미 있으면 유지", "「직매입거래 기본계약서」" in _wrap_saryu_brackets("「직매입거래 기본계약서」"))
assert_test("꺾쇠 보정: 해당 없음 통과", _wrap_saryu_brackets("해당 없음") == "해당 없음")


# ============================================================
#  TEST 6: HTML 새니타이징
# ============================================================
header("6/7  HTML 새니타이징")

def sanitize_html(text):
    text = re.sub(r'<!DOCTYPE[^>]*>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<\?xml[^?]*\?>', '', text, flags=re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<svg[^>]*>.*?</svg>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(
        r'</?(?:html|head|body|div|span|table|tr|td|th|thead|tbody|tfoot|caption|colgroup|col'
        r'|p|br|hr|h[1-6]|ul|ol|li|dl|dt|dd|a|img|video|audio|source|canvas|iframe'
        r'|section|article|header|footer|nav|main|aside|figure|figcaption'
        r'|form|input|button|select|option|textarea|label|fieldset|legend'
        r'|details|summary|dialog|template|slot|meta|link|base|title'
        r'|strong|em|b|i|u|s|small|mark|sub|sup|abbr|cite|code|pre|blockquote'
        r'|ruby|rt|rp|bdi|bdo|wbr|data|time|progress|meter|output'
        r')[^>]*>',
        '', text, flags=re.IGNORECASE
    )
    text = re.sub(r'style="[^"]*"', '', text, flags=re.IGNORECASE)
    text = re.sub(r"style='[^']*'", '', text, flags=re.IGNORECASE)
    text = re.sub(r'class="[^"]*"', '', text, flags=re.IGNORECASE)
    text = re.sub(r'id="[^"]*"', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# 6-1: HTML 태그 제거
assert_test("HTML: div/span 제거", "<div>" not in sanitize_html("<div>내용</div>"))
assert_test("HTML: 내용 보존", "내용" in sanitize_html("<div class='test'>내용</div>"))

# 6-2: style 블록 제거
styled = "<style>body{color:red}</style>본문 내용"
assert_test("HTML: style 블록 제거", "color:red" not in sanitize_html(styled))

# 6-3: script 제거
scripted = "안전한 텍스트<script>alert('xss')</script>"
assert_test("HTML: script 제거 (XSS 방어)", "alert" not in sanitize_html(scripted))

# 6-4: 마크다운은 보존
md_text = "## 제목\n\n- 항목1\n- 항목2\n\n**굵게** _기울임_"
assert_test("HTML: 마크다운 보존", sanitize_html(md_text) == md_text)

# 6-5: 복합 케이스
complex_html = """<!DOCTYPE html>
<html><head><style>.a{}</style></head><body>
<div class="wrapper"><h2>분석 결과</h2>
<p>상표법 위반 — <strong>7년 이하의 징역</strong></p>
<script>console.log('test')</script>
</div></body></html>"""
cleaned = sanitize_html(complex_html)
assert_test("HTML: 복합 케이스 정화", "<" not in cleaned or cleaned.count("<") == 0,
            detail_fail=f"잔여 태그: {cleaned[:100]}")
assert_test("HTML: 내용 보존 (복합)", "분석 결과" in cleaned and "상표법" in cleaned)


# ============================================================
#  TEST 7: 법제처 API 모듈
# ============================================================
header("7/7  법제처 API 모듈 (law_api_module.py)")

try:
    from law_api_module import _resolve_keyword, ABBREVIATIONS

    # 7-1: 약칭 해석
    assert_test("법령 약칭: 표시광고법", _resolve_keyword("표시광고법") == "표시광고")
    assert_test("법령 약칭: 관세법", _resolve_keyword("관세법") == "관세법")
    assert_test("법령 약칭: 대규모유통업법", _resolve_keyword("대규모유통업법") == "대규모유통업")
    assert_test("법령 약칭: 미등록 키워드 원본 반환", _resolve_keyword("형법") == "형법")

    # 7-2: ABBREVIATIONS 완전성
    essential = ["표시광고법", "관세법", "공정거래법", "대규모유통업법"]
    for abbr in essential:
        assert_test(f"약칭 DB: {abbr} 등록됨", abbr in ABBREVIATIONS)

    ok("법제처 API 모듈 임포트 성공")
except ImportError as e:
    fail(f"법제처 API 모듈 임포트 실패: {e}")
except Exception as e:
    fail(f"법제처 API 모듈 테스트 오류: {e}")


# ============================================================
#  최종 결과
# ============================================================
header("📊 벤치마크 최종 결과")

total = passed + failed + warned
print(f"  {C.OK}✅ PASS: {passed}{C.END}")
print(f"  {C.WARN}⚠️  WARN: {warned}{C.END}")
print(f"  {C.FAIL}❌ FAIL: {failed}{C.END}")
print(f"  {'─'*40}")
print(f"  총 {total}건 | 합격률: {passed/total*100:.1f}%" if total > 0 else "")

if errors:
    print(f"\n  {C.FAIL}{C.BOLD}실패 항목:{C.END}")
    for e in errors:
        print(f"  {C.FAIL}  • {e}{C.END}")

if failed == 0:
    print(f"\n  {C.OK}{C.BOLD}🎉 모든 벤치마크 테스트 통과!{C.END}")
elif failed <= 2:
    print(f"\n  {C.WARN}{C.BOLD}⚠️ 일부 테스트 실패 — 확인 필요{C.END}")
else:
    print(f"\n  {C.FAIL}{C.BOLD}⛔ 다수 테스트 실패 — 즉시 수정 필요{C.END}")

print()
sys.exit(1 if failed > 0 else 0)
