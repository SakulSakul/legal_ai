# ============================================================
#  공정거래 법무 AI — 면세점 MD/바이어용 (Gemini 버전)
# ============================================================

import streamlit as st
from google import genai
import os
from datetime import datetime
from docx import Document
import io
from supabase import create_client

# ── 설정 ─────────────────────────────────────────────────────
def get_secret(key):
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, "")

GEMINI_KEY   = get_secret("GEMINI_API_KEY")
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_KEY = get_secret("SUPABASE_KEY")

SUPA        = create_client(SUPABASE_URL, SUPABASE_KEY)
GEMINI      = genai.Client(api_key=GEMINI_KEY)

CONTRACT_TYPES  = ["특약매입", "직매입"]
YAKJEONG_TYPES  = ["협력사원", "인테리어설치", "매장이동", "공동판촉", "기타"]
DOC_CATS = {
    "saryu":    {"label": "사규",   "icon": "🏛"},
    "contract": {"label": "계약서", "icon": "📄"},
    "yakjeong": {"label": "약정서", "icon": "📝"},
}

# 🌟 사내 표준 조항 (Playbook) 데이터
PLAYBOOK = {
    "판촉비 분담 (표준)": "제O조 (판촉비용의 분담)\n대규모유통업법 제11조에 따라 사전 서면 약정 없이 협력사에 판촉비용을 전가할 수 없으며, 당사와 협력사의 예상이익 비율에 따라 분담하되 협력사의 분담 비율은 50%를 초과할 수 없다.",
    "인테리어 비용 (표준)": "제O조 (인테리어 비용)\n매장 이동 및 리뉴얼에 따른 인테리어 비용은 당사의 사유(MD개편 등)인 경우 당사가 전액 부담하며, 협력사의 사유(브랜드 자발적 리뉴얼)인 경우 상호 협의하여 분담한다.",
    "타사 입점 보장 (배타적 거래 금지)": "제O조 (타사 입점 보장)\n당사는 협력사가 타 면세점 및 유통채널에 입점하는 것을 부당하게 제한하지 아니하며, 협력사의 경영 활동에 부당하게 간섭하지 않는다.",
    "직매입 반품 (표준)": "제O조 (반품의 허용)\n직매입 거래의 경우 원칙적으로 반품이 불가하나, 직매입 계약 체결 시 반품조건을 구체적으로 약정하고 그 조건에 따라 반품하는 경우에 한하여 예외적으로 허용한다."
}

# ── Supabase: 문서 & 세션 ─────────────────────────────────────
def load_docs():
    try:
        res = SUPA.table("docs").select("*").order("created_at").execute()
        return res.data or []
    except Exception:
        return []

def save_doc(doc):
    try:
        SUPA.table("docs").upsert({
            "id": doc["id"], "name": doc["name"], "cat": doc["cat"],
            "contract_type": doc.get("contract_type"), "label": doc["label"],
            "text": doc["text"], "size": doc["size"],
        }).execute()
    except Exception as e:
        pass

def delete_doc(doc_id):
    try:
        SUPA.table("docs").delete().eq("id", doc_id).execute()
    except Exception as e:
        pass

def load_sessions():
    try:
        res = SUPA.table("sessions").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception:
        return []

def save_session(sess):
    try:
        SUPA.table("sessions").upsert({
            "id": sess["id"], "title": sess["title"], "date": sess["date"], "messages": sess["messages"],
        }).execute()
    except Exception as e:
        pass

def delete_session_db(sess_id):
    try:
        SUPA.table("sessions").delete().eq("id", sess_id).execute()
    except Exception as e:
        pass

# ── docx 텍스트 추출 ─────────────────────────────────────────
def extract_text(file_bytes):
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

# ── 시스템 프롬프트 ──────────────────────────────────────────
def build_system(docs):
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = fmt_docs(by_cat("saryu"))[:25000]
    contract_text = fmt_docs(by_cat("contract"))[:35000]
    yakjeong_text = fmt_docs(by_cat("yakjeong"))[:20000]

    return (
        "당신은 면세점(보세판매장) 전문 공정거래 AI변호사이자 컴플라이언스 의사결정 보조 AI입니다.\n"
        "단순한 법령 해설을 넘어, 회사의 비즈니스 이익과 법적 리스크를 종합적으로 조율하여 실무적인 결단을 돕고 MD/바이어의 업무를 명확히 가이드합니다.\n\n"
        "[기준 문서 (Ground Truth - 절대 자체를 검토하지 말 것)]\n"
        "아래 제공된 사규, 계약서, 약정서는 당사의 '표준 규칙(정답)'입니다. 이 문서 자체의 위법성이나 문제점을 검토하지 마십시오. 오직 사용자가 채팅창에 입력하거나 첨부한 파일(검토 대상)을 평가할 때 '잣대'로만 사용하십시오.\n\n"
        "① 당사 사규 및 컴플라이언스 정책 (기준표):\n" + saryu_text +
        "\n\n② 거래유형별 당사 표준 계약서 (기준표):\n" + contract_text +
        "\n\n③ 당사 표준 약정서 (기준표):\n" + yakjeong_text +
        "\n\n[답변 방식 및 규칙 - 매우 중요]\n"
        "1. **답변의 시작:** 답변의 제목이나 서두를 작성할 때 '사건명:'이라는 법률적 표현을 절대 사용하지 말고, 반드시 '**문의사항:** [검토 요청 내용 요약]'으로 부드럽게 시작하세요.\n"
        "2. **핵심 역할:** 사용자가 질문한 내용이나 새롭게 첨부한 파일(검토 대상)이, 위의 [기준 문서] 및 [공정거래 관련 법령(대규모유통업법 등)]에 위배되지 않는지 심사하는 것입니다.\n"
        "3. 검토 결과는 반드시 기준을 종합하여 **'진행 가능(승인) / 조건부 가능(수정 필요) / 진행 불가(반려)'**를 명확히 단정지어 말하세요.\n"
        "4. **텍스트 강조 색상 규칙 (🔥매우 중요: 반드시 HTML 태그만 사용, `:red[]` 등 마크다운 사용 절대 금지):**\n"
        "   - 법률/사규 위반 소지가 있거나 위험한 내용은 반드시 `<span style='color:#FF3B30; font-weight:bold;'>여기에 텍스트</span>` 형태로 작성하십시오.\n"
        "   - 적법하거나 수용 가능한 긍정적 사항은 반드시 `<span style='color:#007AFF; font-weight:bold;'>여기에 텍스트</span>` 형태로 작성하십시오.\n"
        "   - (주의) `:red[내용]` 또는 `:blue[내용]` 형식은 화면에 그대로 노출되는 렌더링 오류를 발생시키므로 어떤 상황에서도 절대 사용하지 마십시오.\n"
        "5. 💡 답변의 마지막에는 반드시 **[최종 AI변호사 검토 의견 및 실무 가이드]** 섹션을 추가하여 ① 최종 결론, ② MD Action Plan(수정 대안, 협상 논리)을 명확히 제시하세요.\n\n"
        "[할루시네이션(환각) 방지 및 정확도 확보 엄격 규칙 - 절대 준수]\n"
        "1. **불확실성 인정 (가정 금지):** 검토할 파일이나 텍스트가 명시적으로 제공되지 않은 경우, 절대로 상황을 임의로 가정하거나 추측하지 마십시오. 사실에만 기반해야 하며, 정보가 부족하면 \"검토할 파일이나 텍스트가 제공되지 않았습니다. 정확한 검토를 위해 내용을 첨부해 주세요.\"라고 답변하고 즉시 검토를 중단하십시오.\n"
        "2. **직접 인용 (Direct Quotes):** 제공된 검토 대상 문서에서 업무를 수행하기 전에 반드시 관련된 원문 문장을 토씨 하나 틀리지 않고 그대로 추출(인용)하십시오. 오직 추출된 인용구에만 기반하여 답변을 작성하십시오.\n"
        "3. **출처 기반 검증 (Citations):** 모든 주장이나 검토 의견에는 반드시 그 근거가 되는 인용구와 출처(사규 조항, 계약서 제N조, 법률명 제N조 등)를 명시하여 감사(Audit)가 가능하도록 만드십시오. 제공된 텍스트에서 주장을 뒷받침할 인용구를 찾을 수 없다면 해당 주장을 철회하십시오.\n"
        "4. **단계적 추론 (Chain-of-Thought):** 잘못된 논리나 숨겨진 가정을 방지하기 위해, 최종 결론을 내리기 전에 단계별 추론 과정과 근거를 명확히 설명하십시오.\n"
        "5. **외부 지식 제한:** 구체적인 문서가 제공된 경우, 오직 해당 문서의 내용과 명확한 현행 법령(대규모유통업법 등) 정보만 사용하십시오. AI의 일반적인 사전 학습 지식으로 문서의 구체적 문맥을 임의로 덮어쓰거나 왜곡하지 마십시오."
    )

def call_ai(system_prompt, messages):
    from google.genai import types
    for model_name in ["gemini-2.5-pro", "gemini-2.5-flash"]:
        try:
            history = []
            for m in messages[:-1]:
                role = "model" if m["role"] == "assistant" else "user"
                history.append(types.Content(role=role, parts=[types.Part(text=m["content"])]))
            last_msg = messages[-1]["content"]
            response = GEMINI.models.generate_content(
                model=model_name,
                contents=history + [types.Content(role="user", parts=[types.Part(text=last_msg)])],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )
            return response.text
        except Exception as e:
            if "quota" in str(e).lower() and model_name == "gemini-2.5-flash":
                return "⚠️ **API 할당량 초과. 잠시 후 시도해주세요.**"
            continue 
    return "⚠️ 응답을 가져오지 못했습니다."

# ── Streamlit UI ─────────────────────────────────────────────
def main():
    st.set_page_config(page_title="공정거래 법무 AI", page_icon="⚖", layout="wide")

    st.markdown("""
    <style>
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
        background-color: #F2F2F7 !important; 
    }
    .stApp { background-color: #F2F2F7; }
    
    .stChatMessage {
        background-color: #FFFFFF;
        border-radius: 18px;
        padding: 20px 40px 20px 24px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.04);
        margin-bottom: 16px;
        border: 1px solid #E5E5EA;
        line-height: 1.6;
    }
    
    div.stButton > button:first-child {
        border-radius: 12px;
        font-weight: 600;
        transition: all 0.2s ease;
    }
    button[kind="primary"] {
        background-color: #007AFF !important;
        color: white !important;
        border: none !important;
    }
    button[kind="secondary"] {
        background-color: #FFFFFF !important;
        color: #007AFF !important;
        border: 1.5px solid #007AFF !important;
    }
    
    [data-testid="stSidebar"] {
        background-color: #FFFFFF !important;
        border-right: 1px solid #E5E5EA;
    }
    
    code { color: #d63384; background-color: #f8f9fa; border-radius: 6px; }
    pre { border-radius: 12px; background-color: #F2F2F7 !important; border: 1px solid #E5E5EA; }
    </style>
    """, unsafe_allow_html=True)

    if "docs" not in st.session_state: st.session_state.docs = load_docs()
    if "messages" not in st.session_state: st.session_state.messages = []
    if "sessions" not in st.session_state: st.session_state.sessions = load_sessions()
    if "current_session_id" not in st.session_state: st.session_state.current_session_id = None

    with st.sidebar:
        st.markdown("## ⚖ 공정거래 법무 AI")
        st.caption("면세점 MD 바이어 전용 · Duty-Free Legal Counsel")
        st.divider()

        with st.expander("📘 사내 표준 조항 (Playbook)", expanded=False):
            st.caption("우측 상단의 복사 아이콘을 눌러 실무에 바로 적용하세요.")
            for title, text in PLAYBOOK.items():
                st.markdown(f"**{title}**")
                st.code(text, language="text")

        st.divider()
        st.markdown("### 📂 기준 규칙 DB 등록 (사규/당사 표준양식)")
        st.caption("※ 이곳에 등록된 문서는 검토 기준(정답)으로 사용됩니다.")
        doc_cat = st.selectbox("문서 유형", options=list(DOC_CATS.keys()), format_func=lambda x: DOC_CATS[x]["icon"] + " " + DOC_CATS[x]["label"])
        contract_type = st.selectbox("거래 유형", CONTRACT_TYPES) if doc_cat == "contract" else None
        yakjeong_type = st.selectbox("약정서 유형", YAKJEONG_TYPES) if doc_cat == "yakjeong" else None

        uploaded_files = st.file_uploader("Word 파일 첨부", type=["docx"], accept_multiple_files=True, label_visibility="collapsed")
        if uploaded_files:
            if st.button("DB에 규칙 등록", use_container_width=True, type="primary"):
                for f in uploaded_files:
                    label = f"계약서({contract_type})" if contract_type else f"약정서({yakjeong_type})" if yakjeong_type else DOC_CATS[doc_cat]["label"]
                    label += f": {f.name}"
                    new_doc = {
                        "id": f.name + "_" + str(datetime.now().timestamp()), "name": f.name,
                        "cat": doc_cat, "contract_type": contract_type or yakjeong_type,
                        "label": label, "text": extract_text(f.read()), "size": f.size,
                    }
                    save_doc(new_doc)
                    st.session_state.docs.append(new_doc)
                st.rerun()

        if st.session_state.docs:
            st.divider()
            st.markdown("### 📋 적용 중인 기준 문서")
            for cat_id, cat_info in DOC_CATS.items():
                cat_docs = [d for d in st.session_state.docs if d["cat"] == cat_id]
                if not cat_docs: continue
                st.markdown(f"**{cat_info['icon']} {cat_info['label']}**")
                for doc in cat_docs:
                    col1, col2 = st.columns([5, 1])
                    with col1: st.caption("📎 " + doc["name"])
                    with col2:
                        if st.button("X", key="del_"+doc["id"]):
                            delete_doc(doc["id"])
                            st.session_state.docs = [d for d in st.session_state.docs if d["id"] != doc["id"]]
                            st.rerun()

        st.divider()
        if st.button("새 대화 시작", use_container_width=True):
            st.session_state.messages = []
            st.session_state.current_session_id = None
            st.rerun()
            
        st.markdown("### 🗂 자문 내역")
        for sess in st.session_state.sessions:
            col1, col2 = st.columns([5, 1])
            with col1:
                if st.button(sess["title"], key="sess_"+sess["id"], use_container_width=True):
                    st.session_state.messages = sess["messages"]
                    st.session_state.current_session_id = sess["id"]
                    st.rerun()
            with col2:
                if st.button("🗑", key="delsess_"+sess["id"]):
                    delete_session_db(sess["id"])
                    st.session_state.sessions = [s for s in st.session_state.sessions if s["id"] != sess["id"]]
                    st.rerun()

    # ── 메인 영역 ───────────────────────────────────────────
    st.title("⚖ 공정거래 법무 자문")

    if not st.session_state.messages and st.session_state.docs:
        st.markdown("**자주 묻는 질문**")
        samples = [
            ("📋 검토 요청 예시 1", "협력사에서 보내온 다음 특약매입 계약서 조항이 당사 사규에 맞는지 확인해줘: [여기에 협력사 조항 붙여넣기]"),
            ("🤝 검토 요청 예시 2", "첨부한 파견 약정서(협력사 회신본) 내용 중 법 위반 소지가 있는지 검토해줘."),
            ("🧑‍💼 검토 요청 예시 3", "협력사가 반품 기한을 60일로 연장해달라는데, 대규모유통업법과 당사 계약서 기준으로 수용 가능한가요?")
        ]
        cols = st.columns(3)
        for i, (cat, q) in enumerate(samples):
            with cols[i]:
                if st.button(cat, key="sample_"+str(i), use_container_width=True, help=q):
                    st.session_state["pending_input"] = q
                    st.rerun()

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="⚖" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"], unsafe_allow_html=True)

    if st.session_state.docs:
        with st.expander("🔄 리비전 교차 비교 (당사 초안 vs 협력사 수정본)", expanded=False):
            st.info("당사 초안(기준)과 협력사가 수정한 문서를 나란히 업로드하면, AI변호사가 변경된 독소조항을 찾아 비교 분석합니다.")
            col1, col2 = st.columns(2)
            with col1:
                v1_file = st.file_uploader("📄 V1 (당사 표준 초안)", type=["docx"], key="v1_upload")
            with col2:
                v2_file = st.file_uploader("📝 V2 (협력사 수정본)", type=["docx"], key="v2_upload")
            
            if v1_file and v2_file:
                if st.button("교차 비교 분석 실행", type="primary", use_container_width=True):
                    prompt = (
                        f"당사가 보낸 초안(V1)과 협력사가 회신한 수정본(V2)을 교차 비교해주세요.\n\n"
                        f"1. 협력사가 어느 조항을 어떻게 변경/추가/삭제했는지 핵심만 대조해주세요.\n"
                        f"2. 수정본(V2)의 내용이 DB의 [기준 문서]와 [법령]을 위반하는지 엄격히 심사해주세요.\n\n"
                        f"[V1 당사 초안 내용]\n{extract_text(v1_file.read())}\n\n"
                        f"[V2 협력사 수정본 내용]\n{extract_text(v2_file.read())}"
                    )
                    st.session_state["pending_input"] = prompt
                    st.rerun()

        chat_files = st.file_uploader(
            "📎 검토할 파일 첨부 (협력사 회신본 등)", type=["docx"], accept_multiple_files=True, key="chat_uploader"
        )
        user_input = st.chat_input("검토할 텍스트를 입력하거나 파일을 첨부하세요...")
        query = user_input or st.session_state.pop("pending_input", None)

        if query:
            attached_texts = []
            if chat_files:
                for f in chat_files:
                    text = extract_text(f.read())
                    attached_texts.append(f"=== 검토 대상 첨부 파일: {f.name} ===\n" + text)
            
            # 🚨 AI가 사용자의 입력값을 헷갈리지 않도록 명확하게 구조화
            if attached_texts:
                full_query = f"[사용자 문의사항]\n{query}\n\n[검토 대상 텍스트/첨부파일]\n" + "\n\n".join(attached_texts)
                display_query = query + "\n\n📎 " + ", ".join(f.name for f in chat_files)
            else:
                if "검토" in query and len(query) < 50 and not chat_files:
                    full_query = f"[사용자 문의사항]\n{query}\n\n[검토 대상 텍스트/첨부파일]\n(없음 - 첨부파일이나 텍스트가 제공되지 않았습니다.)"
                else:
                    full_query = f"[사용자 문의사항 및 검토 대상 텍스트]\n{query}\n\n[첨부파일]\n(없음)"
                display_query = query

            st.session_state.messages.append({"role": "user", "content": full_query})
            with st.chat_message("user", avatar="👤"):
                st.markdown(display_query, unsafe_allow_html=True)

            with st.chat_message("assistant", avatar="⚖"):
                with st.spinner("제시된 내용을 당사 사규 및 법령 기준과 대조 중..."):
                    reply = call_ai(build_system(st.session_state.docs), st.session_state.messages)
                st.markdown(reply, unsafe_allow_html=True)
                st.session_state.messages.append({"role": "assistant", "content": reply})

            new_id = st.session_state.current_session_id or str(datetime.now().timestamp())
            current_sess = {"id": new_id, "title": display_query[:25]+"...", "date": datetime.now().isoformat(), "messages": st.session_state.messages}
            save_session(current_sess)
            st.session_state.current_session_id = new_id
            st.rerun()

if __name__ == "__main__":
    main()
