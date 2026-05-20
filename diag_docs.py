#!/usr/bin/env python3
"""
diag_docs.py — Supabase docs 테이블 진단 (이슈 2: 사규 매핑 부재)
사용법:
  - Streamlit Cloud 콘솔 또는 secrets.toml/환경변수가 있는 곳에서:
      python diag_docs.py
  - SUPABASE_URL / SUPABASE_KEY 를 .streamlit/secrets.toml 또는 환경변수로 읽음
"""
import os


def _load_secret(key):
    # 1) 환경변수
    v = os.environ.get(key)
    if v:
        return v
    # 2) .streamlit/secrets.toml
    path = os.path.join(os.getcwd(), ".streamlit", "secrets.toml")
    if os.path.exists(path):
        try:
            import tomllib
            with open(path, "rb") as f:
                data = tomllib.load(f)
            return data.get(key, "")
        except Exception:
            pass
    return ""


def main():
    url = _load_secret("SUPABASE_URL")
    key = _load_secret("SUPABASE_KEY")
    if not url or not key:
        print("❌ SUPABASE_URL / SUPABASE_KEY 를 찾지 못했습니다.")
        return

    from supabase import create_client
    client = create_client(url, key)

    docs = client.table("docs").select("id,name,cat,label").execute()
    rows = docs.data or []
    print(f"총 문서: {len(rows)}건\n")

    from collections import Counter
    cat_counts = Counter(d.get("cat", "?") for d in rows)
    print("카테고리별 분포:")
    for cat, n in cat_counts.most_common():
        print(f"  - {cat}: {n}건")

    saryu_cats = ("saryu", "contract", "yakjeong")
    saryu_rows = [d for d in rows if d.get("cat") in saryu_cats]
    print(f"\n사규/계약/약정 분류 문서: {len(saryu_rows)}건")
    print("제목 샘플(최대 15):")
    for d in saryu_rows[:15]:
        print(f"  - [{d.get('cat','?')}] {d.get('label') or d.get('name','?')}")


if __name__ == "__main__":
    main()
