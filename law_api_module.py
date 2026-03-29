"""
법제처 Open API 연동 모듈
========================
신세계면세점 법률 컴플라이언스 앱용
- 법령 검색 / 조문 조회 / 판례 검색 / 법령해석례 검색
- AI 프롬프트 컨텍스트 생성 헬퍼

사용법:
  1. Streamlit secrets 또는 .env에 LAW_OC 값 설정
  2. 이 파일을 기존 앱 폴더에 복사
  3. from law_api_module import LawAPI 로 import
"""

import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote
from typing import Optional
import streamlit as st


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
def get_oc():
    """OC값을 st.secrets 또는 환경변수에서 가져옴"""
    try:
        return st.secrets["LAW_OC"]
    except Exception:
        import os
        return os.environ.get("LAW_OC", "")


BASE_URL = "http://www.law.go.kr/DRF/lawSearch.do"
DETAIL_URL = "http://www.law.go.kr/DRF/lawService.do"

# 면세점 업무에 자주 쓰는 법령 약칭 → 정식명 매핑
ABBREVIATIONS = {
    "표시광고법": "표시·광고의공정화에관한법률",
    "전상법": "전자상거래등에서의소비자보호에관한법률",
    "관세법": "관세법",
    "외환법": "외국환거래법",
    "관광진흥법": "관광진흥법",
    "개인정보보호법": "개인정보보호법",
    "공정거래법": "독점규제및공정거래에관한법률",
    "소비자기본법": "소비자기본법",
    "화장품법": "화장품법",
    "식품위생법": "식품위생법",
    "약사법": "약사법",
    "주세법": "주세법",
    "담배사업법": "담배사업법",
}


class LawAPI:
    """법제처 Open API 래퍼 클래스"""

    def __init__(self, oc: Optional[str] = None):
        self.oc = oc or get_oc()
        if not self.oc:
            raise ValueError("LAW_OC 값이 설정되지 않았습니다. st.secrets 또는 환경변수를 확인하세요.")

    # ──────────────────────────────────────────
    # 1. 법령 검색
    # ──────────────────────────────────────────
    def search_law(self, keyword: str, display: int = 5) -> list[dict]:
        """
        법령 검색 (약칭 자동 인식)
        
        Args:
            keyword: 검색어 (예: "표시광고법", "관세법 제38조")
            display: 결과 개수 (기본 5)
        
        Returns:
            [{'법령명': '...', '법령ID': '...', '시행일자': '...', '법령종류': '...'}, ...]
        """
        # 약칭 자동 변환
        resolved = ABBREVIATIONS.get(keyword, keyword)

        params = {
            "OC": self.oc,
            "target": "law",
            "type": "XML",
            "query": resolved,
            "display": display,
        }
        try:
            res = requests.get(BASE_URL, params=params, timeout=10)
            res.raise_for_status()
            tree = ET.fromstring(res.text)
        except Exception as e:
            return [{"error": f"API 호출 실패: {str(e)}"}]

        results = []
        # law.go.kr XML 구조: <LawSearch><law>...</law></LawSearch>
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

    # ──────────────────────────────────────────
    # 2. 법령 조문 상세 조회
    # ──────────────────────────────────────────
    def get_law_text(self, law_id: str, jo: Optional[str] = None) -> dict:
        """
        법령 전문 또는 특정 조문 조회
        
        Args:
            law_id: 법령일련번호 또는 MST (search_law 결과에서 획득)
            jo: 조문번호 (예: "003800" → 제38조). None이면 전문 조회
        
        Returns:
            {'법령명': '...', '조문목록': [{'조문번호': '...', '조문제목': '...', '조문내용': '...'}, ...]}
        """
        params = {
            "OC": self.oc,
            "target": "law",
            "type": "XML",
            "ID": law_id,
        }
        if jo:
            params["JO"] = jo

        try:
            res = requests.get(DETAIL_URL, params=params, timeout=15)
            res.raise_for_status()
            tree = ET.fromstring(res.text)
        except Exception as e:
            return {"error": f"API 호출 실패: {str(e)}"}

        law_name = tree.findtext(".//법령명_한글", "")
        articles = []

        for article in tree.iter("조문단위"):
            articles.append({
                "조문번호": article.findtext("조문번호", ""),
                "조문제목": article.findtext("조문제목", ""),
                "조문내용": article.findtext("조문내용", ""),
                "조문시행일자": article.findtext("조문시행일자", ""),
            })

        return {
            "법령명": law_name,
            "시행일자": tree.findtext(".//시행일자", ""),
            "조문목록": articles,
        }

    # ──────────────────────────────────────────
    # 3. 판례 검색
    # ──────────────────────────────────────────
    def search_precedent(self, keyword: str, display: int = 10) -> list[dict]:
        """
        판례 검색
        
        Args:
            keyword: 검색어 (예: "표시광고 부당", "허위과장광고")
            display: 결과 개수
        
        Returns:
            [{'사건명': '...', '사건번호': '...', '선고일자': '...', ...}, ...]
        """
        params = {
            "OC": self.oc,
            "target": "prec",
            "type": "XML",
            "query": keyword,
            "display": display,
        }
        try:
            res = requests.get(BASE_URL, params=params, timeout=10)
            res.raise_for_status()
            tree = ET.fromstring(res.text)
        except Exception as e:
            return [{"error": f"API 호출 실패: {str(e)}"}]

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

    # ──────────────────────────────────────────
    # 4. 판례 상세 조회
    # ──────────────────────────────────────────
    def get_precedent_detail(self, prec_id: str) -> dict:
        """
        판례 전문 조회 (판시사항, 판결요지, 참조조문 등)
        
        Args:
            prec_id: 판례일련번호 (search_precedent 결과에서 획득)
        """
        params = {
            "OC": self.oc,
            "target": "prec",
            "type": "XML",
            "ID": prec_id,
        }
        try:
            res = requests.get(DETAIL_URL, params=params, timeout=15)
            res.raise_for_status()
            tree = ET.fromstring(res.text)
        except Exception as e:
            return {"error": f"API 호출 실패: {str(e)}"}

        return {
            "사건명": tree.findtext(".//사건명", ""),
            "사건번호": tree.findtext(".//사건번호", ""),
            "선고일자": tree.findtext(".//선고일자", ""),
            "법원명": tree.findtext(".//법원명", ""),
            "판시사항": tree.findtext(".//판시사항", ""),
            "판결요지": tree.findtext(".//판결요지", ""),
            "참조조문": tree.findtext(".//참조조문", ""),
            "참조판례": tree.findtext(".//참조판례", ""),
            "판례내용": tree.findtext(".//판례내용", ""),
        }

    # ──────────────────────────────────────────
    # 5. 법령해석례 검색
    # ──────────────────────────────────────────
    def search_interpretation(self, keyword: str, display: int = 10) -> list[dict]:
        """
        법령해석례 검색
        
        Args:
            keyword: 검색어 (예: "표시광고", "전자상거래 환불")
            display: 결과 개수
        """
        params = {
            "OC": self.oc,
            "target": "expc",
            "type": "XML",
            "query": keyword,
            "display": display,
        }
        try:
            res = requests.get(BASE_URL, params=params, timeout=10)
            res.raise_for_status()
            tree = ET.fromstring(res.text)
        except Exception as e:
            return [{"error": f"API 호출 실패: {str(e)}"}]

        results = []
        for item in tree.iter():
            title = item.findtext("안건명")
            if title:
                results.append({
                    "해석례ID": item.findtext("법령해석례일련번호", ""),
                    "안건명": title,
                    "안건번호": item.findtext("안건번호", ""),
                    "회답일자": item.findtext("회답일자", ""),
                    "회답기관": item.findtext("회답기관", ""),
                    "상세링크": item.findtext("법령해석례상세링크", ""),
                })
        return results

    # ──────────────────────────────────────────
    # 6. 행정규칙 검색 (훈령/예규/고시)
    # ──────────────────────────────────────────
    def search_admin_rule(self, keyword: str, display: int = 10) -> list[dict]:
        """
        행정규칙 검색 (훈령, 예규, 고시 등)
        
        Args:
            keyword: 검색어 (예: "면세점", "표시광고")
        """
        params = {
            "OC": self.oc,
            "target": "admrul",
            "type": "XML",
            "query": keyword,
            "display": display,
        }
        try:
            res = requests.get(BASE_URL, params=params, timeout=10)
            res.raise_for_status()
            tree = ET.fromstring(res.text)
        except Exception as e:
            return [{"error": f"API 호출 실패: {str(e)}"}]

        results = []
        for item in tree.iter():
            name = item.findtext("행정규칙명")
            if name:
                results.append({
                    "행정규칙명": name,
                    "행정규칙ID": item.findtext("행정규칙일련번호", ""),
                    "시행일자": item.findtext("시행일자", ""),
                    "발령기관": item.findtext("발령기관", ""),
                    "행정규칙종류": item.findtext("행정규칙종류", ""),
                })
        return results

    # ──────────────────────────────────────────
    # 7. 조문번호 변환 헬퍼
    # ──────────────────────────────────────────
    @staticmethod
    def jo_to_code(jo_num: int) -> str:
        """
        조문번호 → JO 코드 변환
        예: 38 → "003800", 74 → "007400"
        """
        return f"{jo_num:04d}00"

    @staticmethod
    def code_to_jo(code: str) -> int:
        """
        JO 코드 → 조문번호 변환
        예: "003800" → 38
        """
        return int(code[:4])

    # ──────────────────────────────────────────
    # 8. AI 프롬프트 컨텍스트 생성 헬퍼
    # ──────────────────────────────────────────
    def build_ai_context(
        self,
        keyword: str,
        include_law: bool = True,
        include_precedent: bool = False,
        include_interpretation: bool = False,
        max_articles: int = 10,
    ) -> str:
        """
        검색 결과를 AI(Gemini/Claude) 프롬프트에 넣을 수 있는
        텍스트 컨텍스트로 변환

        Args:
            keyword: 검색어
            include_law: 법령 조문 포함 여부
            include_precedent: 판례 포함 여부
            include_interpretation: 법령해석례 포함 여부
            max_articles: 최대 조문 수

        Returns:
            AI 프롬프트에 삽입할 컨텍스트 문자열
        """
        context_parts = []

        # 법령 원문
        if include_law:
            laws = self.search_law(keyword, display=3)
            for law in laws[:2]:  # 상위 2개 법령
                if "error" in law:
                    continue
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
                                if title:
                                    header += f"({title})"
                                context_parts.append(f"\n{header}\n{content}")

        # 판례
        if include_precedent:
            precs = self.search_precedent(keyword, display=5)
            if precs and "error" not in precs[0]:
                context_parts.append("\n\n=== 관련 판례 ===")
                for p in precs[:3]:
                    context_parts.append(
                        f"\n[{p['사건번호']}] {p['사건명']} ({p['선고일자']}, {p['법원명']})"
                    )

        # 법령해석례
        if include_interpretation:
            interps = self.search_interpretation(keyword, display=5)
            if interps and "error" not in interps[0]:
                context_parts.append("\n\n=== 관련 법령해석례 ===")
                for i in interps[:3]:
                    context_parts.append(
                        f"\n[{i['안건번호']}] {i['안건명']} ({i['회답일자']}, {i['회답기관']})"
                    )

        if not context_parts:
            return f"'{keyword}'에 대한 법령 정보를 찾지 못했습니다."

        return "\n".join(context_parts)


# ──────────────────────────────────────────────
# Streamlit 사이드바 위젯 (기존 앱에 추가용)
# ──────────────────────────────────────────────
def render_law_search_sidebar():
    """
    기존 Streamlit 앱의 사이드바에 법령검색 위젯 추가
    
    사용법 (기존 앱의 메인 파일에 추가):
        from law_api_module import render_law_search_sidebar
        render_law_search_sidebar()
    """
    with st.sidebar:
        st.markdown("---")
        st.subheader("📖 법령 실시간 검색")

        law_query = st.text_input(
            "법령 검색어",
            placeholder="예: 표시광고법, 관세법 제38조",
            key="law_search_input",
        )

        col1, col2, col3 = st.columns(3)
        with col1:
            inc_law = st.checkbox("법령", value=True, key="inc_law")
        with col2:
            inc_prec = st.checkbox("판례", value=False, key="inc_prec")
        with col3:
            inc_interp = st.checkbox("해석례", value=False, key="inc_interp")

        if st.button("🔍 검색", key="law_search_btn") and law_query:
            try:
                api = LawAPI()
                with st.spinner("법제처 API 조회 중..."):
                    context = api.build_ai_context(
                        law_query,
                        include_law=inc_law,
                        include_precedent=inc_prec,
                        include_interpretation=inc_interp,
                    )
                st.session_state["law_context"] = context
                st.success("법령 정보 로드 완료!")
                with st.expander("검색 결과 미리보기", expanded=False):
                    st.text(context[:2000] + ("..." if len(context) > 2000 else ""))
            except Exception as e:
                st.error(f"검색 실패: {str(e)}")


# ──────────────────────────────────────────────
# 사용 예시
# ──────────────────────────────────────────────
if __name__ == "__main__":
    """
    테스트 실행:
      LAW_OC=your_oc_id python law_api_module.py
    """
    import os
    oc = os.environ.get("LAW_OC")
    if not oc:
        print("LAW_OC 환경변수를 설정해주세요.")
        print("  예: LAW_OC=myid python law_api_module.py")
        exit(1)

    api = LawAPI(oc=oc)

    print("=" * 60)
    print("1. 법령 검색: '표시광고법'")
    print("=" * 60)
    results = api.search_law("표시광고법")
    for r in results:
        print(f"  {r['법령명']} ({r['법령종류']}) - 시행 {r['시행일자']}")

    print()
    print("=" * 60)
    print("2. 판례 검색: '허위과장광고'")
    print("=" * 60)
    precs = api.search_precedent("허위과장광고", display=3)
    for p in precs:
        print(f"  [{p['사건번호']}] {p['사건명']}")

    print()
    print("=" * 60)
    print("3. AI 컨텍스트 생성: '표시광고'")
    print("=" * 60)
    context = api.build_ai_context("표시광고", include_precedent=True)
    print(context[:1500])
