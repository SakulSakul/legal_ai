# ============================================================
#  🤝 공정거래 실무 어시스턴트 v2.0 — 면세점 MD/바이어용
#  이중 모델: Gemini(문서/검색) + Claude(법률검토) + 고가용성 우회
#  보안: 자동 DLP(개인/기업정보) + 협력사명 지정 마스킹 탑재
#  디자인: 신세계그룹 뉴스룸 테마 적용 (Pretendard, Corporate Red)
# ============================================================

import streamlit as st
import os, io, json, re, time, logging
from datetime import datetime, timedelta

# ── 로깅 설정 ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 설정 ─────────────────────────────────────────────────────
def get_secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")

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
    if not text:
        return text
    # 1. 개인정보 및 식별번호 차단 (자동)
    text = re.sub(r'\b\d{6}[-\s]*[1-4]\d{6}\b', '█주민/외국인번호█', text) 
    text = re.sub(r'\b\d{3}[-\s]*\d{2}[-\s]*\d{5}\b', '█사업자번호█', text) 
    text = re.sub(r'\b\d{6}[-\s]*\d{7}\b', '█법인번호█', text) 
    text = re.sub(r'\b01[016789][-\s]*\d{3,4}[-\s]*\d{4}\b', '█휴대전화█', text)
    text = re.sub(r'\b0[2-9][0-9]?[-\s]*\d{3,4}[-\s]*\d{4}\b', '█전화번호█', text)
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '█이메일█', text)
    text = re.sub(r'\b\d{3,6}[-\s]*\d{2,6}[-\s]*\d{3,6}\b', '█계좌번호█', text) 
    # 2. 당사 식별 키워드 차단
    company_keywords = ['신세계디에프', '신세계면세점', '신세계 DF', 'Shinsegae DF', '신세계']
    for kw in company_keywords:
        text = text.replace(kw, '█당사(내부정보)█')
    # 3. 지정된 협력사명 차단
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
        return False

def delete_doc(doc_id):
    try:
        init_supabase().table("docs").delete().eq("id", doc_id).execute()
        return True
    except Exception:
        return False

def load_sessions():
    try:
        res = init_supabase().table("sessions").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception:
        return []

def save_session(sess):
    try:
        init_supabase().table("sessions").upsert({
            "id": sess["id"], "title": sess["title"],
            "date": sess["date"], "messages": sess["messages"],
        }).execute()
        return True
    except Exception:
        return False

def delete_session_db(sess_id):
    try:
        init_supabase().table("sessions").delete().eq("id", sess_id).execute()
        return True
    except Exception:
        return False

def save_review_log(log_data):
    try:
        init_supabase().table("review_logs").upsert(log_data).execute()
        return True
    except Exception:
        return False

def load_laws():
    try:
        res = init_supabase().table("laws").select("*").order("id").execute()
        return res.data or []
    except Exception:
        return []

def cleanup_old_sessions(days=90):
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        init_supabase().table("sessions").delete().lt("created_at", cutoff).execute()
    except Exception:
        pass

# ── 텍스트 처리 및 라우팅 ─────────────────────────────────────
def extract_text(file_bytes):
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def route_query(query, has_attachment):
    if has_attachment or any(kw in query for kw in REVIEW_KEYWORDS):
        return "claude"
    return "gemini"

def verify_citations(cited_laws, laws_db):
    results = []
    if not cited_laws: return results
    for cite in cited_laws:
        found = False
        matched_content = ""
        for law in laws_db:
            if law["law_short"] in cite and law["article_no"] in cite:
                found = True
                matched_content = law["content"][:100] + "..."
                break
        results.append({"citation": cite, "verified": found, "preview": matched_content if found else ""})
    return results

# ── 시스템 프롬프트 ──────────────────────────────────────────
def build_system_claude(docs, laws_db):
    def by_cat(cat): return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    laws_text = "\n\n---\n\n".join([f"[{l['law_short']} {l['article_no']}] {l.get('article_title','')}\n{l['content']}" for l in laws_db]) if laws_db else "(법령 DB 미등록)"

    return (
        "당신은 면세점 전문 공정거래 실무 어시스턴트 AI입니다.\n"
        "① [외부 법령]과 ② [내부 사규]라는 두 가지 관점에서 교차 검토하여 실무 결단을 내려주세요.\n\n"
        "[기준 문서]\n" + fmt_docs(docs) + "\n\n[적용 법령]\n" + laws_text +
        "\n\n반드시 아래 형식의 ```json``` 블록 하나와 상세 설명 텍스트를 출력하세요.\n\n"
        "```json\n"
        "{\n"
        '  "summary": "1줄 요약", "verdict": "approved|conditional|rejected", "verdict_reason": "판단 근거",\n'
        '  "issues": [{"issue_no": 1, "title": "쟁점", "risk_level": "high|medium|low", "target_clause": "원문", "applicable_law": "법령", "law_analysis": "법령 분석", "applicable_rule": "사규", "rule_analysis": "사규 분석", "recommendation": "권고"}],\n'
        '  "action_plan": "MD 액션", "alternative_clause": "대안 조항", "cited_laws": ["법령명"]\n'
        "}\n"
        "```\n\n"
        "상세 설명에는 :red[위험], :blue[적법] 문법을 사용하세요."
    )

def build_system_gemini(docs):
    return "당신은 면세점 전문 공정거래 실무 어시스턴트입니다. 사규나 계약 절차에 대한 일반 질문에 친절히 답변하세요."

# ── AI 호출 함수 ─────────────────────────────────────────────
def call_claude(system_prompt, messages):
    client = init_anthropic()
    claude_messages = []
    last_role = None
    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        if role == last_role: claude_messages[-1]["content"] += f"\n\n{m['content']}"
        else: claude_messages.append({"role": role, "content": m["content"]}); last_role = role
    if claude_messages and claude_messages[0]["role"] == "assistant": claude_messages.pop(0)

    try:
        response = client.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=4096, system=system_prompt, messages=claude_messages)
        return response.content[0].text
    except Exception as e:
        return f"⚠️ [서버 통신 장애] {str(e)[:50]}"

def call_gemini(system_prompt, messages):
    from google.genai import types
    client = init_gemini()
    try:
        history = [types.Content(role="model" if m["role"]=="assistant" else "user", parts=[types.Part(text=m["content"])]) for m in messages[:-1]]
        response = client.models.generate_content(model="gemini-2.5-flash", contents=history + [types.Content(role="user", parts=[types.Part(text=messages[-1]["content"])])], config=types.GenerateContentConfig(system_instruction=system_prompt, tools=[types.Tool(google_search=types.GoogleSearch())]))
        return response.text
    except Exception: return "⚠️ [서버 통신 장애]"

def dispatch_with_fallback(model_choice, messages, docs, laws_db):
    if model_choice == "claude":
        system = build_system_claude(docs, laws_db)
        reply = call_claude(system, messages)
        if reply.startswith("⚠️"):
            return call_gemini(system, messages), "Gemini (Fallback)"
        return reply, "Claude 3.5 Sonnet"
    else:
        system = build_system_gemini(docs)
        reply = call_gemini(system, messages)
        if reply.startswith("⚠️"):
            return call_claude(system, messages), "Claude (Fallback)"
        return reply, "Gemini"

# ── 📌 파싱 로직 개선 (JSON 코드 노출 방지) ─────────────────────
def parse_review_response(response_text):
    """응답에서 JSON 블록을 추출하고 순수 본문만 분리하여 반환"""
    json_data = None
    detail_text = response_text
    
    # 정규표현식으로 ```json ... ``` 블록 추출
    json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
    
    if json_match:
        try:
            json_str = json_match.group(1).strip()
            json_data = json.loads(json_str)
            # JSON 블록이 차지하던 영역을 공백으로 치환하여 본문에서 제거
            detail_text = response_text.replace(json_match.group(0), "").strip()
        except json.JSONDecodeError:
            detail_text = response_text
    
    return json_data, detail_text

# ── UI 렌더링 함수 ───────────────────────────────────────────
def render_verdict_badge(verdict):
    badges = {"approved": ("🟢 진행 가능 (승인)", "success"), "conditional": ("🟡 조건부 가능 (수정 필요)", "warning"), "rejected": ("🔴 진행 불가 (반려)", "error")}
    label, msg_type = badges.get(verdict, ("⚪ 판단 보류", "info"))
    getattr(st, msg_type)(label)

def render_issues_table(issues, citation_results):
    if not issues: return
    risk_icons = {"high": "🔴", "medium": "🟡", "low": "🟢"}
    for issue in issues:
        risk = issue.get("risk_level", "medium")
        with st.expander(f"{risk_icons.get(risk, '⚪')} 쟁점 {issue.get('issue_no', '?')}: {issue.get('title', '제목 없음')}", expanded=(risk == "high")):
            if issue.get("target_clause"): st.code(issue["target_clause"], language="text")
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"**🔍 법령 관점:** {issue.get('law_analysis', '')}")
            with col2:
                st.markdown(f"**🏛️ 사규 관점:** {issue.get('rule_analysis', '')}")
            st.info(f"💡 **종합 권고:** {issue.get('recommendation', '')}")

def generate_review_docx(json_data, detail_text, query_text):
    from docx import Document
    from docx.shared import Pt, RGBColor
    doc = Document()
    doc.add_heading("공정거래 법률 검토 의견서", level=1)
    if json_data:
        doc.add_heading("1. 문의사항", level=2); doc.add_paragraph(json_data.get("summary", ""))
        doc.add_heading("2. 검토 결론", level=2); doc.add_paragraph(json_data.get("verdict_reason", ""))
    else:
        doc.add_paragraph(detail_text[:5000])
    buffer = io.BytesIO(); doc.save(buffer); buffer.seek(0)
    return buffer.getvalue()

# ── Main UI ──────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="공정거래 실무 어시스턴트 v2.0", page_icon="🤝", layout="wide")

    # 🎨 신세계 테마 디자인 전면 적용
    st.markdown("""
    <style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css");
    html, body, [class*="css"] { font-family: 'Pretendard Variable', -apple-system, sans-serif !important; letter-spacing: -0.02em; color: #222222; }
    .stApp { background-color: #F8F9FA !important; }
    .stChatMessage { border-radius: 12px; padding: 24px; box-shadow: 0 2px 10px rgba(0,0,0,0.03); margin-bottom: 20px; border: 1px solid #EAECEF; background-color: #FFFFFF !important; line-height: 1.6; }
    @media (max-width: 768px) { .stChatMessage { padding: 16px; } }
    div.stButton > button:first-child { border-radius: 6px; font-weight: 600; transition: 0.2s; }
    button[kind="primary"] { background-color: #E3000F !important; color: #FFFFFF !important; border: none !important; }
    button[kind="primary"]:hover { background-color: #C0000C !important; transform: translateY(-1px); }
    [data-testid="stSidebar"] { background-color: #FFFFFF !important; border-right: 1px solid #EAECEF; }
    code { color: #E3000F; background-color: #FCE6E7; border-radius: 4px; padding: 0.2em 0.4em; }
    pre { border-radius: 8px; background-color: #F8F9FA !important; border: 1px solid #EAECEF; }
    </style>
    """, unsafe_allow_html=True)

    # 사이드바 구성
    with st.sidebar:
        st.markdown("## 🤝 공정거래 실무 어시스턴트 v2.0")
        st.markdown("### 🛡️ 정보보안 (DLP) 가동 중")
        st.success("⚠️ AI 전송 전 기밀 정보는 자동으로 블라인드 처리되어 유출을 차단합니다.")
        
        if st.button("✨ 새 대화 시작", use_container_width=True, type="primary"):
            st.session_state.messages = []; st.session_state.current_session_id = None; st.rerun()
        
        st.divider()
        st.markdown("### 🗂 최근 자문 내역")
        if "sessions" not in st.session_state: st.session_state.sessions = load_sessions()
        for sess in st.session_state.sessions:
            if st.button(sess["title"], key="sess_"+sess["id"], use_container_width=True):
                st.session_state.messages = sess["messages"]; st.session_state.current_session_id = sess["id"]; st.rerun()
        
        st.divider()
        with st.expander("⚙️ 기준 문서 DB 관리", expanded=False):
            if "docs" not in st.session_state: st.session_state.docs = load_docs()
            doc_cat = st.selectbox("유형", options=list(DOC_CATS.keys()), format_func=lambda x: DOC_CATS[x]["icon"]+" "+DOC_CATS[x]["label"])
            uploaded_files = st.file_uploader(" Word 파일", type=["docx"], accept_multiple_files=True)
            if uploaded_files and st.button("등록"):
                for f in uploaded_files:
                    new_doc = {"id": str(time.time()), "name": f.name, "cat": doc_cat, "label": f.name, "text": extract_text(f.read()), "size": f.size}
                    if save_doc(new_doc): st.session_state.docs.append(new_doc)
                st.rerun()

    # 메인 영역
    st.title("🤝 공정거래 실무 어시스턴트 v2.0")
    st.caption("사규·표준계약서 질의응답 및 심층 계약/법률 검토 AI")

    if not st.session_state.get("messages") and st.session_state.get("docs"):
        st.info("🔹 **[1단계] 사내 기준 자문:** 일상적인 규정 문의\n🔹 **[2단계] 심층 법무 검토:** 파일 분석 및 위법성 판단")
        samples = [("🔹 단순 규정 문의", "매장 리뉴얼 시 인테리어 비용 분담 기준이 뭐야?"), ("🔹 법률 조항 검토", "판촉비 분담률 60% 요구가 법률적으로 맞아?")]
        cols = st.columns(2)
        for i, (cat, q) in enumerate(samples):
            if cols[i].button(cat, use_container_width=True): st.session_state["pending_input"] = q; st.rerun()

    if "messages" not in st.session_state: st.session_state.messages = []
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🤝" if msg["role"]=="assistant" else "👤"):
            if msg["role"] == "assistant" and msg.get("json_data"):
                jd = msg["json_data"]
                render_verdict_badge(jd.get("verdict", ""))
                st.markdown(f"**📋 {jd.get('summary', '')}**")
                render_issues_table(jd.get("issues", []), [])
                with st.expander("📄 상세 의견 전문"): st.markdown(msg.get("detail_text", ""))
            else: st.markdown(msg["content"])

    st.markdown("---")
    target_partner = st.text_input("🏢 검토 대상 협력사명 입력 (기밀 유출 방지)", placeholder="예: 에르메스 (입력 시 █협력사█로 자동 치환)")
    query = st.chat_input("검토할 텍스트를 입력하거나 파일을 첨부하세요...") or st.session_state.pop("pending_input", None)

    if query:
        safe_query = apply_auto_masking(query, target_partner)
        st.session_state.messages.append({"role": "user", "content": safe_query})
        st.rerun()

    # (비즈니스 로직 생략: 실제 서비스 시에는 dispatch_with_fallback 호출 및 결과 저장 로직 포함)

if __name__ == "__main__":
    main()
