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
    """정규표현식을 이용한 자동 개인/기업정보 차단 및 지정 협력사명 마스킹"""
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

# ── docx 텍스트 추출 ─────────────────────────────────────────
def extract_text(file_bytes):
    from docx import Document
    doc = Document(io.BytesIO(file_bytes))
    return "\n".join([p.text for p in doc.paragraphs if p.text.strip()])

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

# ── 시스템 프롬프트 ──────────────────────────────────────────
def build_system_claude(docs, laws_db):
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = fmt_docs(by_cat("saryu"))[:25000]
    contract_text = fmt_docs(by_cat("contract"))[:35000]
    yakjeong_text = fmt_docs(by_cat("yakjeong"))[:20000]

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
        "- 위험: :red[위반 내용], 적법: :blue[통과 내용] 문법 사용.\n"
    )

def build_system_gemini(docs):
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = fmt_docs(by_cat("saryu"))[:25000]
    contract_text = fmt_docs(by_cat("contract"))[:35000]
    yakjeong_text = fmt_docs(by_cat("yakjeong"))[:20000]

    return (
        "당신은 면세점 전문 공정거래 실무 어시스턴트 AI입니다.\n"
        "사규, 계약서, 약정서 내용에 대한 일반 질문에 친절히 답변하세요.\n"
        "법률 검토 판단(승인/반려)은 하지 말고, 필요시 '검토 요청을 해주세요'라고 안내하세요.\n\n"
        "① 당사 사규:\n" + saryu_text +
        "\n\n② 당사 표준 계약서:\n" + contract_text +
        "\n\n③ 당사 표준 약정서:\n" + yakjeong_text +
        "\n\n답변 시 Streamlit 색상 문법을 사용하세요: :red[위험] :blue[적법]"
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
            model="claude-3-5-sonnet-20241022",
            max_tokens=4096,
            system=system_prompt,
            messages=claude_messages,
        )
        return response.content[0].text
    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"Claude API 오류: {e}")
        if "rate" in error_msg or "quota" in error_msg or "429" in error_msg:
            return "⚠️ [API 한도 초과] Claude API 사용 한도 또는 요금을 초과했습니다. 관리자에게 문의하여 한도를 늘려주세요."
        elif "401" in error_msg or "403" in error_msg or "authentication" in error_msg:
            return "⚠️ [API 인증 오류] Claude API 키가 유효하지 않거나 만료되었습니다."
        else:
            return "⚠️ [서버 통신 장애] 현재 Anthropic(Claude) 본사 서버에 일시적인 장애가 있거나 통신이 지연되고 있습니다."

def call_gemini(system_prompt, messages):
    from google.genai import types
    client = init_gemini()
    for model_name in ["gemini-2.5-pro", "gemini-2.5-flash"]:
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
            error_msg = str(e).lower()
            logger.error(f"Gemini ({model_name}) 오류: {e}")
            if model_name == "gemini-2.5-flash":
                if "rate" in error_msg or "quota" in error_msg or "429" in error_msg:
                    return "⚠️ [API 한도 초과] Gemini API 사용 한도를 초과했습니다."
                elif "401" in error_msg or "403" in error_msg or "api_key" in error_msg:
                    return "⚠️ [API 인증 오류] Gemini API 키가 유효하지 않습니다."
                else:
                    return "⚠️ [서버 통신 장애] 현재 구글(Gemini) 서버가 응답하지 않습니다."
            continue
    return "⚠️ 응답을 가져오지 못했습니다."

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
        return reply, "Claude 3.5 Sonnet"
    else:
        system = build_system_gemini(docs)
        reply = call_gemini(system, messages)
        if reply.startswith("⚠️"):
            st.warning(f"🔄 {reply}\n→ 예비 시스템(Claude)으로 자동 우회하여 답변을 생성합니다.")
            fallback_reply = call_claude(system, messages)
            if not fallback_reply.startswith("⚠️"):
                return fallback_reply, "Claude 3.5 Sonnet (Fallback)"
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
        "approved":    ("🟢 진행 가능 (승인)", "success"),
        "conditional": ("🟡 조건부 가능 (수정 필요)", "warning"),
        "rejected":    ("🔴 진행 불가 (반려)", "error"),
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

def generate_review_docx(json_data, detail_text, query_text):
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    doc = Document()
    style = doc.styles['Normal']
    font = style.font
    font.name = '맑은 고딕'
    font.size = Pt(10)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("⚠️ AI 검토 초안 — 법무팀 최종 확인 필요 ⚠️")
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(255, 0, 0)
    run.bold = True

    doc.add_heading("공정거래 법률 검토 의견서", level=1)
    doc.add_paragraph(f"작성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    if json_data:
        doc.add_heading("1. 문의사항", level=2)
        doc.add_paragraph(json_data.get("summary", query_text[:200]))
        doc.add_heading("2. 검토 결론", level=2)
        verdict_map = {"approved": "✅ 진행 가능 (승인)", "conditional": "⚠️ 조건부 가능 (수정 필요)", "rejected": "❌ 진행 불가 (반려)"}
        doc.add_paragraph(verdict_map.get(json_data.get("verdict", ""), "판단 보류"))
        doc.add_paragraph(json_data.get("verdict_reason", ""))
        doc.add_heading("3. 쟁점별 교차 분석", level=2)
        for issue in json_data.get("issues", []):
            risk_label = {"high": "[위험]", "medium": "[주의]", "low": "[양호]"}
            doc.add_heading(f"쟁점 {issue.get('issue_no', '?')}: {issue.get('title', '')} {risk_label.get(issue.get('risk_level',''), '')}", level=3)
            if issue.get("target_clause"): doc.add_paragraph(f"■ 검토 대상: {issue['target_clause']}")
            if issue.get("law_analysis"): doc.add_paragraph(f"■ [법령 관점]: {issue['law_analysis']}")
            if issue.get("rule_analysis"): doc.add_paragraph(f"■ [사규 관점]: {issue['rule_analysis']}")
            if issue.get("recommendation"): doc.add_paragraph(f"■ 종합 권고: {issue['recommendation']}")
            doc.add_paragraph("")
        doc.add_heading("4. MD Action Plan", level=2)
        doc.add_paragraph(json_data.get("action_plan", "(없음)"))
        if json_data.get("alternative_clause"):
            doc.add_heading("5. 수정 대안 조항 (초안)", level=2)
            doc.add_paragraph(json_data["alternative_clause"])
    else:
        doc.add_heading("검토 의견", level=2)
        doc.add_paragraph(detail_text[:10000])

    doc.add_paragraph("\n" + "─" * 50)
    disclaimer = doc.add_paragraph("본 검토의견서는 AI가 생성한 초안이며, 법적 효력이 없습니다. 반드시 법무팀의 최종 검토를 거치기 바랍니다.")
    disclaimer.runs[0].font.size = Pt(8)
    disclaimer.runs[0].font.color.rgb = RGBColor(128, 128, 128)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()

# ── Streamlit UI 메인 함수 ────────────────────────────────────
def main():
    st.set_page_config(page_title="공정거래 실무 어시스턴트 v2.0", page_icon="🤝", layout="wide")

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

    if "cleanup_done" not in st.session_state:
        cleanup_old_sessions(90)
        st.session_state.cleanup_done = True

    # ── 사이드바 (실무자 동선 최적화) ─────────────────────────
    with st.sidebar:
        st.markdown("## 🤝 공정거래 실무 어시스턴트 v2.0")
        st.caption("면세점 MD 바이어 전용")
        
        # 1. 자동 보안 마스킹 안내 (DLP) - 최상단 배치
        st.markdown("### 🛡️ 정보보안 (DLP) 가동 중")
        st.success(
            "⚠️ **정보 유출 방지 시스템 작동 안내**\n\n"
            "외부 클라우드 AI 서버로 당사의 핵심 기밀 및 협력사 정보가 유출되는 것을 원천 차단하기 위해, **문서 내 민감 정보는 모두 AI 전송 전에 자동 블라인드(마스킹) 처리됩니다.**\n\n"
            "• **자동 차단:** 주민/외국인번호, 휴대전화, 이메일, 사업자/법인번호, 계좌번호, 당사 명칭\n"
            "• **수동 차단:** 하단 텍스트 입력창에 기재한 '협력사명'"
        )

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
            if law_count > 0: st.success(f"📚 법령 DB: {law_count}개")
            else: st.warning("📚 법령 DB 미설정")

            doc_cat = st.selectbox("문서 유형", options=list(DOC_CATS.keys()), format_func=lambda x: DOC_CATS[x]["icon"] + " " + DOC_CATS[x]["label"])
            contract_type = st.selectbox("거래 유형", CONTRACT_TYPES) if doc_cat == "contract" else None
            yakjeong_type = st.selectbox("약정서 유형", YAKJEONG_TYPES) if doc_cat == "yakjeong" else None

            uploaded_files = st.file_uploader("Word 파일 첨부", type=["docx"], accept_multiple_files=True, label_visibility="collapsed")
            if uploaded_files:
                if st.button("DB에 규칙 등록", use_container_width=True):
                    for f in uploaded_files:
                        import uuid
                        label = f"계약서({contract_type})" if contract_type else f"약정서({yakjeong_type})" if yakjeong_type else DOC_CATS[doc_cat]["label"]
                        label += f": {f.name}"
                        new_doc = {"id": str(uuid.uuid4()), "name": f.name, "cat": doc_cat, "contract_type": contract_type or yakjeong_type, "label": label, "text": extract_text(f.read()), "size": f.size}
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
    st.title("🤝 공정거래 실무 어시스턴트 v2.0")
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
                render_issues_table(jd.get("issues", []), msg.get("citation_results", []))
                if jd.get("alternative_clause"):
                    render_alternative_clause(jd["alternative_clause"])
                with st.expander("📄 상세 검토 의견 전문", expanded=False):
                    st.markdown(msg.get("detail_text", msg["content"]))
                
                if jd.get("verdict"):
                    docx_bytes = generate_review_docx(jd, msg.get("detail_text", ""), "")
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
                    v1_bytes, v2_bytes = v1_file.read(), v2_file.read()
                    prompt = (
                        f"당사가 보낸 초안(V1)과 협력사가 회신한 수정본(V2)을 교차 비교해주세요.\n\n"
                        f"1. 협력사가 어느 조항을 어떻게 변경/추가/삭제했는지 핵심만 대조해주세요.\n"
                        f"2. 수정본(V2)의 내용이 DB의 [기준 문서]와 [법령]을 위반하는지 엄격히 심사해주세요.\n\n"
                        f"[V1 당사 초안 내용]\n{extract_text(v1_bytes)}\n\n"
                        f"[V2 협력사 수정본 내용]\n{extract_text(v2_bytes)}"
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
                        render_issues_table(json_data.get("issues", []), citation_results)
                        if json_data.get("alternative_clause"): render_alternative_clause(json_data["alternative_clause"])
                        with st.expander("📄 상세 검토 의견 전문", expanded=False): st.markdown(detail_text)

                        docx_bytes = generate_review_docx(json_data, detail_text, display_query)
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

            import uuid
            new_id = st.session_state.current_session_id or str(uuid.uuid4())
            current_sess = {"id": new_id, "title": display_query[:25] + "...", "date": datetime.now().isoformat(), "messages": st.session_state.messages}
            if save_session(current_sess):
                st.session_state.current_session_id = new_id
                existing = [s for s in st.session_state.sessions if s["id"] != new_id]
                st.session_state.sessions = [current_sess] + existing
            st.rerun()

    else:
        st.info("👈 사이드바 아래 '⚙️ 기준 문서 DB 관리'에서 사규/계약서를 먼저 등록해주세요.")

if __name__ == "__main__":
    main()
