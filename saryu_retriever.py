"""
saryu_retriever.py — 지능형 사규 리트리버 (WP1)

50,000자의 사규 전체를 LLM에 넣는 대신,
질문과 관련된 조항만 추출하여 ~5,000자로 압축.

토큰 절감: 33,000 → 3,500 (약 90% 절감)
"""

import re
from typing import List, Dict, Tuple


def chunk_by_article(text: str, label: str = "") -> List[Dict[str, str]]:
    """
    사규/계약서 텍스트를 조항(제N조) 단위로 분할.
    
    Returns: [{"label": "직매입계약서", "article": "제22조", "title": "계약해지", "text": "..."}]
    """
    chunks = []
    
    # "제N조", "제N조의2" 패턴으로 분할
    pattern = re.compile(r'(제\d+조(?:의\d+)?)\s*[\(（]([^)）]+)[\)）]')
    
    # 조문 위치 찾기
    matches = list(pattern.finditer(text))
    
    if not matches:
        # 조문 패턴이 없으면 전체를 하나의 청크로
        chunks.append({
            "label": label,
            "article": "",
            "title": "",
            "text": text[:2000],
        })
        return chunks
    
    for i, match in enumerate(matches):
        article_no = match.group(1)  # "제22조"
        article_title = match.group(2)  # "계약해지"
        
        # 이 조문의 시작~다음 조문 시작까지
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        
        article_text = text[start:end].strip()
        
        chunks.append({
            "label": label,
            "article": article_no,
            "title": article_title,
            "text": article_text[:1000],  # 조항당 최대 1000자
        })
    
    return chunks


def extract_keywords(query: str) -> List[str]:
    """
    질문에서 검색 키워드를 추출.
    한국어 명사 + 법률 용어 중심.
    """
    # 불용어
    stopwords = {"면세점", "에서", "행위", "대해", "검토", "법률", "해줘", "주세요", 
                 "어떻게", "되나요", "인지", "여부", "관련", "대한", "것이", "경우"}
    
    # 법률 관련 핵심 키워드 (가중치 높음)
    legal_terms = {
        "모조품", "위조", "가품", "짝퉁", "병행수입", "진정상품",
        "표시광고", "허위광고", "과대광고", "과장광고",
        "납품", "단가", "인하", "인상", "수수료",
        "판촉", "행사", "프로모션", "할인",
        "반품", "교환", "환불", "하자",
        "입점", "퇴점", "계약해지", "해지",
        "손해배상", "위약금", "보증",
        "상표", "특허", "저작권", "지식재산",
        "밀수", "통관", "관세", "수입", "수출",
        "사기", "기망", "횡령", "배임",
        "AI워싱", "그린워싱", "친환경", "ESG",
    }
    
    # 질문에서 키워드 추출
    keywords = []
    
    # 법률 용어 우선 매칭
    for term in legal_terms:
        if term in query:
            keywords.append(term)
    
    # 일반 단어 추출 (2글자 이상 한글)
    words = re.findall(r'[가-힣]{2,}', query)
    for w in words:
        if w not in stopwords and w not in keywords:
            keywords.append(w)
    
    return keywords


def score_chunk(chunk: Dict[str, str], keywords: List[str]) -> float:
    """
    조항과 키워드의 매칭 점수 계산.
    """
    text = (chunk["text"] + " " + chunk["title"] + " " + chunk["article"]).lower()
    
    score = 0.0
    for kw in keywords:
        if kw.lower() in text:
            # 제목에 있으면 가중치 3배
            if kw.lower() in chunk["title"].lower():
                score += 3.0
            # 조문 내용에 있으면 1점
            else:
                score += 1.0
    
    # 핵심 조항 보너스 (자주 참조되는 조문)
    key_articles = {"제5조", "제16조", "제16조의2", "제17조", "제17조의2", 
                    "제22조", "제23조", "제4조", "제8조"}
    if chunk["article"] in key_articles:
        score += 0.5
    
    return score


def retrieve_relevant_saryu(
    query: str, 
    docs: List[Dict], 
    max_chars: int = 5000
) -> str:
    """
    질문과 관련된 사규 조항만 추출하여 압축된 텍스트 반환.
    
    Args:
        query: 사용자 질문
        docs: 앱의 문서 목록 [{"cat": "saryu", "label": "...", "text": "..."}]
        max_chars: 최대 출력 문자 수
    
    Returns:
        압축된 사규 텍스트 (~5,000자)
    """
    # 1. 키워드 추출
    keywords = extract_keywords(query)
    
    if not keywords:
        # 키워드 없으면 각 문서의 앞부분만
        result = []
        for doc in docs:
            if doc.get("cat") in ("saryu", "contract", "yakjeong"):
                result.append(f"[{doc.get('label', '')}]\n{doc.get('text', '')[:500]}")
        return "\n\n".join(result)[:max_chars]
    
    # 2. 모든 문서를 조항 단위로 분할
    all_chunks = []
    for doc in docs:
        if doc.get("cat") in ("saryu", "contract", "yakjeong"):
            chunks = chunk_by_article(doc.get("text", ""), doc.get("label", ""))
            all_chunks.extend(chunks)
    
    if not all_chunks:
        return "(사규 등록 없음)"
    
    # 3. 각 조항의 매칭 점수 계산
    scored = [(chunk, score_chunk(chunk, keywords)) for chunk in all_chunks]
    
    # 4. 점수 높은 순으로 정렬
    scored.sort(key=lambda x: x[1], reverse=True)
    
    # 5. 상위 조항부터 max_chars까지 조립
    result_parts = []
    total_chars = 0
    
    for chunk, score in scored:
        if score <= 0:
            break  # 관련 없는 조항은 건너뜀
        
        entry = f"[{chunk['label']}] {chunk['article']} ({chunk['title']})\n{chunk['text']}"
        
        if total_chars + len(entry) > max_chars:
            break
        
        result_parts.append(entry)
        total_chars += len(entry)
    
    if not result_parts:
        # 매칭된 조항이 없으면 각 문서의 앞부분
        for doc in docs:
            if doc.get("cat") in ("saryu", "contract", "yakjeong"):
                result_parts.append(f"[{doc.get('label', '')}]\n{doc.get('text', '')[:500]}")
        return "\n\n".join(result_parts)[:max_chars]
    
    return "\n\n---\n\n".join(result_parts)


# 테스트
if __name__ == "__main__":
    # 테스트 데이터
    test_docs = [
        {
            "cat": "contract",
            "label": "직매입거래 기본계약서",
            "text": """제5조(관계법령 준수) 공급자는 상표법, 관세법, 표시광고법 등 제반 관계법령을 준수하여야 한다.
제16조의2(손해배상) 가품이나 모조품 납품 시 손해배상 책임을 진다.
제22조(계약해지) ① 다음 각 호의 사유 발생 시 서면통지로 계약을 해지할 수 있다.
  5. 위조상품, 장물 등 위법성이 있는 상품을 납품한 경우"""
        },
        {
            "cat": "saryu",
            "label": "사내규정집",
            "text": """입점절차 - 입점상담: 위해·불법상품 취급(가품 등), 과대·과장광고 사유로 거래대상 제외
퇴점절차: 법규위반, 대외이미지 손상 시 퇴점 사유에 해당"""
        }
    ]
    
    query = "면세점에서 모조품 판매 행위에 대해 법률 검토해줘"
    result = retrieve_relevant_saryu(query, test_docs)
    print(f"키워드: {extract_keywords(query)}")
    print(f"추출 결과 ({len(result)}자):")
    print(result)
