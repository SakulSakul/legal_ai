# ============================================================
#  공정거래 법무 AI v2.0 — 면세점 MD/바이어용
#  이중 모델: Gemini(문서/검색) + Claude(법률검토)
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

PLAYBOOK = {
    "판촉비 분담 (표준)": "제O조 (판촉비용의 분담)\n대규모유통업법 제11조에 따라 사전 서면 약정 없이 협력사에 판촉비용을 전가할 수 없으며, 당사와 협력사의 예상이익 비율에 따라 분담하되 협력사의 분담 비율은 50%를 초과할 수 없다.",
    "인테리어 비용 (표준)": "제O조 (인테리어 비용)\n매장 이동 및 리뉴얼에 따른 인테리어 비용은 당사의 사유(MD개편 등)인 경우 당사가 전액 부담하며, 협력사의 사유(브랜드 자발적 리뉴얼)인 경우 상호 협의하여 분담한다.",
    "타사 입점 보장 (배타적 거래 금지)": "제O조 (타사 입점 보장)\n당사는 협력사가 타 면세점 및 유통채널에 입점하는 것을 부당하게 제한하지 아니하며, 협력사의 경영 활동에 부당하게 간섭하지 않는다.",
    "직매입 반품 (표준)": "제O조 (반품의 허용)\n직매입 거래의 경우 원칙적으로 반품이 불가하나, 직매입 계약 체결 시 반품조건을 구체적으로 약정하고 그 조건에 따라 반품하는 경우에 한하여 예외적으로 허용한다."
}

REVIEW_KEYWORDS = ["검토", "확인", "위반", "적법", "수용", "반품", "계약", "약정",
                   "조항", "독소", "비교", "분석", "판촉", "감액", "반려", "승인"]

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
    """90일 초과 세션 자동 삭제"""
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
    """검토 요청 → claude, 일반 질문 → gemini"""
    if has_attachment:
        return "claude"
    if any(kw in query for kw in REVIEW_KEYWORDS):
        return "claude"
    return "gemini"

# ── 법령 인용 검증 ───────────────────────────────────────────
def verify_citations(cited_laws, laws_db):
    """AI가 인용한 법령이 DB에 존재하는지 검증"""
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
    """Claude용 시스템 프롬프트 (구조화 JSON 출력 강제)"""
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = fmt_docs(by_cat("saryu"))[:25000]
    contract_text = fmt_docs(by_cat("contract"))[:35000]
    yakjeong_text = fmt_docs(by_cat("yakjeong"))[:20000]

    # 법령 DB 텍스트 구성
    laws_text = ""
    if laws_db:
        law_entries = []
        for law in laws_db:
            law_entries.append(f"[{law['law_short']} {law['article_no']}] {law.get('article_title','')}\n{law['content']}")
        laws_text = "\n\n---\n\n".join(law_entries)
    else:
        laws_text = "(법령 DB 미등록 — 일반 법률 지식으로 판단)"

    return (
        "당신은 면세점(보세판매장) 전문 공정거래 AI변호사이자 컴플라이언스 의사결정 보조 AI입니다.\n"
        "단순한 법령 해설을 넘어, 회사의 비즈니스 이익과 법적 리스크를 종합적으로 조율하여 실무적인 결단을 돕고 MD/바이어의 업무를 명확히 가이드합니다.\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "[기준 문서 (Ground Truth — 절대 자체를 검토하지 말 것)]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "아래 제공된 사규, 계약서, 약정서는 당사의 '표준 규칙(정답)'입니다.\n"
        "이 문서 자체의 위법성이나 문제점을 검토하지 마십시오.\n"
        "오직 사용자가 채팅창에 입력하거나 첨부한 파일(검토 대상)을 평가할 때 '잣대'로만 사용하십시오.\n\n"

        "① 당사 사규 및 컴플라이언스 정책 (기준표):\n" + saryu_text +
        "\n\n② 거래유형별 당사 표준 계약서 (기준표):\n" + contract_text +
        "\n\n③ 당사 표준 약정서 (기준표):\n" + yakjeong_text +

        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "[적용 법령 DB (현행 법령 원문)]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n" +
        laws_text +

        "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "[답변 형식 — 엄격 준수]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "법률 검토 요청을 받으면 반드시 아래 형식으로 응답하세요.\n"
        "```json``` 블록 하나와 상세 설명 텍스트 하나를 순서대로 출력합니다.\n\n"

        "**[PART 1: JSON 블록]**\n"
        "```json\n"
        "{\n"
        '  "summary": "문의사항 1줄 요약",\n'
        '  "verdict": "approved | conditional | rejected",\n'
        '  "verdict_reason": "최종 판단 근거 2-3줄",\n'
        '  "issues": [\n'
        '    {\n'
        '      "issue_no": 1,\n'
        '      "title": "쟁점 제목",\n'
        '      "risk_level": "high | medium | low",\n'
        '      "target_clause": "검토 대상 문서에서 해당 원문 그대로 인용",\n'
        '      "applicable_law": "적용 법령 (예: 대규모유통업법 제11조 제1항)",\n'
        '      "applicable_law_text": "위 법령 DB에서 해당 조문 원문 인용",\n'
        '      "applicable_rule": "적용 사규/기준문서 조항 (해당시)",\n'
        '      "analysis": "왜 위반/적법인지 구체적 분석",\n'
        '      "recommendation": "수정 권고 또는 수용 가능 사유"\n'
        '    }\n'
        '  ],\n'
        '  "action_plan": "MD가 취해야 할 구체적 액션 (단계별)",\n'
        '  "alternative_clause": "수정 대안 조항 초안 (해당시, 없으면 null)",\n'
        '  "cited_laws": ["대규모유통업법 제11조", "공정거래법 제45조"]\n'
        "}\n"
        "```\n\n"

        "**[PART 2: 상세 설명]**\n"
        "JSON 아래에 읽기 편한 마크다운 형식으로 상세 검토 의견을 작성하세요.\n"
        "- 서두: **문의사항:** [요약]으로 시작 (절대 '사건명:' 사용 금지)\n"
        "- 위험 사항: :red[텍스트] 형태로 표시\n"
        "- 적법 사항: :blue[텍스트] 형태로 표시\n"
        "- :red[ 또는 :blue[ 앞에 반드시 띄어쓰기 한 칸\n"
        "- 대괄호 안에 줄바꿈 금지, 핵심 단어/짧은 구절 단위로 색상 적용\n"
        "- 마지막에 **[최종 AI변호사 검토 의견 및 실무 가이드]** 섹션 필수\n\n"

        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "[할루시네이션 방지 규칙 — 절대 준수]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. 검토 대상 부재 시: 즉시 \"검토할 파일이나 텍스트가 제공되지 않았습니다.\"라고만 답변\n"
        "2. 직접 인용: 검토 대상에서 관련 원문을 토씨 하나 틀리지 않고 추출 후 답변\n"
        "3. 출처 기반 검증: 모든 주장에 근거 인용구와 출처 명시 (사규 조항, 법률명 제N조)\n"
        "4. 법령 인용 시 반드시 위 [적용 법령 DB]에 있는 조문 원문을 우선 사용\n"
        "5. DB에 없는 법령을 인용할 때는 \"(법령 DB 미등록 — 원문 확인 필요)\"를 반드시 병기\n"
        "6. 외부 지식 제한: 제공된 문서와 현행 법령 정보만 사용, AI 사전학습 지식으로 덮어쓰기 금지"
    )

def build_system_gemini(docs):
    """Gemini용 시스템 프롬프트 (일반 질의/검색용)"""
    def by_cat(cat):
        return [d for d in docs if d["cat"] == cat]
    def fmt_docs(ds):
        if not ds: return "(등록 없음)"
        return "\n\n---\n\n".join(["[" + d["label"] + "]\n" + d["text"] for d in ds])

    saryu_text    = fmt_docs(by_cat("saryu"))[:25000]
    contract_text = fmt_docs(by_cat("contract"))[:35000]
    yakjeong_text = fmt_docs(by_cat("yakjeong"))[:20000]

    return (
        "당신은 면세점(보세판매장) 전문 공정거래 AI 어시스턴트입니다.\n"
        "사규, 계약서, 약정서 내용에 대한 일반 질문에 친절히 답변하세요.\n"
        "법률 검토 판단(승인/반려)은 하지 말고, 필요시 '검토 요청을 해주세요'라고 안내하세요.\n\n"
        "① 당사 사규:\n" + saryu_text +
        "\n\n② 당사 표준 계약서:\n" + contract_text +
        "\n\n③ 당사 표준 약정서:\n" + yakjeong_text +
        "\n\n답변 시 Streamlit 색상 문법을 사용하세요: :red[위험] :blue[적법]"
    )

# ── AI 호출 함수 ─────────────────────────────────────────────
def call_claude(system_prompt, messages):
    """Claude 3.5 Sonnet 호출 (법률 검토용)"""
    client = init_anthropic()
    claude_messages = []
    last_role = None

    for m in messages:
        role = "assistant" if m["role"] == "assistant" else "user"
        content = m["content"]
        
        # Claude API의 Alternating (교차) 역할 강제 규칙 처리
        if role == last_role:
            # 같은 역할이 연속으로 나오면 이전 메시지 내용과 병합
            claude_messages[-1]["content"] += f"\n\n{content}"
        else:
            claude_messages.append({"role": role, "content": content})
            last_role = role
            
    # 첫 번째 메시지는 반드시 'user'여야 함 (보안상 첫 메시지가 assistant인 경우 제거)
    if claude_messages and claude_messages[0]["role"] == "assistant":
        claude_messages.pop(0)

    try:
        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",  # 📌 최신 3.5 Sonnet 모델로 수정
            max_tokens=4096,                     # 📌 표준 최대 토큰 값으로 수정
            system=system_prompt,
            messages=claude_messages,
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Claude API 오류: {e}")
        if "rate" in str(e).lower() or "quota" in str(e).lower():
            return "⚠️ **Claude API 할당량 초과. 잠시 후 시도해주세요.**"
        return f"⚠️ Claude 응답 오류: {str(e)[:100]}"

def call_gemini(system_prompt, messages):
    """Gemini 호출 (일반 질의용)"""
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
            logger.error(f"Gemini ({model_name}) 오류: {e}")
            if model_name == "gemini-2.5-flash":
                return f"⚠️ Gemini 응답 오류: {str(e)[:100]}"
            continue
    return "⚠️ 응답을 가져오지 못했습니다."

# ── JSON 파싱 ────────────────────────────────────────────────
def parse_review_response(response_text):
    """Claude 응답에서 JSON 블록과 상세 설명을 분리"""
    json_data = None
    detail_text = response_text

    # ```json ... ``` 블록 추출
    json_match = re.search(r'```json\s*\n(.*?)\n```', response_text, re.DOTALL)
    if json_match:
        try:
            json_data = json.loads(json_match.group(1))
            # JSON 블록 이후의 텍스트를 상세 설명으로
            detail_text = response_text[json_match.end():].strip()
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e}")
            detail_text = response_text

    return json_data, detail_text

# ── UI 렌더링 함수 ───────────────────────────────────────────
def render_verdict_badge(verdict):
    """신호등 배지"""
    badges = {
        "approved":    ("🟢 진행 가능 (승인)", "success"),
        "conditional": ("🟡 조건부 가능 (수정 필요)", "warning"),
        "rejected":    ("🔴 진행 불가 (반려)", "error"),
    }
    label, msg_type = badges.get(verdict, ("⚪ 판단 보류", "info"))
    getattr(st, msg_type)(label)

def render_issues_table(issues, citation_results):
    """쟁점별 테이블 (expander)"""
    if not issues:
        return
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
                    # 인용 검증 결과 매칭
                    verified = "⚪"
                    for cr in citation_results:
                        if any(part in cr["citation"] for part in law_ref.split()):
                            verified = "✅" if cr["verified"] else "⚠️"
                            break
                    st.markdown(f"**⚖️ 적용 법령:** {law_ref} {verified}")
            with col2:
                if issue.get("applicable_rule"):
                    st.markdown(f"**📋 적용 사규:** {issue['applicable_rule']}")

            if issue.get("applicable_law_text"):
                st.caption(f"법령 원문: {issue['applicable_law_text'][:150]}...")

            if issue.get("analysis"):
                st.markdown(f"**🔍 분석:** {issue['analysis']}")

            if issue.get("recommendation"):
                st.info(f"💡 **권고:** {issue['recommendation']}")

def render_alternative_clause(clause):
    """대안 조항 복사 블록"""
    if clause and clause != "null":
        st.markdown("---")
        st.markdown("### 📝 수정 대안 조항 (초안)")
        st.caption("아래 조항을 복사하여 협상 메일이나 수정 계약서에 활용하세요.")
        st.code(clause, language="text")

# ── 검토의견서 docx 생성 ─────────────────────────────────────
def generate_review_docx(json_data, detail_text, query_text):
    """검토의견서 docx 생성 → bytes 반환"""
    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # 스타일 설정
    style = doc.styles['Normal']
    font = style.font
    font.name = '맑은 고딕'
    font.size = Pt(10)

    # 워터마크 고지
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("⚠️ AI 검토 초안 — 법무팀 최종 확인 필요 ⚠️")
    run.font.size = Pt(12)
    run.font.color.rgb = RGBColor(255, 0, 0)
    run.bold = True

    doc.add_paragraph("")

    # 제목
    doc.add_heading("공정거래 법률 검토 의견서", level=1)
    doc.add_paragraph(f"작성일: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    doc.add_paragraph(f"작성: AI 법무 검토 시스템 (Claude 3.5 Sonnet)")  # 📌 이름 변경
    doc.add_paragraph("")

    if json_data:
        # 1. 문의사항
        doc.add_heading("1. 문의사항", level=2)
        doc.add_paragraph(json_data.get("summary", query_text[:200]))

        # 2. 검토 결론
        doc.add_heading("2. 검토 결론", level=2)
        verdict_map = {
            "approved": "✅ 진행 가능 (승인)",
            "conditional": "⚠️ 조건부 가능 (수정 필요)",
            "rejected": "❌ 진행 불가 (반려)",
        }
        doc.add_paragraph(verdict_map.get(json_data.get("verdict", ""), "판단 보류"))
        doc.add_paragraph(json_data.get("verdict_reason", ""))

        # 3. 쟁점별 분석
        doc.add_heading("3. 쟁점별 분석", level=2)
        for issue in json_data.get("issues", []):
            risk_label = {"high": "[위험]", "medium": "[주의]", "low": "[양호]"}
            doc.add_heading(
                f"쟁점 {issue.get('issue_no', '?')}: {issue.get('title', '')} {risk_label.get(issue.get('risk_level',''), '')}",
                level=3
            )
            if issue.get("target_clause"):
                doc.add_paragraph(f"검토 대상: {issue['target_clause']}")
            if issue.get("applicable_law"):
                doc.add_paragraph(f"적용 법령: {issue['applicable_law']}")
            if issue.get("applicable_rule"):
                doc.add_paragraph(f"적용 사규: {issue['applicable_rule']}")
            if issue.get("analysis"):
                doc.add_paragraph(f"분석: {issue['analysis']}")
            if issue.get("recommendation"):
                doc.add_paragraph(f"권고: {issue['recommendation']}")
            doc.add_paragraph("")

        # 4. Action Plan
        doc.add_heading("4. MD Action Plan", level=2)
        doc.add_paragraph(json_data.get("action_plan", "(없음)"))

        # 5. 대안 조항
        if json_data.get("alternative_clause"):
            doc.add_heading("5. 수정 대안 조항 (초안)", level=2)
            doc.add_paragraph(json_data["alternative_clause"])

        # 6. 인용 법령
        doc.add_heading("6. 인용 법령 목록", level=2)
        for law in json_data.get("cited_laws", []):
            doc.add_paragraph(f"• {law}")
    else:
        # JSON 파싱 실패 시 전체 텍스트
        doc.add_heading("검토 의견", level=2)
        doc.add_paragraph(detail_text[:10000])

    # 면책 고지
    doc.add_paragraph("")
    doc.add_paragraph("─" * 50)
    disclaimer = doc.add_paragraph(
        "본 검토의견서는 AI가 생성한 초안이며, 법적 효력이 없습니다. "
        "반드시 법무팀의 최종 검토를 거쳐 의사결정에 활용하시기 바랍니다."
    )
    disclaimer.runs[0].font.size = Pt(8)
    disclaimer.runs[0].font.color.rgb = RGBColor(128, 128, 128)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()

# ── Streamlit UI ─────────────────────────────────────────────
def main():
    st.set_page_config(page_title="공정거래 법무 AI v2.0", page_icon="⚖", layout="wide")

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

    # ── 비밀번호 게이트 ──────────────────────────────────────
    app_pw = get_secret("APP_PASSWORD")
    if app_pw:
        if "authenticated" not in st.session_state:
            st.session_state.authenticated = False
        if not st.session_state.authenticated:
            st.markdown("## ⚖ 공정거래 법무 AI")
            st.caption("접근이 제한된 시스템입니다.")
            pw = st.text_input("비밀번호를 입력하세요", type="password")
            if pw:
                if pw == app_pw:
                    st.session_state.authenticated = True
                    st.rerun()
                else:
                    st.error("비밀번호가 올바르지 않습니다.")
            st.stop()

    # ── 상태 초기화 ──────────────────────────────────────────
    if "docs" not in st.session_state:
        st.session_state.docs = load_docs()
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "sessions" not in st.session_state:
        st.session_state.sessions = load_sessions()
    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = None
    if "laws_db" not in st.session_state:
        st.session_state.laws_db = load_laws()

    # 90일 초과 세션 정리 (앱 시작 시 1회)
    if "cleanup_done" not in st.session_state:
        cleanup_old_sessions(90)
        st.session_state.cleanup_done = True

    # ── 사이드바 ─────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## ⚖ 공정거래 법무 AI v2.0")
        st.caption("면세점 MD 바이어 전용 · Gemini + Claude")
        st.divider()

        # 법령 DB 상태
        law_count = len(st.session_state.laws_db)
        if law_count > 0:
            st.success(f"📚 법령 DB: {law_count}개 조문 적재됨")
        else:
            st.warning("📚 법령 DB 미설정 — Supabase에 laws 테이블을 생성하세요")

        with st.expander("📘 사내 표준 조항 (Playbook)", expanded=False):
            st.caption("우측 상단의 복사 아이콘을 눌러 실무에 바로 적용하세요.")
            for title, text in PLAYBOOK.items():
                st.markdown(f"**{title}**")
                st.code(text, language="text")

        st.divider()
        st.markdown("### 📂 기준 규칙 DB 등록")
        st.caption("※ 이곳에 등록된 문서는 검토 기준(정답)으로 사용됩니다.")
        doc_cat = st.selectbox("문서 유형", options=list(DOC_CATS.keys()),
                               format_func=lambda x: DOC_CATS[x]["icon"] + " " + DOC_CATS[x]["label"])
        contract_type = st.selectbox("거래 유형", CONTRACT_TYPES) if doc_cat == "contract" else None
        yakjeong_type = st.selectbox("약정서 유형", YAKJEONG_TYPES) if doc_cat == "yakjeong" else None

        uploaded_files = st.file_uploader("Word 파일 첨부", type=["docx"],
                                          accept_multiple_files=True, label_visibility="collapsed")
        if uploaded_files:
            if st.button("DB에 규칙 등록", use_container_width=True, type="primary"):
                for f in uploaded_files:
                    import uuid
                    label = f"계약서({contract_type})" if contract_type else \
                            f"약정서({yakjeong_type})" if yakjeong_type else DOC_CATS[doc_cat]["label"]
                    label += f": {f.name}"
                    new_doc = {
                        "id": str(uuid.uuid4()),
                        "name": f.name, "cat": doc_cat,
                        "contract_type": contract_type or yakjeong_type,
                        "label": label,
                        "text": extract_text(f.read()),
                        "size": f.size,
                    }
                    if save_doc(new_doc):
                        st.session_state.docs.append(new_doc)
                st.rerun()

        if st.session_state.docs:
            st.divider()
            st.markdown("### 📋 적용 중인 기준 문서")
            for cat_id, cat_info in DOC_CATS.items():
                cat_docs = [d for d in st.session_state.docs if d["cat"] == cat_id]
                if not cat_docs:
                    continue
                st.markdown(f"**{cat_info['icon']} {cat_info['label']}**")
                for doc in cat_docs:
                    col1, col2 = st.columns([5, 1])
                    with col1:
                        st.caption("📎 " + doc["name"])
                    with col2:
                        if st.button("X", key="del_" + doc["id"]):
                            if delete_doc(doc["id"]):
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
                if st.button(sess["title"], key="sess_" + sess["id"], use_container_width=True):
                    st.session_state.messages = sess["messages"]
                    st.session_state.current_session_id = sess["id"]
                    st.rerun()
            with col2:
                if st.button("🗑", key="delsess_" + sess["id"]):
                    if delete_session_db(sess["id"]):
                        st.session_state.sessions = [s for s in st.session_state.sessions if s["id"] != sess["id"]]
                        st.rerun()

    # ── 메인 영역 ────────────────────────────────────────────
    st.title("⚖ 공정거래 법무 자문 v2.0")

    if not st.session_state.messages and st.session_state.docs:
        st.markdown("**자주 묻는 질문**")
        samples = [
            ("📋 계약 검토", "협력사에서 보내온 다음 특약매입 계약서 조항이 당사 사규에 맞는지 확인해줘: [여기에 협력사 조항 붙여넣기]"),
            ("🤝 약정 검토", "첨부한 파견 약정서(협력사 회신본) 내용 중 법 위반 소지가 있는지 검토해줘."),
            ("🧑‍💼 법률 질의", "협력사가 반품 기한을 60일로 연장해달라는데, 대규모유통업법과 당사 계약서 기준으로 수용 가능한가요?")
        ]
        cols = st.columns(3)
        for i, (cat, q) in enumerate(samples):
            with cols[i]:
                if st.button(cat, key="sample_" + str(i), use_container_width=True, help=q):
                    st.session_state["pending_input"] = q
                    st.rerun()

    # 대화 히스토리 렌더링
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"], avatar="⚖" if msg["role"] == "assistant" else "👤"):
            # 구조화된 검토 결과가 있으면 리치 UI로 표시
            if msg["role"] == "assistant" and msg.get("json_data"):
                jd = msg["json_data"]
                render_verdict_badge(jd.get("verdict", ""))
                st.markdown(f"**📋 {jd.get('summary', '')}**")
                render_issues_table(jd.get("issues", []), msg.get("citation_results", []))
                if jd.get("alternative_clause"):
                    render_alternative_clause(jd["alternative_clause"])
                with st.expander("📄 상세 검토 의견 전문", expanded=False):
                    st.markdown(msg.get("detail_text", msg["content"]))
                # 검토의견서 다운로드
                if jd.get("verdict"):
                    docx_bytes = generate_review_docx(jd, msg.get("detail_text", ""), "")
                    st.download_button(
                        "📥 검토의견서 다운로드 (.docx)",
                        data=docx_bytes,
                        file_name=f"검토의견서_{datetime.now().strftime('%Y%m%d_%H%M')}.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key=f"dl_{msg.get('msg_id', datetime.now().timestamp())}"
                    )
            else:
                st.markdown(msg["content"])

            if "time" in msg and msg["time"]:
                model_label = msg.get("model", "")
                st.caption(f"⏱️ {msg['time']:.1f}초 · {model_label}")

    # ── 입력 처리 ────────────────────────────────────────────
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
                    v1_bytes = v1_file.read()
                    v2_bytes = v2_file.read()
                    prompt = (
                        f"당사가 보낸 초안(V1)과 협력사가 회신한 수정본(V2)을 교차 비교해주세요.\n\n"
                        f"1. 협력사가 어느 조항을 어떻게 변경/추가/삭제했는지 핵심만 대조해주세요.\n"
                        f"2. 수정본(V2)의 내용이 DB의 [기준 문서]와 [법령]을 위반하는지 엄격히 심사해주세요.\n\n"
                        f"[V1 당사 초안 내용]\n{extract_text(v1_bytes)}\n\n"
                        f"[V2 협력사 수정본 내용]\n{extract_text(v2_bytes)}"
                    )
                    st.session_state["pending_input"] = prompt
                    st.rerun()

        chat_files = st.file_uploader(
            "📎 검토할 파일 첨부 (협력사 회신본 등)", type=["docx"],
            accept_multiple_files=True, key="chat_uploader"
        )
        user_input = st.chat_input("검토할 텍스트를 입력하거나 파일을 첨부하세요...")
        query = user_input or st.session_state.pop("pending_input", None)

        if query:
            attached_texts = []
            if chat_files:
                for f in chat_files:
                    f.seek(0)
                    text = extract_text(f.read())
                    attached_texts.append(f"=== 검토 대상 첨부 파일: {f.name} ===\n" + text)

            has_attachment = bool(attached_texts)

            if attached_texts:
                full_query = f"[사용자 문의사항]\n{query}\n\n[검토 대상 텍스트/첨부파일]\n" + "\n\n".join(attached_texts)
                display_query = query + "\n\n📎 " + ", ".join(f.name for f in chat_files)
            else:
                # 개선된 판별 로직: 실제 검토 대상 텍스트가 포함되어 있는지 확인
                has_review_content = len(query) > 80 or "조" in query or "항" in query or ":" in query
                if any(kw in query for kw in ["검토", "확인", "분석"]) and not has_review_content:
                    full_query = f"[사용자 문의사항]\n{query}\n\n[검토 대상 텍스트/첨부파일]\n(없음 - 첨부파일이나 텍스트가 제공되지 않았습니다.)"
                else:
                    full_query = f"[사용자 문의사항 및 검토 대상 텍스트]\n{query}\n\n[첨부파일]\n(없음)"
                display_query = query

            st.session_state.messages.append({"role": "user", "content": full_query})
            with st.chat_message("user", avatar="👤"):
                st.markdown(display_query)

            # 라우팅
            model_choice = route_query(query, has_attachment)

            with st.chat_message("assistant", avatar="⚖"):
                if model_choice == "claude":
                    spinner_msg = "⚖ Claude가 법령 및 사규 기준으로 검토 중..."
                else:
                    spinner_msg = "💬 Gemini가 답변 생성 중..."

                with st.spinner(spinner_msg):
                    start_time = time.time()
                    if model_choice == "claude":
                        system = build_system_claude(st.session_state.docs, st.session_state.laws_db)
                        reply = call_claude(system, st.session_state.messages)
                    else:
                        system = build_system_gemini(st.session_state.docs)
                        reply = call_gemini(system, st.session_state.messages)
                    elapsed = time.time() - start_time

                # 응답 처리
                msg_data = {
                    "role": "assistant",
                    "content": reply,
                    "time": elapsed,
                    "model": "Claude 3.5 Sonnet" if model_choice == "claude" else "Gemini",  # 📌 모델명 표기 수정
                    "msg_id": str(datetime.now().timestamp()),
                }

                if model_choice == "claude":
                    json_data, detail_text = parse_review_response(reply)
                    if json_data:
                        # 법령 인용 검증
                        citation_results = verify_citations(
                            json_data.get("cited_laws", []),
                            st.session_state.laws_db
                        )
                        msg_data["json_data"] = json_data
                        msg_data["detail_text"] = detail_text
                        msg_data["citation_results"] = citation_results

                        # 리치 UI 렌더링
                        render_verdict_badge(json_data.get("verdict", ""))
                        st.markdown(f"**📋 {json_data.get('summary', '')}**")
                        render_issues_table(json_data.get("issues", []), citation_results)
                        if json_data.get("alternative_clause"):
                            render_alternative_clause(json_data["alternative_clause"])
                        with st.expander("📄 상세 검토 의견 전문", expanded=False):
                            st.markdown(detail_text)

                        # 검토의견서 다운로드
                        docx_bytes = generate_review_docx(json_data, detail_text, display_query)
                        st.download_button(
                            "📥 검토의견서 다운로드 (.docx)",
                            data=docx_bytes,
                            file_name=f"검토의견서_{datetime.now().strftime('%Y%m%d_%H%M')}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )

                        # 검토 이력 저장
                        save_review_log({
                            "id": msg_data["msg_id"],
                            "session_id": st.session_state.current_session_id,
                            "verdict": json_data.get("verdict", "unknown"),
                            "issues": json_data.get("issues"),
                            "action_plan": json_data.get("action_plan"),
                            "cited_laws": json_data.get("cited_laws"),
                            "citation_verified": all(cr["verified"] for cr in citation_results) if citation_results else False,
                        })
                    else:
                        # JSON 파싱 실패 → 일반 텍스트로 표시
                        st.markdown(reply)
                else:
                    st.markdown(reply)

                model_label = msg_data["model"]
                st.caption(f"⏱️ {elapsed:.1f}초 · {model_label}")

                st.session_state.messages.append(msg_data)

            # 세션 저장
            import uuid
            new_id = st.session_state.current_session_id or str(uuid.uuid4())
            current_sess = {
                "id": new_id,
                "title": display_query[:25] + "...",
                "date": datetime.now().isoformat(),
                "messages": st.session_state.messages,
            }
            if save_session(current_sess):
                st.session_state.current_session_id = new_id
                # 세션 목록 갱신
                existing = [s for s in st.session_state.sessions if s["id"] != new_id]
                st.session_state.sessions =
