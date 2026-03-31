# ============================================================
#  🤝 공정거래 실무 어시스턴트 v2.2 — 면세점 MD/바이어용
#  이중 모델: Gemini(문서/검색) + Claude(법률검토) + 고가용성 우회
#  보안: 자동 DLP(개인/기업정보) + 협력사명 지정 마스킹 탑재
#  디자인: 신세계그룹 뉴스룸 테마 적용 (Noto Sans KR, #E02B20)
#
#  v2.2 주요 변경사항:
#  - Claude BadRequestError 방지 (Payload 텍스트 안전 한도 하향)
#  - 보세판매장 특허 및 운영에 관한 고시 최우선 검토 강제 프롬프트 추가
#  - 국가법령정보센터 다이렉트 링크(URL) 자동 생성 추가
#  - 대화 연속성 유지를 위한 동적 UI 개편 (연속 질의응답 모드)
# ============================================================

import streamlit as st
import os, io, json, re, time, logging, uuid
import urllib.parse
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

CLAUDE_MODEL = get_secret("CLAUDE_MODEL", "claude-3-5-sonnet-20240620")
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
    if not text:
        return text
    text = re.sub(r'\b\d{6}[-\s]*[1-4]\d{6}\b', '█주민/외국인번호█', text) 
    text = re.sub(r'\b\d{3}-\d{2}-\d{5}\b', '█사업자번호█', text) 
    text = re.sub(r'\b\d{6}-\d{7}\b', '█법인번호█', text) 
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

# ── docx 텍스트 추출 ───────────────────────
def extract_text(file_bytes):
    if not file_bytes:
        return "(빈 파일입니다.)"
    try:
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        if not paragraphs:
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
        return f"(파일 읽기 실패: 손상되었거나 지원하지 않는 형식입니다 — {type(e).__name__})"

def truncate_at_boundary(text, max_chars):
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_separator = truncated.rfind("\n\n---\n\n")
    if last_separator > max_chars * 0.5:
        return truncated[:last_separator]
    last_newline = truncated.rfind("\n")
    if last_newline > max_chars * 0.7:
        return truncated[:last_newline]
    return truncated

def route_query(query, has_attachment):
    if has_attachment:
        return "claude"
    if any(kw in query for kw in REVIEW_KEYWORDS):
        return "claude"
    for msg in st.session_state.get("messages", []):
        if msg.get("json_data"):
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
            if law["article_no"] not in cite:
                continue
            law_short = law.get("law_short", "")
            law_name = law.get("law_name", "")
            if (law_short and law_short in cite) or \
               (law_name and law_name in cite) or \
               (law_short and cite_partial_match(law_short, cite)) or \
               (law_name and cite_partial_match(law_name, cite)):
                found = True
                matched_content = law["content"][:100] + "..."
                break
        results.append({
            "citation": cite,
            "verified": found,
            "preview": matched_content if found else ""
        })
    return results

def cite_partial_match(name, cite):
    core = name
    for suffix in ["법", "고시", "시행령", "시행규칙"]:
        if core.endswith(suffix):
            core = core[:-len(suffix)]
            break
    return len(core) >= 3 and core in cite

def classify_api_error(error):
    error_msg = str(error).lower()
    if any(kw in error_msg for kw in ["rate", "quota", "429", "resource_exhausted"]):
        return "rate_limit", "API 사용 한도 초과"
    elif any(kw in error_msg for kw in ["401", "403", "authentication", "api_key", "permission"]):
        return "auth", "API 키 인증 오류"
    elif any(kw in error_msg for kw in ["400", "badrequest", "bad_request", "invalid"]):
        return "bad_request", "요청 텍스트가 너무 길거나 형식이 잘못되었습니다."
    elif any(kw in error_msg for kw in ["500", "502", "503", "504", "unavailable", "timeout"]):
        return "server", "서버 응답 지연"
    else:
        return "unknown", f"알 수 없는 오류: {type(error).__name__}"

# ── 시스템 프롬프트 (오류 방지 및 고시 하드코딩) ──────────────────────────────────────────
def build_system_claude(docs, laws_db):
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    # v2.2 변경: BadRequestError 방지를 위해 Max token 안전 범위 하향 조정
    saryu_text    = truncate_at_boundary(fmt_docs(by_cat("saryu")), 15000)
    contract_text = truncate_at_boundary(fmt_docs(by_cat("contract")), 20000)
    yakjeong_text = truncate_at_boundary(fmt_docs(by_cat("yakjeong")), 15000)

    CORE_LAWS = {
        "대규모유통업법", "대규모유통업법 시행령", "공정거래법", "하도급법",
        "관세법", "상생협력법", "유통산업발전법",
        "대외무역법", "외국환거래법", "환급특례법",
        "보세판매장고시",
    }
    
    laws_text = ""
    if laws_db:
        core_entries = []
        for law in laws_db:
            if law["law_short"] in CORE_LAWS:
                core_entries.append(f"[{law['law_short']} {law['article_no']}] {law.get('article_title','')}\n{law['content']}")
        core_text = "\n\n---\n\n".join(core_entries) if core_entries else ""
        core_text = truncate_at_boundary(core_text, 25000) # 한도 하향
        
        aux_entries = []
        for law in laws_db:
            if law["law_short"] not in CORE_LAWS:
                title = law.get('article_title', '')
                aux_entries.append(f"- {law['law_short']} {law['article_no']} {title}")
        aux_text = "\n".join(sorted(set(aux_entries))) if aux_entries else ""
        
        laws_text = core_text
        if aux_text:
            laws_text += (
                "\n\n[보조 법령 목록]\n" + aux_text
            )
    else:
        laws_text = "(법령 DB 미등록)"

    return (
        "당신은 면세점 전문 공정거래 실무 어시스턴트 AI입니다.\n"
        "아래 3가지 관점에서 교차 검토하여 실무 결단을 내려주세요:\n"
        "① [외부 법령] — 법률, 시행령, 고시\n"
        "② [행정규칙] — 관세청 고시 등 실무 세부 기준\n"
        "③ [내부 사규/표준문서] — 당사 컴플라이언스 정책, 표준 계약서\n\n"
        
        "🚨 **[가장 중요한 핵심 지시사항 - 절대 누락 금지]** 🚨\n"
        "면세점(보세판매장) 관련 질의 및 계약 검토 시, **「보세판매장 특허 및 운영에 관한 고시」** 및 **「관세법」**은 면세점 업무의 최상위 기준입니다.\n"
        "어떠한 경우라도 이 고시와 법령을 누락하지 말고, 가장 우선적으로 탐색 및 적용하여 위법성 여부를 판단하세요. 판례나 해석례가 있으면 함께 제시하세요.\n\n"

        "[기준 문서]\n"
        "① 당사 사규:\n" + saryu_text +
        "\n\n② 당사 표준 계약서:\n" + contract_text +
        "\n\n③ 당사 표준 약정서:\n" + yakjeong_text +
        "\n\n[적용 법령 DB]\n" + laws_text +

        "\n\n[답변 형식]\n"
        "반드시 아래 형식의 ```json``` 블록 하나와 상세 설명 텍스트를 출력하세요.\n"
        "```json\n"
        "{\n"
        '  "summary": "문의사항 1줄 요약",\n'
        '  "verdict": "approved | conditional | rejected",\n'
        '  "verdict_reason": "판단 근거",\n'
        '  "issues": [\n'
        '    {\n'
        '      "issue_no": 1,\n'
        '      "title": "쟁점 제목",\n'
        '      "risk_level": "high | medium | low",\n'
        '      "target_clause": "검토 대상 원문",\n'
        '      "applicable_law": "적용 법령 (예: 대규모유통업법 제11조)",\n'
        '      "law_analysis": "법령 관점 평가",\n'
        '      "applicable_rule": "적용 사규",\n'
        '      "rule_analysis": "사규 관점 평가",\n'
        '      "recommendation": "권고안"\n'
        '    }\n'
        '  ],\n'
        '  "action_plan": "MD 액션 플랜",\n'
        '  "alternative_clause": "수정 대안 조항",\n'
        '  "cited_laws": ["대규모유통업법 제11조"]\n'
        "}\n"
        "```\n\n"
        "**[상세 설명]**\n"
        "JSON 아래에 마크다운으로 상세히 작성하세요.\n"
        "위험은 🔴, 적법은 🔵 이모지를 사용하세요. (Streamlit 색상 단축코드 금지)\n\n"
        "**[후속 대화 규칙]**\n"
        "사용자가 이전 검토에 대해 추가 질문하면 JSON 블록 없이 마크다운으로만 자연스럽게 답변하세요.\n"
    )

def build_system_gemini(docs):
    return "당신은 면세점 공정거래 어시스턴트입니다. 법률 검토는 피하고 일반 질문에만 답하세요."

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
        return f"⚠️ [{err_type}] Claude: {err_msg}"

def call_gemini(system_prompt, messages):
    from google.genai import types
    client = init_gemini()
    last_error_type = None
    
    for model_name in GEMINI_MODELS:
        if last_error_type == "rate_limit":
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
            last_error_type, last_err_msg = classify_api_error(e)
            if model_name == GEMINI_MODELS[-1] or last_error_type == "rate_limit":
                return f"⚠️ [{last_error_type}] Gemini: {last_err_msg}"
            continue
    return "⚠️ Gemini 응답 실패."

def dispatch_with_fallback(model_choice, messages, docs, laws_db):
    if model_choice == "claude":
        system = build_system_claude(docs, laws_db)
        reply = call_claude(system, messages)
        if reply.startswith("⚠️"):
            st.warning(f"🔄 {reply}\n→ 예비 시스템(Gemini)으로 우회합니다.")
            fallback_reply = call_gemini(system, messages)
            if not fallback_reply.startswith("⚠️"):
                return fallback_reply, "Gemini (Fallback)"
            return reply, "Claude (Failed)"
        return reply, f"Claude ({CLAUDE_MODEL})"
    else:
        system = build_system_gemini(docs)
        reply = call_gemini(system, messages)
        if reply.startswith("⚠️"):
            st.warning(f"🔄 {reply}\n→ 예비 시스템(Claude)으로 우회합니다.")
            fallback_reply = call_claude(system, messages)
            if not fallback_reply.startswith("⚠️"):
                return fallback_reply, f"Claude (Fallback)"
            return reply, "Gemini (Failed)"
        return reply, "Gemini"

def parse_review_response(response_text):
    json_data = None
    detail_text = response_text
    json_match = re.search(r'```json\s*\n(.*?)\n```', response_text, re.DOTALL)
    if json_match:
        try:
            json_data = json.loads(json_match.group(1))
            detail_text = response_text[json_match.end():].strip()
        except json.JSONDecodeError:
            pass
    return json_data, detail_text

def render_verdict_badge(verdict):
    badges = {
        "approved":    ("🟢 위험 요소 미발견 (사내변호사 확인 권장)", "success"),
        "conditional": ("🟡 수정 필요 사항 발견", "warning"),
        "rejected":    ("🔴 중대 위험 발견 (진행 보류 권고)", "error"),
    }
    label, msg_type = badges.get(verdict, ("⚪ 판단 보류", "info"))
    getattr(st, msg_type)(label)

# ── v2.2 법령 국가법령정보센터 링크 생성 함수 ──
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
                            verified = "✅ DB검증 완료" if cr["verified"] else "⚠️ DB 미검증"
                            break
                    
                    # 🔗 법령 링크 자동 생성 로직
                    law_name_only = law_ref.split(" 제")[0].strip()
                    encoded_law = urllib.parse.quote(law_name_only)
                    law_link = f"https://www.law.go.kr/LSW/lsInfoP.do?lsNm={encoded_law}#AJAX"
                    
                    st.markdown(f"**⚖️ 적용 법령:** [{law_ref}]({law_link}) 🔗 *(클릭 시 국가법령정보센터 이동)* {verified}")
                    
                if issue.get("law_analysis"):
                    st.markdown(f"**🔍 법령·판례 관점:** {issue['law_analysis']}")
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
        st.code(clause, language="text")

def _apply_shading(paragraph, hex_color):
    from docx.oxml.ns import nsdecls
    from docx.oxml import parse_xml
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}" w:val="clear"/>')
    paragraph.paragraph_format.element.get_or_add_pPr().append(shading)

def generate_review_docx(json_data, detail_text, query_text):
    from docx import Document
    from docx.shared import Pt, RGBColor, Cm
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()
    for section in doc.sections:
        section.top_margin = Cm(1.27); section.bottom_margin = Cm(1.27)
        section.left_margin = Cm(1.27); section.right_margin = Cm(1.27)

    style = doc.styles['Normal']
    style.font.name = '맑은 고딕'; style.font.size = Pt(10)

    p_title = doc.add_paragraph()
    p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _apply_shading(p_title, "2F5496")
    run_title = p_title.add_run("🤝 공정거래 실무 어시스턴트 v2.2 — 검토 의견서")
    run_title.font.size = Pt(18); run_title.bold = True; run_title.font.color.rgb = RGBColor(255, 255, 255)

    if json_data:
        verdict = json_data.get("verdict", "")
        p_verdict = doc.add_paragraph()
        run_v = p_verdict.add_run(f"결론: {verdict.upper()}")
        run_v.font.size = Pt(14); run_v.bold = True
        
        doc.add_paragraph(f"📋 요약: {json_data.get('summary', '')}")
        
        if json_data.get("issues"):
            doc.add_heading("쟁점별 교차 분석", level=2)
            for issue in json_data["issues"]:
                doc.add_heading(f"쟁점: {issue.get('title', '')}", level=3)
                if issue.get("applicable_law"): doc.add_paragraph(f"적용 법령: {issue['applicable_law']}")
                if issue.get("law_analysis"): doc.add_paragraph(f"법령 분석: {issue['law_analysis']}")
                if issue.get("recommendation"): doc.add_paragraph(f"권고: {issue['recommendation']}")
    else:
        doc.add_heading("검토 의견", level=2)
        doc.add_paragraph(detail_text[:5000])

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()

# ── Streamlit UI 메인 함수 ────────────────────────────────────
def main():
    st.set_page_config(page_title="공정거래 실무 어시스턴트 v2.2", page_icon="🤝", layout="wide")

    st.markdown("""
    <style>
    @import url("https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap");
    html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif !important; }
    .stApp { background-color: #FFFFFF !important; }
    .stChatMessage { border: 1px solid #E8E8E8; background-color: #FFFFFF !important; }
    button[kind="primary"] { background-color: #E02B20 !important; color: #FFFFFF !important; border: none !important; }
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
    if "needs_rerun" not in st.session_state: st.session_state.needs_rerun = False

    if st.session_state.needs_rerun:
        st.session_state.needs_rerun = False
        st.rerun()

    # ── 사이드바 ─────────────────────────
    with st.sidebar:
        st.markdown("## 🤝 공정거래 실무 어시스턴트 v2.2")
        st.success("🛡️ **보안(DLP) 가동 중**: 문서 내 민감 정보는 모두 자동 마스킹 처리됩니다.")
        
        if st.button("✨ 새 대화 시작 (문서 초기화)", use_container_width=True, type="primary"):
            st.session_state.messages = []
            st.session_state.current_session_id = None
            st.rerun()

        st.divider()
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
        with st.expander("⚙️ 기준 문서 DB 관리 (관리자 전용)", expanded=False):
            doc_cat = st.selectbox("문서 유형", options=list(DOC_CATS.keys()))
            contract_type = st.selectbox("거래 유형", CONTRACT_TYPES) if doc_cat == "contract" else None
            yakjeong_type = st.selectbox("약정서 유형", YAKJEONG_TYPES) if doc_cat == "yakjeong" else None

            uploaded_files = st.file_uploader("Word 파일 첨부", type=["docx"], accept_multiple_files=True)
            if uploaded_files:
                if st.button("DB에 규칙 등록", use_container_width=True):
                    for f in uploaded_files:
                        label = f"{doc_cat}: {f.name}"
                        file_bytes = f.read()
                        new_doc = {
                            "id": str(uuid.uuid4()), "name": f.name, "cat": doc_cat,
                            "contract_type": contract_type or yakjeong_type, "label": label,
                            "text": extract_text(file_bytes), "size": len(file_bytes),
                        }
                        if save_doc(new_doc): st.session_state.docs.append(new_doc)
                    st.rerun()

            if st.session_state.docs:
                for doc in st.session_state.docs:
                    col1, col2 = st.columns([5, 1])
                    with col1: st.caption(doc["name"])
                    with col2:
                        if st.button("X", key="del_" + doc["id"]):
                            if delete_doc(doc["id"]):
                                st.session_state.docs = [d for d in st.session_state.docs if d["id"] != doc["id"]]
                                st.rerun()

    # ── 메인 영역 ────────────────────────────────────────────
    st.title("🤝 공정거래 실무 어시스턴트 v2.2")

    if not st.session_state.messages and st.session_state.docs:
        st.info("💡 처음이신가요? 법률 검토를 원하시면 아래에 파일을 첨부하거나 질문을 입력하세요.")

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
                    st.download_button("📥 검토의견서 다운로드 (.docx)", data=docx_bytes, file_name=f"검토_{datetime.now().strftime('%H%M')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key=f"dl_{msg.get('msg_id', datetime.now().timestamp())}")
            else:
                st.markdown(msg["content"])

    # ── 입력 처리 및 동적 UI 개편 (v2.2) ─────────────────────────
    if st.session_state.docs:
        st.markdown("---")
        
        # 이전 대화가 있는지 확인하여 UI 모드 분기
        is_continuing = len(st.session_state.messages) > 0

        if is_continuing:
            st.markdown("### 💬 추가 질의 응답 모드")
            st.info("이전 검토 결과에 대해 궁금한 점을 계속 질문할 수 있습니다. (새로운 문서를 검토하시려면 왼쪽 사이드바의 **'✨ 새 대화 시작'**을 눌러주세요)")
            
            # 파일 업로더는 보기 좋게 접어둠
            with st.expander("📎 (선택) 추가 문서 첨부 및 비교하기", expanded=False):
                target_partner = st.text_input("🏢 검토 대상 협력사명 (마스킹용)", key="target_partner_cont")
                chat_files = st.file_uploader("📎 파일 첨부", type=["docx"], accept_multiple_files=True, key="chat_uploader_cont")
        else:
            st.markdown("### 💬 신규 검토 요청")
            target_partner = st.text_input("🏢 검토 대상 협력사명 (마스킹용)", placeholder="예: 에르메스 (입력 시 마스킹됨)", key="target_partner_new")
            chat_files = st.file_uploader("📎 검토할 파일 첨부", type=["docx"], accept_multiple_files=True, key="chat_uploader_new")

        user_input = st.chat_input("질문 내용이나 지시사항을 입력하세요...")
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
                full_query = f"[사용자 문의사항]\n{safe_query}\n\n[첨부파일]\n(없음)"
                display_query = safe_query

            st.session_state.messages.append({"role": "user", "content": full_query})
            with st.chat_message("user", avatar="👤"):
                st.markdown(display_query)

            model_choice = route_query(safe_query, has_attachment)

            with st.chat_message("assistant", avatar="🤝"):
                spinner_msg = "⚖ 법령 및 당사 규정 교차 검토 중..." if model_choice == "claude" else "💬 분석 중..."
                with st.spinner(spinner_msg):
                    reply, actual_model = dispatch_with_fallback(model_choice, st.session_state.messages, st.session_state.docs, st.session_state.laws_db)
                    
                msg_data = {"role": "assistant", "content": reply, "model": actual_model, "msg_id": str(datetime.now().timestamp())}

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
                        st.download_button("📥 검토의견서 다운로드 (.docx)", data=docx_bytes, file_name=f"검토_{datetime.now().strftime('%H%M')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

                        save_review_log({
                            "id": msg_data["msg_id"], "session_id": st.session_state.current_session_id, "verdict": json_data.get("verdict", "unknown")
                        })
                    else:
                        st.markdown(reply)
                else:
                    st.markdown(reply)

                st.session_state.messages.append(msg_data)

            new_id = st.session_state.current_session_id or str(uuid.uuid4())
            current_sess = {"id": new_id, "title": display_query[:25] + "...", "date": datetime.now().isoformat(), "messages": st.session_state.messages}
            if save_session(current_sess):
                st.session_state.current_session_id = new_id
                existing = [s for s in st.session_state.sessions if s["id"] != new_id]
                st.session_state.sessions = [current_sess] + existing
            st.session_state.needs_rerun = True

if __name__ == "__main__":
    main()
