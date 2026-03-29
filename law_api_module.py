"""
법제처 Open API 연동 모듈 v1.4
========================
v1.4 변경사항:
  - 법령 조문: 법제처 원문 링크 추가 + 별표/서식 안내
  - 판례 전문: AI 요약 (결론→배경→핵심이유→실무시사점) + 원문
  - 해석례 전문: AI 요약 (결론→Q&A→핵심이유→실무의미) + 원문
  - Gemini API를 활용한 요약 생성
"""

import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
from typing import Optional
import logging
import streamlit as st

logger = logging.getLogger(__name__)

def get_oc():
    try:
        return st.secrets["LAW_OC"]
    except Exception:
        import os
        return os.environ.get("LAW_OC", "")

BASE_URL = "http://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "http://www.law.go.kr/DRF/lawService.do"

ABBREVIATIONS = {
    "표시광고법": "표시광고", "전상법": "전자상거래", "관세법": "관세법",
    "외환법": "외국환거래법", "관광진흥법": "관광진흥법", "개인정보보호법": "개인정보보호법",
    "공정거래법": "공정거래", "소비자기본법": "소비자기본법", "화장품법": "화장품법",
    "식품위생법": "식품위생법", "약사법": "약사법", "주세법": "주세법",
    "담배사업법": "담배사업법", "대규모유통업법": "대규모유통업", "하도급법": "하도급",
}

def _resolve_keyword(keyword):
    if keyword in ABBREVIATIONS:
        return ABBREVIATIONS[keyword]
    for abbr, resolved in ABBREVIATIONS.items():
        if abbr in keyword:
            remaining = keyword.replace(abbr, "").strip()
            return f"{resolved} {remaining}" if remaining else resolved
    return keyword

def _make_request(base_url, params_dict):
    param_parts = []
    for key, value in params_dict.items():
        if value is not None:
            param_parts.append(f"{key}={quote(str(value), safe='')}")
    full_url = base_url + "?" + "&".join(param_parts)
    logger.info(f"법제처 API 요청: {full_url}")
    try:
        res = requests.get(full_url, timeout=15)
        if res.encoding and 'euc' in res.encoding.lower():
            res.encoding = 'euc-kr'
        elif res.apparent_encoding:
            res.encoding = res.apparent_encoding
        content = res.text
        if not content or len(content.strip()) < 10:
            return None, f"빈 응답 (HTTP {res.status_code})"
        try:
            return ET.fromstring(content), None
        except ET.ParseError as e:
            logger.error(f"XML 파싱 실패: {e}")
            return None, "XML 파싱 실패"
    except requests.Timeout:
        return None, "요청 시간 초과"
    except requests.ConnectionError:
        return None, "서버 연결 실패"
    except Exception as e:
        return None, f"요청 실패: {type(e).__name__}"


# ──────────────────────────────────────────────
# AI 요약 생성 (Gemini 활용)
# ──────────────────────────────────────────────
def _summarize_with_ai(prompt_text, max_chars=3000):
    """Gemini API로 요약 생성. 실패 시 None 반환."""
    try:
        from google import genai
        from google.genai import types
        
        try:
            api_key = st.secrets["GEMINI_API_KEY"]
        except Exception:
            import os
            api_key = os.environ.get("GEMINI_API_KEY", "")
        
        if not api_key:
            return None
        
        client = genai.Client(api_key=api_key)
        
        # 입력 텍스트가 너무 길면 자르기
        if len(prompt_text) > 8000:
            prompt_text = prompt_text[:8000] + "\n...(이하 생략)"
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[types.Content(role="user", parts=[types.Part(text=prompt_text)])],
            config=types.GenerateContentConfig(
                system_instruction="당신은 법률 문서를 일반인이 이해하기 쉽게 요약하는 전문가입니다. 마크다운 형식으로 깔끔하게 작성하세요. 이모지를 적절히 활용하세요.",
            ),
        )
        return response.text
    except Exception as e:
        logger.warning(f"AI 요약 생성 실패: {e}")
        return None


def _summarize_precedent(detail):
    """판례를 '결론→배경→핵심이유→실무시사점' 구조로 요약"""
    parts = []
    if detail.get("사건명"): parts.append(f"사건명: {detail['사건명']}")
    if detail.get("사건번호"): parts.append(f"사건번호: {detail['사건번호']}")
    if detail.get("선고일자"): parts.append(f"선고일자: {detail['선고일자']}")
    if detail.get("법원명"): parts.append(f"법원: {detail['법원명']}")
    if detail.get("판시사항"): parts.append(f"판시사항:\n{detail['판시사항']}")
    if detail.get("판결요지"): parts.append(f"판결요지:\n{detail['판결요지']}")
    if detail.get("참조조문"): parts.append(f"참조조문: {detail['참조조문']}")
    if detail.get("판례내용"): parts.append(f"판례내용:\n{detail['판례내용'][:3000]}")
    
    if not parts:
        return None
    
    raw_text = "\n\n".join(parts)
    
    prompt = f"""아래 판례를 일반인(면세점 MD, 바이어)이 이해하기 쉽게 요약해주세요.

반드시 아래 4단계 구조로 작성하세요:

### 📌 결론
- 법원이 최종적으로 어떤 판단을 내렸는지 한두 문장으로 명확하게

### 📋 배경
- 이 사건이 왜 발생했는지, 당사자 간 분쟁의 핵심이 무엇인지 쉬운 말로

### 💡 핵심 이유
- 법원이 그렇게 판단한 주요 근거를 번호 매겨 3개 이내로

### 🏢 실무적 시사점
- 면세점 MD/바이어가 이 판례에서 챙겨야 할 실무 포인트

---
[판례 원문]
{raw_text}"""
    
    return _summarize_with_ai(prompt)


def _summarize_interpretation(detail):
    """해석례를 '결론(두괄식)→Q&A→핵심이유→실무의미' 구조로 요약"""
    parts = []
    if detail.get("안건명"): parts.append(f"안건명: {detail['안건명']}")
    if detail.get("안건번호"): parts.append(f"안건번호: {detail['안건번호']}")
    if detail.get("회답일자"): parts.append(f"회답일자: {detail['회답일자']}")
    if detail.get("회답기관"): parts.append(f"회답기관: {detail['회답기관']}")
    if detail.get("질의요지"): parts.append(f"질의요지:\n{detail['질의요지']}")
    if detail.get("회답"): parts.append(f"회답:\n{detail['회답']}")
    if detail.get("이유"): parts.append(f"이유:\n{detail['이유']}")
    if detail.get("참조조문"): parts.append(f"참조조문: {detail['참조조문']}")
    
    if not parts:
        return None
    
    raw_text = "\n\n".join(parts)
    
    prompt = f"""아래 법령해석례를 일반인(면세점 MD, 바이어)이 이해하기 쉽게 요약해주세요.

반드시 아래 4단계 구조로 작성하세요:

### 📌 결론 (한줄 요약)
- 법제처가 내린 결론을 한두 문장으로 두괄식 제시

### ❓ 쉽게 풀어쓴 Q&A
- **Q:** 질의 내용을 일상 언어로 바꿔서 질문 형태로
- **A:** 회답 내용을 일상 언어로 바꿔서 답변 형태로

### 💡 핵심 이유
- 법제처가 그렇게 판단한 주요 근거를 번호 매겨 3개 이내로

### 🏢 실무적 의미
- 면세점 MD/바이어 업무에서 이 해석례가 어떤 의미가 있는지

---
[해석례 원문]
{raw_text}"""
    
    return _summarize_with_ai(prompt)


# ──────────────────────────────────────────────
# 법제처 원문 링크 생성
# ──────────────────────────────────────────────
def _build_law_link(mst, oc):
    """법제처 원문 HTML 링크 생성"""
    return f"https://www.law.go.kr/DRF/lawService.do?OC={oc}&target=law&MST={mst}&type=HTML"

def _build_law_go_kr_link(law_name):
    """국가법령정보센터 검색 링크 생성"""
    encoded = quote(law_name, safe='')
    return f"https://www.law.go.kr/법령/{encoded}"


class LawAPI:
    def __init__(self, oc: Optional[str] = None):
        self.oc = oc or get_oc()
        if not self.oc:
            raise ValueError("LAW_OC 값이 설정되지 않았습니다.")

    def search_law(self, keyword: str, display: int = 5) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        params = {"OC": self.oc, "target": "law", "type": "XML", "query": resolved, "display": str(display)}
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]
        results = []
        for item in tree.iter():
            law_name = item.findtext("법령명한글")
            if law_name:
                detail_link = item.findtext("법령상세링크", "")
                mst = ""
                if "MST=" in detail_link:
                    mst = detail_link.split("MST=")[1].split("&")[0]
                if not mst:
                    mst = item.findtext("법령일련번호", "")
                results.append({
                    "법령명": law_name, "법령ID": item.findtext("법령일련번호", ""),
                    "MST": mst, "시행일자": item.findtext("시행일자", ""),
                    "법령종류": item.findtext("법령종류", ""),
                    "소관부처": item.findtext("소관부처", ""),
                    "상세링크": detail_link,
                })
        return results

    def get_law_text(self, mst: str, jo: Optional[str] = None) -> dict:
        params = {"OC": self.oc, "target": "law", "type": "XML", "MST": mst}
        if jo: params["JO"] = jo
        tree, error = _make_request(DETAIL_URL, params)
        if error:
            return {"error": f"API 호출 실패: {error}"}
        law_name = tree.findtext(".//법령명_한글", "") or tree.findtext(".//법령명한글", "")
        articles = []
        for article in tree.iter("조문단위"):
            content = article.findtext("조문내용", "")
            if content:
                articles.append({
                    "조문번호": article.findtext("조문번호", ""),
                    "조문제목": article.findtext("조문제목", ""),
                    "조문내용": content,
                    "조문시행일자": article.findtext("조문시행일자", ""),
                })
        if not articles and not law_name:
            return {"error": "조문을 파싱하지 못했습니다."}
        return {"법령명": law_name, "시행일자": tree.findtext(".//시행일자", ""), "조문목록": articles}

    def search_precedent(self, keyword: str, display: int = 10) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        params = {"OC": self.oc, "target": "prec", "type": "XML", "query": resolved, "display": str(display)}
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]
        results = []
        for item in tree.iter():
            case_name = item.findtext("사건명")
            if case_name:
                results.append({
                    "판례ID": item.findtext("판례일련번호", ""),
                    "사건명": case_name, "사건번호": item.findtext("사건번호", ""),
                    "선고일자": item.findtext("선고일자", ""),
                    "법원명": item.findtext("법원명", ""),
                    "사건종류": item.findtext("사건종류명", ""),
                    "판결유형": item.findtext("판결유형", ""),
                    "상세링크": item.findtext("판례상세링크", ""),
                })
        return results

    def get_precedent_detail(self, prec_id: str) -> dict:
        params = {"OC": self.oc, "target": "prec", "type": "XML", "ID": prec_id}
        tree, error = _make_request(DETAIL_URL, params)
        if error:
            return {"error": f"API 호출 실패: {error}"}
        return {
            "사건명": tree.findtext(".//사건명", ""), "사건번호": tree.findtext(".//사건번호", ""),
            "선고일자": tree.findtext(".//선고일자", ""), "법원명": tree.findtext(".//법원명", ""),
            "판시사항": tree.findtext(".//판시사항", ""), "판결요지": tree.findtext(".//판결요지", ""),
            "참조조문": tree.findtext(".//참조조문", ""), "참조판례": tree.findtext(".//참조판례", ""),
            "판례내용": tree.findtext(".//판례내용", ""),
        }

    def search_interpretation(self, keyword: str, display: int = 10) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        params = {"OC": self.oc, "target": "expc", "type": "XML", "query": resolved, "display": str(display)}
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]
        results = []
        for item in tree.iter():
            title = item.findtext("안건명")
            if title:
                detail_link = item.findtext("법령해석례상세링크", "")
                interp_id = item.findtext("법령해석례일련번호", "")
                if not interp_id and "ID=" in detail_link:
                    interp_id = detail_link.split("ID=")[1].split("&")[0]
                results.append({
                    "해석례ID": interp_id, "안건명": title,
                    "안건번호": item.findtext("안건번호", ""),
                    "회답일자": item.findtext("회답일자", ""),
                    "회답기관": item.findtext("회답기관", ""),
                    "상세링크": detail_link,
                })
        return results

    def get_interpretation_detail(self, interp_id: str) -> dict:
        params = {"OC": self.oc, "target": "expc", "type": "XML", "ID": interp_id}
        tree, error = _make_request(DETAIL_URL, params)
        if error:
            return {"error": f"API 호출 실패: {error}"}
        return {
            "안건명": tree.findtext(".//안건명", ""), "안건번호": tree.findtext(".//안건번호", ""),
            "회답일자": tree.findtext(".//회답일자", ""), "회답기관": tree.findtext(".//회답기관", ""),
            "질의요지": tree.findtext(".//질의요지", ""), "회답": tree.findtext(".//회답", ""),
            "이유": tree.findtext(".//이유", ""), "참조조문": tree.findtext(".//참조조문", ""),
        }

    def search_admin_rule(self, keyword: str, display: int = 10) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        params = {"OC": self.oc, "target": "admrul", "type": "XML", "query": resolved, "display": str(display)}
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]
        results = []
        for item in tree.iter():
            name = item.findtext("행정규칙명")
            if name:
                results.append({
                    "행정규칙명": name, "행정규칙ID": item.findtext("행정규칙일련번호", ""),
                    "시행일자": item.findtext("시행일자", ""), "발령기관": item.findtext("발령기관", ""),
                    "행정규칙종류": item.findtext("행정규칙종류", ""),
                })
        return results

    @staticmethod
    def jo_to_code(jo_num: int) -> str:
        return f"{jo_num:04d}00"

    @staticmethod
    def code_to_jo(code: str) -> int:
        try: return int(code[:4])
        except (ValueError, IndexError): return 0

    def build_ai_context(self, keyword, include_law=True, include_precedent=False,
                         include_interpretation=False, max_articles=10) -> str:
        context_parts = []
        if include_law:
            laws = self.search_law(keyword, display=3)
            for law in laws[:2]:
                if "error" in law: continue
                mst = law.get("MST", "")
                if mst:
                    detail = self.get_law_text(mst)
                    if "error" not in detail and detail.get("조문목록"):
                        context_parts.append(f"\n=== {detail['법령명']} (시행 {detail['시행일자']}) ===")
                        for art in detail["조문목록"][:max_articles]:
                            title, content, num = art.get("조문제목", ""), art.get("조문내용", ""), art.get("조문번호", "")
                            if content:
                                header = f"제{LawAPI.code_to_jo(num)}조" if num and len(num) >= 4 else num
                                if title: header += f"({title})"
                                context_parts.append(f"\n{header}\n{content}")
        if include_precedent:
            precs = self.search_precedent(keyword, display=5)
            valid = [p for p in precs if "error" not in p]
            if valid:
                context_parts.append("\n\n=== 관련 판례 ===")
                for p in valid[:3]:
                    context_parts.append(f"\n[{p['사건번호']}] {p['사건명']} ({p['선고일자']}, {p['법원명']})")
        if include_interpretation:
            interps = self.search_interpretation(keyword, display=5)
            valid = [i for i in interps if "error" not in i]
            if valid:
                context_parts.append("\n\n=== 관련 법령해석례 ===")
                for i in valid[:3]:
                    context_parts.append(f"\n[{i['안건번호']}] {i['안건명']} ({i['회답일자']}, {i['회답기관']})")
        if not context_parts:
            return f"'{keyword}'에 대한 법령 정보를 찾지 못했습니다."
        return "\n".join(context_parts)


# ──────────────────────────────────────────────
# UI: 사이드바 검색 위젯
# ──────────────────────────────────────────────
def render_law_search_sidebar():
    with st.sidebar:
        st.markdown("---")
        st.subheader("📖 법령 실시간 검색")
        law_query = st.text_input("법령 검색어", placeholder="예: 표시광고법, 대규모유통업법, 관세법", key="law_search_input")
        col1, col2, col3 = st.columns(3)
        with col1: inc_law = st.checkbox("법령", value=True, key="inc_law")
        with col2: inc_prec = st.checkbox("판례", value=False, key="inc_prec")
        with col3: inc_interp = st.checkbox("해석례", value=False, key="inc_interp")

        if st.button("🔍 검색", key="law_search_btn") and law_query:
            try:
                api = LawAPI()
                with st.spinner("법제처 API 조회 중..."):
                    law_list = api.search_law(law_query, display=5) if inc_law else []
                    prec_list = api.search_precedent(law_query, display=5) if inc_prec else []
                    interp_list = api.search_interpretation(law_query, display=5) if inc_interp else []
                    context = api.build_ai_context(law_query, include_law=inc_law, include_precedent=inc_prec, include_interpretation=inc_interp)
                st.session_state["law_context"] = context
                st.session_state["law_search_results"] = {
                    "query": law_query, "resolved": _resolve_keyword(law_query),
                    "laws": [l for l in law_list if "error" not in l],
                    "precedents": [p for p in prec_list if "error" not in p],
                    "interpretations": [i for i in interp_list if "error" not in i],
                    "has_results": "찾지 못했습니다" not in context,
                }
                if "찾지 못했습니다" in context:
                    st.warning("검색 결과 없음")
                    st.caption(f"💡 '{law_query}' → '{_resolve_keyword(law_query)}'로 검색됨")
                else:
                    st.success("✅ 검색 완료 — 메인 화면 확인")
            except Exception as e:
                st.error(f"검색 실패: {str(e)}")

        if st.session_state.get("law_search_results", {}).get("has_results"):
            sr = st.session_state["law_search_results"]
            counts = []
            if sr["laws"]: counts.append(f"법령 {len(sr['laws'])}건")
            if sr["precedents"]: counts.append(f"판례 {len(sr['precedents'])}건")
            if sr["interpretations"]: counts.append(f"해석례 {len(sr['interpretations'])}건")
            if counts:
                st.caption(f"📊 {', '.join(counts)}")
                st.caption("💡 AI 검토 시 자동 참조됩니다.")
            if st.button("🗑 검색 결과 초기화", key="clear_law_results"):
                st.session_state.pop("law_search_results", None)
                st.session_state.pop("law_context", None)
                st.rerun()


# ──────────────────────────────────────────────
# UI: 메인 영역 검색 결과 표시
# ──────────────────────────────────────────────
def render_law_search_results():
    sr = st.session_state.get("law_search_results")
    if not sr or not sr.get("has_results"):
        return

    st.markdown("---")
    query_display = sr['query']
    resolved_display = sr.get('resolved', query_display)
    if query_display != resolved_display:
        st.markdown(f"### 📖 법령 검색 결과: '{query_display}' → '{resolved_display}'")
    else:
        st.markdown(f"### 📖 법령 검색 결과: '{query_display}'")
    st.caption("법제처 국가법령정보센터 실시간 조회 | AI 검토 시 자동 참조됩니다")

    tab_names = []
    if sr["laws"]: tab_names.append(f"⚖️ 법령 ({len(sr['laws'])})")
    if sr["precedents"]: tab_names.append(f"📚 판례 ({len(sr['precedents'])})")
    if sr["interpretations"]: tab_names.append(f"📋 해석례 ({len(sr['interpretations'])})")
    if not tab_names: return

    tabs = st.tabs(tab_names)
    tab_idx = 0

    # ── 법령 탭 ──
    if sr["laws"]:
        with tabs[tab_idx]:
            for i, law in enumerate(sr["laws"]):
                with st.expander(
                    f"**{law['법령명']}** ({law.get('법령종류', '')}) — 시행 {law.get('시행일자', '')}",
                    expanded=(i == 0)
                ):
                    col1, col2, col3 = st.columns(3)
                    with col1: st.caption(f"📂 {law.get('법령종류', '')}")
                    with col2: st.caption(f"🏛 {law.get('소관부처', '')}")
                    with col3: st.caption(f"📅 시행 {law.get('시행일자', '')}")

                    mst = law.get("MST", "")
                    law_name = law.get("법령명", "")

                    # 법제처 원문 링크 (별표/서식 포함)
                    if mst:
                        try:
                            oc = get_oc()
                            html_link = _build_law_link(mst, oc)
                            search_link = _build_law_go_kr_link(law_name)
                            st.markdown(f"🔗 [**법제처에서 원문 보기** (별표·서식 포함)]({html_link})  |  [국가법령정보센터에서 검색]({search_link})")
                        except Exception:
                            pass

                    if mst:
                        cache_key = f"law_detail_{mst}"
                        if st.button(f"📄 조문 전문 보기", key=f"load_law_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("조문 로드 중..."):
                                    detail = api.get_law_text(mst)
                                if "error" not in detail and detail.get("조문목록"):
                                    st.session_state[cache_key] = detail
                                else:
                                    st.warning(f"⚠️ {detail.get('error', '조문을 불러오지 못했습니다.')}")
                            except Exception as e:
                                st.error(f"조문 로드 실패: {e}")

                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            st.markdown(f"**{detail['법령명']}** (시행 {detail['시행일자']}) — 총 {len(detail['조문목록'])}개 조문")
                            
                            # 별표/서식 안내
                            st.info("⚠️ **별표·별지서식 안내:** 법령에 포함된 별표, 서식, 표 등은 이미지로 구성되어 있어 텍스트로 표시되지 않을 수 있습니다. 위 '법제처에서 원문 보기' 링크에서 확인하세요.")
                            
                            for art in detail["조문목록"]:
                                num = art.get("조문번호", "")
                                title = art.get("조문제목", "")
                                content = art.get("조문내용", "")
                                if content:
                                    jo_num = LawAPI.code_to_jo(num) if num and len(num) >= 4 else num
                                    header = f"**제{jo_num}조**" if jo_num else ""
                                    if title: header += f" ({title})"
                                    st.markdown(header)
                                    st.markdown(content)
                                    st.markdown("---")
        tab_idx += 1

    # ── 판례 탭 ──
    if sr["precedents"]:
        with tabs[tab_idx]:
            for i, prec in enumerate(sr["precedents"]):
                with st.expander(
                    f"**[{prec.get('사건번호', '')}]** {prec['사건명'][:60]}",
                    expanded=(i == 0)
                ):
                    col1, col2, col3 = st.columns(3)
                    with col1: st.caption(f"🏛 {prec.get('법원명', '')}")
                    with col2: st.caption(f"📅 {prec.get('선고일자', '')}")
                    with col3: st.caption(f"📂 {prec.get('사건종류', '')}")

                    prec_id = prec.get("판례ID", "")
                    if prec_id:
                        cache_key = f"prec_detail_{prec_id}"
                        summary_key = f"prec_summary_{prec_id}"
                        
                        if st.button(f"📄 판례 전문 보기", key=f"load_prec_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("판례 로드 중..."):
                                    detail = api.get_precedent_detail(prec_id)
                                if "error" not in detail:
                                    st.session_state[cache_key] = detail
                                    # AI 요약 생성
                                    with st.spinner("🤖 AI가 판례를 쉽게 요약하는 중..."):
                                        summary = _summarize_precedent(detail)
                                        if summary:
                                            st.session_state[summary_key] = summary
                                else:
                                    st.warning("판례를 불러오지 못했습니다.")
                            except Exception as e:
                                st.error(f"판례 로드 실패: {e}")

                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            has_content = False
                            
                            # AI 요약 (있으면 먼저 표시)
                            if summary_key in st.session_state:
                                st.markdown("## 🤖 AI 요약")
                                st.markdown(st.session_state[summary_key])
                                st.markdown("---")
                                has_content = True
                            
                            # 원문 (접을 수 있게)
                            raw_parts = []
                            if detail.get("판시사항"): raw_parts.append(("📌 판시사항", detail["판시사항"]))
                            if detail.get("판결요지"): raw_parts.append(("📌 판결요지", detail["판결요지"]))
                            if detail.get("참조조문"): raw_parts.append(("⚖️ 참조조문", detail["참조조문"]))
                            if detail.get("판례내용"): raw_parts.append(("📄 판례 전문", detail["판례내용"]))
                            
                            if raw_parts:
                                with st.expander("📜 원문 전체 보기", expanded=False):
                                    for title, content in raw_parts:
                                        st.markdown(f"**{title}**")
                                        st.markdown(content[:5000])
                                        st.markdown("---")
                                has_content = True
                            
                            if not has_content:
                                st.info("이 판례는 법제처 API에서 상세 내용을 제공하지 않습니다.\n\n[국가법령정보센터](https://www.law.go.kr)에서 직접 검색해 보세요.")
        tab_idx += 1

    # ── 해석례 탭 ──
    if sr["interpretations"]:
        with tabs[tab_idx]:
            for i, interp in enumerate(sr["interpretations"]):
                with st.expander(
                    f"**[{interp.get('안건번호', '')}]** {interp['안건명'][:60]}",
                    expanded=(i == 0)
                ):
                    col1, col2 = st.columns(2)
                    with col1: st.caption(f"📅 {interp.get('회답일자', '')}")
                    with col2: st.caption(f"🏛 {interp.get('회답기관', '')}")

                    interp_id = interp.get("해석례ID", "")
                    if interp_id:
                        cache_key = f"interp_detail_{interp_id}"
                        summary_key = f"interp_summary_{interp_id}"
                        
                        if st.button(f"📄 해석례 전문 보기", key=f"load_interp_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("해석례 로드 중..."):
                                    detail = api.get_interpretation_detail(interp_id)
                                if "error" not in detail:
                                    st.session_state[cache_key] = detail
                                    # AI 요약 생성
                                    with st.spinner("🤖 AI가 해석례를 쉽게 요약하는 중..."):
                                        summary = _summarize_interpretation(detail)
                                        if summary:
                                            st.session_state[summary_key] = summary
                                else:
                                    st.warning("해석례를 불러오지 못했습니다.")
                            except Exception as e:
                                st.error(f"해석례 로드 실패: {e}")

                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            has_content = False
                            
                            # AI 요약 (있으면 먼저 표시)
                            if summary_key in st.session_state:
                                st.markdown("## 🤖 AI 요약")
                                st.markdown(st.session_state[summary_key])
                                st.markdown("---")
                                has_content = True
                            
                            # 원문 (접을 수 있게)
                            raw_parts = []
                            if detail.get("질의요지"): raw_parts.append(("❓ 질의요지", detail["질의요지"]))
                            if detail.get("회답"): raw_parts.append(("💬 회답", detail["회답"]))
                            if detail.get("이유"): raw_parts.append(("📝 이유", detail["이유"]))
                            if detail.get("참조조문"): raw_parts.append(("⚖️ 참조조문", detail["참조조문"]))
                            
                            if raw_parts:
                                with st.expander("📜 원문 전체 보기", expanded=False):
                                    for title, content in raw_parts:
                                        st.markdown(f"**{title}**")
                                        st.markdown(content[:5000])
                                        st.markdown("---")
                                has_content = True
                            
                            if not has_content:
                                st.info("이 해석례는 법제처 API에서 상세 내용을 제공하지 않습니다.\n\n[국가법령정보센터](https://www.law.go.kr)에서 직접 검색해 보세요.")
                    else:
                        st.caption("(상세 조회 ID 없음)")

    st.markdown("---")
