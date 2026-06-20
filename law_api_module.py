"""
법제처 Open API 연동 모듈 v2.0.1
========================
v2.0.1 변경사항 (긴급 수정):
  - _mcp_call()에 tools=[{"type":"mcp_toolset","mcp_server_name":"korean-law"}] 추가
    (mcp_servers만 넘기고 toolset 참조 누락 → Anthropic API 400 에러로 MCP 100% 실패하던 문제)

v2.0 변경사항 (백엔드 전환):
  - 백엔드를 law.go.kr 직접 호출 → korean-law-mcp 경유로 전환
    (https://korean-law-mcp.fly.dev/mcp, 도쿄 리전)
  - 원인: Streamlit Cloud 미국 IP가 law.go.kr에서 차단 (v1.7.1 진단으로 확인)
  - Anthropic Claude(Haiku) + MCP connector beta로 도구 호출 후 결과 JSON 그대로 반환
  - LawAPI 공개 메소드 시그니처 그대로 유지 (UI 코드 무수정)
  - 직접 호출 관련 로직 전부 제거:
    HTTPS/HTTP 폴백, urllib3 Retry/세션, 헤더 우회, 청크 폴백 등
  - 진단 패널은 MCP 연결 상태만 표시하도록 단순화

이전 이력:
  v1.7.1: 봇 차단 회피용 UA 표준화 + 헤더 없는 fallback
  v1.7:   ConnectionReset 대응 (connect/read retry, 청크 폴백, 큰 응답 헤더 튜닝)
  v1.6:   HTTPS 폴백, 재시도, 진단 도구 추가
  v1.5:   expander 제목 40자 제한
  v1.4:   법령 원문 링크 / 판례·해석례 AI 요약
"""

import os
import json
import re
import logging
from typing import Optional, Any
import streamlit as st

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# 설정/시크릿
# ──────────────────────────────────────────────
MCP_SERVER_URL = "https://korean-law-mcp.fly.dev/mcp"
MCP_SERVER_NAME = "korean-law"
MCP_MODEL = "claude-haiku-4-5-20251001"
MCP_BETA = "mcp-client-2025-11-20"


def get_oc():
    """LAW_OC 값 조회 (HTML 원문 링크 생성 용도로만 잔존)."""
    try:
        oc = st.secrets.get("LAW_OC", "")
    except Exception:
        oc = ""
    if not oc:
        oc = os.environ.get("LAW_OC", "")
    return oc or ""


def get_anthropic_key() -> str:
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        key = ""
    if not key:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ValueError("ANTHROPIC_API_KEY가 설정되지 않았습니다.")
    return key


# ──────────────────────────────────────────────
# 약어 매핑
# ──────────────────────────────────────────────
ABBREVIATIONS = {
    "표시광고법": "표시광고", "전상법": "전자상거래", "관세법": "관세법",
    "외환법": "외국환거래법", "관광진흥법": "관광진흥법", "개인정보보호법": "개인정보보호법",
    "공정거래법": "공정거래", "소비자기본법": "소비자기본법", "화장품법": "화장품법",
    "식품위생법": "식품위생법", "약사법": "약사법", "주세법": "주세법",
    "담배사업법": "담배사업법", "대규모유통업법": "대규모유통업", "하도급법": "하도급",
}


def _resolve_keyword(keyword: str) -> str:
    if keyword in ABBREVIATIONS:
        return ABBREVIATIONS[keyword]
    for abbr, resolved in ABBREVIATIONS.items():
        if abbr in keyword:
            remaining = keyword.replace(abbr, "").strip()
            return f"{resolved} {remaining}" if remaining else resolved
    return keyword


# ──────────────────────────────────────────────
# Anthropic MCP 클라이언트
# ──────────────────────────────────────────────
@st.cache_resource
def _get_anthropic_client():
    import anthropic
    return anthropic.Anthropic(api_key=get_anthropic_key())


def _as_dict(obj) -> dict:
    """SDK 객체 또는 dict 모두 dict처럼 다루기 위한 헬퍼."""
    if isinstance(obj, dict):
        return obj
    try:
        return obj.model_dump()
    except Exception:
        pass
    try:
        return dict(obj)
    except Exception:
        return {}


def _get_attr(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_mcp_result(response) -> Any:
    """Anthropic 응답에서 mcp_tool_result 블록의 JSON 데이터 추출."""
    content = _get_attr(response, "content", []) or []
    for block in content:
        btype = _get_attr(block, "type")
        if btype != "mcp_tool_result":
            continue
        if _get_attr(block, "is_error"):
            err = _get_attr(block, "content") or "MCP tool error"
            raise RuntimeError(f"MCP tool error: {err}")
        inner = _get_attr(block, "content") or []
        if isinstance(inner, str):
            return _parse_json_loose(inner)
        for item in inner:
            text = _get_attr(item, "text")
            if text:
                return _parse_json_loose(text)
    # fallback: 일반 text 블록에서 JSON 추출 시도
    for block in content:
        if _get_attr(block, "type") == "text":
            text = _get_attr(block, "text", "")
            if text:
                try:
                    return _parse_json_loose(text)
                except Exception:
                    continue
    raise RuntimeError("MCP 응답에서 결과를 찾지 못함")


def _parse_json_loose(text: str) -> Any:
    """JSON 파싱 (앞뒤 잡음 허용)."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 코드펜스 제거
    m = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 첫 { 또는 [ 부터 끝까지
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise json.JSONDecodeError("결과 JSON 파싱 실패", text, 0)


def _mcp_call(tool_name: str, arguments: dict) -> Any:
    """korean-law-mcp 서버의 지정 도구를 호출하고 결과 JSON 반환."""
    client = _get_anthropic_client()
    args_json = json.dumps(arguments, ensure_ascii=False)
    user_prompt = (
        f"korean-law MCP 서버의 `{tool_name}` 도구를 다음 인자로 정확히 1회 호출하고, "
        f"도구 결과를 추가 설명 없이 JSON 그대로 출력해줘.\n"
        f"arguments: {args_json}"
    )
    system_instruction = (
        "당신은 korean-law MCP 도구의 결과를 그대로 전달하는 프록시입니다. "
        "지정된 도구를 호출하고 도구 결과(JSON)만 반환하세요. 부연 설명, 요약, 마크다운 금지."
    )

    logger.info(f"MCP 호출: tool={tool_name}, args={args_json}")
    try:
        response = client.beta.messages.create(
            model=MCP_MODEL,
            max_tokens=4096,
            betas=[MCP_BETA],
            mcp_servers=[{
                "type": "url",
                "url": MCP_SERVER_URL,
                "name": MCP_SERVER_NAME,
            }],
            tools=[{
                "type": "mcp_toolset",
                "mcp_server_name": MCP_SERVER_NAME,
            }],
            system=system_instruction,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        logger.error(f"MCP API 호출 실패: {type(e).__name__}: {e}")
        raise RuntimeError(f"MCP 호출 실패: {type(e).__name__}: {e}")

    return _extract_mcp_result(response)


# ──────────────────────────────────────────────
# AI 요약 생성 (Gemini 활용) — 기존 유지
# ──────────────────────────────────────────────
def _summarize_with_ai(prompt_text):
    try:
        from google import genai
        from google.genai import types
        try:
            api_key = st.secrets["GEMINI_API_KEY"]
        except Exception:
            api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return None
        client = genai.Client(api_key=api_key)
        if len(prompt_text) > 8000:
            prompt_text = prompt_text[:8000] + "\n...(이하 생략)"
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[types.Content(role="user", parts=[types.Part(text=prompt_text)])],
            config=types.GenerateContentConfig(
                system_instruction="당신은 법률 문서를 일반인이 이해하기 쉽게 요약하는 전문가입니다. 마크다운 형식으로 깔끔하게 작성하세요. 이모지를 적절히 활용하세요.",
            ),
        )
        return response.text
    except Exception as e:
        logger.warning(f"AI 요약 생성 실패: {e}")
        return None


def _summarize_precedent(detail):
    parts = []
    if detail.get("사건명"): parts.append(f"사건명: {detail['사건명']}")
    if detail.get("사건번호"): parts.append(f"사건번호: {detail['사건번호']}")
    if detail.get("선고일자"): parts.append(f"선고일자: {detail['선고일자']}")
    if detail.get("법원명"): parts.append(f"법원: {detail['법원명']}")
    if detail.get("판시사항"): parts.append(f"판시사항:\n{detail['판시사항']}")
    if detail.get("판결요지"): parts.append(f"판결요지:\n{detail['판결요지']}")
    if detail.get("참조조문"): parts.append(f"참조조문: {detail['참조조문']}")
    if detail.get("판례내용"): parts.append(f"판례내용:\n{detail['판례내용'][:3000]}")
    if not parts: return None
    raw_text = "\n\n".join(parts)
    prompt = f"""아래 판례를 일반인(면세점 MD, 바이어)이 이해하기 쉽게 요약해주세요.

반드시 아래 4단계 구조로 작성하세요:

### 📌 결론
- 법원이 최종적으로 어떤 판단을 내렸는지 한두 문장으로 명확하게

### 📋 배경
- 이 사건이 왜 발생했는지, 당사자 간 분쟁의 핵심이 무엇인지 쉬운 말로

### 💡 핵심 이유
- 법원이 그렇게 판단한 주요 근거를 번호 매겨 3개 이내로

### 🏢 실무적 시사점
- 면세점 MD/바이어가 이 판례에서 챙겨야 할 실무 포인트

---
[판례 원문]
{raw_text}"""
    return _summarize_with_ai(prompt)


def _summarize_interpretation(detail):
    parts = []
    if detail.get("안건명"): parts.append(f"안건명: {detail['안건명']}")
    if detail.get("안건번호"): parts.append(f"안건번호: {detail['안건번호']}")
    if detail.get("회답일자"): parts.append(f"회답일자: {detail['회답일자']}")
    if detail.get("회답기관"): parts.append(f"회답기관: {detail['회답기관']}")
    if detail.get("질의요지"): parts.append(f"질의요지:\n{detail['질의요지']}")
    if detail.get("회답"): parts.append(f"회답:\n{detail['회답']}")
    if detail.get("이유"): parts.append(f"이유:\n{detail['이유']}")
    if detail.get("참조조문"): parts.append(f"참조조문: {detail['참조조문']}")
    if not parts: return None
    raw_text = "\n\n".join(parts)
    prompt = f"""아래 법령해석례를 일반인(면세점 MD, 바이어)이 이해하기 쉽게 요약해주세요.

반드시 아래 4단계 구조로 작성하세요:

### 📌 결론 (한줄 요약)
- 법제처가 내린 결론을 한두 문장으로 두괄식 제시

### ❓ 쉽게 풀어쓴 Q&A
- **Q:** 질의 내용을 일상 언어로 바꿔서 질문 형태로
- **A:** 회답 내용을 일상 언어로 바꿔서 답변 형태로

### 💡 핵심 이유
- 법제처가 그렇게 판단한 주요 근거를 번호 매겨 3개 이내로

### 🏢 실무적 의미
- 면세점 MD/바이어 업무에서 이 해석례가 어떤 의미가 있는지

---
[해석례 원문]
{raw_text}"""
    return _summarize_with_ai(prompt)


# ──────────────────────────────────────────────
# 법제처 원문 링크 (HTML 직링크 — Streamlit Cloud 아닌 사용자 브라우저에서 열림)
# ──────────────────────────────────────────────
def _build_law_link(mst, oc):
    return f"https://www.law.go.kr/DRF/lawService.do?OC={oc}&target=law&MST={mst}&type=HTML"


def _build_law_go_kr_link(law_name):
    from urllib.parse import quote
    return f"https://www.law.go.kr/법령/{quote(law_name, safe='')}"


# ──────────────────────────────────────────────
# MCP 응답 → UI 호환 dict 매핑
# ──────────────────────────────────────────────
def _pick(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v not in (None, ""):
            return v
    return default


def _ensure_list(data) -> list:
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("results", "items", "data", "law", "laws", "list"):
            v = data.get(k)
            if isinstance(v, list):
                return v
        return [data]
    return []


def _map_law_item(item: dict) -> dict:
    detail_link = _pick(item, "법령상세링크", "상세링크", "detail_link", "link")
    mst = _pick(item, "MST", "mst", "법령일련번호", "law_id")
    if not mst and isinstance(detail_link, str) and "MST=" in detail_link:
        mst = detail_link.split("MST=")[1].split("&")[0]
    return {
        "법령명": _pick(item, "법령명한글", "법령명", "name", "title"),
        "법령ID": _pick(item, "법령일련번호", "law_id"),
        "MST": mst,
        "시행일자": _pick(item, "시행일자", "enforce_date"),
        "법령종류": _pick(item, "법령종류", "law_type"),
        "소관부처": _pick(item, "소관부처", "ministry"),
        "상세링크": detail_link,
    }


def _map_prec_item(item: dict) -> dict:
    return {
        "판례ID": _pick(item, "판례일련번호", "판례ID", "id", "case_id"),
        "사건명": _pick(item, "사건명", "name", "title"),
        "사건번호": _pick(item, "사건번호", "case_no"),
        "선고일자": _pick(item, "선고일자", "decision_date"),
        "법원명": _pick(item, "법원명", "court"),
        "사건종류": _pick(item, "사건종류명", "사건종류", "case_type"),
        "판결유형": _pick(item, "판결유형", "decision_type"),
        "상세링크": _pick(item, "판례상세링크", "상세링크", "detail_link"),
    }


def _map_interp_item(item: dict) -> dict:
    detail_link = _pick(item, "법령해석례상세링크", "상세링크", "detail_link")
    interp_id = _pick(item, "법령해석례일련번호", "해석례ID", "id")
    if not interp_id and isinstance(detail_link, str) and "ID=" in detail_link:
        interp_id = detail_link.split("ID=")[1].split("&")[0]
    return {
        "해석례ID": interp_id,
        "안건명": _pick(item, "안건명", "name", "title"),
        "안건번호": _pick(item, "안건번호", "case_no"),
        "회답일자": _pick(item, "회답일자", "answer_date"),
        "회답기관": _pick(item, "회답기관", "agency"),
        "상세링크": detail_link,
    }


def _map_law_text(data) -> dict:
    if not isinstance(data, dict):
        return {"error": "법령 본문 응답 형식 오류"}
    if data.get("error"):
        return {"error": str(data["error"])}
    raw_articles = (data.get("조문목록") or data.get("articles")
                    or data.get("조문") or [])
    articles = []
    for art in raw_articles:
        if not isinstance(art, dict):
            continue
        content = _pick(art, "조문내용", "content", "text")
        if not content:
            continue
        articles.append({
            "조문번호": _pick(art, "조문번호", "num"),
            "조문제목": _pick(art, "조문제목", "title"),
            "조문내용": content,
            "조문시행일자": _pick(art, "조문시행일자", "enforce_date"),
        })
    law_name = _pick(data, "법령명", "법령명_한글", "법령명한글", "name")
    sihaeng = _pick(data, "시행일자", "enforce_date")
    if not articles and not law_name:
        return {"error": "조문을 파싱하지 못했습니다."}
    return {"법령명": law_name, "시행일자": sihaeng, "조문목록": articles}


def _map_prec_detail(data) -> dict:
    if not isinstance(data, dict):
        return {"error": "판례 응답 형식 오류"}
    if data.get("error"):
        return {"error": str(data["error"])}
    return {
        "사건명": _pick(data, "사건명", "name", "title"),
        "사건번호": _pick(data, "사건번호", "case_no"),
        "선고일자": _pick(data, "선고일자", "decision_date"),
        "법원명": _pick(data, "법원명", "court"),
        "판시사항": _pick(data, "판시사항", "holding"),
        "판결요지": _pick(data, "판결요지", "summary"),
        "참조조문": _pick(data, "참조조문", "referenced_articles"),
        "참조판례": _pick(data, "참조판례", "referenced_cases"),
        "판례내용": _pick(data, "판례내용", "content", "text"),
    }


def _map_interp_detail(data) -> dict:
    if not isinstance(data, dict):
        return {"error": "해석례 응답 형식 오류"}
    if data.get("error"):
        return {"error": str(data["error"])}
    return {
        "안건명": _pick(data, "안건명", "name", "title"),
        "안건번호": _pick(data, "안건번호", "case_no"),
        "회답일자": _pick(data, "회답일자", "answer_date"),
        "회답기관": _pick(data, "회답기관", "agency"),
        "질의요지": _pick(data, "질의요지", "question"),
        "회답": _pick(data, "회답", "answer"),
        "이유": _pick(data, "이유", "reasoning"),
        "참조조문": _pick(data, "참조조문", "referenced_articles"),
    }


# ──────────────────────────────────────────────
# LawAPI (공개 시그니처 유지, 백엔드만 MCP로 교체)
# ──────────────────────────────────────────────
class LawAPI:
    def __init__(self, oc: Optional[str] = None):
        # oc는 v1.x 호환을 위해 받지만 MCP 호출에는 사용하지 않음
        self.oc = oc or get_oc()
        # ANTHROPIC_API_KEY 사전 검증
        get_anthropic_key()

    def search_law(self, keyword: str, display: int = 5) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        try:
            raw = _mcp_call("search_law", {"keyword": resolved, "display": display})
        except Exception as e:
            return [{"error": f"API 호출 실패: {e}"}]
        return [_map_law_item(item) for item in _ensure_list(raw) if isinstance(item, dict)]

    def get_law_text(self, mst: str, jo: Optional[str] = None) -> dict:
        args = {"mst": mst}
        if jo:
            args["jo"] = jo
        try:
            raw = _mcp_call("get_law_text", args)
        except Exception as e:
            return {"error": f"API 호출 실패: {e}"}
        if isinstance(raw, list) and raw:
            raw = raw[0]
        return _map_law_text(raw)

    def search_precedent(self, keyword: str, display: int = 10) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        try:
            raw = _mcp_call("search_decisions", {
                "keyword": resolved, "display": display, "domain": "prec",
            })
        except Exception as e:
            return [{"error": f"API 호출 실패: {e}"}]
        return [_map_prec_item(item) for item in _ensure_list(raw) if isinstance(item, dict)]

    def search_interpretation(self, keyword: str, display: int = 10) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        try:
            raw = _mcp_call("search_decisions", {
                "keyword": resolved, "display": display, "domain": "expc",
            })
        except Exception as e:
            return [{"error": f"API 호출 실패: {e}"}]
        return [_map_interp_item(item) for item in _ensure_list(raw) if isinstance(item, dict)]

    def get_precedent_detail(self, prec_id: str) -> dict:
        try:
            raw = _mcp_call("get_decision_text", {"id": prec_id, "domain": "prec"})
        except Exception as e:
            return {"error": f"API 호출 실패: {e}"}
        if isinstance(raw, list) and raw:
            raw = raw[0]
        return _map_prec_detail(raw)

    def get_interpretation_detail(self, interp_id: str) -> dict:
        try:
            raw = _mcp_call("get_decision_text", {"id": interp_id, "domain": "expc"})
        except Exception as e:
            return {"error": f"API 호출 실패: {e}"}
        if isinstance(raw, list) and raw:
            raw = raw[0]
        return _map_interp_detail(raw)

    def search_admin_rule(self, keyword: str, display: int = 10) -> list[dict]:
        resolved = _resolve_keyword(keyword)
        try:
            raw = _mcp_call("search_law", {
                "keyword": resolved, "display": display, "target": "admrul",
            })
        except Exception as e:
            return [{"error": f"API 호출 실패: {e}"}]
        results = []
        for item in _ensure_list(raw):
            if not isinstance(item, dict):
                continue
            results.append({
                "행정규칙명": _pick(item, "행정규칙명", "name", "title"),
                "행정규칙ID": _pick(item, "행정규칙일련번호", "id"),
                "시행일자": _pick(item, "시행일자", "enforce_date"),
                "발령기관": _pick(item, "발령기관", "agency"),
                "행정규칙종류": _pick(item, "행정규칙종류", "type"),
            })
        return results

    def get_admin_rule_text(self, admrul_id: str) -> dict:
        """행정규칙 본문 조회 (MCP 경유). get_law_text 도구에 target='admrul' 힌트 전달."""
        try:
            raw = _mcp_call("get_law_text", {"id": admrul_id, "target": "admrul"})
        except Exception as e:
            return {"error": f"API 호출 실패: {e}"}
        if isinstance(raw, list) and raw:
            raw = raw[0]
        return _map_law_text(raw)

    @staticmethod
    def jo_to_code(jo_num: int) -> str:
        return f"{jo_num:04d}00"

    @staticmethod
    def code_to_jo(code: str) -> int:
        try: return int(code[:4])
        except (ValueError, IndexError): return 0

    def build_ai_context(self, keyword, include_law=True, include_precedent=False,
                         include_interpretation=False, max_articles=10) -> str:
        context_parts = []
        if include_law:
            laws = self.search_law(keyword, display=3)
            for law in laws[:2]:
                if "error" in law: continue
                mst = law.get("MST", "")
                if mst:
                    detail = self.get_law_text(mst)
                    if "error" not in detail and detail.get("조문목록"):
                        context_parts.append(f"\n=== {detail['법령명']} (시행 {detail['시행일자']}) ===")
                        for art in detail["조문목록"][:max_articles]:
                            title, content, num = art.get("조문제목", ""), art.get("조문내용", ""), art.get("조문번호", "")
                            if content:
                                header = f"제{LawAPI.code_to_jo(num)}조" if num and len(num) >= 4 else num
                                if title: header += f"({title})"
                                context_parts.append(f"\n{header}\n{content}")
        if include_precedent:
            precs = self.search_precedent(keyword, display=5)
            valid = [p for p in precs if "error" not in p]
            if valid:
                context_parts.append("\n\n=== 관련 판례 ===")
                for p in valid[:3]:
                    context_parts.append(f"\n[{p['사건번호']}] {p['사건명']} ({p['선고일자']}, {p['법원명']})")
        if include_interpretation:
            interps = self.search_interpretation(keyword, display=5)
            valid = [i for i in interps if "error" not in i]
            if valid:
                context_parts.append("\n\n=== 관련 법령해석례 ===")
                for i in valid[:3]:
                    context_parts.append(f"\n[{i['안건번호']}] {i['안건명']} ({i['회답일자']}, {i['회답기관']})")
        if not context_parts:
            return f"'{keyword}'에 대한 법령 정보를 찾지 못했습니다."
        return "\n".join(context_parts)


# ──────────────────────────────────────────────
# API 진단 (MCP 연결 테스트)
# ──────────────────────────────────────────────
def run_api_diagnostics() -> dict:
    result = {
        "backend": "korean-law-mcp",
        "mcp_url": MCP_SERVER_URL,
        "anthropic_key_configured": False,
        "mcp_ok": False,
        "mcp_error": None,
        "sample_result": None,
    }
    try:
        get_anthropic_key()
        result["anthropic_key_configured"] = True
    except Exception as e:
        result["mcp_error"] = str(e)
        return result

    try:
        raw = _mcp_call("search_law", {"keyword": "관세법", "display": 1})
        items = _ensure_list(raw)
        if items:
            mapped = _map_law_item(items[0]) if isinstance(items[0], dict) else {}
            result["sample_result"] = mapped.get("법령명", "(법령명 없음)")
        result["mcp_ok"] = True
    except Exception as e:
        result["mcp_error"] = f"{type(e).__name__}: {e}"

    return result


def render_api_diagnostics_panel():
    """사이드바에 MCP 백엔드 연결 진단 패널 렌더."""
    with st.sidebar:
        st.markdown("---")
        if st.button("🔧 법령 API 연결 진단", key="api_diag_btn", use_container_width=True):
            with st.spinner("korean-law-mcp 진단 중..."):
                st.session_state["api_diag_result"] = run_api_diagnostics()

        diag = st.session_state.get("api_diag_result")
        if diag:
            st.markdown("**🔧 진단 결과 (v2.0 MCP 백엔드)**")
            st.caption(f"백엔드: `{diag.get('backend')}` · URL: `{diag.get('mcp_url')}`")

            if diag.get("anthropic_key_configured"):
                st.success("ANTHROPIC_API_KEY 설정됨")
            else:
                st.error("ANTHROPIC_API_KEY 미설정")

            if diag.get("mcp_ok"):
                st.success("MCP 연결 성공")
                if diag.get("sample_result"):
                    st.caption(f"📄 샘플 응답: {diag['sample_result']}")
            else:
                st.error(f"MCP 호출 실패: {diag.get('mcp_error') or '알 수 없음'}")


# ──────────────────────────────────────────────
# UI: 사이드바 검색 위젯 (UI 영역 변경 없음 — v1.x 그대로)
# ──────────────────────────────────────────────
def render_law_search_sidebar():
    with st.sidebar:
        st.markdown("---")
        st.subheader("📖 법령 실시간 검색")
        law_query = st.text_input("법령 검색어", placeholder="예: 표시광고법, 대규모유통업법, 관세법", key="law_search_input")
        col1, col2, col3 = st.columns(3)
        with col1: inc_law = st.checkbox("법령", value=True, key="inc_law")
        with col2: inc_prec = st.checkbox("판례", value=False, key="inc_prec")
        with col3: inc_interp = st.checkbox("해석례", value=False, key="inc_interp")

        if st.button("🔍 검색", key="law_search_btn") and law_query:
            try:
                api = LawAPI()
                failures = {}
                with st.spinner("법제처 API 조회 중..."):
                    law_list = api.search_law(law_query, display=5) if inc_law else []
                    prec_list = api.search_precedent(law_query, display=5) if inc_prec else []
                    interp_list = api.search_interpretation(law_query, display=5) if inc_interp else []

                    if inc_law and law_list and isinstance(law_list[0], dict) and "error" in law_list[0]:
                        failures["법령"] = law_list[0]["error"]
                    if inc_prec and prec_list and isinstance(prec_list[0], dict) and "error" in prec_list[0]:
                        failures["판례"] = prec_list[0]["error"]
                    if inc_interp and interp_list and isinstance(interp_list[0], dict) and "error" in interp_list[0]:
                        failures["해석례"] = interp_list[0]["error"]

                    context = api.build_ai_context(
                        law_query,
                        include_law=inc_law,
                        include_precedent=inc_prec,
                        include_interpretation=inc_interp,
                    )
                st.session_state["law_context"] = context
                st.session_state["law_search_results"] = {
                    "query": law_query, "resolved": _resolve_keyword(law_query),
                    "laws": [l for l in law_list if "error" not in l],
                    "precedents": [p for p in prec_list if "error" not in p],
                    "interpretations": [i for i in interp_list if "error" not in i],
                    "has_results": "찾지 못했습니다" not in context,
                }

                if failures:
                    st.error("⚠️ 일부 API 호출 실패")
                    for cat, msg in failures.items():
                        st.markdown(f"- **{cat}**: {msg}")
                    st.caption("아래 '🔧 법령 API 연결 진단' 버튼으로 점검")

                if "찾지 못했습니다" in context:
                    st.warning("검색 결과 없음")
                    st.caption(f"💡 '{law_query}' → '{_resolve_keyword(law_query)}'로 검색됨")
                else:
                    st.success("✅ 검색 완료 — 메인 화면 확인")
            except Exception as e:
                st.error(f"검색 실패: {str(e)}")

        if st.session_state.get("law_search_results", {}).get("has_results"):
            sr = st.session_state["law_search_results"]
            counts = []
            if sr["laws"]: counts.append(f"법령 {len(sr['laws'])}건")
            if sr["precedents"]: counts.append(f"판례 {len(sr['precedents'])}건")
            if sr["interpretations"]: counts.append(f"해석례 {len(sr['interpretations'])}건")
            if counts:
                st.caption(f"📊 {', '.join(counts)}")
                st.caption("💡 AI 검토 시 자동 참조됩니다.")
            if st.button("🗑 검색 결과 초기화", key="clear_law_results"):
                st.session_state.pop("law_search_results", None)
                st.session_state.pop("law_context", None)
                st.rerun()

    render_api_diagnostics_panel()


# ──────────────────────────────────────────────
# UI: 메인 영역 검색 결과 표시 (변경 없음)
# ──────────────────────────────────────────────
def render_law_search_results():
    sr = st.session_state.get("law_search_results")
    if not sr or not sr.get("has_results"):
        return

    st.markdown("---")
    query_display = sr['query']
    resolved_display = sr.get('resolved', query_display)
    if query_display != resolved_display:
        st.markdown(f"### 📖 법령 검색 결과: '{query_display}' → '{resolved_display}'")
    else:
        st.markdown(f"### 📖 법령 검색 결과: '{query_display}'")
    st.caption("법제처 국가법령정보센터 실시간 조회 | AI 검토 시 자동 참조됩니다")

    tab_names = []
    if sr["laws"]: tab_names.append(f"⚖️ 법령 ({len(sr['laws'])})")
    if sr["precedents"]: tab_names.append(f"📚 판례 ({len(sr['precedents'])})")
    if sr["interpretations"]: tab_names.append(f"📋 해석례 ({len(sr['interpretations'])})")
    if not tab_names: return

    tabs = st.tabs(tab_names)
    tab_idx = 0

    # ── 법령 탭 ──
    if sr["laws"]:
        with tabs[tab_idx]:
            for i, law in enumerate(sr["laws"]):
                law_name_short = law['법령명'][:40] + ('…' if len(law['법령명']) > 40 else '')
                with st.expander(
                    f"**{law_name_short}** ({law.get('법령종류', '')})",
                    expanded=(i == 0)
                ):
                    st.markdown(f"**{law['법령명']}** ({law.get('법령종류', '')}) — 시행 {law.get('시행일자', '')}")

                    col1, col2, col3 = st.columns(3)
                    with col1: st.caption(f"📂 {law.get('법령종류', '')}")
                    with col2: st.caption(f"🏛 {law.get('소관부처', '')}")
                    with col3: st.caption(f"📅 시행 {law.get('시행일자', '')}")

                    mst = law.get("MST", "")
                    law_name = law.get("법령명", "")

                    if mst:
                        try:
                            oc = get_oc()
                            html_link = _build_law_link(mst, oc)
                            search_link = _build_law_go_kr_link(law_name)
                            st.markdown(f"🔗 [**법제처에서 원문 보기** (별표·서식 포함)]({html_link})  |  [국가법령정보센터에서 검색]({search_link})")
                        except Exception:
                            pass

                    if mst:
                        cache_key = f"law_detail_{mst}"
                        if st.button(f"📄 조문 전문 보기", key=f"load_law_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("조문 로드 중..."):
                                    detail = api.get_law_text(mst)
                                if "error" not in detail and detail.get("조문목록"):
                                    st.session_state[cache_key] = detail
                                else:
                                    st.warning(f"⚠️ {detail.get('error', '조문을 불러오지 못했습니다.')}")
                            except Exception as e:
                                st.error(f"조문 로드 실패: {e}")

                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            st.markdown(f"**{detail['법령명']}** (시행 {detail['시행일자']}) — 총 {len(detail['조문목록'])}개 조문")

                            st.info("⚠️ **별표·별지서식 안내:** 법령에 포함된 별표, 서식, 표 등은 이미지로 구성되어 있어 텍스트로 표시되지 않을 수 있습니다. 위 '법제처에서 원문 보기' 링크에서 확인하세요.")

                            for art in detail["조문목록"]:
                                num = art.get("조문번호", "")
                                title = art.get("조문제목", "")
                                content = art.get("조문내용", "")
                                if content:
                                    jo_num = LawAPI.code_to_jo(num) if num and len(num) >= 4 else num
                                    header = f"**제{jo_num}조**" if jo_num else ""
                                    if title: header += f" ({title})"
                                    st.markdown(header)
                                    st.markdown(content)
                                    st.markdown("---")
        tab_idx += 1

    # ── 판례 탭 ──
    if sr["precedents"]:
        with tabs[tab_idx]:
            for i, prec in enumerate(sr["precedents"]):
                case_name_short = prec['사건명'][:40] + ('…' if len(prec['사건명']) > 40 else '')
                with st.expander(
                    f"[{prec.get('사건번호', '')}] {case_name_short}",
                    expanded=(i == 0)
                ):
                    st.markdown(f"**[{prec.get('사건번호', '')}]** {prec['사건명']}")

                    col1, col2, col3 = st.columns(3)
                    with col1: st.caption(f"🏛 {prec.get('법원명', '')}")
                    with col2: st.caption(f"📅 {prec.get('선고일자', '')}")
                    with col3: st.caption(f"📂 {prec.get('사건종류', '')}")

                    prec_id = prec.get("판례ID", "")
                    if prec_id:
                        cache_key = f"prec_detail_{prec_id}"
                        summary_key = f"prec_summary_{prec_id}"

                        if st.button(f"📄 판례 전문 보기", key=f"load_prec_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("판례 로드 중..."):
                                    detail = api.get_precedent_detail(prec_id)
                                if "error" not in detail:
                                    st.session_state[cache_key] = detail
                                    with st.spinner("🤖 AI가 판례를 쉽게 요약하는 중..."):
                                        summary = _summarize_precedent(detail)
                                        if summary:
                                            st.session_state[summary_key] = summary
                                else:
                                    st.warning("판례를 불러오지 못했습니다.")
                            except Exception as e:
                                st.error(f"판례 로드 실패: {e}")

                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            has_content = False

                            if summary_key in st.session_state:
                                st.markdown("## 🤖 AI 요약")
                                st.markdown(st.session_state[summary_key])
                                st.markdown("---")
                                has_content = True

                            raw_parts = []
                            if detail.get("판시사항"): raw_parts.append(("📌 판시사항", detail["판시사항"]))
                            if detail.get("판결요지"): raw_parts.append(("📌 판결요지", detail["판결요지"]))
                            if detail.get("참조조문"): raw_parts.append(("⚖️ 참조조문", detail["참조조문"]))
                            if detail.get("판례내용"): raw_parts.append(("📄 판례 전문", detail["판례내용"]))

                            if raw_parts:
                                with st.expander("📜 원문 전체 보기", expanded=False):
                                    for title, content in raw_parts:
                                        st.markdown(f"**{title}**")
                                        st.markdown(content[:5000])
                                        st.markdown("---")
                                has_content = True

                            if not has_content:
                                st.info("이 판례는 법제처 API에서 상세 내용을 제공하지 않습니다.\n\n[국가법령정보센터](https://www.law.go.kr)에서 직접 검색해 보세요.")
        tab_idx += 1

    # ── 해석례 탭 ──
    if sr["interpretations"]:
        with tabs[tab_idx]:
            for i, interp in enumerate(sr["interpretations"]):
                interp_name_short = interp['안건명'][:40] + ('…' if len(interp['안건명']) > 40 else '')
                with st.expander(
                    f"[{interp.get('안건번호', '')}] {interp_name_short}",
                    expanded=(i == 0)
                ):
                    st.markdown(f"**[{interp.get('안건번호', '')}]** {interp['안건명']}")

                    col1, col2 = st.columns(2)
                    with col1: st.caption(f"📅 {interp.get('회답일자', '')}")
                    with col2: st.caption(f"🏛 {interp.get('회답기관', '')}")

                    interp_id = interp.get("해석례ID", "")
                    if interp_id:
                        cache_key = f"interp_detail_{interp_id}"
                        summary_key = f"interp_summary_{interp_id}"

                        if st.button(f"📄 해석례 전문 보기", key=f"load_interp_{i}"):
                            try:
                                api = LawAPI()
                                with st.spinner("해석례 로드 중..."):
                                    detail = api.get_interpretation_detail(interp_id)
                                if "error" not in detail:
                                    st.session_state[cache_key] = detail
                                    with st.spinner("🤖 AI가 해석례를 쉽게 요약하는 중..."):
                                        summary = _summarize_interpretation(detail)
                                        if summary:
                                            st.session_state[summary_key] = summary
                                else:
                                    st.warning("해석례를 불러오지 못했습니다.")
                            except Exception as e:
                                st.error(f"해석례 로드 실패: {e}")

                        if cache_key in st.session_state:
                            detail = st.session_state[cache_key]
                            has_content = False

                            if summary_key in st.session_state:
                                st.markdown("## 🤖 AI 요약")
                                st.markdown(st.session_state[summary_key])
                                st.markdown("---")
                                has_content = True

                            raw_parts = []
                            if detail.get("질의요지"): raw_parts.append(("❓ 질의요지", detail["질의요지"]))
                            if detail.get("회답"): raw_parts.append(("💬 회답", detail["회답"]))
                            if detail.get("이유"): raw_parts.append(("📝 이유", detail["이유"]))
                            if detail.get("참조조문"): raw_parts.append(("⚖️ 참조조문", detail["참조조문"]))

                            if raw_parts:
                                with st.expander("📜 원문 전체 보기", expanded=False):
                                    for title, content in raw_parts:
                                        st.markdown(f"**{title}**")
                                        st.markdown(content[:5000])
                                        st.markdown("---")
                                has_content = True

                            if not has_content:
                                st.info("이 해석례는 법제처 API에서 상세 내용을 제공하지 않습니다.\n\n[국가법령정보센터](https://www.law.go.kr)에서 직접 검색해 보세요.")
                    else:
                        st.caption("(상세 조회 ID 없음)")

    st.markdown("---")
