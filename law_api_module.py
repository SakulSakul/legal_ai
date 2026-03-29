"""
법제처 Open API 연동 모듈 v1.1
========================
신세계면세점 법률 컴플라이언스 앱용
- 법령 검색 / 조문 조회 / 판례 검색 / 법령해석례 검색
- AI 프롬프트 컨텍스트 생성 헬퍼

v1.1 변경사항:
  - 법제처 API 한글 인코딩 문제 수정 (URL 직접 구성)
  - XML 파싱 에러 진단 강화 (raw 응답 로깅)
  - 응답 인코딩 자동 감지 추가
  - 검색 결과 없을 때 디버깅 정보 제공
  - 약칭 매핑 정식 법령명 수정

사용법:
  1. Streamlit secrets 또는 .env에 LAW_OC 값 설정
  2. 이 파일을 기존 앱 폴더에 복사
  3. from law_api_module import LawAPI 로 import
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
    """법제처 API 요청 헬퍼 — URL을 직접 구성하여 인코딩 문제 방지."""
    # URL 직접 구성
    param_parts = []
    for key, value in params_dict.items():
        if value is not None:
            encoded_value = quote(str(value), safe='')
            param_parts.append(f"{key}={encoded_value}")
    
    full_url = base_url + "?" + "&".join(param_parts)
    logger.info(f"법제처 API 요청: {full_url}")
    
    try:
        res = requests.get(full_url, timeout=15)
        
        # 응답 인코딩 처리
        if res.encoding and 'euc' in res.encoding.lower():
            res.encoding = 'euc-kr'
        elif res.apparent_encoding:
            res.encoding = res.apparent_encoding
        
        content = res.text
        
        # 빈 응답 체크
        if not content or len(content.strip()) < 10:
            logger.warning(f"법제처 API 빈 응답: status={res.status_code}, length={len(content)}")
            return None, f"빈 응답 (HTTP {res.status_code})"
        
        # XML 파싱
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
    """법제처 Open API 래퍼 클래스"""

    def __init__(self, oc: Optional[str] = None):
        self.oc = oc or get_oc()
        if not self.oc:
            raise ValueError("LAW_OC 값이 설정되지 않았습니다. st.secrets 또는 환경변수를 확인하세요.")

    # ──────────────────────────────────────────
    # 1. 법령 검색
    # ──────────────────────────────────────────
    def search_law(self, keyword: str, display: int = 5) -> list[dict]:
        """법령 검색 (약칭 자동 인식)"""
        resolved = ABBREVIATIONS.get(keyword, keyword)

        params = {
            "OC": self.oc,
            "target": "law",
            "type": "XML",
            "query": resolved,
            "display": str(display),
        }
        
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]

        total = tree.findtext("totalCnt") or tree.findtext(".//totalCnt")
        logger.info(f"법령 검색 '{resolved}': totalCnt={total}")
        
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
        
        if not results and total and int(total) == 0:
            logger.info(f"검색 결과 0건: '{resolved}'")
        elif not results:
            all_tags = set()
            for elem in tree.iter():
                all_tags.add(elem.tag)
            logger.warning(f"검색 결과 파싱 실패. XML 태그 목록: {all_tags}")
            
        return results

    # ──────────────────────────────────────────
    # 2. 법령 조문 상세 조회
    # ──────────────────────────────────────────
    def get_law_text(self, law_id: str, jo: Optional[str] = None) -> dict:
        """법령 전문 또는 특정 조문 조회"""
        params = {
            "OC": self.oc,
            "target": "law",
            "type": "XML",
            "ID": law_id,
        }
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

        return {
            "법령명": law_name,
            "시행일자": tree.findtext(".//시행일자", ""),
            "조문목록": articles,
        }

    # ──────────────────────────────────────────
    # 3. 판례 검색
    # ──────────────────────────────────────────
    def search_precedent(self, keyword: str, display: int = 10) -> list[dict]:
        """판례 검색"""
        params = {
            "OC": self.oc,
            "target": "prec",
            "type": "XML",
            "query": keyword,
            "display": str(display),
        }
        
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]

        total = tree.findtext("totalCnt") or tree.findtext(".//totalCnt")
        logger.info(f"판례 검색 '{keyword}': totalCnt={total}")

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
        """판례 전문 조회"""
        params = {
            "OC": self.oc,
            "target": "prec",
            "type": "XML",
            "ID": prec_id,
        }
        
        tree, error = _make_request(DETAIL_URL, params)
        if error:
            return {"error": f"API 호출 실패: {error}"}

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
        """법령해석례 검색"""
        params = {
            "OC": self.oc,
            "target": "expc",
            "type": "XML",
            "query": keyword,
            "display": str(display),
        }
        
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]

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
        """행정규칙 검색"""
        params = {
            "OC": self.oc,
            "target": "admrul",
            "type": "XML",
            "query": keyword,
            "display": str(display),
        }
        
        tree, error = _make_request(BASE_URL, params)
        if error:
            return [{"error": f"API 호출 실패: {error}"}]

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
        return f"{jo_num:04d}00"

    @staticmethod
    def code_to_jo(code: str) -> int:
        try:
            return int(code[:4])
        except (ValueError, IndexError):
            return 0

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
        """검색 결과를 AI 프롬프트에 넣을 수 있는 텍스트로 변환"""
        context_parts = []

        if include_law:
            laws = self.search_law(keyword, display=3)
            for law in laws[:2]:
                if "error" in law:
                    logger.warning(f"법령 검색 에러: {law['error']}")
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

        if include_precedent:
            precs = self.search_precedent(keyword, display=5)
            valid_precs = [p for p in precs if "error" not in p]
            if valid_precs:
                context_parts.append("\n\n=== 관련 판례 ===")
                for p in valid_precs[:3]:
                    context_parts.append(
                        f"\n[{p['사건번호']}] {p['사건명']} ({p['선고일자']}, {p['법원명']})"
                    )

        if include_interpretation:
            interps = self.search_interpretation(keyword, display=5)
            valid_interps = [i for i in interps if "error" not in i]
            if valid_interps:
                context_parts.append("\n\n=== 관련 법령해석례 ===")
                for i in valid_interps[:3]:
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
    """기존 Streamlit 앱의 사이드바에 법령검색 위젯 추가"""
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
                
                if "찾지 못했습니다" in context:
                    st.warning(f"'{law_query}' 검색 결과가 없습니다.")
                    st.caption("💡 짧은 키워드로 다시 시도해 보세요.\n예: '표시광고', '대규모유통업', '관세법'")
                    # 디버깅 정보
                    with st.expander("🔧 디버그 정보", expanded=False):
                        resolved = ABBREVIATIONS.get(law_query, law_query)
                        st.caption(f"검색어: '{law_query}' → '{resolved}'")
                        st.caption(f"OC: {api.oc[:3]}***")
                        test_url = f"http://www.law.go.kr/DRF/lawSearch.do?OC={api.oc}&target=law&type=XML&query={quote(resolved, safe='')}&display=1"
                        st.caption(f"요청 URL:")
                        st.code(test_url, language="text")
                        try:
                            test_res = requests.get(test_url, timeout=10)
                            if test_res.apparent_encoding:
                                test_res.encoding = test_res.apparent_encoding
                            st.caption(f"HTTP {test_res.status_code} | 인코딩: {test_res.encoding}")
                            st.code(test_res.text[:1500], language="xml")
                        except Exception as e:
                            st.error(f"직접 요청 실패: {e}")
                else:
                    st.success("법령 정보 로드 완료!")
                    with st.expander("검색 결과 미리보기", expanded=False):
                        st.text(context[:2000] + ("..." if len(context) > 2000 else ""))
            except ValueError as ve:
                st.error(f"설정 오류: {str(ve)}")
            except Exception as e:
                st.error(f"검색 실패: {str(e)}")
                logger.error(f"법령 검색 위젯 에러: {e}", exc_info=True)


# ──────────────────────────────────────────────
# 사용 예시
# ──────────────────────────────────────────────
if __name__ == "__main__":
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
    if results and "error" not in results[0]:
        for r in results:
            print(f"  {r['법령명']} ({r['법령종류']}) - 시행 {r['시행일자']}")
    else:
        print(f"  검색 실패: {results}")

    print()
    print("=" * 60)
    print("2. 판례 검색: '허위과장광고'")
    print("=" * 60)
    precs = api.search_precedent("허위과장광고", display=3)
    if precs and "error" not in precs[0]:
        for p in precs:
            print(f"  [{p['사건번호']}] {p['사건명']}")
    else:
        print(f"  검색 실패: {precs}")

    print()
    print("=" * 60)
    print("3. AI 컨텍스트 생성: '표시광고'")
    print("=" * 60)
    context = api.build_ai_context("표시광고", include_precedent=True)
    print(context[:1500])
