"""
법제처 Open API 연동 모듈 v1.2
========================
신세계면세점 법률 컴플라이언스 앱용

v1.2 변경사항:
  - 검색 위젯은 사이드바, 검색 결과는 메인 영역에 표시
  - render_law_search_sidebar()는 사이드바 입력만 담당
  - render_law_search_results()를 메인 영역에서 호출하여 결과 표시
"""

import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
from typing import Optional
import logging
import streamlit as st

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
def get_oc():
    try:
        return st.secrets["LAW_OC"]
    except Exception:
        import os
        return os.environ.get("LAW_OC", "")

BASE_URL = "http://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "http://www.law.go.kr/DRF/lawService.do"

ABBREVIATIONS = {
    "표시광고법": "표시광고",
    "전상법": "전자상거래",
    "관세법": "관세법",
    "외환법": "외국환거래법",
    "관광진흥법": "관광진흥법",
    "개인정보보호법": "개인정보보호법",
    "공정거래법": "공정거래",
    "소비자기본법": "소비자기본법",
    "화장품법": "화장품법",
    "식품위생법": "식품위생법",
    "약사법": "약사법",
    "주세법": "주세법",
    "담배사업법": "담배사업법",
    "대규모유통업법": "대규모유통업",
    "하도급법": "하도급",
}


def _make_request(base_url, params_dict):
    param_parts = []
    for key, value in params_dict.items():
        if value is not None:
            encoded_value = quote(str(value), safe='')
            param_parts.append(f"{key}={encoded_value}")
    
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
            tree = ET.fromstring(content)
            return tree, None
        except ET.ParseError as e:
            preview = content[:500].replace('\n', ' ')
            logger.error(f"XML 파싱 실패: {e}\n응답 미리보기: {preview}")
            return None, f"XML 파싱 실패: {str(e)[:100]}"
            
    except requests.Timeout:
        return None, "요청 시간 초과 (15초)"
    except requests.ConnectionError:
        return None, "서버 연결 실패"
    except Exception as e:
        return None, f"요청 실패: {type(e).__name__}: {str(e)[:100]}"


class LawAPI:
    def __init__(self, oc: Optional[str] = None):
        self.oc = oc or get_oc()
        if not self.oc:
            raise ValueError("LAW_OC 값이 설정되지 않았습니다.")

    def search_law(self, keyword: str, display: int = 5) -> list[dict]:
        resolved = ABBREVIATIONS.get(keyword, keyword)
        params = {"OC": self.oc, "target": "law", "type": "XML", "query": resolved, "display": str(display)}
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]
        results = []
        for item in tree.iter():
            law_name = item.findtext("법령명한글")
            if law_name:
                results.append({
                    "법령명": law_name,
                    "법령ID": item.findtext("법령일련번호", ""),
                    "MST": item.findtext("법령MST", "") or item.findtext("법령일련번호", ""),
                    "시행일자": item.findtext("시행일자", ""),
                    "법령종류": item.findtext("법령종류", ""),
                    "소관부처": item.findtext("소관부처", ""),
                    "상세링크": item.findtext("법령상세링크", ""),
                })
        return results

    def get_law_text(self, law_id: str, jo: Optional[str] = None) -> dict:
        params = {"OC": self.oc, "target": "law", "type": "XML", "ID": law_id}
        if jo:
            params["JO"] = jo
        tree, error = _make_request(DETAIL_URL, params)
        if error:
            return {"error": f"API 호출 실패: {error}"}
        law_name = tree.findtext(".//법령명_한글", "") or tree.findtext(".//법령명한글", "")
        articles = []
        for article in tree.iter("조문단위"):
            articles.append({
                "조문번호": article.findtext("조문번호", ""),
                "조문제목": article.findtext("조문제목", ""),
                "조문내용": article.findtext("조문내용", ""),
                "조문시행일자": article.findtext("조문시행일자", ""),
            })
        return {"법령명": law_name, "시행일자": tree.findtext(".//시행일자", ""), "조문목록": articles}

    def search_precedent(self, keyword: str, display: int = 10) -> list[dict]:
        params = {"OC": self.oc, "target": "prec", "type": "XML", "query": keyword, "display": str(display)}
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]
        results = []
        for item in tree.iter():
            case_name = item.findtext("사건명")
            if case_name:
                results.append({
                    "판례ID": item.findtext("판례일련번호", ""),
                    "사건명": case_name,
                    "사건번호": item.findtext("사건번호", ""),
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
        params = {"OC": self.oc, "target": "expc", "type": "XML", "query": keyword, "display": str(display)}
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]
        results = []
        for item in tree.iter():
            title = item.findtext("안건명")
            if title:
                results.append({
                    "해석례ID": item.findtext("법령해석례일련번호", ""),
                    "안건명": title, "안건번호": item.findtext("안건번호", ""),
                    "회답일자": item.findtext("회답일자", ""),
                    "회답기관": item.findtext("회답기관", ""),
                    "상세링크": item.findtext("법령해석례상세링크", ""),
                })
        return results

    def search_admin_rule(self, keyword: str, display: int = 10) -> list[dict]:
        params = {"OC": self.oc, "target": "admrul", "type": "XML", "query": keyword, "display": str(display)}
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
        try:
            return int(code[:4])
        except (ValueError, IndexError):
            return 0

    def build_ai_context(self, keyword, include_law=True, include_precedent=False,
                         include_interpretation=False, max_articles=10) -> str:
        context_parts = []
        if include_law:
            laws = self.search_law(keyword, display=3)
            for law in laws[:2]:
                if "error" in law: continue
                law_id = law.get("MST") or law.get("법령ID", "")
                if law_id:
                    detail = self.get_law_text(law_id)
                    if "error" not in detail and detail.get("조문목록"):
                        context_parts.append(f"\n=== {detail['법령명']} (시행 {detail['시행일자']}) ===")
                        for art in detail["조문목록"][:max_articles]:
                            title = art.get("조문제목", "")
                            content = art.get("조문내용", "")
                            num = art.get("조문번호", "")
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
# UI: 사이드바 검색 위젯 (입력만)
# ──────────────────────────────────────────────
def render_law_search_sidebar():
    """사이드바에 검색 입력 위젯 배치. 검색 실행 시 결과를 session_state에 저장."""
    with st.sidebar:
        st.markdown("---")
        st.subheader("📖 법령 실시간 검색")

        law_query = st.text_input(
            "법령 검색어",
            placeholder="예: 표시광고법, 관세법",
            key="law_search_input",
        )

        col1, col2, col3 = st.columns(3)
        with col1: inc_law = st.checkbox("법령", value=True, key="inc_law")
        with col2: inc_prec = st.checkbox("판례", value=False, key="inc_prec")
        with col3: inc_interp = st.checkbox("해석례", value=False, key="inc_interp")

        if st.button("🔍 검색", key="law_search_btn") and law_query:
            try:
                api = LawAPI()
                with st.spinner("법제처 API 조회 중..."):
                    # 법령 목록 (메인 영역 카드 표시용)
                    law_list = api.search_law(law_query, display=5) if inc_law else []
                    prec_list = api.search_precedent(law_query, display=5) if inc_prec else []
                    interp_list = api.search_interpretation(law_query, display=5) if inc_interp else []
                    
                    # AI 컨텍스트 (프롬프트 주입용)
                    context = api.build_ai_context(
                        law_query,
                        include_law=inc_law,
                        include_precedent=inc_prec,
                        include_interpretation=inc_interp,
                    )
                
                # 결과를 session_state에 저장 → 메인 영역에서 렌더링
                st.session_state["law_context"] = context
                st.session_state["law_search_results"] = {
                    "query": law_query,
                    "laws": [l for l in law_list if "error" not in l],
                    "precedents": [p for p in prec_list if "error" not in p],
                    "interpretations": [i for i in interp_list if "error" not in i],
                    "has_results": "찾지 못했습니다" not in context,
                }
                
                if "찾지 못했습니다" in context:
                    st.warning("검색 결과 없음")
                    st.caption("💡 짧은 키워드로 시도해 보세요.")
                else:
                    st.success("✅ 검색 완료 — 메인 화면에서 결과를 확인하세요.")
                    
            except Exception as e:
                st.error(f"검색 실패: {str(e)}")

        # 이전 검색 결과가 있으면 표시
        if st.session_state.get("law_search_results", {}).get("has_results"):
            sr = st.session_state["law_search_results"]
            counts = []
            if sr["laws"]: counts.append(f"법령 {len(sr['laws'])}건")
            if sr["precedents"]: counts.append(f"판례 {len(sr['precedents'])}건")
            if sr["interpretations"]: counts.append(f"해석례 {len(sr['interpretations'])}건")
            if counts:
                st.caption(f"📊 현재 로드: {', '.join(counts)}")
                st.caption("💡 이 결과는 AI 검토 시 자동 참조됩니다.")
            
            if st.button("🗑 검색 결과 초기화", key="clear_law_results"):
                st.session_state.pop("law_search_results", None)
                st.session_state.pop("law_context", None)
                st.rerun()


# ──────────────────────────────────────────────
# UI: 메인 영역 검색 결과 표시
# ──────────────────────────────────────────────
def render_law_search_results():
    """메인 영역에 법령 검색 결과를 넓게 표시.
    app.py의 메인 영역(채팅 위 또는 아래)에서 호출."""
    
    sr = st.session_state.get("law_search_results")
    if not sr or not sr.get("has_results"):
        return
    
    st.markdown("---")
    st.markdown(f"### 📖 법령 검색 결과: '{sr['query']}'")
    st.caption("법제처 국가법령정보센터 실시간 조회 | AI 검토 시 자동 참조됩니다")
    
    # ── 탭으로 구분 ──
    tab_names = []
    if sr["laws"]: tab_names.append(f"⚖️ 법령 ({len(sr['laws'])})")
    if sr["precedents"]: tab_names.append(f"📚 판례 ({len(sr['precedents'])})")
    if sr["interpretations"]: tab_names.append(f"📋 해석례 ({len(sr['interpretations'])})")
    
    if not tab_names:
        return
    
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
                    
                    # 조문 로드 버튼
                    law_id = law.get("MST") or law.get("법령ID", "")
                    if law_id:
                        cache_key = f"law_detail_{law_id}"
                        if st.button(f"📄 조문 전문 보기", key=f"load_law_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("조문 로드 중..."):
                                    detail = api.get_law_text(law_id)
                                if "error" not in detail and detail.get("조문목록"):
                                    st.session_state[cache_key] = detail
                                else:
                                    st.warning("조문을 불러오지 못했습니다.")
                            except Exception as e:
                                st.error(f"조문 로드 실패: {e}")
                        
                        # 캐시된 조문 표시
                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            st.markdown(f"**{detail['법령명']}** (시행 {detail['시행일자']})")
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
                        if st.button(f"📄 판례 전문 보기", key=f"load_prec_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("판례 로드 중..."):
                                    detail = api.get_precedent_detail(prec_id)
                                if "error" not in detail:
                                    st.session_state[cache_key] = detail
                                else:
                                    st.warning("판례를 불러오지 못했습니다.")
                            except Exception as e:
                                st.error(f"판례 로드 실패: {e}")
                        
                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            if detail.get("판시사항"):
                                st.markdown("**📌 판시사항**")
                                st.markdown(detail["판시사항"])
                            if detail.get("판결요지"):
                                st.markdown("**📌 판결요지**")
                                st.markdown(detail["판결요지"])
                            if detail.get("참조조문"):
                                st.markdown(f"**⚖️ 참조조문:** {detail['참조조문']}")
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
    
    st.markdown("---")
