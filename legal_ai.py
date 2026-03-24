# ============================================================
#  공정거래 법무 AI — 면세점 MD/바이어용 (Gemini 버전)
#
#  [설치]
#  pip install streamlit google-generativeai python-docx supabase
#
#  [Streamlit Cloud Secrets 설정]
#  GEMINI_API_KEY = "AIzaxxxx"
#  SUPABASE_URL   = "https://xxxx.supabase.co"
#  SUPABASE_KEY   = "eyxxxx"
#
#  [Supabase 테이블 SQL - SQL Editor에서 실행]
#
#  CREATE TABLE docs (
#    id TEXT PRIMARY KEY,
#    name TEXT, cat TEXT, contract_type TEXT,
#    label TEXT, text TEXT, size INTEGER,
#    created_at TIMESTAMPTZ DEFAULT NOW()
#  );
#
#  CREATE TABLE sessions (
#    id TEXT PRIMARY KEY,
#    title TEXT, date TEXT,
#    messages JSONB,
#    created_at TIMESTAMPTZ DEFAULT NOW()
#  );
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

# ── Supabase: 문서 ────────────────────────────────────────────
def load_docs():
    try:
        res = SUPA.table("docs").select("*").order("created_at").execute()
        return res.data or []
    except Exception:
        return []

def save_doc(doc):
    try:
        SUPA.table("docs").upsert({
            "id":            doc["id"],
            "name":          doc["name"],
            "cat":           doc["cat"],
            "contract_type": doc.get("contract_type"),
            "label":         doc["label"],
            "text":          doc["text"],
            "size":          doc["size"],
        }).execute()
    except Exception as e:
        st.error("문서 저장 오류: " + str(e))

def delete_doc(doc_id):
    try:
        SUPA.table("docs").delete().eq("id", doc_id).execute()
    except Exception as e:
        st.error("문서 삭제 오류: " + str(e))

# ── Supabase: 세션 ───────────────────────────────────────────
def load_sessions():
    try:
        res = SUPA.table("sessions").select("*").order("created_at", desc=True).execute()
        return res.data or []
    except Exception:
        return []

def save_session(sess):
    try:
        SUPA.table("sessions").upsert({
            "id":       sess["id"],
            "title":    sess["title"],
            "date":     sess["date"],
            "messages": sess["messages"],
        }).execute()
    except Exception as e:
        st.error("세션 저장 오류: " + str(e))

def delete_session_db(sess_id):
    try:
        SUPA.table("sessions").delete().eq("id", sess_id).execute()
    except Exception as e:
        st.error("세션 삭제 오류: " + str(e))

# ── docx 텍스트 추출 ─────────────────────────────────────────
def extract_text(file_bytes):
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

# ── 시스템 프롬프트 ──────────────────────────────────────────
def build_system(docs):
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]

    def fmt_docs(ds):
        if not ds:
            return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = fmt_docs(by_cat("saryu"))[:25000]
    contract_text = fmt_docs(by_cat("contract"))[:35000]
    yakjeong_text = fmt_docs(by_cat("yakjeong"))[:20000]

    return (
        "당신은 면세점(보세판매장) 전문 공정거래 AI변호사이자 컴플라이언스 의사결정 보조 AI입니다.\n"
        "단순한 법령 해설을 넘어, 회사의 비즈니스 이익과 법적 리스크를 종합적으로 조율하여 실무적인 결단을 돕고 MD/바이어의 업무를 명확히 가이드합니다.\n\n"
        "[활용 가능한 문서]\n"
        "① 사규 컴플라이언스 정책:\n"
        + saryu_text +
        "\n\n② 거래유형별 기본거래계약서 (특약매입/직매입/임대차/위탁):\n"
        + contract_text +
        "\n\n③ 관련 약정서:\n"
        + yakjeong_text +
        "\n\n[전문 법령 영역]\n"
        "- 대규모유통업에서의 거래 공정화에 관한 법률(대규모유통업법) 및 시행령 시행규칙\n"
        "- 보세판매장 운영에 관한 고시(관세청 고시)\n"
        "- 독점규제 및 공정거래에 관한 법률(공정거래법)\n"
        "- 하도급거래 공정화에 관한 법률\n\n"
        "[답변 방식]\n"
        "1. Google 검색을 통해 대규모유통업법, 보세판매장 고시 등 관련 법령 최신 조항을 먼저 확인하세요.\n"
        "2. 사규 → 계약서 조항 → 약정서 → 법령 순서로 교차 분석하세요.\n"
        "3. 계약 조항과 법령이 충돌하는 경우 리스크 수준(高/中/低)을 명시하세요.\n"
        "4. 출처 표기 형식 (반드시 준수):\n"
        "   - 사규: [사규: 문서명 > 조항]\n"
        "   - 계약서: [계약서(거래유형): 문서명 > 제N조]\n"
        "   - 약정서: [약정서: 문서명 > 제N조]\n"
        "   - 법령: [법령: 대규모유통업법 제N조(제목)]\n"
        "   - 고시: [고시: 보세판매장 운영에 관한 고시 제N조]\n"
        "5. 체크리스트 요청 시 체크/경고/엑스 형식으로 항목별 판단을 제시하세요.\n"
        "6. 답변 중간에 [법적 근거 요약] 섹션으로 주요 쟁점을 먼저 정리하세요.\n"
        "7. 💡 가장 마지막에는 반드시 **[최종 AI변호사 검토 의견 및 실무 가이드]** 섹션을 추가하세요. 보조 의사결정자의 관점에서 ① 추천하는 방향(진행 가능 / 조건부 진행 / 보류 및 거절), ② MD/바이어를 위한 구체적인 Action Plan(계약서 수정 대안, 협상 논리, 내부 품의 시 유의사항 등)을 명확하게 제시하세요.\n\n"
        "[주의] 본 AI의 답변은 내부 참고용 1차 검토 의견이므로, 최종 법적 책임이 따르는 중대 사안은 반드시 CSR팀 법무 담당자의 크로스체크를 받으시기 바랍니다."
    )

# ── AI 호출 (Gemini + Google 검색 grounding) ─────────────────
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
            err = str(e)
            if "ResourceExhausted" in err or "429" in err or "quota" in err.lower():
                if model_name == "gemini-2.5-flash":
                    return (
                        "⚠️ **Gemini API 할당량을 초과했습니다.**\n\n"
                        "**1~2분 후 다시 시도해 주세요.**"
                    )
                continue 
            else:
                return "⚠️ 오류가 발생했습니다: " + err[:200]

    return "⚠️ 응답을 가져오지 못했습니다. 다시 시도해 주세요."

# ── Streamlit UI ─────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="공정거래 법무 AI",
        page_icon="⚖",
        layout="wide",
    )

    st.markdown("""
    <style>
    #MainMenu, footer, header {visibility: hidden;}
    .block-container {padding: 1.2rem 1.5rem;}
    .stChatMessage {border-radius: 10px; margin-bottom: 6px;}
    </style>
    """, unsafe_allow_html=True)

    if "docs" not in st.session_state:
        st.session_state.docs = load_docs()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "sessions" not in st.session_state:
        st.session_state.sessions = load_sessions()
    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = None

    # ── 사이드바 ────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## ⚖ 공정거래 법무 AI")
        st.caption("면세점 MD 바이어 전용 · Duty-Free Legal Counsel")
        st.info("📌 연동 법령\n\n대규모유통업법 · 보세판매장 고시\n공정거래법 · 하도급법")
        st.divider()

        st.markdown("### 📂 문서 업로드")

        doc_cat = st.selectbox(
            "문서 유형",
            options=list(DOC_CATS.keys()),
            format_func=lambda x: DOC_CATS[x]["icon"] + " " + DOC_CATS[x]["label"],
            key="upload_cat"
        )

        contract_type = None
        if doc_cat == "contract":
            contract_type = st.selectbox("거래 유형", CONTRACT_TYPES, key="upload_type")

        yakjeong_type = None
        if doc_cat == "yakjeong":
            yakjeong_type = st.selectbox("약정서 유형", YAKJEONG_TYPES, key="upload_yak_type")

        uploaded_files = st.file_uploader(
            "Word 파일 선택 (.docx)",
            type=["docx"],
            accept_multiple_files=True,
            key="file_uploader",
            label_visibility="collapsed"
        )

        if uploaded_files:
            if st.button("문서 등록", use_container_width=True, type="primary"):
                added = 0
                existing_keys = {d["name"] + d["cat"] for d in st.session_state.docs}
                for f in uploaded_files:
                    key = f.name + doc_cat
                    if key not in existing_keys:
                        text = extract_text(f.read())
                        if contract_type:
                            label = "계약서(" + contract_type + "): " + f.name
                        elif yakjeong_type:
                            label = "약정서(" + yakjeong_type + "): " + f.name
                        else:
                            label = DOC_CATS[doc_cat]["label"] + ": " + f.name
                        new_doc = {
                            "id":            f.name + "_" + doc_cat + "_" + str(datetime.now().timestamp()),
                            "name":          f.name,
                            "cat":           doc_cat,
                            "contract_type": contract_type or yakjeong_type,
                            "label":         label,
                            "text":          text,
                            "size":          f.size,
                        }
                        save_doc(new_doc)
                        st.session_state.docs.append(new_doc)
                        added += 1
                if added:
                    st.success(str(added) + "개 문서 등록 완료")
                    st.rerun()
                else:
                    st.info("이미 등록된 문서입니다")

        if st.session_state.docs:
            st.divider()
            st.markdown("### 📋 등록된 문서")
            for cat_id, cat_info in DOC_CATS.items():
                cat_docs = [d for d in st.session_state.docs if d["cat"] == cat_id]
                if not cat_docs:
                    continue
                st.markdown("**" + cat_info["icon"] + " " + cat_info["label"] + "**")
                for doc in cat_docs:
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        if doc.get("contract_type"):
                            name_display = "[" + doc["contract_type"] + "] " + doc["name"]
                        else:
                            name_display = doc["name"]
                        st.caption("📎 " + name_display)
                    with col2:
                        if st.button("X", key="del_" + doc["id"], help="삭제"):
                            delete_doc(doc["id"])
                            st.session_state.docs = [d for d in st.session_state.docs if d["id"] != doc["id"]]
                            st.rerun()

        st.divider()
        if st.button("새 대화 시작", use_container_width=True):
            st.session_state.messages = []
            st.session_state.current_session_id = None
            st.rerun()

        st.markdown("### 🗂 자문 내역")
        if st.session_state.sessions:
            for sess in st.session_state.sessions:
                col1, col2 = st.columns([5, 1])
                with col1:
                    if sess["id"] == st.session_state.current_session_id:
                        btn_label = "▶ " + sess["title"]
                    else:
                        btn_label = sess["title"]
                    if st.button(btn_label, key="sess_" + sess["id"], use_container_width=True):
                        st.session_state.messages = sess["messages"]
                        st.session_state.current_session_id = sess["id"]
                        st.rerun()
                with col2:
                    if st.button("🗑", key="delsess_" + sess["id"], help="삭제"):
                        delete_session_db(sess["id"])
                        st.session_state.sessions = [s for s in st.session_state.sessions if s["id"] != sess["id"]]
                        if st.session_state.current_session_id == sess["id"]:
                            st.session_state.messages = []
                            st.session_state.current_session_id = None
                        st.rerun()
        else:
            st.caption("저장된 자문 없음")

    # ── 메인 영역 ───────────────────────────────────────────
    st.title("⚖ 공정거래 법무 자문")

    if st.session_state.docs:
        badge_parts = []
        for cat_id, cat_info in DOC_CATS.items():
            cnt = sum(1 for d in st.session_state.docs if d["cat"] == cat_id)
            if cnt:
                badge_parts.append(cat_info["icon"] + " " + cat_info["label"] + " " + str(cnt))
        st.caption("  |  ".join(badge_parts) + "  |  🔍 대규모유통업법 · 보세판매장 고시 실시간 검색")
        st.divider()
    else:
        st.info("👆 사이드바에서 사규, 계약서, 약정서를 업로드하면 자문이 시작됩니다.")

    if not st.session_state.messages and st.session_state.docs:
        st.markdown("**자주 묻는 질문**")
        samples = [
            ("📋 특약매입 체크",    "특약매입 계약 체결 전 대규모유통업법 기준 필수 체크리스트를 작성해 주세요."),
            ("🔍 직매입 반품검토",  "당사 직매입 계약서의 반품 조건(시즌아웃 등)이 대규모유통업법 예외 조항에 부합하는지 검토해 주세요."),
            ("🤝 공동판촉 분담",    "브랜드사와 공동 판촉 행사 진행 시, 판촉비용 분담 약정서 내용이 대규모유통업법 위반 소지가 없는지 분석해 주세요."),
            ("🛠 인테리어 비용",    "매장 리뉴얼 시 인테리어 설치 비용 분담 조항이 당사 사규 및 공정거래법상 문제가 없는지 확인해 주세요."),
            ("🧑‍💼 협력사원 파견",    "협력사원(판촉사원) 파견 약정서 상 면세점의 업무 지시 및 관리 권한 범위가 적법하게 규정되어 있는지 검토해 주세요."),
            ("🚫 불공정 조항",      "입점 약정서 내 '타 면세점 납품 제한' 등 배타적 거래 조항의 공정거래법상 리스크 수준을 판단해 주세요."),
        ]
        cols = st.columns(3)
        for i, (cat, q) in enumerate(samples):
            with cols[i % 3]:
                if st.button(cat, key="sample_" + str(i), use_container_width=True, help=q):
                    st.session_state["pending_input"] = q
                    st.rerun()

    for msg in st.session_state.messages:
        avatar = "⚖" if msg["role"] == "assistant" else "👤"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])

    if not st.session_state.docs:
        st.chat_input("문서를 먼저 업로드해 주세요", disabled=True)
    else:
        # ── 채팅용 임시 파일 업로드 ──────────────────────────
        chat_files = st.file_uploader(
            "📎 비교할 파일 첨부 (선택, 여러 개 가능)",
            type=["docx"],
            accept_multiple_files=True,
            key="chat_uploader",
            help="계약서 비교 등 일회성 분석용 파일을 첨부하세요. 사이드바 등록 없이 이 대화에서만 사용됩니다.",
        )

        user_input = st.chat_input("질문을 입력하세요... (파일 첨부 후 비교 요청 가능)")
        query = user_input or st.session_state.pop("pending_input", None)

        if query:
            attached_texts = []
            if chat_files:
                for f in chat_files:
                    text = extract_text(f.read())
                    attached_texts.append("=== 첨부 파일: " + f.name + " ===\n" + text)

            if attached_texts:
                full_query = (
                    query + "\n\n[첨부된 파일 내용]\n" + "\n\n".join(attached_texts)
                )
                display_query = query + "\n\n📎 " + ", ".join(f.name for f in chat_files)
            else:
                full_query = query
                display_query = query

            st.session_state.messages.append({"role": "user", "content": full_query})
            with st.chat_message("user", avatar="👤"):
                st.markdown(display_query)

            with st.chat_message("assistant", avatar="⚖"):
                with st.spinner("문서 분석 중... 관련 법령 검색 중..."):
                    system = build_system(st.session_state.docs)
                    reply = call_ai(system, st.session_state.messages)
                st.markdown(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})

            sessions = st.session_state.sessions
            if st.session_state.current_session_id:
                sessions = [
                    dict(s, messages=st.session_state.messages)
                    if s["id"] == st.session_state.current_session_id else s
                    for s in sessions
                ]
                current_sess = next(s for s in sessions if s["id"] == st.session_state.current_session_id)
            else:
                new_id = str(datetime.now().timestamp())
                title = query[:28] + ("..." if len(query) > 28 else "")
                current_sess = {
                    "id":       new_id,
                    "title":    title,
                    "date":     datetime.now().isoformat(),
                    "messages": st.session_state.messages,
                }
                sessions = [current_sess] + sessions
                st.session_state.current_session_id = new_id

            save_session(current_sess)
            st.session_state.sessions = sessions
            st.rerun()

    if st.session_state.messages:
        st.caption("⚠ 본 AI 자문은 내부 참고용이며, 중요 의사결정은 CSR팀 법무 사내변호사 최종 검토를 권장합니다.")


if __name__ == "__main__":
    main()
