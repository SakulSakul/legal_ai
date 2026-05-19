#!/usr/bin/env python3
# ============================================================
#  📚 법령 DB 자동 업데이트 — update_laws.py
#  v2.0 통일: MCP 경유 — 모든 법령/행정규칙 조회를 LawAPI(MCP) 경유로 통일
#  (Streamlit Cloud IP 차단 회피, OC 후보 순회 제거)
#
#  데이터 흐름: LawAPI(korean-law-mcp) → Supabase laws 테이블
#
#  사전 준비:
#    1. .streamlit/secrets.toml 또는 환경변수에 ANTHROPIC_API_KEY 등록
#    2. python update_laws.py 실행
#
#  자동화: GitHub Actions (update-laws.yml) 으로 주 1회 자동 실행
# ============================================================

import os, sys, time, json, re
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import quote_plus

# ── 컬러 출력 ─────────────────────────────────────────────
class C:
    OK = "\033[92m"; WARN = "\033[93m"; FAIL = "\033[91m"
    BOLD = "\033[1m"; DIM = "\033[2m"; END = "\033[0m"

def ok(msg):   print(f"  {C.OK}✅{C.END} {msg}")
def warn(msg): print(f"  {C.WARN}⚠️{C.END}  {msg}")
def fail(msg): print(f"  {C.FAIL}❌{C.END} {msg}")
def info(msg): print(f"  {C.DIM}ℹ️{C.END}  {msg}")
def header(msg): print(f"\n{C.BOLD}{'─'*55}\n  {msg}\n{'─'*55}{C.END}")

# ── Secrets 로드 ─────────────────────────────────────────
def load_secret(key):
    toml_path = os.path.join(os.getcwd(), ".streamlit", "secrets.toml")
    if os.path.exists(toml_path):
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                tomllib = None
        if tomllib:
            with open(toml_path, "rb") as f:
                secrets = tomllib.load(f)
            if key in secrets:
                return secrets[key]
    return os.environ.get(key)

# ── 관리 대상 법령 및 조문 ─────────────────────────────────
# 면세점(보세판매장) MD/바이어 실무에 필요한 법령 + 핵심 조문
TARGET_LAWS = [
    # ━━━ 공정거래 관련 ━━━
    {
        "law_name": "대규모유통업에서의 거래 공정화에 관한 법률",
        "law_short": "대규모유통업법",
        "articles": ["제6조", "제7조", "제8조", "제10조", "제11조", "제12조", "제13조"],
    },
    {
        "law_name": "대규모유통업에서의 거래 공정화에 관한 법률 시행령",
        "law_short": "대규모유통업법 시행령",
        "articles": ["제5조", "제7조"],
    },
    {
        "law_name": "독점규제 및 공정거래에 관한 법률",
        "law_short": "공정거래법",
        "articles": ["제45조"],
    },
    {
        "law_name": "하도급거래 공정화에 관한 법률",
        "law_short": "하도급법",
        "articles": ["제3조", "제4조", "제8조"],
    },
    # ━━━ 관세/면세점 관련 ━━━
    {
        "law_name": "관세법",
        "law_short": "관세법",
        "articles": ["제176조", "제177조", "제178조", "제196조", "제197조", "제198조", "제199조", "제235조", "제269조", "제270조", "제271조"],
        # 178: 특허취소, 196: 보세판매장, 235: 지식재산권보호, 269: 밀수출입죄
    },
    # ━━━ 지식재산/형사 관련 (신규) ━━━
    {
        "law_name": "상표법",
        "law_short": "상표법",
        "articles": ["제2조", "제33조", "제34조", "제108조", "제230조", "제235조", "제236조"],
        # 230: 침해죄(7년/1억), 235: 양벌규정(법인3억), 236: 몰수(필요적)
    },
    {
        "law_name": "부정경쟁방지 및 영업비밀보호에 관한 법률",
        "law_short": "부정경쟁방지법",
        "articles": ["제2조", "제4조", "제14조의2", "제18조", "제18조의2", "제18조의3", "제18조의5"],
        # 14의2: 징벌적손해배상(5배), 18/18의2/18의3: 벌칙, 18의5: 몰수
    },
    {
        "law_name": "형법",
        "law_short": "형법",
        "articles": ["제347조", "제347조의2"],
    },
    # ━━━ 소비자/표시광고 관련 ━━━
    {
        "law_name": "소비자기본법",
        "law_short": "소비자기본법",
        "articles": ["제4조", "제19조", "제20조", "제46조"],
        # 46: 위해방지 의무(수거·파기·환급)
    },
    {
        "law_name": "표시ㆍ광고의 공정화에 관한 법률",
        "law_short": "표시광고법",
        "articles": ["제3조", "제4조", "제5조"],
    },
    # ━━━ 식품/건강기능식품 관련 ━━━
    {
        "law_name": "건강기능식품에 관한 법률",
        "law_short": "건강기능식품법",
        "articles": ["제6조", "제18조", "제44조"],
    },
    {
        "law_name": "식품위생법",
        "law_short": "식품위생법",
        "articles": ["제10조", "제12조", "제13조"],
    },
    # ━━━ 세법 관련 ━━━
    {
        "law_name": "부가가치세법",
        "law_short": "부가가치세법",
        "articles": ["제11조", "제12조", "제24조", "제26조"],
    },
    {
        "law_name": "개별소비세법",
        "law_short": "개별소비세법",
        "articles": ["제1조", "제4조", "제18조"],
    },
    {
        "law_name": "주세법",
        "law_short": "주세법",
        "articles": ["제1조", "제5조", "제22조"],
    },
    # ━━━ 상생협력 관련 ━━━
    {
        "law_name": "대ㆍ중소기업 상생협력 촉진에 관한 법률",
        "law_short": "상생협력법",
        "articles": ["제20조", "제21조", "제25조"],
    },
    # ━━━ 면세점 수출입/무역 관련 (신규) ━━━
    {
        "law_name": "대외무역법",
        "law_short": "대외무역법",
        "articles": ["제2조", "제11조", "제19조", "제33조"],
        # 2: 정의, 11: 수출입승인, 19: 원산지표시, 33: 전략물자
    },
    {
        "law_name": "외국환거래법",
        "law_short": "외국환거래법",
        "articles": ["제3조", "제8조", "제15조", "제16조"],
        # 3: 정의, 8: 신고, 15: 자본거래 신고, 16: 지급수단 수출입
    },
    {
        "law_name": "수출용 원재료에 대한 관세 등 환급에 관한 특례법",
        "law_short": "환급특례법",
        "articles": ["제3조", "제4조", "제10조"],
        # 3: 환급대상, 4: 환급방법, 10: 간이정액환급
    },
    # ━━━ 유통산업 관련 (신규) ━━━
    {
        "law_name": "유통산업발전법",
        "law_short": "유통산업발전법",
        "articles": ["제2조", "제8조", "제12조", "제12조의2"],
    },
]

# ── 관리 대상 행정규칙 (고시 등) ─────────────────────────────
TARGET_ADMRULS = [
    {
        "admrul_name": "보세판매장 특허 및 운영에 관한 고시",
        "law_short": "보세판매장고시",
        "articles": [
            "제1조", "제2조", "제3조", "제4조", "제5조", "제6조", "제7조", "제8조", "제9조", "제10조",
            "제11조", "제12조", "제13조", "제14조", "제15조", "제16조", "제17조", "제18조", "제19조", "제20조",
            "제21조", "제22조", "제23조", "제24조", "제25조", "제26조", "제27조", "제28조", "제29조", "제30조",
            "제31조", "제32조", "제33조", "제34조", "제35조", "제36조", "제37조", "제38조", "제39조", "제40조",
        ],
    },
    {
        "admrul_name": "보세판매장 운영에 관한 고시",
        "law_short": "보세판매장운영고시",
        "articles": [
            "제1조", "제2조", "제3조", "제4조", "제5조", "제6조", "제7조", "제8조", "제9조", "제10조",
            "제11조", "제12조", "제13조", "제14조", "제15조", "제16조", "제17조", "제18조", "제19조", "제20조",
            "제21조", "제22조", "제23조", "제24조", "제25조", "제26조", "제27조", "제28조", "제29조", "제30조",
        ],
        # 3: 판매장운영, 7: 물품반입, 9: 판매기록, 12: 교환권, 14: 재고관리, 28: 행정제재
    },
]

# 행정규칙 ID prefix
ADMRUL_ID_PREFIX = {
    "보세판매장고시": "bonded_shop_notice",
    "보세판매장운영고시": "bonded_shop_ops_notice",
}


# ── 행정규칙 검색 → 행정규칙ID (LawAPI/MCP 경유) ────────────────────────────────
def search_admrul_id(service_key, admrul_name):
    """LawAPI(MCP)로 행정규칙 검색. service_key는 v1.x 호환 인자(사용 안 함)."""
    from law_api_module import LawAPI

    info(f"행정규칙 검색: {admrul_name}")
    try:
        results = LawAPI().search_admin_rule(admrul_name, display=10)
    except Exception as e:
        fail(f"행정규칙 검색 실패: {e}")
        return None, None

    if not results or (isinstance(results[0], dict) and "error" in results[0]):
        msg = results[0]["error"] if results else "결과 없음"
        fail(f"행정규칙 검색 실패: {msg}")
        return None, None

    # 정확 매칭 우선, 그 다음 부분 매칭
    exact = [r for r in results if r.get("행정규칙명") == admrul_name and r.get("행정규칙ID")]
    partial = [
        r for r in results
        if r.get("행정규칙ID") and r.get("행정규칙명")
        and (admrul_name in r["행정규칙명"] or r["행정규칙명"] in admrul_name)
    ]
    chosen = (exact + partial + [r for r in results if r.get("행정규칙ID")])
    if not chosen:
        warn("행정규칙ID 미발견")
        return None, None
    c = chosen[0]
    ok(f"발견: {c.get('행정규칙명', '?')} (ID: {c['행정규칙ID']})")
    return c["행정규칙ID"], c.get("상세링크", "")


def fetch_admrul_articles(service_key, admrul_id, detail_link=""):
    """행정규칙 본문 조회 (LawAPI/MCP 경유). 반환: dict {"법령명","시행일자","조문목록"} 또는 None."""
    from law_api_module import LawAPI
    try:
        detail = LawAPI().get_admin_rule_text(admrul_id)
    except Exception as e:
        fail(f"행정규칙 조문 조회 실패: {e}")
        return None
    if isinstance(detail, dict) and "error" in detail:
        fail(f"행정규칙 조문 조회 실패: {detail['error']}")
        return None
    ok("  행정규칙 조문 조회 성공")
    return detail

# ── API: 법령 검색 → MST + 상세링크 (LawAPI/MCP 경유) ─────────────────────────
def search_law_id(service_key, law_name):
    """LawAPI(MCP)로 법령명 → MST 조회. service_key는 v1.x 호환 인자(사용 안 함)."""
    from law_api_module import LawAPI

    info(f"검색: {law_name}")
    try:
        results = LawAPI().search_law(law_name, display=20)
    except Exception as e:
        fail(f"검색 실패: {e}")
        return None, None

    if not results or (isinstance(results[0], dict) and "error" in results[0]):
        msg = results[0]["error"] if results else "결과 없음"
        fail(f"검색 실패: {msg}")
        return None, None

    # 정확 매칭 → 부분 매칭(앞 10자) → 첫 결과
    exact = [r for r in results if r.get("법령명") == law_name and r.get("MST")]
    partial = [
        r for r in results
        if r.get("MST") and r.get("법령명")
        and (law_name in r["법령명"] or r["법령명"] in law_name
             or r["법령명"][:10] in law_name or law_name[:10] in r["법령명"])
    ]
    chosen = (exact + partial + [r for r in results if r.get("MST")])
    if not chosen:
        warn("법령ID 미발견")
        return None, None
    c = chosen[0]
    ok(f"발견: {c.get('법령명', '?')} (MST: {c['MST']})")
    return c["MST"], c.get("상세링크", "")


# ── API: 법령 본문(조문) 조회 (LawAPI/MCP 경유) ────────────────────────────────
def fetch_law_articles(service_key, law_id, detail_link=""):
    """법령 조문 조회 (LawAPI/MCP 경유). 반환: dict {"법령명","시행일자","조문목록"} 또는 None.
    service_key/detail_link는 v1.x 호환 인자(사용 안 함)."""
    from law_api_module import LawAPI
    try:
        detail = LawAPI().get_law_text(law_id)
    except Exception as e:
        fail(f"조문 조회 실패: {e}")
        return None
    if isinstance(detail, dict) and "error" in detail:
        fail(f"조문 조회 실패: {detail['error']}")
        return None
    ok("  조문 조회 성공")
    return detail


# ── 조문 번호 정규화 ───────────────────────────────────────
def _normalize_article_no(raw):
    """다양한 조문번호 표기를 '제N조' 또는 '제N조의M' 형식으로 정규화.
    예: '2' → '제2조', '000200' → '제2조', '000201' → '제2조의1',
        '2의3' → '제2조의3', '제3조' → '제3조' (그대로)
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("제"):
        if not s.endswith("조") and not re.search(r'조의\d+$', s):
            s = s + "조"
        return s
    # 6자리 코드 ("000200")
    if s.isdigit() and len(s) >= 4:
        main = int(s[:-2]) if len(s) >= 5 else int(s)
        sub = int(s[-2:]) if len(s) >= 5 else 0
        if sub == 0:
            return f"제{main}조"
        return f"제{main}조의{sub}"
    # "2의3" 형태
    if "의" in s:
        return f"제{s}조"
    # 단순 숫자
    return f"제{s}조"


# ── 조문 추출 ─────────────────────────────────────────────
def extract_articles(law_root, target_articles):
    # v2.0: LawAPI 반환 dict 처리
    if isinstance(law_root, dict):
        return _extract_from_lawapi_dict(law_root, target_articles)

    results = []

    # 조문 태그 탐색 (다양한 XML 구조 대응)
    article_elems = []
    for tag in ['조문단위', '조문', 'Article', 'Jo', '조']:
        article_elems = law_root.findall(f'.//{tag}')
        if article_elems:
            info(f"  조문 태그: <{tag}> ({len(article_elems)}개)")
            break
    
    # 2차 탐색: 자식 중 '조문번호'를 가진 요소
    if not article_elems:
        for elem in law_root.iter():
            for c in elem:
                if '조문번호' in c.tag or '조번호' in c.tag:
                    article_elems.append(elem)
                    break
        if article_elems:
            info(f"  2차 탐색으로 {len(article_elems)}개 조문 발견")
    
    # 3차 탐색: <조문내용> 직접 파싱 (행정규칙 — 조문번호 태그 없이 텍스트에 포함)
    if not article_elems:
        admrul_contents = law_root.findall('.//조문내용')
        if admrul_contents:
            info(f"  행정규칙 모드: <조문내용> {len(admrul_contents)}개에서 조문 추출")
            return _extract_admrul_articles(admrul_contents, target_articles)
    
    # 4차 탐색: 조문내용을 자식으로 가진 요소
    if not article_elems:
        for elem in law_root.iter():
            if elem.find('조문내용') is not None or elem.find('조내용') is not None:
                article_elems.append(elem)
        if article_elems:
            info(f"  4차 탐색(조문내용 부모) {len(article_elems)}개 발견")
    
    if not article_elems:
        tags = sorted(set(e.tag for e in law_root.iter()))
        warn(f"조문 태그 미발견. 전체 태그({len(tags)}개): {tags[:25]}")
        for t_art in target_articles:
            for elem in law_root.iter():
                if elem.text and t_art in (elem.text or ""):
                    info(f"  힌트: '{t_art}' 텍스트 발견 — 부모 태그: <{elem.tag}>")
                    break
        return results
    
    for art in article_elems:
        # 번호
        raw_no = ""
        for t in ['조문번호', '조번호', '조문키']:
            e = art.find(t)
            if e is not None and e.text:
                raw_no = e.text.strip()
                break
        if not raw_no:
            continue
        
        # 정규화
        no = raw_no
        if not no.startswith("제"):
            no = f"제{no}"
        if not no.endswith("조") and not re.search(r'조의\d+$', no):
            no = f"{no}조"
        
        if no not in target_articles:
            continue
        
        # 제목
        title = ""
        for t in ['조문제목', '조제목']:
            e = art.find(t)
            if e is not None and e.text:
                title = e.text.strip()
                break
        
        # 내용
        parts = []
        for t in ['조문내용', '조내용']:
            e = art.find(t)
            if e is not None and e.text:
                parts.append(e.text.strip())
        
        for hang in art.findall('.//항'):
            ce = hang.find('항내용')
            ce = ce if ce is not None else hang
            if ce is not None and ce.text:
                parts.append(ce.text.strip())
            for ho in hang.findall('.//호'):
                he = ho.find('호내용')
                he = he if he is not None else ho
                if he is not None and he.text:
                    parts.append("  " + he.text.strip())
                for mok in ho.findall('.//목'):
                    me = mok.find('목내용')
                    me = me if me is not None else mok
                    if me is not None and me.text:
                        parts.append("    " + me.text.strip())
        
        content = "\n".join(parts)
        if content:
            results.append({"article_no": no, "article_title": title, "content": content})
            ok(f"  {no} {title} — {len(content)}자")
        else:
            warn(f"  {no} — 내용 비어있음")
    
    return results


def _extract_from_lawapi_dict(detail: dict, target_articles):
    """LawAPI(MCP) 반환 dict에서 target_articles에 해당하는 조문만 추출.
    detail: {"법령명","시행일자","조문목록":[{"조문번호","조문제목","조문내용",...}, ...]}
    """
    results = []
    target_set = set(target_articles)
    raw_articles = detail.get("조문목록") or []
    info(f"  MCP 조문 {len(raw_articles)}개 수신")

    # 1차: 구조화된 조문 매칭
    structured_hits = {}
    for art in raw_articles:
        if not isinstance(art, dict):
            continue
        raw_no = str(art.get("조문번호", "") or "").strip()
        content = str(art.get("조문내용", "") or "").strip()
        if not content:
            continue
        no = _normalize_article_no(raw_no)
        if no in target_set:
            structured_hits[no] = {
                "title": str(art.get("조문제목", "") or "").strip(),
                "content": content,
            }

    for no in target_articles:
        if no in structured_hits:
            data = structured_hits[no]
            results.append({
                "article_no": no,
                "article_title": data["title"],
                "content": data["content"],
            })
            ok(f"  {no} {data['title']} — {len(data['content'])}자")

    # 2차: 행정규칙처럼 조문번호가 본문 앞부분에 인라인된 경우
    if len(results) < len(target_articles):
        missing = target_set - {r["article_no"] for r in results}
        if missing:
            inline_pool = []
            for art in raw_articles:
                if not isinstance(art, dict):
                    continue
                content = str(art.get("조문내용", "") or "").strip()
                if content:
                    inline_pool.append(content)
            for art_no in list(missing):
                merged_parts = []
                title = ""
                for text in inline_pool:
                    if text.startswith(art_no) or re.match(
                        rf"^{re.escape(art_no)}\b", text
                    ):
                        m = re.match(rf"^{re.escape(art_no)}\s*[\(（]([^)）]+)[\)）]", text)
                        if m and not title:
                            title = m.group(1)
                        merged_parts.append(text)
                if merged_parts:
                    content = "\n\n".join(merged_parts)
                    results.append({
                        "article_no": art_no,
                        "article_title": title,
                        "content": content,
                    })
                    ok(f"  {art_no} {title} — {len(content)}자 (인라인 매칭)")

    if not results:
        seen_nos = [
            _normalize_article_no(str(a.get("조문번호", "") or ""))
            for a in raw_articles if isinstance(a, dict)
        ]
        warn(f"대상 조문 미발견. MCP 응답의 조문번호 샘플: {seen_nos[:20]}")
    return results


def _extract_admrul_articles(content_elems, target_articles):
    """행정규칙 전용 파서 — <조문내용> 텍스트에서 조문번호를 정규식으로 추출.
    
    행정규칙은 <조문단위>/<조문번호> 구조가 없고, 
    <조문내용> 하나에 "제3조(특허요건) ①..." 형태로 통째로 들어있음.
    """
    results = []
    
    # 모든 <조문내용> 텍스트를 조문번호 기준으로 분류
    article_map = {}  # {"제3조": [텍스트1, 텍스트2, ...]}
    
    for elem in content_elems:
        text = (elem.text or "").strip()
        if not text:
            continue
        
        # 텍스트 시작에서 조문번호 추출: "제3조", "제10조의2" 등
        match = re.match(r'(제\d+조(?:의\d+)?)', text)
        if match:
            art_no = match.group(1)
            if art_no in target_articles:
                # 제목 추출: "제3조(특허요건)" → "특허요건"
                title_match = re.match(r'제\d+조(?:의\d+)?\s*[\(（]([^)）]+)[\)）]', text)
                title = title_match.group(1) if title_match else ""
                
                if art_no not in article_map:
                    article_map[art_no] = {"title": title, "parts": []}
                article_map[art_no]["parts"].append(text)
    
    # 결과 조립
    for art_no in target_articles:
        if art_no in article_map:
            data = article_map[art_no]
            content = "\n\n".join(data["parts"])
            title = data["title"]
            results.append({"article_no": art_no, "article_title": title, "content": content})
            ok(f"  {art_no} {title} — {len(content)}자")
    
    if not results:
        # 디버그: 어떤 조문번호가 텍스트에 있는지 확인
        found_nos = set()
        for elem in content_elems:
            text = (elem.text or "").strip()
            match = re.match(r'(제\d+조(?:의\d+)?)', text)
            if match:
                found_nos.add(match.group(1))
        warn(f"대상 조문 미발견. 텍스트에서 발견된 조문번호: {sorted(found_nos)[:20]}")
    
    return results


# ── Supabase 저장 ─────────────────────────────────────────
def _generate_law_id(law_short, article_no):
    """기존 DB의 ID 패턴에 맞춰 ID 생성.
    예: 대규모유통업법 + 제6조 → retail_act_06
    """
    prefix_map = {
        "대규모유통업법": "retail_act",
        "대규모유통업법 시행령": "retail_decree",
        "공정거래법": "fair_trade_act",
        "하도급법": "subcontract_act",
        "관세법": "customs_act",
        "소비자기본법": "consumer_act",
        "표시광고법": "ads_act",
        "건강기능식품법": "health_food_act",
        "식품위생법": "food_safety_act",
        "부가가치세법": "vat_act",
        "개별소비세법": "excise_act",
        "주세법": "liquor_tax_act",
        "상생협력법": "coexist_act",
        "대외무역법": "trade_act",
        "외국환거래법": "forex_act",
        "환급특례법": "refund_act",
        "유통산업발전법": "distribution_act",
        "보세판매장고시": "bonded_shop_notice",
        "상표법": "trademark_act",
        "부정경쟁방지법": "unfair_comp_act",
        "형법": "criminal_act",
    }
    prefix = prefix_map.get(law_short, law_short.replace(" ", "_"))
    # 제6조 → 06, 제45조 → 45
    num = re.sub(r'[^0-9]', '', article_no)
    if num and len(num) == 1:
        num = "0" + num
    return f"{prefix}_{num}"

def update_supabase(sb, law_short, law_name, articles):
    updated = unchanged = 0
    for art in articles:
        try:
            # 기존 데이터 조회
            existing = sb.table("laws").select("id, content").eq("law_short", law_short).eq("article_no", art["article_no"]).execute()
            
            if existing.data:
                # 기존 레코드 있음 → 내용 비교 후 변경 시만 업데이트
                if existing.data[0].get("content", "").strip() == art["content"].strip():
                    unchanged += 1
                    continue
                # 변경됨 → 기존 ID로 업데이트
                sb.table("laws").update({
                    "content": art["content"],
                    "article_title": art["article_title"],
                    "last_updated": datetime.now().isoformat(),
                }).eq("id", existing.data[0]["id"]).execute()
                updated += 1
            else:
                # 새 레코드 → id 생성 + 전체 컬럼 insert
                new_id = _generate_law_id(law_short, art["article_no"])
                sb.table("laws").insert({
                    "id": new_id,
                    "law_name": law_name,
                    "law_short": law_short,
                    "article_no": art["article_no"],
                    "article_title": art["article_title"],
                    "content": art["content"],
                    "last_updated": datetime.now().isoformat(),
                }).execute()
                updated += 1
                
        except Exception as e:
            fail(f"  DB 오류 ({law_short} {art['article_no']}): {e}")
    return updated, unchanged


# ── 메인 ─────────────────────────────────────────────────
def main():
    header("📚 법령 DB 자동 업데이트 (data.go.kr)")
    print(f"  실행: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  대상: {sum(len(l['articles']) for l in TARGET_LAWS)}개 조문 / {len(TARGET_LAWS)}개 법령")
    
    header("1. 설정 확인")
    
    key = load_secret("DATA_GO_KR_KEY")
    if not key:
        fail("DATA_GO_KR_KEY 없음")
        print(f"\n  {C.BOLD}설정 방법:{C.END}")
        print(f"  1. https://www.data.go.kr 회원가입 (일반인 OK)")
        print(f"  2. 아래 URL에서 '활용신청' 클릭 → 즉시 키 발급")
        print(f"     https://www.data.go.kr/data/15000115/openapi.do")
        print(f"  3. 마이페이지 → 인증키 발급현황 → '인코딩' 키 복사")
        print(f"  4. .streamlit/secrets.toml 에 추가:")
        print(f'     DATA_GO_KR_KEY = "복사한인코딩키"')
        sys.exit(1)
    ok(f"DATA_GO_KR_KEY: {key[:12]}...")
    
    sb_url = load_secret("SUPABASE_URL")
    sb_key = load_secret("SUPABASE_KEY")
    if not sb_url or not sb_key:
        fail("SUPABASE 설정 없음")
        sys.exit(1)
    
    try:
        from supabase import create_client
        sb = create_client(sb_url, sb_key)
        ok("Supabase OK")
    except Exception as e:
        fail(f"Supabase 실패: {e}")
        sys.exit(1)

    total_up = total_unch = total_fail = 0
    
    for law in TARGET_LAWS:
        header(f"📖 {law['law_short']} ({len(law['articles'])}개 조문)")
        
        law_id, detail_link = search_law_id(key, law["law_name"])
        if not law_id:
            total_fail += len(law["articles"])
            continue
        time.sleep(0.5)
        
        root = fetch_law_articles(key, law_id, detail_link)
        if root is None:
            total_fail += len(law["articles"])
            continue
        
        arts = extract_articles(root, law["articles"])
        if not arts:
            warn("추출 실패")
            total_fail += len(law["articles"])
            continue
        
        missing = set(law["articles"]) - {a["article_no"] for a in arts}
        if missing:
            warn(f"미발견: {', '.join(sorted(missing))}")
            total_fail += len(missing)
        
        u, uc = update_supabase(sb, law["law_short"], law["law_name"], arts)
        total_up += u
        total_unch += uc
        info(f"업데이트 {u}건 / 변경없음 {uc}건")
        time.sleep(1)
    
    # ── 행정규칙 처리 ──
    for admrul in TARGET_ADMRULS:
        header(f"📜 {admrul['law_short']} ({len(admrul['articles'])}개 조문)")
        
        admrul_id, detail_link = search_admrul_id(key, admrul["admrul_name"])
        if not admrul_id:
            total_fail += len(admrul["articles"])
            continue
        time.sleep(0.5)
        
        root = fetch_admrul_articles(key, admrul_id, detail_link)
        if root is None:
            total_fail += len(admrul["articles"])
            continue
        
        arts = extract_articles(root, admrul["articles"])
        if not arts:
            warn("추출 실패")
            total_fail += len(admrul["articles"])
            continue
        
        missing = set(admrul["articles"]) - {a["article_no"] for a in arts}
        if missing:
            warn(f"미발견: {', '.join(sorted(missing))}")
            total_fail += len(missing)
        
        u, uc = update_supabase(sb, admrul["law_short"], admrul["admrul_name"], arts)
        total_up += u
        total_unch += uc
        info(f"업데이트 {u}건 / 변경없음 {uc}건")
        time.sleep(1)
    
    header("📊 최종 결과")
    print(f"  {C.OK}✅ 업데이트: {total_up}건{C.END}")
    print(f"  {C.DIM}⏸️  변경없음: {total_unch}건{C.END}")
    print(f"  {C.FAIL}❌ 실패:     {total_fail}건{C.END}")
    print(f"  {'─'*30}")
    
    if total_fail == 0:
        print(f"  {C.OK}{C.BOLD}🎉 모든 조문 최신 상태!{C.END}")
    elif total_up > 0:
        print(f"  {C.WARN}{C.BOLD}⚠️ 일부 실패, {total_up}건 업데이트 완료{C.END}")
    else:
        print(f"  {C.FAIL}{C.BOLD}⛔ 실패 — API키/네트워크 확인{C.END}")
    
    print()
    return {"updated": total_up, "unchanged": total_unch, "failed": total_fail}


def run_update():
    """legal_ai.py에서 import하여 호출할 수 있는 래퍼 함수."""
    return main()


if __name__ == "__main__":
    result = main()
    if result["failed"] > 0 and result["updated"] == 0:
        sys.exit(1)
