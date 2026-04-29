"""NEXUS AI · 임직원용 Streamlit 프론트엔드 (PoC)."""

from __future__ import annotations

import streamlit as st

from nexus_ai.core.chatbot import ask
from nexus_ai.core.config import CATEGORIES, load_hotlines, settings


st.set_page_config(page_title="NEXUS AI", page_icon="🛡️", layout="wide")


@st.cache_resource(show_spinner=False)
def _supabase():
    from supabase import create_client
    s = settings()
    if not s.supabase_url or not s.supabase_key:
        return None
    return create_client(s.supabase_url, s.supabase_key)


def _sidebar():
    with st.sidebar:
        st.markdown("### 🛡️ NEXUS AI")
        st.caption("전사 지능형 사규·사건사고 대응 어시스턴트")
        st.markdown("---")
        st.markdown("**카테고리 선택** *(질의 범위 최적화)*")
        cat = st.selectbox(
            "카테고리",
            options=("전체",) + CATEGORIES,
            index=0,
            label_visibility="collapsed",
        )
        st.markdown("---")
        st.caption(
            "본 챗봇은 사규/윤리 관점 답변을 제공합니다. "
            "인사 행정 사항(채용·평가·복무, 신고·조사 절차)은 인사팀으로 문의하세요."
        )
        return cat


def _hotline_button(hotlines: dict[str, str]):
    url = hotlines.get("ethics_hotline_url") or hotlines.get("internal_report_url")
    if url:
        st.link_button("🔔 윤리팀 익명 제보 채널로 연결", url, use_container_width=True)


def main():
    sb = _supabase()
    cat = _sidebar()

    if sb is None:
        st.error("⚠️ Supabase 설정이 없습니다. SUPABASE_URL / SUPABASE_KEY 를 secrets 에 추가하세요.")
        st.stop()

    if "history" not in st.session_state:
        st.session_state["history"] = []  # list[(role, content, meta)]

    hotlines = load_hotlines(sb)

    st.markdown("## 무엇을 도와드릴까요?")
    st.caption("사규/윤리강령/사례집/징계규정을 통합 검색합니다. (출처 자동 표기)")

    for role, content, meta in st.session_state["history"]:
        with st.chat_message(role):
            st.markdown(content)
            if role == "assistant" and meta.get("contexts"):
                with st.expander("참조 문서 보기"):
                    for c in meta["contexts"]:
                        head = c.get("doc_title") or "문서"
                        if c.get("article_no"):
                            head += f" · {c['article_no']}"
                        elif c.get("case_no"):
                            head += f" · 사례집 #{c['case_no']}"
                        st.markdown(f"**{head}**")
                        st.caption((c.get("text") or "")[:600])
            if role == "assistant":
                _hotline_button(hotlines)

    q = st.chat_input("질문을 입력하세요. 이름·부서 등 식별정보는 자동 마스킹됩니다.")
    if not q:
        return

    st.session_state["history"].append(("user", q, {}))
    with st.chat_message("user"):
        st.markdown(q)

    with st.chat_message("assistant"):
        with st.spinner("관련 사규 검색 및 답변 생성 중..."):
            try:
                ans = ask(sb, question=q, category=cat)
            except Exception as e:
                st.error(f"오류가 발생했습니다: {e}")
                return

        if ans.is_critical:
            st.warning("⚠️ 본 사안은 **심각 사안 응답 모드**로 처리되었습니다.")
        st.markdown(ans.text)

        with st.expander("참조 문서 보기"):
            for c in ans.contexts:
                head = c.get("doc_title") or "문서"
                if c.get("article_no"):
                    head += f" · {c['article_no']}"
                elif c.get("case_no"):
                    head += f" · 사례집 #{c['case_no']}"
                st.markdown(f"**{head}**")
                st.caption((c.get("text") or "")[:600])

        _hotline_button(hotlines)

    st.session_state["history"].append((
        "assistant", ans.text,
        {"contexts": ans.contexts, "critical": ans.is_critical, "kind": ans.critical_kind},
    ))


if __name__ == "__main__":
    main()
