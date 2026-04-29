"""NEXUS AI · 관리자용 Streamlit 대시보드 (PoC).

기능:
- DOCX 업로드 → 청킹 → 카테고리 자동 추천 → 관리자 확인 → 적재
- 사규 버전 목록 (active / archived)
- 리스크 트렌드 레이더 (카테고리별 질의 빈도, k-anonymity 5 보장)
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

import streamlit as st

from nexus_ai.core.config import CATEGORIES, settings
from nexus_ai.parser.docx_parser import (
    looks_like_hr_procedure, parse_docx, suggest_categories,
)
from nexus_ai.parser.ingest import ingest_docx


st.set_page_config(page_title="NEXUS AI · Admin", page_icon="🛠️", layout="wide")


@st.cache_resource(show_spinner=False)
def _supabase():
    from supabase import create_client
    s = settings()
    if not s.supabase_url or not s.supabase_key:
        return None
    return create_client(s.supabase_url, s.supabase_key)


def _tab_upload(sb):
    st.subheader("📥 DOCX 업로드 및 적재")

    file = st.file_uploader("워드 파일 업로드", type=["docx"])
    if not file:
        return

    file_bytes = file.read()
    title_default = file.name.rsplit(".", 1)[0]
    chunks = parse_docx(file_bytes)

    # 신고절차 문서 자동 차단
    sample = "\n".join(c.text for c in chunks[:6])
    if looks_like_hr_procedure(title_default, sample):
        st.error(
            "🚫 신고·조사 절차 문서로 판단됩니다. NEXUS DB에는 적재하지 않습니다. "
            "(인사 챗봇 전용 영역)"
        )
        return

    auto_cats = suggest_categories(sample)
    st.info(f"자동 추천 카테고리: **{', '.join(auto_cats)}** — 아래에서 확정/수정")

    col1, col2 = st.columns(2)
    with col1:
        title = st.text_input("문서 제목", value=title_default)
        kind = st.selectbox("문서 종류", options=["rule", "case", "penalty"],
                            format_func=lambda x: {"rule":"사규","case":"사례","penalty":"징계"}[x])
        version = st.text_input("개정 차수 (예: v3, 2026-04 개정)", value="v1")
    with col2:
        eff = st.date_input("시행일", value=dt.date.today())
        cats = st.multiselect("카테고리(다중 선택)", options=list(CATEGORIES), default=auto_cats)
        uploader = st.text_input("등록자 (식별용)", value="")

    st.markdown(f"**미리보기 — 청크 {len(chunks)}개**")
    with st.expander("청크 5개 미리보기"):
        for c in chunks[:5]:
            head = c.article_no or (f"#{c.case_no}" if c.case_no else f"chunk {c.chunk_idx}")
            st.markdown(f"**{head}**")
            st.caption(c.text[:500])

    if st.button("✅ 임베딩 + DB 적재 실행", type="primary"):
        if not cats:
            st.error("카테고리를 1개 이상 선택하세요.")
            return
        with st.spinner("임베딩 및 적재 중..."):
            res = ingest_docx(
                sb,
                file_bytes=file_bytes,
                title=title,
                doc_kind=kind,
                version=version,
                effective_date=eff,
                uploaded_by=uploader or None,
                confirmed_categories=cats,
            )
        if res.skipped_hr_procedure:
            st.error("신고절차 문서로 판단되어 적재가 차단되었습니다.")
        else:
            msg = f"적재 완료: 청크 {res.chunks_inserted}개"
            if res.archived_previous:
                msg += " · 이전 버전 자동 archived"
            st.success(msg)


def _tab_versions(sb):
    st.subheader("📚 문서/버전 관리")
    show_archived = st.toggle("archived 포함", value=False)
    q = sb.table("nexus_documents").select("*").order("uploaded_at", desc=True)
    if not show_archived:
        q = q.eq("status", "active")
    rows = q.execute().data or []
    if not rows:
        st.info("등록된 문서가 없습니다.")
        return
    st.dataframe(rows, use_container_width=True)


def _tab_radar(sb):
    st.subheader("📡 리스크 트렌드 레이더")
    days = st.slider("조회 기간(일)", 7, 90, 30)
    since = (dt.datetime.utcnow() - dt.timedelta(days=days)).isoformat()
    rows = (
        sb.table("query_logs")
          .select("ts,category,is_critical,critical_kind,dept_hash")
          .gte("ts", since)
          .execute()
          .data or []
    )
    if not rows:
        st.info("기간 내 질의 로그가 없습니다.")
        return

    # 카테고리별 빈도
    cat_counts = Counter((r["category"] or "공통") for r in rows)
    st.markdown("#### 카테고리별 질의 수")
    st.bar_chart(cat_counts)

    # 일자 × 카테고리 시계열
    series: dict[str, Counter[str]] = defaultdict(Counter)
    for r in rows:
        d = (r["ts"] or "")[:10]
        series[d][r["category"] or "공통"] += 1
    st.markdown("#### 일자별 추이")
    flat = [{"date": d, **dict(c)} for d, c in sorted(series.items())]
    st.line_chart(flat, x="date")

    # 부서별 슬라이스 (k-anonymity: 5 미만은 마스킹)
    st.markdown("#### 부서별 (k=5 보장)")
    dept_counts = Counter(r.get("dept_hash") or "(미식별)" for r in rows)
    safe = {k: v for k, v in dept_counts.items() if v >= 5}
    suppressed = sum(1 for v in dept_counts.values() if v < 5)
    if safe:
        st.bar_chart(safe)
    st.caption(f"k<5 슬라이스 {suppressed}건은 익명성 보호를 위해 표시하지 않습니다.")

    # 심각 사안 비율
    crit = sum(1 for r in rows if r.get("is_critical"))
    st.metric("심각 사안 비율", f"{(crit/len(rows))*100:.1f}%", delta=f"{crit}건")


def main():
    sb = _supabase()
    if sb is None:
        st.error("⚠️ Supabase 설정이 없습니다.")
        st.stop()

    st.title("🛠️ NEXUS AI · Admin")
    t1, t2, t3 = st.tabs(["📥 업로드", "📚 버전", "📡 레이더"])
    with t1: _tab_upload(sb)
    with t2: _tab_versions(sb)
    with t3: _tab_radar(sb)


if __name__ == "__main__":
    main()
