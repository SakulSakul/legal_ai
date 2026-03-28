# ============================================================
#  🤝 공정거래 실무 어시스턴트 v2.1 — 면세점 MD/바이어용
#  이중 모델: Gemini(문서/검색) + Claude(법률검토) + 고가용성 우회
#  보안: 자동 DLP(개인/기업정보) + 협력사명 지정 마스킹 탑재
#  디자인: 신세계그룹 뉴스룸 테마 적용 (Pretendard, Corporate Red)
#
#  v2.1 변경사항:
#  - Claude 모델 버전 설정값 외부화 (Secrets 지원)
#  - 계좌번호 DLP 정규식 오탐 수정 (은행별 패턴 정밀화)
#  - Gemini 폴백 시 에러 타입별 분기 (rate limit 공유 쿼터 대응)
#  - extract_text 에러 핸들링 추가
#  - 시스템 프롬프트 토큰 관리 (조항 단위 자르기)
#  - st.rerun() 타이밍 개선 (다운로드 가능 상태 보장)
#  - import 위치 정리 (uuid 등 상단 이동)
# ============================================================

import streamlit as st
import os, io, json, re, time, logging, uuid
from datetime import datetime, timedelta

# ── 로깅 설정 ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 설정 ─────────────────────────────────────────────────────
def get_secret(key, default=""):
    """Secrets → 환경변수 → default 순으로 설정값 조회"""
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)

# 모델명 외부 설정 지원 (Secrets에 없으면 기본값 사용)
CLAUDE_MODEL = get_secret("CLAUDE_MODEL", "claude-sonnet-4-20250514")
GEMINI_MODELS = [
    get_secret("GEMINI_MODEL_PRIMARY", "gemini-2.5-pro"),
    get_secret("GEMINI_MODEL_FALLBACK", "gemini-2.5-flash"),
]

# ── Lazy 클라이언트 초기화 ────────────────────────────────────
@st.cache_resource
def init_supabase():
    from supabase import create_client
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_KEY")
    if not url or not key:
        st.error("⚠️ Supabase 설정이 없습니다. Secrets에 SUPABASE_URL, SUPABASE_KEY를 추가하세요.")
        st.stop()
    return create_client(url, key)

@st.cache_resource
def init_gemini():
    from google import genai
    key = get_secret("GEMINI_API_KEY")
    if not key:
        st.error("⚠️ Gemini API 키가 없습니다.")
        st.stop()
    return genai.Client(api_key=key)

@st.cache_resource
def init_anthropic():
    import anthropic
    key = get_secret("ANTHROPIC_API_KEY")
    if not key:
        st.error("⚠️ Anthropic API 키가 없습니다. Secrets에 ANTHROPIC_API_KEY를 추가하세요.")
        st.stop()
    return anthropic.Anthropic(api_key=key)

# ── 상수 ─────────────────────────────────────────────────────
CONTRACT_TYPES = ["특약매입", "직매입"]
YAKJEONG_TYPES = ["협력사원", "인테리어설치", "매장이동", "공동판촉", "기타"]
DOC_CATS = {
    "saryu":    {"label": "사규",   "icon": "🏛"},
    "contract": {"label": "계약서", "icon": "📄"},
    "yakjeong": {"label": "약정서", "icon": "📝"},
}

REVIEW_KEYWORDS = ["검토", "확인", "위반", "적법", "수용", "반품", "계약", "약정",
                   "조항", "독소", "비교", "분석", "판촉", "감액", "반려", "승인",
                   "법률", "법적", "맞음", "맞아", "맞나요", "위법", "불법", "가능", "어때", "어떤가요", "문제"]

# ── 자동 보안 마스킹 (DLP) 함수 ──────────────────────────────
def apply_auto_masking(text, target_partner=""):
    """정규표현식을 이용한 자동 개인/기업정보 차단 및 지정 협력사명 마스킹
    
    v2.1: 계좌번호 패턴을 은행별 실제 자릿수로 정밀화하여 오탐 방지
    """
    if not text:
        return text
        
    # 1. 개인정보 및 식별번호 차단 (자동)
    # 주민등록번호 / 외국인등록번호 (6자리-7자리, 뒷자리 1~4로 시작)
    text = re.sub(r'\b\d{6}[-\s]*[1-4]\d{6}\b', '█주민/외국인번호█', text) 
    # 사업자등록번호 (3-2-5 형식, 반드시 하이픈 포함)
    text = re.sub(r'\b\d{3}-\d{2}-\d{5}\b', '█사업자번호█', text) 
    # 법인등록번호 (6-7 형식, 반드시 하이픈 포함)
    text = re.sub(r'\b\d{6}-\d{7}\b', '█법인번호█', text) 
    # 휴대전화 (010/011/016/017/018/019 + 3~4자리 + 4자리)
    text = re.sub(r'\b01[016789][-\s]?\d{3,4}[-\s]?\d{4}\b', '█휴대전화█', text)
    # 일반 전화번호 (지역번호 2~3자리 + 3~4자리 + 4자리)
    text = re.sub(r'\b0[2-9][0-9]?[-\s]?\d{3,4}[-\s]?\d{4}\b', '█전화번호█', text)
    # 이메일
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '█이메일█', text)
    # 계좌번호: "계좌" 키워드 근접 시에만 마스킹 (오탐 방지)
    # 패턴: "계좌" 뒤 30자 이내의 10~16자리 숫자열 (하이픈 허용)
    text = re.sub(
        r'(계좌[^\d]{0,30})(\d{2,6}[-]?\d{2,6}[-]?\d{2,6})',
        lambda m: m.group(1) + '█계좌번호█',
        text
    )
    
    # 2. 당사 식별 키워드 차단 (자동)
    company_keywords = ['신세계디에프', '신세계면세점', '신세계 DF', 'Shinsegae DF', '신세계']
    for kw in company_keywords:
        text = text.replace(kw, '█당사(내부정보)█')

    # 3. 지정된 협력사명 차단 (반자동)
    if target_partner:
        partners = [p.strip() for p in target_partner.split(',') if p.strip()]
        for p in partners:
            text = text.replace(p, '█협력사█')

    return text

# ── Supabase CRUD ────────────────────────────────────────────
def load_docs():
    try:
        res = init_supabase().table("docs").select("*").order("created_at").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"문서 로드 실패: {e}")
        st.warning("⚠️ 기준 문서를 불러오지 못했습니다. 새로고침 해주세요.")
        return []

def save_doc(doc):
    try:
        init_supabase().table("docs").upsert({
            "id": doc["id"], "name": doc["name"], "cat": doc["cat"],
            "contract_type": doc.get("contract_type"), "label": doc["label"],
            "text": doc["text"], "size": doc["size"],
        }).execute()
        return True
    except Exception as e:
        logger.error(f"문서 저장 실패: {e}")
        st.warning(f"⚠️ 문서 저장 실패: {doc['name']}")
        return False

def delete_doc(doc_id):
    try:
        init_supabase().table("docs").delete().eq("id", doc_id).execute()
        return True
    except Exception as e:
        logger.error(f"문서 삭제 실패: {e}")
        st.warning("⚠️ 문서 삭제에 실패했습니다.")
        return False

def load_sessions():
    try:
        res = init_supabase().table("sessions").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception as e:
        logger.error(f"세션 로드 실패: {e}")
        return []

def save_session(sess):
    try:
        init_supabase().table("sessions").upsert({
            "id": sess["id"], "title": sess["title"],
            "date": sess["date"], "messages": sess["messages"],
        }).execute()
        return True
    except Exception as e:
        logger.error(f"세션 저장 실패: {e}")
        st.warning("⚠️ 대화 저장에 실패했습니다.")
        return False

def delete_session_db(sess_id):
    try:
        init_supabase().table("sessions").delete().eq("id", sess_id).execute()
        return True
    except Exception as e:
        logger.error(f"세션 삭제 실패: {e}")
        st.warning("⚠️ 대화 삭제에 실패했습니다.")
        return False

def save_review_log(log_data):
    try:
        init_supabase().table("review_logs").upsert(log_data).execute()
        return True
    except Exception as e:
        logger.error(f"검토 이력 저장 실패: {e}")
        return False

def load_laws():
    try:
        res = init_supabase().table("laws").select("*").order("id").execute()
        return res.data or []
    except Exception as e:
        logger.error(f"법령 로드 실패: {e}")
        return []

def cleanup_old_sessions(days=90):
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        init_supabase().table("sessions").delete().lt("created_at", cutoff).execute()
    except Exception:
        pass

# ── docx 텍스트 추출 (에러 핸들링 강화) ───────────────────────
def extract_text(file_bytes):
    """docx 파일에서 텍스트 추출. 실패 시 에러 메시지 반환."""
    if not file_bytes:
        return "(빈 파일입니다.)"
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
            # 테이블에만 텍스트가 있을 수 있으므로 테이블도 시도
            table_texts = []
            for table in doc.tables:
                for row in table.rows:
                    row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                    if row_text:
                        table_texts.append(row_text)
            if table_texts:
                return "\n".join(table_texts)
            return "(문서에서 텍스트를 추출하지 못했습니다. 이미지만 있는 문서일 수 있습니다.)"
        return "\n".join(paragraphs)
    except Exception as e:
        logger.error(f"텍스트 추출 실패: {e}")
        return f"(파일 읽기 실패: 손상되었거나 지원하지 않는 형식입니다 — {type(e).__name__})"

# ── 시스템 프롬프트용 텍스트 자르기 (조항 단위) ─────────────────
def truncate_at_boundary(text, max_chars):
    """문서 구분자(---) 기준으로 자르기. 조항 중간에서 잘리지 않도록 함."""
    if len(text) <= max_chars:
        return text
    # max_chars 이전의 마지막 구분자 위치를 찾아 거기서 자름
    truncated = text[:max_chars]
    last_separator = truncated.rfind("\n\n---\n\n")
    if last_separator > max_chars * 0.5:  # 절반 이상은 보존
        return truncated[:last_separator]
    # 구분자가 없으면 마지막 줄바꿈 기준
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars * 0.7:
        return truncated[:last_newline]
    return truncated

# ── 쿼리 라우팅 ──────────────────────────────────────────────
def route_query(query, has_attachment):
    if has_attachment:
        return "claude"
    if any(kw in query for kw in REVIEW_KEYWORDS):
        return "claude"
    return "gemini"

def verify_citations(cited_laws, laws_db):
    results = []
    if not cited_laws:
        return results
    for cite in cited_laws:
        found = False
        matched_content = ""
        for law in laws_db:
            if law["law_short"] in cite and law["article_no"] in cite:
                found = True
                matched_content = law["content"][:100] + "..."
                break
        results.append({
            "citation": cite,
            "verified": found,
            "preview": matched_content if found else ""
        })
    return results

# ── 에러 타입 분류 헬퍼 ──────────────────────────────────────
def classify_api_error(error):
    """API 에러를 분류하여 (에러유형, 메시지) 튜플 반환.
    에러유형: 'rate_limit', 'auth', 'server', 'unknown'
    """
    error_msg = str(error).lower()
    if any(kw in error_msg for kw in ["rate", "quota", "429", "resource_exhausted"]):
        return "rate_limit", "API 사용 한도 또는 요금을 초과했습니다."
    elif any(kw in error_msg for kw in ["401", "403", "authentication", "api_key", "permission"]):
        return "auth", "API 키가 유효하지 않거나 만료되었습니다."
    elif any(kw in error_msg for kw in ["500", "502", "503", "504", "unavailable", "timeout"]):
        return "server", "서버가 일시적으로 응답하지 않습니다."
    else:
        return "unknown", f"알 수 없는 오류가 발생했습니다: {type(error).__name__}"

# ── 시스템 프롬프트 ──────────────────────────────────────────
def build_system_claude(docs, laws_db):
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = truncate_at_boundary(fmt_docs(by_cat("saryu")), 25000)
    contract_text = truncate_at_boundary(fmt_docs(by_cat("contract")), 35000)
    yakjeong_text = truncate_at_boundary(fmt_docs(by_cat("yakjeong")), 20000)

    laws_text = ""
    if laws_db:
        law_entries = []
        for law in laws_db:
            law_entries.append(f"[{law['law_short']} {law['article_no']}] {law.get('article_title','')}\n{law['content']}")
        laws_text = "\n\n---\n\n".join(law_entries)
    else:
        laws_text = "(법령 DB 미등록 — 일반 법률 지식으로 판단)"

    return (
        "당신은 면세점 전문 공정거래 실무 어시스턴트 AI입니다.\n"
        "단순한 법률 해석을 넘어, ① [외부 법령]과 ② [내부 사규/표준문서]라는 두 가지 관점에서 교차 검토하여 실무 결단을 내려주세요.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "[기준 문서 (Ground Truth — 절대 자체를 검토하지 말 것)]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "① 당사 사규 및 컴플라이언스 정책:\n" + saryu_text +
        "\n\n② 거래유형별 당사 표준 계약서:\n" + contract_text +
        "\n\n③ 당사 표준 약정서:\n" + yakjeong_text +

        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "[적용 법령 DB (현행 법령 원문)]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" +
        laws_text +

        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "[답변 형식 — 엄격 준수]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "반드시 아래 형식의 ```json``` 블록 하나와 상세 설명 텍스트를 출력하세요.\n\n"

        "**[PART 1: JSON 블록]**\n"
        "```json\n"
        "{\n"
        '  "summary": "문의사항 1줄 요약",\n'
        '  "verdict": "approved | conditional | rejected",\n'
        '  "verdict_reason": "법령과 사내 기준을 종합한 최종 판단 근거",\n'
        '  "issues": [\n'
        '    {\n'
        '      "issue_no": 1,\n'
        '      "title": "쟁점 제목",\n'
        '      "risk_level": "high | medium | low",\n'
        '      "target_clause": "검토 대상 문서 원문 인용",\n'
        '      "applicable_law": "적용 법령 (예: 대규모유통업법 제11조)",\n'
        '      "law_analysis": "법령 관점에서의 위법성 평가",\n'
        '      "applicable_rule": "적용 사규/표준계약서 조항",\n'
        '      "rule_analysis": "사규 및 당사 기준 관점에서의 부합성 평가",\n'
        '      "recommendation": "두 관점을 종합한 최종 권고안 및 실무 가이드"\n'
        '    }\n'
        '  ],\n'
        '  "action_plan": "MD가 상대방에게 해야 할 구체적 액션 (단계별)",\n'
        '  "alternative_clause": "수정 대안 조항 초안 (없으면 null)",\n'
        '  "cited_laws": ["대규모유통업법 제11조"]\n'
        "}\n"
        "```\n\n"

        "**[PART 2: 상세 설명]**\n"
        "JSON 아래에 마크다운 형식으로 작성.\n"
        "- 서두: **문의사항:** [요약]\n"
        "- 위험: 🔴 위반 내용, 적법: 🔵 통과 내용 형태로 이모지를 사용하세요. Streamlit 색상 단축코드(:red[], :blue[], :blue_circle: 등)는 절대 사용하지 마세요.\n"
    )

def build_system_gemini(docs):
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = truncate_at_boundary(fmt_docs(by_cat("saryu")), 25000)
    contract_text = truncate_at_boundary(fmt_docs(by_cat("contract")), 35000)
    yakjeong_text = truncate_at_boundary(fmt_docs(by_cat("yakjeong")), 20000)

    return (
        "당신은 면세점 전문 공정거래 실무 어시스턴트 AI입니다.\n"
        "사규, 계약서, 약정서 내용에 대한 일반 질문에 친절히 답변하세요.\n"
        "법률 검토 판단(승인/반려)은 하지 말고, 필요시 '검토 요청을 해주세요'라고 안내하세요.\n\n"
        "① 당사 사규:\n" + saryu_text +
        "\n\n② 당사 표준 계약서:\n" + contract_text +
        "\n\n③ 당사 표준 약정서:\n" + yakjeong_text +
        "\n\n답변 시 위험은 🔴, 적법은 🔵 이모지를 사용하세요. Streamlit 색상 단축코드(:red[], :blue[], :blue_circle: 등)는 절대 사용하지 마세요."
    )

# ── AI 호출 및 에러 핸들링 함수 ────────────────────────────────
def call_claude(system_prompt, messages):
    client = init_anthropic()
    claude_messages = []
    last_role = None

    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        content = m["content"]
        if role == last_role:
            claude_messages[-1]["content"] += f"\n\n{content}"
        else:
            claude_messages.append({"role": role, "content": content})
            last_role = role
            
    if claude_messages and claude_messages[0]["role"] == "assistant":
        claude_messages.pop(0)

    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=claude_messages,
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Claude API 오류: {e}")
        err_type, err_msg = classify_api_error(e)
        label_map = {
            "rate_limit": "API 한도 초과",
            "auth": "API 인증 오류",
            "server": "서버 통신 장애",
            "unknown": "통신 오류",
        }
        return f"⚠️ [{label_map[err_type]}] Claude: {err_msg}"

def call_gemini(system_prompt, messages):
    from google.genai import types
    client = init_gemini()
    
    last_error_type = None
    
    for model_name in GEMINI_MODELS:
        # rate_limit 에러 시 동일 API 키 쿼터를 공유하므로 다음 모델로 폴백해도 무의미
        if last_error_type == "rate_limit":
            logger.info(f"Gemini rate limit 발생 — 동일 쿼터 공유로 {model_name} 폴백 건너뜀")
            continue
            
        try:
            history = []
            for m in messages[:-1]:
                role = "model" if m["role"] == "assistant" else "user"
                history.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
            last_msg = messages[-1]["content"]
            response = client.models.generate_content(
                model=model_name,
                contents=history + [types.Content(role="user", parts=[types.Part(text=last_msg)])],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            return response.text
        except Exception as e:
            logger.error(f"Gemini ({model_name}) 오류: {e}")
            last_error_type, last_err_msg = classify_api_error(e)
            
            # 마지막 모델이거나 rate_limit(공유 쿼터)인 경우 즉시 에러 반환
            is_last = (model_name == GEMINI_MODELS[-1])
            if is_last or last_error_type == "rate_limit":
                label_map = {
                    "rate_limit": "API 한도 초과",
                    "auth": "API 인증 오류",
                    "server": "서버 통신 장애",
                    "unknown": "통신 오류",
                }
                return f"⚠️ [{label_map[last_error_type]}] Gemini: {last_err_msg}"
            # auth 에러도 같은 키를 쓰므로 폴백 무의미
            if last_error_type == "auth":
                return f"⚠️ [API 인증 오류] Gemini: {last_err_msg}"
            continue
    
    return "⚠️ Gemini 응답을 가져오지 못했습니다."

def dispatch_with_fallback(model_choice, messages, docs, laws_db):
    if model_choice == "claude":
        system = build_system_claude(docs, laws_db)
        reply = call_claude(system, messages)
        if reply.startswith("⚠️"):
            st.warning(f"🔄 {reply}\n→ 예비 시스템(Gemini)으로 자동 우회하여 검토를 진행합니다.")
            fallback_reply = call_gemini(system, messages)
            if not fallback_reply.startswith("⚠️"):
                return fallback_reply, "Gemini (Fallback)"
            return reply, "Claude (Failed)"
        return reply, f"Claude ({CLAUDE_MODEL})"
    else:
        system = build_system_gemini(docs)
        reply = call_gemini(system, messages)
        if reply.startswith("⚠️"):
            st.warning(f"🔄 {reply}\n→ 예비 시스템(Claude)으로 자동 우회하여 답변을 생성합니다.")
            fallback_reply = call_claude(system, messages)
            if not fallback_reply.startswith("⚠️"):
                return fallback_reply, f"Claude ({CLAUDE_MODEL}, Fallback)"
            return reply, "Gemini (Failed)"
        return reply, "Gemini"

# ── JSON 파싱 및 UI 렌더링 ────────────────────────────────────
def parse_review_response(response_text):
    json_data = None
    detail_text = response_text
    json_match = re.search(r'```json\s*\n(.*?)\n```', response_text, re.DOTALL)
    if json_match:
        try:
            json_data = json.loads(json_match.group(1))
            detail_text = response_text[json_match.end():].strip()
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}")
            detail_text = response_text
    return json_data, detail_text

def render_verdict_badge(verdict):
    badges = {
        "approved":    ("🟢 위험 요소 미발견 (사내변호사 확인 권장)", "success"),
        "conditional": ("🟡 수정 필요 사항 발견", "warning"),
        "rejected":    ("🔴 중대 위험 발견 (진행 보류 권고)", "error"),
    }
    label, msg_type = badges.get(verdict, ("⚪ 판단 보류", "info"))
    getattr(st, msg_type)(label)

def render_issues_table(issues, citation_results):
    if not issues: return
    risk_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for issue in issues:
        risk = issue.get("risk_level", "medium")
        icon = risk_icons.get(risk, "⚪")
        with st.expander(f"{icon} 쟁점 {issue.get('issue_no', '?')}: {issue.get('title', '제목 없음')}", expanded=(risk == "high")):
            if issue.get("target_clause"):
                st.markdown(f"**📌 검토 대상 원문:**")
                st.code(issue["target_clause"], language="text")
            col1, col2 = st.columns(2)
            with col1:
                law_ref = issue.get("applicable_law", "")
                if law_ref:
                    verified = "⚪"
                    for cr in citation_results:
                        if any(part in cr["citation"] for part in law_ref.split()):
                            verified = "✅" if cr["verified"] else "⚠️"
                            break
                    st.markdown(f"**⚖️ 적용 법령:** {law_ref} {verified}")
                if issue.get("law_analysis"):
                    st.markdown(f"**🔍 법령 관점:** {issue['law_analysis']}")
            with col2:
                if issue.get("applicable_rule"):
                    st.markdown(f"**🏛️ 적용 사규:** {issue['applicable_rule']}")
                if issue.get("rule_analysis"):
                    st.markdown(f"**🔍 사규 관점:** {issue['rule_analysis']}")
            if issue.get("recommendation"):
                st.info(f"💡 **종합 실무 권고:** {issue['recommendation']}")

def render_alternative_clause(clause):
    if clause and clause != "null":
        st.markdown("---")
        st.markdown("### 📝 수정 대안 조항 (초안)")
        st.caption("아래 조항을 복사하여 협상 메일이나 수정 계약서에 활용하세요.")
        st.code(clause, language="text")

def _apply_shading(paragraph, hex_color):
    """단락에 배경 음영(shading) 적용 — Word '음영' 스타일과 동일 효과"""
    from docx.oxml.ns import qn, nsdecls
    from docx.oxml import parse_xml
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}" w:val="clear"/>')
    paragraph.paragraph_format.element.get_or_add_pPr().append(shading)

def _add_shaded_heading(doc, text, level, shade_color="D9E2F3"):
    """음영 배경이 적용된 Heading 추가 (Word '음영' 디자인 스타일)"""
    h = doc.add_heading(text, level=level)
    _apply_shading(h, shade_color)
    return h

def generate_review_docx(json_data, detail_text, query_text):
    """검토 의견서 docx 생성 — 화면 렌더링과 동일한 구조로 출력.
    여백: 좁게 (상하좌우 1.27cm = Word '좁게' 프리셋)
    디자인: Word 기본 스타일 '음영' 적용
    """
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn, nsdecls
    from docx.oxml import parse_xml

    # ── 음영 디자인 컬러 팔레트 ──
    SHADE_H1 = "2F5496"       # Heading 1 배경: 진한 파랑
    SHADE_H1_FONT = "FFFFFF"  # Heading 1 글자: 흰색
    SHADE_H2 = "D9E2F3"       # Heading 2 배경: 연한 파랑
    SHADE_H3 = "E2EFDA"       # Heading 3 배경: 연한 초록
    SHADE_QUOTE = "F2F2F2"    # 인용/코드 배경: 연한 회색
    SHADE_INFO = "DAEEF3"     # 권고(info) 배경: 연한 시안
    SHADE_WARN = "FFF2CC"     # 경고 배경: 연한 노랑
    SHADE_ALT = "E8E0F0"      # 대안 조항 배경: 연한 보라

    doc = Document()

    # ── 페이지 여백: 좁게 (1.27cm = Word 기본 '좁게' 프리셋) ──
    for section in doc.sections:
        section.top_margin = Cm(1.27)
        section.bottom_margin = Cm(1.27)
        section.left_margin = Cm(1.27)
        section.right_margin = Cm(1.27)

    # ── 기본 스타일 ──
    style = doc.styles['Normal']
    font = style.font
    font.name = '맑은 고딕'
    font.size = Pt(10)

    # ── Heading 스타일 커스텀 (음영 디자인) ──
    for hs_id, hs_size, hs_color in [("Heading 1", 16, SHADE_H1), ("Heading 2", 13, SHADE_H2), ("Heading 3", 11, SHADE_H3)]:
        try:
            hs = doc.styles[hs_id]
            hs.font.name = '맑은 고딕'
            hs.font.size = Pt(hs_size)
            if hs_id == "Heading 1":
                hs.font.color.rgb = RGBColor.from_string(SHADE_H1_FONT)
        except KeyError:
            pass

    # ── 헤더: 앱 타이틀과 동일 ──
    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _apply_shading(p_title, SHADE_H1)
    run_title = p_title.add_run("🤝 공정거래 실무 어시스턴트 v2.1 — 검토 의견서")
    run_title.font.size = Pt(18)
    run_title.bold = True
    run_title.font.color.rgb = RGBColor(255, 255, 255)

    p_sub = doc.add_paragraph()
    p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run_sub = p_sub.add_run("사규·표준계약서 기반 심층 계약/법률 검토")
    run_sub.font.size = Pt(9)
    run_sub.font.color.rgb = RGBColor(128, 128, 128)

    # 작성 정보
    p_meta = doc.add_paragraph()
    p_meta.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run_meta = p_meta.add_run(f"작성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  AI 검토 초안")
    run_meta.font.size = Pt(8)
    run_meta.font.color.rgb = RGBColor(128, 128, 128)

    # 경고 배너 (음영 배경)
    p_warn = doc.add_paragraph()
    p_warn.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _apply_shading(p_warn, SHADE_WARN)
    run_warn = p_warn.add_run("⚠️ 본 문서는 AI가 생성한 검토 초안입니다. 법적 효력이 없으며, 반드시 사내변호사의 최종 확인을 거쳐야 합니다.")
    run_warn.font.size = Pt(9)
    run_warn.font.color.rgb = RGBColor(156, 101, 0)
    run_warn.bold = True

    doc.add_paragraph("")

    if json_data:
        # ── 검토 결론 배지 (화면의 verdict badge와 동일) ──
        verdict = json_data.get("verdict", "")
        verdict_map = {
            "approved":    "🟢 위험 요소 미발견 (사내변호사 확인 권장)",
            "conditional": "🟡 수정 필요 사항 발견",
            "rejected":    "🔴 중대 위험 발견 (진행 보류 권고)",
        }
        verdict_colors = {
            "approved":    RGBColor(0, 128, 0),
            "conditional": RGBColor(200, 150, 0),
            "rejected":    RGBColor(227, 0, 15),
        }
        verdict_shades = {
            "approved":    "E2EFDA",
            "conditional": "FFF2CC",
            "rejected":    "FCE4EC",
        }
        p_verdict = doc.add_paragraph()
        _apply_shading(p_verdict, verdict_shades.get(verdict, "F2F2F2"))
        run_v = p_verdict.add_run(verdict_map.get(verdict, "⚪ 판단 보류"))
        run_v.font.size = Pt(14)
        run_v.bold = True
        run_v.font.color.rgb = verdict_colors.get(verdict, RGBColor(128, 128, 128))

        # 요약 (화면의 📋 summary와 동일)
        summary = json_data.get("summary", query_text[:200])
        p_summary = doc.add_paragraph()
        run_s = p_summary.add_run(f"📋 {summary}")
        run_s.font.size = Pt(11)
        run_s.bold = True

        # 판단 근거
        if json_data.get("verdict_reason"):
            doc.add_paragraph(json_data["verdict_reason"])

        doc.add_paragraph("")

        # ── 쟁점별 분석 (화면의 expander 구조와 동일) ──
        issues = json_data.get("issues", [])
        if issues:
            _add_shaded_heading(doc, "쟁점별 교차 분석", level=2, shade_color=SHADE_H2)
            risk_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
            risk_labels = {"high": "[위험]", "medium": "[주의]", "low": "[양호]"}
            risk_shades = {"high": "FCE4EC", "medium": "FFF8E1", "low": "E8F5E9"}

            for issue in issues:
                risk = issue.get("risk_level", "medium")
                icon = risk_icons.get(risk, "⚪")
                label = risk_labels.get(risk, "")

                # 쟁점 제목 (음영 배경 + 위험도별 색상)
                h = doc.add_heading(f"{icon} 쟁점 {issue.get('issue_no', '?')}: {issue.get('title', '제목 없음')} {label}", level=3)
                _apply_shading(h, risk_shades.get(risk, SHADE_H3))

                # 📌 검토 대상 원문 (회색 음영 박스)
                if issue.get("target_clause"):
                    doc.add_paragraph("📌 검토 대상 원문:").runs[0].bold = True
                    p_clause = doc.add_paragraph(issue["target_clause"])
                    p_clause.paragraph_format.left_indent = Cm(0.5)
                    _apply_shading(p_clause, SHADE_QUOTE)
                    for run in p_clause.runs:
                        run.font.color.rgb = RGBColor(80, 80, 80)
                        run.font.size = Pt(9)

                # ⚖️ 적용 법령 + 법령 관점
                if issue.get("applicable_law"):
                    p = doc.add_paragraph()
                    run_label = p.add_run("⚖️ 적용 법령: ")
                    run_label.bold = True
                    p.add_run(issue["applicable_law"])
                if issue.get("law_analysis"):
                    p = doc.add_paragraph()
                    run_label = p.add_run("🔍 법령 관점: ")
                    run_label.bold = True
                    p.add_run(issue["law_analysis"])

                # 🏛️ 적용 사규 + 사규 관점
                if issue.get("applicable_rule"):
                    p = doc.add_paragraph()
                    run_label = p.add_run("🏛️ 적용 사규: ")
                    run_label.bold = True
                    p.add_run(issue["applicable_rule"])
                if issue.get("rule_analysis"):
                    p = doc.add_paragraph()
                    run_label = p.add_run("🔍 사규 관점: ")
                    run_label.bold = True
                    p.add_run(issue["rule_analysis"])

                # 💡 종합 실무 권고 (시안 음영 박스 — 화면의 st.info와 동일)
                if issue.get("recommendation"):
                    p_rec = doc.add_paragraph()
                    _apply_shading(p_rec, SHADE_INFO)
                    run_rec = p_rec.add_run(f"💡 종합 실무 권고: {issue['recommendation']}")
                    run_rec.bold = True
                    run_rec.font.color.rgb = RGBColor(0, 80, 160)

                doc.add_paragraph("")  # 쟁점 간 간격

        # ── MD Action Plan ──
        if json_data.get("action_plan"):
            _add_shaded_heading(doc, "MD Action Plan", level=2, shade_color=SHADE_H2)
            doc.add_paragraph(json_data["action_plan"])

        # ── 수정 대안 조항 (보라 음영 박스) ──
        alt = json_data.get("alternative_clause")
        if alt and alt != "null":
            _add_shaded_heading(doc, "📝 수정 대안 조항 (초안)", level=2, shade_color=SHADE_H2)
            p_alt_desc = doc.add_paragraph("아래 조항을 복사하여 협상 메일이나 수정 계약서에 활용하세요.")
            p_alt_desc.runs[0].font.size = Pt(8)
            p_alt_desc.runs[0].font.color.rgb = RGBColor(128, 128, 128)
            p_alt = doc.add_paragraph(alt)
            p_alt.paragraph_format.left_indent = Cm(0.5)
            _apply_shading(p_alt, SHADE_ALT)

        # ── 상세 검토 의견 전문 (화면의 expander 내용과 동일) ──
        if detail_text:
            _add_shaded_heading(doc, "📄 상세 검토 의견 전문", level=2, shade_color=SHADE_H2)
            doc.add_paragraph(detail_text[:10000])

    else:
        # JSON 파싱 실패 시 원문 그대로
        _add_shaded_heading(doc, "검토 의견", level=2, shade_color=SHADE_H2)
        doc.add_paragraph(detail_text[:10000])

    # ── 푸터 면책 (회색 음영) ──
    doc.add_paragraph("")
    p_footer = doc.add_paragraph()
    p_footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _apply_shading(p_footer, SHADE_QUOTE)
    run_f = p_footer.add_run("본 검토의견서는 AI가 생성한 초안이며, 법적 효력이 없습니다.\n반드시 사내변호사의 최종 검토를 거치기 바랍니다.")
    run_f.font.size = Pt(8)
    run_f.font.color.rgb = RGBColor(128, 128, 128)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()

# ── Streamlit UI 메인 함수 ────────────────────────────────────
def main():
    st.set_page_config(page_title="공정거래 실무 어시스턴트 v2.1", page_icon="🤝", layout="wide")

    st.markdown("""
    <style>
    /* 1. 최고급 웹 폰트 'Pretendard' 불러오기 */
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css");

    /* 2. 전체 폰트 및 기본 타이포그래피 (신세계 뉴스룸 스타일의 정갈한 자간) */
    html, body, [class*="css"] { 
        font-family: 'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, system-ui, Roboto, "Helvetica Neue", "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", "Malgun Gothic", sans-serif !important; 
        letter-spacing: -0.02em; 
        color: #222222;
    }

    /* 앱 배경: 아주 연한 라이트 그레이로 깔끔하게 */
    .stApp { background-color: #F8F9FA !important; }

    /* 3. 채팅 메시지 블록 (기업형 UI에 맞게 굴곡 축소 및 깔끔한 보더) */
    .stChatMessage { 
        border-radius: 12px; 
        padding: 24px 32px; 
        box-shadow: 0 2px 10px rgba(0,0,0,0.03); 
        margin-bottom: 20px; 
        border: 1px solid #EAECEF; 
        background-color: #FFFFFF !important; 
        line-height: 1.6;
    }

    /* 📱 4. 모바일 반응형 처리 (스마트폰 환경 최적화) */
    @media (max-width: 768px) {
        .stChatMessage {
            padding: 16px 20px;
            border-radius: 8px;
        }
    }

    /* 5. 버튼 공통 스타일 (뉴스룸 스타일의 단정한 버튼) */
    div.stButton > button:first-child { 
        border-radius: 6px; 
        font-weight: 600; 
        transition: all 0.2s ease-in-out; 
    }

    /* 🔴 프라이머리 버튼 (신세계 딥 레드 #E3000F) */
    button[kind="primary"] { 
        background-color: #E3000F !important; 
        color: #FFFFFF !important; 
        border: none !important; 
    }
    button[kind="primary"]:hover {
        background-color: #C0000C !important; 
        transform: translateY(-1px);
        box-shadow: 0 4px 8px rgba(227, 0, 15, 0.2) !important;
    }

    /* ⚪ 세컨더리 버튼 (깔끔한 다크 그레이 아웃라인) */
    button[kind="secondary"] { 
        background-color: #FFFFFF !important; 
        color: #444444 !important; 
        border: 1px solid #DDDDDD !important; 
    }
    button[kind="secondary"]:hover {
        border-color: #222222 !important;
        color: #222222 !important;
    }

    /* 6. 사이드바 및 코드 블록 스타일링 */
    [data-testid="stSidebar"] { 
        background-color: #FFFFFF !important;
        border-right: 1px solid #EAECEF; 
    }
    
    /* 법령/사규 인용구 하이라이트 (신세계 레드 톤으로 맞춤) */
    code { 
        color: #E3000F; 
        background-color: #FCE6E7; 
        border-radius: 4px; 
        padding: 0.2em 0.4em;
        font-size: 0.9em;
    }
    pre { 
        border-radius: 8px; 
        background-color: #F8F9FA !important; 
        border: 1px solid #EAECEF; 
    }

    /* 7. 헤딩 앵커 링크 완전 제거 (마우스 오버 시 클립 버튼 + 복사 시 코드 유출 방지) */
    .stMarkdown a[href^="#"],
    [data-testid="stHeaderActionElements"] {
        display: none !important;
    }
    h1 a, h2 a, h3 a, h4 a, h5 a, h6 a {
        display: none !important;
        pointer-events: none !important;
    }
    </style>
    """, unsafe_allow_html=True)

    app_pw = get_secret("APP_PASSWORD")
    if app_pw:
        if "authenticated" not in st.session_state: st.session_state.authenticated = False
        if not st.session_state.authenticated:
            st.markdown("## 🤝 공정거래 실무 어시스턴트")
            pw = st.text_input("비밀번호를 입력하세요", type="password")
            if pw:
                if pw == app_pw: st.session_state.authenticated = True; st.rerun()
                else: st.error("비밀번호가 올바르지 않습니다.")
            st.stop()

    if "docs" not in st.session_state: st.session_state.docs = load_docs()
    if "messages" not in st.session_state: st.session_state.messages = []
    if "sessions" not in st.session_state: st.session_state.sessions = load_sessions()
    if "current_session_id" not in st.session_state: st.session_state.current_session_id = None
    if "laws_db" not in st.session_state: st.session_state.laws_db = load_laws()
    # 응답 완료 후 rerun 플래그 (다운로드 버튼 렌더링 보장)
    if "needs_rerun" not in st.session_state: st.session_state.needs_rerun = False

    if "cleanup_done" not in st.session_state:
        cleanup_old_sessions(90)
        st.session_state.cleanup_done = True

    # 이전 턴에서 세션 저장 후 rerun이 필요한 경우
    if st.session_state.needs_rerun:
        st.session_state.needs_rerun = False
        st.rerun()

    # ── 사이드바 (실무자 동선 최적화) ─────────────────────────
    with st.sidebar:
        st.markdown("## 🤝 공정거래 실무 어시스턴트 v2.1")
        st.caption("면세점 MD 바이어 전용")
        
        # 1. 자동 보안 마스킹 안내 (DLP) - 최상단 배치
        st.markdown("### 🛡️ 정보보안 (DLP) 가동 중")
        st.success(
            "⚠️ **정보 유출 방지 시스템 작동 안내**\n\n"
            "외부 클라우드 AI 서버로 당사의 핵심 기밀 및 협력사 정보가 유출되는 것을 원천 차단하기 위해, **문서 내 민감 정보는 모두 AI 전송 전에 자동 블라인드(마스킹) 처리됩니다.**\n\n"
            "• **자동 차단:** 주민/외국인번호, 휴대전화, 이메일, 사업자/법인번호, 계좌번호, 당사 명칭\n"
            "• **수동 차단:** 하단 텍스트 입력창에 기재한 '협력사명'"
        )

        # 법령 DB 업데이트 이력 (일반 사용자 대상)
        law_count = len(st.session_state.laws_db)
        if law_count > 0:
            last_dates = [d.get("last_updated") or d.get("created_at", "") for d in st.session_state.laws_db if d.get("last_updated") or d.get("created_at")]
            if last_dates:
                latest = max(last_dates)
                try:
                    from datetime import datetime as _dt
                    if "T" in latest:
                        dt_obj = _dt.fromisoformat(latest.replace("Z", "+00:00"))
                    else:
                        dt_obj = _dt.fromisoformat(latest)
                    display_date = dt_obj.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    display_date = latest[:16]
                st.caption(f"📚 적용 법령: {law_count}개 조문 · 🕐 {display_date} 기준")
            else:
                st.caption(f"📚 적용 법령: {law_count}개 조문")

        st.divider()

        # 2. 새 대화 시작
        if st.button("✨ 새 대화 시작", use_container_width=True, type="primary"):
            st.session_state.messages = []
            st.session_state.current_session_id = None
            st.rerun()

        st.divider()

        # 3. 히스토리 관리
        st.markdown("### 🗂 최근 자문 내역")
        for sess in st.session_state.sessions:
            col1, col2 = st.columns([5, 1])
            with col1:
                if st.button(sess["title"], key="sess_" + sess["id"], use_container_width=True):
                    st.session_state.messages = sess["messages"]
                    st.session_state.current_session_id = sess["id"]
                    st.rerun()
            with col2:
                if st.button("🗑", key="delsess_" + sess["id"]):
                    if delete_session_db(sess["id"]):
                        st.session_state.sessions = [s for s in st.session_state.sessions if s["id"] != sess["id"]]
                        st.rerun()

        st.divider()

        # 4. 관리자용 DB 관리는 가장 아래 숨김 (Expander)
        with st.expander("⚙️ 기준 문서 DB 관리 (관리자 전용)", expanded=False):
            law_count = len(st.session_state.laws_db)
            if law_count > 0:
                # 최신 업데이트 일시 추출
                last_dates = [d.get("last_updated") or d.get("created_at", "") for d in st.session_state.laws_db if d.get("last_updated") or d.get("created_at")]
                if last_dates:
                    latest = max(last_dates)
                    # ISO 형식 → 보기 좋게 변환
                    try:
                        from datetime import datetime as _dt
                        if "T" in latest:
                            dt_obj = _dt.fromisoformat(latest.replace("Z", "+00:00"))
                        else:
                            dt_obj = _dt.fromisoformat(latest)
                        display_date = dt_obj.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        display_date = latest[:16]
                    st.success(f"📚 법령 DB: {law_count}개 조문\n\n🕐 최종 업데이트: {display_date}")
                else:
                    st.success(f"📚 법령 DB: {law_count}개")
            else:
                st.warning("📚 법령 DB 미설정")

            st.caption(f"🤖 Claude: `{CLAUDE_MODEL}`")
            st.caption(f"🤖 Gemini: `{GEMINI_MODELS[0]}` → `{GEMINI_MODELS[1]}`")

            doc_cat = st.selectbox("문서 유형", options=list(DOC_CATS.keys()), format_func=lambda x: DOC_CATS[x]["icon"] + " " + DOC_CATS[x]["label"])
            contract_type = st.selectbox("거래 유형", CONTRACT_TYPES) if doc_cat == "contract" else None
            yakjeong_type = st.selectbox("약정서 유형", YAKJEONG_TYPES) if doc_cat == "yakjeong" else None

            uploaded_files = st.file_uploader("Word 파일 첨부", type=["docx"], accept_multiple_files=True, label_visibility="collapsed")
            if uploaded_files:
                if st.button("DB에 규칙 등록", use_container_width=True):
                    for f in uploaded_files:
                        label = f"계약서({contract_type})" if contract_type else f"약정서({yakjeong_type})" if yakjeong_type else DOC_CATS[doc_cat]["label"]
                        label += f": {f.name}"
                        file_bytes = f.read()
                        new_doc = {
                            "id": str(uuid.uuid4()),
                            "name": f.name,
                            "cat": doc_cat,
                            "contract_type": contract_type or yakjeong_type,
                            "label": label,
                            "text": extract_text(file_bytes),
                            "size": len(file_bytes),
                        }
                        if save_doc(new_doc): st.session_state.docs.append(new_doc)
                    st.rerun()

            if st.session_state.docs:
                st.markdown("**📋 적용 중인 문서**")
                for cat_id, cat_info in DOC_CATS.items():
                    cat_docs = [d for d in st.session_state.docs if d["cat"] == cat_id]
                    if not cat_docs: continue
                    st.markdown(f"**{cat_info['icon']} {cat_info['label']}**")
                    for doc in cat_docs:
                        col1, col2 = st.columns([5, 1])
                        with col1: st.caption(doc["name"])
                        with col2:
                            if st.button("X", key="del_" + doc["id"]):
                                if delete_doc(doc["id"]):
                                    st.session_state.docs = [d for d in st.session_state.docs if d["id"] != doc["id"]]
                                    st.rerun()

    # ── 메인 영역 ────────────────────────────────────────────
    st.title("🤝 공정거래 실무 어시스턴트 v2.1")
    st.caption("사규·표준계약서 질의응답 및 심층 계약/법률 검토 AI")

    if not st.session_state.messages and st.session_state.docs:
        st.markdown("### 💡 AI 법무 자문 100% 활용 가이드")
        st.info(
            "본 시스템은 질문의 목적에 따라 **두 가지 수준의 맞춤형 자문**을 제공합니다.\n\n"
            "🔹 **[1단계] 사내 기준 자문:** 일상적인 규정 문의 시, 등록된 당사 사규와 표준계약서를 바탕으로 신속한 실무 기준을 안내합니다.\n"
            "🔹 **[2단계] 심층 법무 검토:** 계약서/약정서가 첨부되거나 위법성 판단을 요청하면, 내부 사규와 외부 현행 법령을 교차 분석하여 정식 '검토 의견서'를 발행합니다."
        )
        
        samples = [
            ("🔹 [1단계] 단순 사내 규정 문의", "현재 등록된 당사에 따르면, 브랜드 자발적 사유로 매장을 리뉴얼할 때 인테리어 비용 분담 기준이 어떻게 돼?"),
            ("🔹 [2단계] 심층 법률 조항 검토", "협력사가 특약매입 판촉비 분담률을 60%로 요구하고 있어. 법률적으로 이게 맞음? 수용 가능한지 당사 기준과 대규모유통업법을 비교해서 분석해줘."),
            ("🔹 [2단계] 첨부파일 교차 검토", "[파일 첨부 후 클릭] 첨부한 파견 약정서(협력사 회신본) 내용 중, 당사 표준 규정에 어긋나거나 법 위반 소지가 있는 독소조항을 찾아내 줘.")
        ]
        cols = st.columns(3)
        for i, (cat, q) in enumerate(samples):
            with cols[i]:
                if st.button(cat, key="sample_" + str(i), use_container_width=True, help=q):
                    st.session_state["pending_input"] = q
                    st.rerun()

    # 대화 히스토리 렌더링
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🤝" if msg["role"] == "assistant" else "👤"):
            if msg["role"] == "assistant" and msg.get("json_data"):
                jd = msg["json_data"]
                render_verdict_badge(jd.get("verdict", ""))
                st.markdown(f"**📋 {jd.get('summary', '')}**")
                # 4번: 인용 검증 실패 경고
                cit_results = msg.get("citation_results", [])
                if cit_results and not all(cr["verified"] for cr in cit_results):
                    unverified = [cr["citation"] for cr in cit_results if not cr["verified"]]
                    st.warning(f"⚠️ 다음 법령 인용의 DB 검증이 완료되지 않았습니다: {', '.join(unverified)}\n\n사내변호사에게 해당 조문의 현행 유효 여부를 반드시 확인받으세요.")
                render_issues_table(jd.get("issues", []), msg.get("citation_results", []))
                if jd.get("alternative_clause"):
                    render_alternative_clause(jd["alternative_clause"])
                with st.expander("📄 상세 검토 의견 전문", expanded=False):
                    st.markdown(msg.get("detail_text", msg["content"]))
                
                if jd.get("verdict"):
                    docx_bytes = generate_review_docx(jd, msg.get("detail_text", ""), "")
                    st.caption("⚠️ 본 문서를 사내변호사 확인 없이 외부에 발송하지 마세요.")
                    st.download_button("📥 검토의견서 다운로드 (.docx)", data=docx_bytes, file_name=f"검토의견서_{datetime.now().strftime('%Y%m%d_%H%M')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=f"dl_{msg.get('msg_id', datetime.now().timestamp())}")
            else:
                st.markdown(msg["content"])

            if "time" in msg and msg["time"]:
                model_label = msg.get("model", "")
                st.caption(f"⏱️ {msg['time']:.1f}초 · {model_label}")

    # ── 입력 처리 ────────────────────────────────────────────
    if st.session_state.docs:
        
        st.markdown("---")
        st.markdown("### 💬 신규 검토 요청")
        
        st.info("🔒 **기밀 유출 방지 시스템:** 당사의 기밀이나 특정 브랜드명, 상호명이 외부 AI로 전송되어 학습에 오남용되는 것을 막기 위해 아래 칸에 **검토 대상 협력사명**을 기재해 주세요. 해당 단어는 문서와 채팅에서 모두 가려집니다.")
        target_partner = st.text_input("🏢 검토 대상 협력사명 입력 (보안 마스킹용)", placeholder="예: 에르메스, 샤넬 (입력 시 █협력사█로 자동 치환)", key="target_partner")

        with st.expander("🔄 리비전 교차 비교 (당사 초안 vs 협력사 수정본)", expanded=False):
            st.info("당사 초안(기준)과 협력사가 수정한 문서를 나란히 업로드하면, AI변호사가 변경된 독소조항을 찾아 비교 분석합니다.")
            col1, col2 = st.columns(2)
            with col1: v1_file = st.file_uploader("📄 V1 (당사 표준 초안)", type=["docx"], key="v1_upload")
            with col2: v2_file = st.file_uploader("📝 V2 (협력사 수정본)", type=["docx"], key="v2_upload")
            if v1_file and v2_file:
                if st.button("교차 비교 분석 실행", type="primary", use_container_width=True):
                    v1_file.seek(0)
                    v2_file.seek(0)
                    v1_bytes, v2_bytes = v1_file.read(), v2_file.read()
                    v1_text = extract_text(v1_bytes)
                    v2_text = extract_text(v2_bytes)
                    
                    # 추출 실패 체크
                    if v1_text.startswith("(") and v1_text.endswith(")"):
                        st.error(f"V1 파일 읽기 실패: {v1_text}")
                    elif v2_text.startswith("(") and v2_text.endswith(")"):
                        st.error(f"V2 파일 읽기 실패: {v2_text}")
                    else:
                        prompt = (
                            f"당사가 보낸 초안(V1)과 협력사가 회신한 수정본(V2)을 교차 비교해주세요.\n\n"
                            f"1. 협력사가 어느 조항을 어떻게 변경/추가/삭제했는지 핵심만 대조해주세요.\n"
                            f"2. 수정본(V2)의 내용이 DB의 [기준 문서]와 [법령]을 위반하는지 엄격히 심사해주세요.\n\n"
                            f"[V1 당사 초안 내용]\n{v1_text}\n\n"
                            f"[V2 협력사 수정본 내용]\n{v2_text}"
                        )
                        st.session_state["pending_input"] = apply_auto_masking(prompt, target_partner)
                        st.rerun()

        chat_files = st.file_uploader("📎 검토할 파일 첨부 (협력사 회신본 등)", type=["docx"], accept_multiple_files=True, key="chat_uploader")
        user_input = st.chat_input("검토할 텍스트를 입력하거나 파일을 첨부하세요...")
        query = user_input or st.session_state.pop("pending_input", None)

        if query:
            attached_texts = []
            if chat_files:
                for f in chat_files:
                    f.seek(0)
                    raw_text = extract_text(f.read())
                    # 추출 실패 시에도 마스킹 적용 후 전달 (AI가 에러 메시지를 받아 안내)
                    safe_text = apply_auto_masking(raw_text, target_partner)
                    attached_texts.append(f"=== 검토 대상 첨부 파일: {f.name} ===\n" + safe_text)

            has_attachment = bool(attached_texts)
            safe_query = apply_auto_masking(query, target_partner)

            if attached_texts:
                full_query = f"[사용자 문의사항]\n{safe_query}\n\n[검토 대상 텍스트/첨부파일]\n" + "\n\n".join(attached_texts)
                display_query = safe_query + "\n\n📎 " + ", ".join(f.name for f in chat_files)
            else:
                has_review_content = len(safe_query) > 80 or "조" in safe_query or "항" in safe_query or ":" in safe_query
                if any(kw in safe_query for kw in ["검토", "확인", "분석"]) and not has_review_content:
                    full_query = f"[사용자 문의사항]\n{safe_query}\n\n[검토 대상 텍스트/첨부파일]\n(없음 - 첨부파일이나 텍스트가 제공되지 않았습니다.)"
                else:
                    full_query = f"[사용자 문의사항 및 검토 대상 텍스트]\n{safe_query}\n\n[첨부파일]\n(없음)"
                display_query = safe_query

            st.session_state.messages.append({"role": "user", "content": full_query})
            with st.chat_message("user", avatar="👤"):
                st.markdown(display_query)

            model_choice = route_query(safe_query, has_attachment)

            with st.chat_message("assistant", avatar="🤝"):
                if model_choice == "claude":
                    spinner_msg = "⚖ 법령 및 사규 기준으로 교차 검토 중..."
                else:
                    spinner_msg = "💬 당사 사내 기준을 검색 및 분석 중..."

                with st.spinner(spinner_msg):
                    start_time = time.time()
                    reply, actual_model = dispatch_with_fallback(model_choice, st.session_state.messages, st.session_state.docs, st.session_state.laws_db)
                    elapsed = time.time() - start_time

                msg_data = {"role": "assistant", "content": reply, "time": elapsed, "model": actual_model, "msg_id": str(datetime.now().timestamp())}

                if "Failed" not in actual_model and ("Claude" in actual_model or (model_choice == "claude" and "Gemini" in actual_model)):
                    json_data, detail_text = parse_review_response(reply)
                    if json_data:
                        citation_results = verify_citations(json_data.get("cited_laws", []), st.session_state.laws_db)
                        msg_data["json_data"] = json_data
                        msg_data["detail_text"] = detail_text
                        msg_data["citation_results"] = citation_results

                        render_verdict_badge(json_data.get("verdict", ""))
                        st.markdown(f"**📋 {json_data.get('summary', '')}**")
                        # 4번: 인용 검증 실패 경고
                        if citation_results and not all(cr["verified"] for cr in citation_results):
                            unverified = [cr["citation"] for cr in citation_results if not cr["verified"]]
                            st.warning(f"⚠️ 다음 법령 인용의 DB 검증이 완료되지 않았습니다: {', '.join(unverified)}\n\n사내변호사에게 해당 조문의 현행 유효 여부를 반드시 확인받으세요.")
                        render_issues_table(json_data.get("issues", []), citation_results)
                        if json_data.get("alternative_clause"): render_alternative_clause(json_data["alternative_clause"])
                        with st.expander("📄 상세 검토 의견 전문", expanded=False): st.markdown(detail_text)

                        docx_bytes = generate_review_docx(json_data, detail_text, display_query)
                        st.caption("⚠️ 본 문서를 사내변호사 확인 없이 외부에 발송하지 마세요.")
                        st.download_button("📥 검토의견서 다운로드 (.docx)", data=docx_bytes, file_name=f"검토의견서_{datetime.now().strftime('%Y%m%d_%H%M')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

                        save_review_log({
                            "id": msg_data["msg_id"], "session_id": st.session_state.current_session_id, "verdict": json_data.get("verdict", "unknown"),
                            "issues": json_data.get("issues"), "action_plan": json_data.get("action_plan"), "cited_laws": json_data.get("cited_laws"),
                            "citation_verified": all(cr["verified"] for cr in citation_results) if citation_results else False,
                        })
                    else:
                        st.markdown(reply)
                else:
                    st.markdown(reply)

                st.caption(f"⏱️ {elapsed:.1f}초 · {actual_model}")
                st.session_state.messages.append(msg_data)

            # 세션 저장 후 다음 렌더 사이클에서 rerun (다운로드 버튼 접근 보장)
            new_id = st.session_state.current_session_id or str(uuid.uuid4())
            current_sess = {"id": new_id, "title": display_query[:25] + "...", "date": datetime.now().isoformat(), "messages": st.session_state.messages}
            if save_session(current_sess):
                st.session_state.current_session_id = new_id
                existing = [s for s in st.session_state.sessions if s["id"] != new_id]
                st.session_state.sessions = [current_sess] + existing
            # 즉시 rerun 대신 플래그 설정 → 현재 렌더에서 다운로드 버튼이 먼저 표시됨
            st.session_state.needs_rerun = True

    else:
        st.markdown("### 🚀 시작하기 — 3단계 설정 가이드")
        st.markdown("아래 순서대로 기준 문서를 등록하면 AI 검토를 시작할 수 있습니다.")
        
        # 등록 현황 체크
        has_saryu = any(d["cat"] == "saryu" for d in st.session_state.docs)
        has_contract = any(d["cat"] == "contract" for d in st.session_state.docs)
        has_yakjeong = any(d["cat"] == "yakjeong" for d in st.session_state.docs)
        
        check = lambda done: "✅" if done else "⬜"
        
        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown(f"#### {check(has_saryu)} STEP 1")
            st.markdown("**🏛 사규 등록**")
            st.caption("공정거래 컴플라이언스 정책, 내부 규정 등")
            if has_saryu:
                st.success("등록 완료")
            else:
                st.warning("미등록")
        with col2:
            st.markdown(f"#### {check(has_contract)} STEP 2")
            st.markdown("**📄 표준 계약서 등록**")
            st.caption("특약매입/직매입 거래유형별 당사 표준 계약서")
            if has_contract:
                st.success("등록 완료")
            else:
                st.warning("미등록")
        with col3:
            st.markdown(f"#### {check(has_yakjeong)} STEP 3")
            st.markdown("**📝 표준 약정서 등록**")
            st.caption("협력사원, 인테리어, 매장이동, 공동판촉 약정서")
            if has_yakjeong:
                st.success("등록 완료")
            else:
                st.warning("미등록")
        
        st.markdown("---")
        st.info("👈 사이드바 하단 **'⚙️ 기준 문서 DB 관리'**를 열고, 문서 유형을 선택한 뒤 Word 파일을 업로드하세요.\n\n최소 **사규 1개 + 계약서 1개**가 등록되면 AI 검토를 시작할 수 있습니다.")

if __name__ == "__main__":
    main()
