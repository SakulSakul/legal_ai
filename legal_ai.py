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
    yakjeong_
