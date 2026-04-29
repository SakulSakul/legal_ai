"""NEXUS AI · 관리자용 Streamlit 대시보드 (PoC).

기능:
- DOCX 업로드 → 청킹 → 카테고리 자동 추천 → 관리자 확인 → 적재
- 사규 버전 목록 (active / archived)
- 리스크 트렌드 레이더 (카테고리별 질의 빈도, k-anonymity 5 보장)
- Phase 3.5 도메인 검수 (샘플 등록 + CSV 일괄 + 회차 실행 + 4지표 채점)
- 핫라인/안내 문구 편집 (사내 익명제보, 외부 상담채널, 인사 라우팅)
- 심각 사안 키워드 사전 편집
"""

from __future__ import annotations

import csv
import datetime as dt
import io
from collections import Counter, defaultdict

import streamlit as st

from nexus_ai.core.config import CATEGORIES, settings
from nexus_ai.core.review import run_review, threshold_breached
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


def _tab_review(sb):
    st.subheader("🔬 Phase 3.5 도메인 검수")
    st.caption(
        "윤리·CSR·안전·정보보안팀이 검증 항목을 등록하고, 회차 단위로 자동 채점합니다. "
        "통과 기준 미달 시 Phase 3 회귀를 검토하세요."
    )

    sub_add, sub_csv, sub_list, sub_run, sub_runs = st.tabs(
        ["➕ 단건 등록", "📂 CSV 일괄", "📋 샘플 목록", "▶ 회차 실행", "📊 회차 결과"]
    )

    # ── 단건 등록 ──────────────────────────────────────────
    with sub_add:
        with st.form("review_add"):
            c1, c2 = st.columns(2)
            with c1:
                domain = st.selectbox(
                    "검수 도메인",
                    options=["윤리", "CSR", "안전", "정보보안", "공정거래",
                             "재무", "영업", "총무", "환경", "기타"],
                )
                category = st.selectbox(
                    "질의 카테고리", options=("자동",) + CATEGORIES, index=0
                )
                expected_critical = st.checkbox("심각 사안 응답 모드 트리거 기대")
                expected_kind = st.selectbox(
                    "심각 사안 종류 (선택)",
                    options=["", "safety", "harassment"],
                    disabled=not expected_critical,
                )
            with c2:
                created_by = st.text_input("작성자 (검수자명/팀)", value="")
                expected_citation = st.text_input(
                    "기대 출처 패턴 (예: 윤리강령 제4조)",
                    value="",
                )
                expected_keywords_raw = st.text_area(
                    "기대 키워드 (쉼표 구분, 답변에 포함되어야 함)",
                    value="",
                    height=80,
                )
            question = st.text_area("평가용 질문", height=120)
            notes = st.text_area("메모 (선택)", height=80)
            submit = st.form_submit_button("등록", type="primary")

        if submit:
            if not question.strip():
                st.error("질문을 입력하세요.")
            else:
                kws = [k.strip() for k in expected_keywords_raw.split(",") if k.strip()]
                sb.table("review_samples").insert({
                    "category": None if category == "자동" else category,
                    "question": question.strip(),
                    "expected_keywords": kws,
                    "expected_citation": expected_citation.strip() or None,
                    "expected_critical": bool(expected_critical),
                    "expected_critical_kind": expected_kind or None,
                    "domain": domain,
                    "notes": notes.strip() or None,
                    "created_by": created_by.strip() or None,
                }).execute()
                st.success("샘플이 등록되었습니다.")

    # ── CSV 일괄 ───────────────────────────────────────────
    with sub_csv:
        st.markdown(
            "**CSV 컬럼:** `domain, category, question, expected_keywords, "
            "expected_citation, expected_critical, expected_critical_kind, notes`  \n"
            "`expected_keywords` 는 `;` 로 구분, `expected_critical` 은 `true/false`."
        )
        upl = st.file_uploader("CSV 업로드", type=["csv"])
        if upl:
            text = upl.read().decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            rows: list[dict] = []
            for r in reader:
                cat = (r.get("category") or "").strip()
                rows.append({
                    "domain": (r.get("domain") or "").strip() or None,
                    "category": cat if cat in CATEGORIES else None,
                    "question": (r.get("question") or "").strip(),
                    "expected_keywords": [
                        k.strip() for k in (r.get("expected_keywords") or "").split(";")
                        if k.strip()
                    ],
                    "expected_citation": (r.get("expected_citation") or "").strip() or None,
                    "expected_critical":
                        str(r.get("expected_critical","")).strip().lower() in ("1","true","y","yes"),
                    "expected_critical_kind":
                        (r.get("expected_critical_kind") or "").strip() or None,
                    "notes": (r.get("notes") or "").strip() or None,
                })
            rows = [r for r in rows if r["question"]]
            st.write(f"미리보기: {len(rows)} 건")
            st.dataframe(rows[:20], use_container_width=True)
            if rows and st.button("일괄 등록", type="primary"):
                BATCH = 50
                for i in range(0, len(rows), BATCH):
                    sb.table("review_samples").insert(rows[i:i+BATCH]).execute()
                st.success(f"{len(rows)} 건 등록 완료")

    # ── 샘플 목록 ──────────────────────────────────────────
    with sub_list:
        only_active = st.toggle("active 만 표시", value=True, key="rv_active")
        q = sb.table("review_samples").select("*").order("id", desc=True)
        if only_active:
            q = q.eq("is_active", True)
        rows = q.execute().data or []
        st.write(f"총 {len(rows)} 건")
        if rows:
            st.dataframe(rows, use_container_width=True)
            ids_to_disable = st.multiselect(
                "비활성화할 샘플 ID", options=[r["id"] for r in rows]
            )
            if ids_to_disable and st.button("비활성화"):
                sb.table("review_samples").update({"is_active": False}).in_(
                    "id", ids_to_disable
                ).execute()
                st.success(f"{len(ids_to_disable)} 건 비활성화")

    # ── 회차 실행 ──────────────────────────────────────────
    with sub_run:
        st.caption("선택한 샘플(또는 active 전체) 에 대해 챗봇을 실행하고 4지표로 자동 채점합니다.")
        active = (
            sb.table("review_samples").select("id,domain,question,category")
              .eq("is_active", True).order("id").execute().data or []
        )
        if not active:
            st.info("active 샘플이 없습니다.")
        else:
            opts = {f"#{r['id']} [{r.get('domain') or '-'}] {r['question'][:60]}": r["id"]
                    for r in active}
            chosen = st.multiselect("실행할 샘플 (비우면 전체)", list(opts.keys()))
            triggered_by = st.text_input("실행자", value="")
            if st.button("▶ 검수 실행", type="primary"):
                ids = [opts[k] for k in chosen] if chosen else None
                with st.spinner("검수 실행 중... (샘플당 LLM 1회 호출)"):
                    res = run_review(sb, sample_ids=ids,
                                     triggered_by=triggered_by or None)
                if not res.get("run_id"):
                    st.warning(res.get("message"))
                else:
                    st.success(f"회차 #{res['run_id']} 종료 — "
                               f"통과 {res['passed']}/{res['total']}")
                    st.json(res["metrics"])

    # ── 회차 결과 ──────────────────────────────────────────
    with sub_runs:
        runs = (
            sb.table("review_runs").select("*").order("id", desc=True).limit(20)
              .execute().data or []
        )
        if not runs:
            st.info("실행 이력이 없습니다.")
            return
        run_label = {f"#{r['id']} {r['started_at'][:19]} ({r['passed']}/{r['total']})": r
                     for r in runs}
        sel = st.selectbox("회차 선택", list(run_label.keys()))
        run = run_label[sel]
        col1, col2, col3 = st.columns(3)
        m = run.get("metrics") or {}
        col1.metric("통과율", f"{(m.get('pass_rate',0)*100):.1f}%")
        col2.metric("정확도 평균", f"{(m.get('accuracy_avg',0)*100):.1f}%")
        col3.metric("핫라인 누락률", f"{(m.get('hotline_missing_avg',0)*100):.1f}%")

        breached = threshold_breached(m, run.get("threshold") or {})
        if breached:
            st.error(
                "🚨 통과 기준 미달 항목: " + ", ".join(breached)
                + " — Phase 3 회귀 검토 필요"
            )
        else:
            st.success("✅ 모든 통과 기준 충족")

        results = (
            sb.table("review_results").select("*")
              .eq("run_id", run["id"]).order("id").execute().data or []
        )
        st.dataframe(results, use_container_width=True)
        # 실패 사유 분포
        all_reasons = [r for x in results for r in (x.get("failure_reasons") or [])]
        if all_reasons:
            st.markdown("#### 실패 사유 분포")
            st.bar_chart(Counter(all_reasons))


def _tab_hotlines(sb):
    st.subheader("📞 핫라인 / 안내 문구 관리")
    st.caption(
        "사내 익명제보 URL · 외부 상담채널 · 인사 라우팅 문구 등을 코드 수정 없이 즉시 반영합니다. "
        "인사 챗봇 오픈 시 `hr_chatbot_url` 만 채우면 자동 전환됩니다."
    )

    rows = sb.table("hotline_config").select("*").order("key").execute().data or []
    existing = {r["key"]: r for r in rows}

    LABELS = {
        "internal_report_url": "사내 익명 제보채널 URL",
        "external_hotline":    "외부 상담채널 (예: 고용노동부 1350)",
        "ethics_hotline_url":  "윤리팀 익명 제보채널 URL",
        "hr_contact_text":     "인사팀 안내 문구 (인사 챗봇 미오픈 시)",
        "hr_chatbot_url":      "인사 챗봇 URL (채우면 자동 전환)",
    }

    with st.form("hotline_form"):
        edited: dict[str, str] = {}
        for key, label in LABELS.items():
            cur = (existing.get(key) or {}).get("value", "")
            if key == "hr_contact_text":
                edited[key] = st.text_area(label, value=cur, height=80)
            else:
                edited[key] = st.text_input(label, value=cur)
        submit = st.form_submit_button("저장", type="primary")

    if submit:
        ts = dt.datetime.utcnow().isoformat()
        for key, val in edited.items():
            sb.table("hotline_config").upsert({
                "key": key,
                "value": val.strip(),
                "description": LABELS[key],
                "updated_at": ts,
            }).execute()
        st.success("저장되었습니다. (사용자 챗봇은 다음 응답부터 즉시 반영)")

    st.markdown("---")
    st.markdown("#### ➕ 사용자 정의 키 추가")
    with st.form("hotline_add"):
        c1, c2 = st.columns([1,2])
        with c1:
            new_key = st.text_input("key (영문/언더스코어)", value="")
        with c2:
            new_val = st.text_input("value", value="")
        new_desc = st.text_input("설명 (선택)", value="")
        if st.form_submit_button("추가"):
            if not new_key.strip():
                st.error("key 를 입력하세요.")
            else:
                sb.table("hotline_config").upsert({
                    "key": new_key.strip(),
                    "value": new_val.strip(),
                    "description": new_desc.strip() or None,
                }).execute()
                st.success("추가되었습니다.")


def _tab_keywords(sb):
    st.subheader("🚨 심각 사안 키워드 사전")
    st.caption(
        "안전(safety) / 괴롭힘·성희롱(harassment) 트리거 키워드를 직접 관리합니다. "
        "도메인 전문가 검수 후 보강하세요."
    )
    rows = (
        sb.table("critical_keywords").select("*")
          .order("kind").order("keyword").execute().data or []
    )
    safety = [r for r in rows if r["kind"] == "safety"]
    harass = [r for r in rows if r["kind"] == "harassment"]

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### 🦺 safety")
        st.dataframe(safety, use_container_width=True, hide_index=True)
    with c2:
        st.markdown("#### 🛑 harassment")
        st.dataframe(harass, use_container_width=True, hide_index=True)

    with st.form("kw_add"):
        kc1, kc2, kc3 = st.columns([1, 2, 1])
        with kc1:
            kind = st.selectbox("종류", options=["safety", "harassment"])
        with kc2:
            keyword = st.text_input("키워드")
        with kc3:
            active = st.checkbox("활성", value=True)
        if st.form_submit_button("추가", type="primary"):
            if not keyword.strip():
                st.error("키워드를 입력하세요.")
            else:
                sb.table("critical_keywords").upsert({
                    "kind": kind, "keyword": keyword.strip(), "is_active": active,
                }).execute()
                st.success("추가/갱신되었습니다.")
                st.rerun()

    with st.expander("키워드 비활성화 / 활성화"):
        all_kws = [f"[{r['kind']}] {r['keyword']} ({'on' if r['is_active'] else 'off'})"
                   for r in rows]
        idx = st.multiselect("선택", options=list(range(len(rows))),
                             format_func=lambda i: all_kws[i])
        target_state = st.radio("상태", options=["활성화", "비활성화"], horizontal=True)
        if st.button("적용"):
            new_state = (target_state == "활성화")
            for i in idx:
                sb.table("critical_keywords").update(
                    {"is_active": new_state}
                ).eq("id", rows[i]["id"]).execute()
            st.success(f"{len(idx)} 건 갱신")
            st.rerun()


def main():
    sb = _supabase()
    if sb is None:
        st.error("⚠️ Supabase 설정이 없습니다.")
        st.stop()

    st.title("🛠️ NEXUS AI · Admin")
    tabs = st.tabs([
        "📥 업로드", "📚 버전", "📡 레이더",
        "🔬 검수 (Phase 3.5)", "📞 핫라인", "🚨 키워드",
    ])
    with tabs[0]: _tab_upload(sb)
    with tabs[1]: _tab_versions(sb)
    with tabs[2]: _tab_radar(sb)
    with tabs[3]: _tab_review(sb)
    with tabs[4]: _tab_hotlines(sb)
    with tabs[5]: _tab_keywords(sb)


if __name__ == "__main__":
    main()
