#!/usr/bin/env python3
# ============================================================
#  📚 법령 DB 자동 업데이트 — update_laws.py
#  공공데이터포털(data.go.kr) API → Supabase laws 테이블
#
#  ★ 일반인 즉시 사용 가능 (공무원 인증 불필요) ★
#
#  사전 준비:
#    1. https://www.data.go.kr 회원가입 (일반인 OK, 즉시)
#    2. "법제처_국가법령정보 공유서비스" 활용 신청 → 즉시 인증키 발급
#       URL: https://www.data.go.kr/data/15000115/openapi.do
#    3. .streamlit/secrets.toml에 추가:
#       DATA_GO_KR_KEY = "발급받은인코딩키"
#    4. python update_laws.py 실행
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
TARGET_LAWS = [
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
]

# ── API: 법령 검색 → 법령ID ─────────────────────────────────
def search_law_id(service_key, law_name):
    import requests
    
    url = "http://apis.data.go.kr/1170000/law/lawSearchList.do"
    params = {
        "ServiceKey": service_key,
        "target": "law",
        "query": law_name,
        "display": "5",
        "type": "XML",
    }
    
    info(f"검색: {law_name}")
    try:
        res = requests.get(url, params=params, timeout=30)
        res.raise_for_status()
    except Exception as e:
        fail(f"검색 API 실패: {e}")
        return None
    
    try:
        root = ET.fromstring(res.text)
    except ET.ParseError:
        if "SERVICE_KEY" in res.text or "인증" in res.text:
            fail("API 인증키 오류 — 인코딩 키를 확인하세요")
        else:
            fail(f"XML 파싱 실패 — {res.text[:200]}")
        return None
    
    # 에러 체크
    err = root.findtext('.//returnReasonCode') or root.findtext('.//resultCode')
    if err and err not in ("00", "0"):
        msg = root.findtext('.//returnAuthMsg') or root.findtext('.//resultMsg') or ""
        fail(f"API 오류 ({err}): {msg}")
        return None
    
    # 법령ID 탐색 (다양한 XML 구조 대응)
    for item in root.iter():
        children = list(item)
        if len(children) < 2:
            continue
        
        name_val = ""
        id_val = ""
        for child in children:
            tag = child.tag or ""
            text = (child.text or "").strip()
            if not text:
                continue
            if any(k in tag for k in ['법령명한글', '법령명', 'lawNm']):
                name_val = text
            if any(k in tag for k in ['법령ID', 'MST', '법령일련번호', 'lsId', '법령키']):
                id_val = text
        
        if name_val and id_val and (law_name in name_val or name_val in law_name):
            ok(f"발견: {name_val} (ID: {id_val})")
            return id_val
    
    warn("법령ID 미발견 — 태그 구조 확인 필요")
    tags = [(e.tag, (e.text or "")[:25]) for e in root.iter() if e.text and e.text.strip()]
    info(f"응답 샘플: {tags[:15]}")
    return None


# ── API: 법령 본문(조문) 조회 ────────────────────────────────
def fetch_law_articles(service_key, law_id):
    import requests
    
    url = "http://apis.data.go.kr/1170000/law/lawServiceInfo.do"
    params = {
        "ServiceKey": service_key,
        "target": "law",
        "MST": law_id,
        "type": "XML",
    }
    
    try:
        res = requests.get(url, params=params, timeout=60)
        res.raise_for_status()
        return ET.fromstring(res.text)
    except Exception as e:
        fail(f"조문 조회 실패: {e}")
        return None


# ── 조문 추출 ─────────────────────────────────────────────
def extract_articles(law_root, target_articles):
    results = []
    
    # 조문 태그 탐색
    article_elems = []
    for tag in ['조문단위', '조문', 'Article', 'Jo']:
        article_elems = law_root.findall(f'.//{tag}')
        if article_elems:
            break
    
    if not article_elems:
        for elem in law_root.iter():
            for c in elem:
                if '조문번호' in c.tag or '조번호' in c.tag:
                    article_elems.append(elem)
                    break
    
    if not article_elems:
        tags = sorted(set(e.tag for e in law_root.iter()))
        warn(f"조문 태그 미발견. 태그: {tags[:20]}")
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
            ce = hang.find('항내용') or hang
            if ce is not None and ce.text:
                parts.append(ce.text.strip())
            for ho in hang.findall('.//호'):
                he = ho.find('호내용') or ho
                if he is not None and he.text:
                    parts.append("  " + he.text.strip())
                for mok in ho.findall('.//목'):
                    me = mok.find('목내용') or mok
                    if me is not None and me.text:
                        parts.append("    " + me.text.strip())
        
        content = "\n".join(parts)
        if content:
            results.append({"article_no": no, "article_title": title, "content": content})
            ok(f"  {no} {title} — {len(content)}자")
        else:
            warn(f"  {no} — 내용 비어있음")
    
    return results


# ── Supabase 저장 ─────────────────────────────────────────
def update_supabase(sb, law_short, articles):
    updated = unchanged = 0
    for art in articles:
        record = {
            "law_short": law_short,
            "article_no": art["article_no"],
            "article_title": art["article_title"],
            "content": art["content"],
            "updated_at": datetime.now().isoformat(),
        }
        try:
            existing = sb.table("laws").select("id, content").eq("law_short", law_short).eq("article_no", art["article_no"]).execute()
            if existing.data:
                if existing.data[0].get("content", "").strip() == art["content"].strip():
                    unchanged += 1
                    continue
                record["id"] = existing.data[0]["id"]
            sb.table("laws").upsert(record).execute()
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
    
    import requests
    
    total_up = total_unch = total_fail = 0
    
    for law in TARGET_LAWS:
        header(f"📖 {law['law_short']} ({len(law['articles'])}개 조문)")
        
        law_id = search_law_id(key, law["law_name"])
        if not law_id:
            total_fail += len(law["articles"])
            continue
        time.sleep(0.5)
        
        root = fetch_law_articles(key, law_id)
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
        
        u, uc = update_supabase(sb, law["law_short"], arts)
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
    sys.exit(1 if total_fail > 0 and total_up == 0 else 0)

if __name__ == "__main__":
    main()
