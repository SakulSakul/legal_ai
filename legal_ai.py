# ============================================================
#  🤝 공정거래 실무 어시스턴트 v2.0 — 면세점 MD/바이어용
#  기능: 삭제 버튼 복구, JSON 노출 방지, 보안 마스킹, 신세계 테마
# ============================================================

import streamlit as st
import os, io, json, re, time, logging
import uuid
from datetime import datetime, timedelta

# ── 로깅 설정 ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── 설정 및 클라이언트 초기화 ──────────────────────────────────
def get_secret(key):
    try: return st.secrets[key]
    except: return os.environ.get(key, "")

@st.cache_resource
def init_supabase():
    from supabase import create_client
    url, key = get_secret("SUPABASE_URL"), get_secret("SUPABASE_KEY")
    if not url or not key: st.error("⚠️ Supabase 설정이 없습니다."); st.stop()
    return create_client(url, key)

@st.cache_resource
def init_gemini():
    from google import genai
    key = get_secret("GEMINI_API_KEY")
    if not key: st.error("⚠️ Gemini API 키가 없습니다."); st.stop()
    return genai.Client(api_key=key)

@st.cache_resource
def init_anthropic():
    import anthropic
    key = get_secret("ANTHROPIC_API_KEY")
    if not key: st.error("⚠️ Anthropic API 키가 없습니다."); st.stop()
    return anthropic.Anthropic(api_key=key)

# ── 상수 및 보안 기능 ─────────────────────────────────────────
CONTRACT_TYPES = ["특약매입", "직매입"]
YAKJEONG_TYPES = ["협력사원", "인테리어설치", "매장이동", "공동판촉", "기타"]
DOC_CATS = {"saryu": {"label": "사규", "icon": "🏛"}, "contract": {"label": "계약서", "icon": "📄"}, "yakjeong": {"label": "약정서", "icon": "📝"}}
REVIEW_KEYWORDS = ["검토", "확인", "위반", "적법", "수용", "반품", "계약", "약정", "조항", "독소", "비교", "분석"]

def apply_auto_masking(text, target_partner=""):
    if not text: return text
    text = re.sub(r'\b\d{6}[-\s]*[1-4]\d{6}\b', '█주민번호█', text)
    text = re.sub(r'\b01[016789][-\s]*\d{3,4}[-\s]*\d{4}\b', '█휴대전화█', text)
    for kw in ['신세계디에프', '신세계면세점', '신세계 DF']: text = text.replace(kw, '█당사█')
    if target_partner:
        for p in [p.strip() for p in target_partner.split(',') if p.strip()]:
            text = text.replace(p, '█협력사█')
    return text

# ── DB CRUD (삭제 기능 포함) ──────────────────────────────────
def load_docs():
    try: return init_supabase().table("docs").select("*").order("created_at").execute().data or []
    except: return []

def save_doc(doc):
    try: init_supabase().table("docs").upsert(doc).execute(); return True
    except: return False

def delete_doc(doc_id):
    try: init_supabase().table("docs").delete().eq("id", doc_id).execute(); return True
    except: return False

def load_sessions():
    try: return init_supabase().table("sessions").select("*").order("created_at", desc=True).execute().data or []
    except: return []

def save_session(sess):
    try: init_supabase().table("sessions").upsert(sess).execute(); return True
    except: return False

def delete_session_db(sess_id):
    """자문 내역 삭제 로직"""
    try:
        init_supabase().table("sessions").delete().eq("id", sess_id).execute()
        return True
    except Exception as e:
        logger.error(f"세션 삭제 실패: {e}")
        return False

def load_laws():
    try: return init_supabase().table("laws").select("*").order("id").execute().data or []
    except: return []

# ── AI 호출 및 파싱 ──────────────────────────────────────────
def extract_text(file_bytes):
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

def call_claude(system_prompt, messages):
    client = init_anthropic()
    c_msgs = []
    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        c_msgs.append({"role": role, "content": m["content"]})
    try:
        res = client.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=4096, system=system_prompt, messages=c_msgs)
        return res.content[0].text
    except: return "⚠️ 통신 장애"

def call_gemini(system_prompt, messages):
    from google.genai import types
    client = init_gemini()
    try:
        history = [types.Content(role="model" if m["role"]=="assistant" else "user", parts=[types.Part(text=m["content"])]) for m in messages[:-1]]
        res = client.models.generate_content(model="gemini-2.5-flash", contents=history + [types.Content(role="user", parts=[types.Part(text=messages[-1]["content"])])], config=types.GenerateContentConfig(system_instruction=system_prompt))
        return res.text
    except: return "⚠️ 통신 장애"

def parse_review_response(response_text):
    """JSON 노출 방지 및 본문 분리 파싱"""
    json_data, detail_text = None, response_text
    match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
    if match:
        try:
            json_data = json.loads(match.group(1).strip())
            detail_text = response_text.replace(match.group(0), "").strip()
        except: pass
    return json_data, detail_text

# ── UI 구성 ──────────────────────────────────────────────────
def render_verdict_badge(verdict):
    badges = {"approved": ("🟢 진행 가능", "success"), "conditional": ("🟡 조건부 가능", "warning"), "rejected": ("🔴 진행 불가", "error")}
    label, msg_type = badges.get(verdict, ("⚪ 판단 보류", "info"))
    getattr(st, msg_type)(label)

def render_issues_table(issues):
    for issue in issues:
        risk = issue.get("risk_level", "medium")
        with st.expander(f"📌 {issue.get('title', '쟁점')}", expanded=(risk == "high")):
            st.markdown(f"**⚖️ 법령/사규 분석:**\n{issue.get('law_analysis', '')}\n\n{issue.get('rule_analysis', '')}")
            st.info(f"💡 권고: {issue.get('recommendation', '')}")

# ── 메인 실행 ────────────────────────────────────────────────
def main():
    st.set_page_config(page_title="🤝 공정거래 실무 어시스턴트 v2.0", page_icon="🤝", layout="wide")
    
    # 신세계 프리미엄 디자인 CSS
    st.markdown("""
    <style>
    @import url("https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css");
    html, body, [class*="css"] { font-family: 'Pretendard Variable', sans-serif !important; }
    .stApp { background-color: #F8F9FA !important; }
    .stChatMessage { border-radius: 12px; padding: 20px; border: 1px solid #EAECEF; background-color: #FFFFFF !important; }
    button[kind="primary"] { background-color: #E3000F !important; color: white !important; border: none; }
    </style>
    """, unsafe_allow_html=True)

    if "docs" not in st.session_state: st.session_state.docs = load_docs()
    if "messages" not in st.session_state: st.session_state.messages = []
    if "sessions" not in st.session_state: st.session_state.sessions = load_sessions()

    # ── 사이드바 히스토리 및 삭제 기능 ──
    with st.sidebar:
        st.markdown("## 🤝 공정거래 어시스턴트")
        if st.button("✨ 새 대화 시작", use_container_width=True, type="primary"):
            st.session_state.messages = []; st.session_state.current_session_id = None; st.rerun()
        
        st.divider()
        st.markdown("### 🗂 최근 자문 내역")
        for sess in st.session_state.sessions:
            col1, col2 = st.columns([5, 1])
            with col1:
                if st.button(sess["title"], key="s_"+sess["id"], use_container_width=True):
                    st.session_state.messages = sess["messages"]; st.session_state.current_session_id = sess["id"]; st.rerun()
            with col2:
                # 🗑️ 삭제 버튼 복구
                if st.button("🗑️", key="d_"+sess["id"], help="내역 삭제"):
                    if delete_session_db(sess["id"]):
                        st.session_state.sessions = load_sessions() # 목록 새로고침
                        st.rerun()

    # ── 메인 채팅 화면 ──
    st.title("🤝 공정거래 실무 어시스턴트 v2.0")
    
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="🤝" if msg["role"]=="assistant" else "👤"):
            if msg.get("json_data"):
                render_verdict_badge(msg["json_data"].get("verdict", ""))
                st.markdown(f"### {msg['json_data'].get('summary', '')}")
                render_issues_table(msg["json_data"].get("issues", []))
                with st.expander("📄 상세 의견 전문"): st.markdown(msg.get("detail_text", ""))
            else: st.markdown(msg["content"])

    st.markdown("---")
    target_partner = st.text_input("🏢 협력사명 입력 (보안 마스킹용)")
    query = st.chat_input("문의 내용을 입력하세요...")

    if query:
        safe_query = apply_auto_masking(query, target_partner)
        st.session_state.messages.append({"role": "user", "content": safe_query})
        
        with st.chat_message("assistant", avatar="🤝"):
            with st.spinner("법률/사규 검토 중..."):
                model_choice = "claude" if any(kw in query for kw in REVIEW_KEYWORDS) else "gemini"
                laws_db = load_laws()
                
                # 시스템 프롬프트 구성 (요약)
                system_p = "당신은 면세점 공정거래 전문가입니다. 반드시 ```json``` 블록과 상세 설명을 함께 출력하세요."
                
                if model_choice == "claude": reply = call_claude(system_p, st.session_state.messages)
                else: reply = call_gemini(system_p, st.session_state.messages)
                
                json_data, detail_text = parse_review_response(reply)
                msg_data = {"role": "assistant", "content": reply, "json_data": json_data, "detail_text": detail_text}
                st.session_state.messages.append(msg_data)
                
                # 세션 저장
                sess_id = st.session_state.get("current_session_id") or str(uuid.uuid4())
                save_session({"id": sess_id, "title": query[:15]+"...", "messages": st.session_state.messages, "created_at": datetime.now().isoformat()})
                st.session_state.current_session_id = sess_id
                st.rerun()

if __name__ == "__main__":
    main()
