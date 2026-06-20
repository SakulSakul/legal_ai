"""
test_staleness.py — Step 6 법령 staleness 감지 (key-free·mocked MCP)

검증:
  - 비교 로직: penalty 숫자가 현재 법령 텍스트에 등장 → OK / 다른 형량 → STALE /
    못 가져옴·모호 → NEEDS_REVIEW (false OK/STALE보다 안전)
  - mock MCP로 fetch→비교 통합 흐름 (실 MCP·키 없음)
  - staleness_util은 streamlit·키·MCP를 일절 import 안 함 (격리)
API·시크릿·실 MCP 일절 사용하지 않는다.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

import staleness_util as S  # noqa: E402

_PAD = " 이 조문은 충분히 길어 30자 임계를 넘기기 위한 패딩 텍스트입니다."


# ── 비교 로직: 징역 ──────────────────────────────────────
def test_imprisonment_ok():
    p = {"imprisonment_max_years": 20, "fine_max_krw": None, "corporate_fine_max_krw": None, "law_ref": "형법 제347조"}
    r = S.check_penalty_staleness(p, "제347조(사기) ... 20년 이하의 징역 또는 5천만원 이하의 벌금에 처한다." + _PAD)
    assert r["verdict"] == "OK", r


def test_imprisonment_stale():
    p = {"imprisonment_max_years": 20, "fine_max_krw": None, "corporate_fine_max_krw": None, "law_ref": "형법 제347조"}
    r = S.check_penalty_staleness(p, "제347조(사기) ... 10년 이하의 징역 또는 2천만원 이하의 벌금에 처한다." + _PAD)
    assert r["verdict"] == "STALE", r


def test_needs_review_empty_or_failed():
    p = {"imprisonment_max_years": 20, "fine_max_krw": None, "corporate_fine_max_krw": None, "law_ref": "형법 제347조"}
    assert S.check_penalty_staleness(p, "")["verdict"] == "NEEDS_REVIEW"
    assert S.check_penalty_staleness(p, "MCP 서버 연결 실패")["verdict"] == "NEEDS_REVIEW"


def test_needs_review_ambiguous():
    """형량 표현이 없는 텍스트 → STALE로 단정하지 않고 NEEDS_REVIEW."""
    p = {"imprisonment_max_years": 20, "fine_max_krw": None, "corporate_fine_max_krw": None, "law_ref": "형법 제347조"}
    r = S.check_penalty_staleness(p, "이 조문은 정의 규정으로 형량에 대한 언급이 전혀 없는 일반 조항 텍스트입니다 패딩.")
    assert r["verdict"] == "NEEDS_REVIEW", r


# ── 비교 로직: 벌금/양벌 ─────────────────────────────────
def test_fine_ok():
    p = {"imprisonment_max_years": None, "fine_max_krw": 50000000, "corporate_fine_max_krw": None, "law_ref": "x"}
    r = S.check_penalty_staleness(p, "... 5천만원 이하의 벌금에 처한다." + _PAD)
    assert r["verdict"] == "OK", r


def test_fine_stale():
    p = {"imprisonment_max_years": None, "fine_max_krw": 50000000, "corporate_fine_max_krw": None, "law_ref": "x"}
    r = S.check_penalty_staleness(p, "... 2천만원 이하의 벌금에 처한다." + _PAD)
    assert r["verdict"] == "STALE", r


def test_corporate_fine_ok():
    p = {"imprisonment_max_years": 7, "fine_max_krw": 100000000, "corporate_fine_max_krw": 300000000, "law_ref": "상표법 제230조"}
    r = S.check_penalty_staleness(p, "7년 이하의 징역 또는 1억원 이하의 벌금. 법인에게 3억원 이하의 벌금." + _PAD)
    assert r["verdict"] == "OK", r


def test_all_null_penalty_needs_review():
    """형사처벌 없는 issue(행정제재) → 검증할 숫자 없음 → NEEDS_REVIEW."""
    p = {"imprisonment_max_years": None, "fine_max_krw": None, "corporate_fine_max_krw": None, "law_ref": "보세판매장고시"}
    r = S.check_penalty_staleness(p, "보세판매장 운영에 관한 고시 제28조 본문 텍스트입니다." + _PAD)
    assert r["verdict"] == "NEEDS_REVIEW", r


# ── mock MCP 통합 흐름 ───────────────────────────────────
def test_mock_mcp_flow_ok_and_review():
    issues = [
        {"id": "fraud", "title": "형법 사기죄", "penalty": {"imprisonment_max_years": 20, "fine_max_krw": 50000000, "corporate_fine_max_krw": None, "law_ref": "형법 제347조"}},
        {"id": "tm", "title": "상표법 위반", "penalty": {"imprisonment_max_years": 7, "fine_max_krw": 100000000, "corporate_fine_max_krw": 300000000, "law_ref": "상표법 제230조"}},
        {"id": "notice", "title": "보세판매장고시", "penalty": {"imprisonment_max_years": None, "fine_max_krw": None, "corporate_fine_max_krw": None, "law_ref": "보세판매장 운영에 관한 고시 제28조"}},
    ]
    texts = {
        "형법 제347조": (True, "제347조 ... 20년 이하의 징역 또는 5천만원 이하의 벌금에 처한다." + _PAD),
        "상표법 제230조": (True, "제230조 ... 7년 이하의 징역 또는 1억원 이하의 벌금. 법인에게 3억원 이하의 벌금." + _PAD),
        "보세판매장 운영에 관한 고시 제28조": (False, "MCP가 고시는 제공하지 않음"),
    }
    report = S.run_staleness_check(issues, lambda ref: texts.get(ref, (False, "")))
    by = {r["issue_id"]: r for r in report}
    assert by["fraud"]["verdict"] == "OK"
    assert by["tm"]["verdict"] == "OK"
    assert by["notice"]["verdict"] == "NEEDS_REVIEW"  # MCP 미제공 → 빈 텍스트
    # 리포트 구조
    assert by["fraud"]["law_ref"] == "형법 제347조"
    assert "fields" in by["fraud"]


def test_mock_mcp_detects_stale():
    issues = [{"id": "fraud", "title": "형법 사기죄", "penalty": {"imprisonment_max_years": 20, "fine_max_krw": None, "corporate_fine_max_krw": None, "law_ref": "형법 제347조"}}]
    def mock(ref):
        return (True, "제347조 ... 10년 이하의 징역 또는 2천만원 이하의 벌금에 처한다." + _PAD)
    report = S.run_staleness_check(issues, mock)
    assert report[0]["verdict"] == "STALE"


# ── 격리: 감지 전용 / key-free ──────────────────────────
def test_staleness_util_is_key_and_streamlit_free():
    src = open(os.path.join(ROOT, "staleness_util.py"), encoding="utf-8").read()
    for forbidden in ("import streamlit", "get_secret", "secrets.toml", "call_mcp", "requests"):
        assert forbidden not in src, f"staleness_util은 키/MCP/streamlit-free여야 함: '{forbidden}' 발견"


def test_detection_only_no_blocks_write():
    """staleness_util은 legal_blocks.json을 쓰지 않는다(감지 전용)."""
    src = open(os.path.join(ROOT, "staleness_util.py"), encoding="utf-8").read()
    assert "open(" not in src and "legal_blocks" not in src, "감지 전용 — 파일 쓰기/블록 수정 금지"
