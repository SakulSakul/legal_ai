#!/usr/bin/env python3
# ============================================================
#  🔑 API Key Health Check — 공정거래 실무 어시스턴트 v2.1
#  터미널에서 독립 실행: python test_api.py
#
#  테스트 항목:
#   1. 환경변수 / .streamlit/secrets.toml 로드
#   2. Supabase 연결 + 테이블 접근
#   3. Gemini API (Primary → Fallback 모델)
#   4. Anthropic (Claude) API
#   5. APP_PASSWORD 설정 여부
# ============================================================

import os, sys, time, json

# ── 컬러 출력 헬퍼 ───────────────────────────────────────────
class C:
    OK   = "\033[92m"  # 초록
    WARN = "\033[93m"  # 노랑
    FAIL = "\033[91m"  # 빨강
    BOLD = "\033[1m"
    DIM  = "\033[2m"
    END  = "\033[0m"

def ok(msg):   print(f"  {C.OK}✅ PASS{C.END}  {msg}")
def warn(msg): print(f"  {C.WARN}⚠️  WARN{C.END}  {msg}")
def fail(msg): print(f"  {C.FAIL}❌ FAIL{C.END}  {msg}")
def info(msg): print(f"  {C.DIM}ℹ️  INFO{C.END}  {msg}")
def header(msg): print(f"\n{C.BOLD}{'─'*50}\n  {msg}\n{'─'*50}{C.END}")

# ── Secrets 로드 (Streamlit secrets.toml 또는 환경변수) ────────
def load_secret(key):
    """secrets.toml → 환경변수 → None 순으로 탐색"""
    # 1) .streamlit/secrets.toml 파싱 시도
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
    # 2) 환경변수
    return os.environ.get(key)

# ── 결과 집계 ─────────────────────────────────────────────────
results = {"pass": 0, "warn": 0, "fail": 0}

def record(status):
    results[status] += 1

# ============================================================
#  TEST 1: 필수 키 존재 여부
# ============================================================
header("1/5  필수 환경변수 & Secrets 확인")

REQUIRED_KEYS = {
    "SUPABASE_URL":    "Supabase 프로젝트 URL",
    "SUPABASE_KEY":    "Supabase anon/service key",
    "GEMINI_API_KEY":  "Google Gemini API key",
    "ANTHROPIC_API_KEY": "Anthropic Claude API key",
}
OPTIONAL_KEYS = {
    "APP_PASSWORD":     "앱 접속 비밀번호 (선택)",
    "CLAUDE_MODEL":     "Claude 모델 오버라이드 (선택)",
    "GEMINI_MODEL_PRIMARY": "Gemini 주 모델 오버라이드 (선택)",
    "GEMINI_MODEL_FALLBACK": "Gemini 예비 모델 오버라이드 (선택)",
}

secrets = {}
all_required_present = True

for key, desc in REQUIRED_KEYS.items():
    val = load_secret(key)
    if val:
        masked = val[:8] + "..." + val[-4:] if len(val) > 16 else val[:4] + "..."
        ok(f"{key} = {masked}  ({desc})")
        secrets[key] = val
        record("pass")
    else:
        fail(f"{key} 없음  ({desc})")
        all_required_present = False
        record("fail")

for key, desc in OPTIONAL_KEYS.items():
    val = load_secret(key)
    if val:
        ok(f"{key} 설정됨  ({desc})")
        secrets[key] = val
    else:
        info(f"{key} 미설정 → 기본값 사용  ({desc})")

if not all_required_present:
    print(f"\n{C.FAIL}⛔ 필수 키가 누락되어 이후 테스트를 건너뜁니다.{C.END}")
    print(f"   .streamlit/secrets.toml 또는 환경변수를 확인하세요.\n")
    sys.exit(1)

# ============================================================
#  TEST 2: Supabase 연결 + 테이블 접근
# ============================================================
header("2/5  Supabase 연결 테스트")

try:
    from supabase import create_client
    sb = create_client(secrets["SUPABASE_URL"], secrets["SUPABASE_KEY"])
    ok("Supabase 클라이언트 생성 성공")
    record("pass")
except Exception as e:
    fail(f"Supabase 클라이언트 생성 실패: {e}")
    record("fail")
    sb = None

if sb:
    tables = ["docs", "sessions", "review_logs", "laws"]
    for table in tables:
        try:
            res = sb.table(table).select("id").limit(1).execute()
            row_count = len(res.data) if res.data else 0
            ok(f"테이블 '{table}' 접근 OK (샘플 {row_count}행)")
            record("pass")
        except Exception as e:
            err_str = str(e)
            if "does not exist" in err_str or "404" in err_str:
                warn(f"테이블 '{table}' 없음 — 생성 필요 (앱 첫 실행 전 DDL 필요)")
                record("warn")
            elif "permission" in err_str.lower() or "401" in err_str:
                fail(f"테이블 '{table}' 권한 없음 — RLS 정책 확인 필요")
                record("fail")
            else:
                fail(f"테이블 '{table}' 조회 실패: {e}")
                record("fail")

# ============================================================
#  TEST 3: Gemini API
# ============================================================
header("3/5  Gemini API 테스트")

gemini_primary = secrets.get("GEMINI_MODEL_PRIMARY", "gemini-2.5-pro")
gemini_fallback = secrets.get("GEMINI_MODEL_FALLBACK", "gemini-2.5-flash")

try:
    from google import genai
    from google.genai import types
    gclient = genai.Client(api_key=secrets["GEMINI_API_KEY"])
    ok("Gemini 클라이언트 생성 성공")
    record("pass")
except Exception as e:
    fail(f"Gemini 클라이언트 생성 실패: {e}")
    record("fail")
    gclient = None

if gclient:
    for model_name in [gemini_primary, gemini_fallback]:
        try:
            t0 = time.time()
            resp = gclient.models.generate_content(
                model=model_name,
                contents=[types.Content(role="user", parts=[types.Part(text="테스트입니다. '정상'이라고만 답해주세요.")])],
                config=types.GenerateContentConfig(
                    system_instruction="테스트 요청에 '정상'이라고만 답하세요.",
                ),
            )
            elapsed = time.time() - t0
            reply = (resp.text or "").strip()[:50]
            ok(f"{model_name} → 응답: \"{reply}\"  ({elapsed:.1f}초)")
            record("pass")
        except Exception as e:
            err_str = str(e).lower()
            if "quota" in err_str or "429" in err_str or "rate" in err_str:
                warn(f"{model_name} → Rate limit / 쿼터 초과 (키는 유효, 한도 확인 필요)")
                record("warn")
            elif "404" in err_str or "not found" in err_str:
                warn(f"{model_name} → 모델을 찾을 수 없음 (모델명 확인 필요)")
                record("warn")
            elif "api_key" in err_str or "401" in err_str or "403" in err_str:
                fail(f"{model_name} → API 키 인증 실패")
                record("fail")
            else:
                fail(f"{model_name} → {e}")
                record("fail")

# ============================================================
#  TEST 4: Anthropic (Claude) API
# ============================================================
header("4/5  Anthropic (Claude) API 테스트")

claude_model = secrets.get("CLAUDE_MODEL", "claude-sonnet-4-20250514")

try:
    import anthropic
    aclient = anthropic.Anthropic(api_key=secrets["ANTHROPIC_API_KEY"])
    ok("Anthropic 클라이언트 생성 성공")
    record("pass")
except Exception as e:
    fail(f"Anthropic 클라이언트 생성 실패: {e}")
    record("fail")
    aclient = None

if aclient:
    try:
        t0 = time.time()
        resp = aclient.messages.create(
            model=claude_model,
            max_tokens=50,
            system="테스트 요청에 '정상'이라고만 답하세요.",
            messages=[{"role": "user", "content": "테스트입니다. '정상'이라고만 답해주세요."}],
        )
        elapsed = time.time() - t0
        reply = resp.content[0].text.strip()[:50]
        ok(f"{claude_model} → 응답: \"{reply}\"  ({elapsed:.1f}초)")
        
        # 토큰 사용량 표시
        usage = resp.usage
        info(f"토큰: 입력 {usage.input_tokens} / 출력 {usage.output_tokens}")
        record("pass")
    except Exception as e:
        err_str = str(e).lower()
        if "rate" in err_str or "429" in err_str:
            warn(f"{claude_model} → Rate limit (키는 유효, 한도 확인 필요)")
            record("warn")
        elif "not_found" in err_str or "404" in err_str:
            warn(f"{claude_model} → 모델을 찾을 수 없음 (모델명 확인: {claude_model})")
            record("warn")
        elif "authentication" in err_str or "401" in err_str:
            fail(f"{claude_model} → API 키 인증 실패")
            record("fail")
        elif "permission" in err_str or "403" in err_str:
            fail(f"{claude_model} → 권한 없음 (API 키 권한 또는 모델 접근 확인)")
            record("fail")
        else:
            fail(f"{claude_model} → {e}")
            record("fail")

# ============================================================
#  TEST 5: APP_PASSWORD
# ============================================================
header("5/5  앱 비밀번호 설정 확인")

app_pw = secrets.get("APP_PASSWORD") or load_secret("APP_PASSWORD")
if app_pw:
    if len(app_pw) >= 8:
        ok(f"APP_PASSWORD 설정됨 (길이: {len(app_pw)}자)")
        record("pass")
    else:
        warn(f"APP_PASSWORD가 너무 짧음 ({len(app_pw)}자) — 8자 이상 권장")
        record("warn")
else:
    warn("APP_PASSWORD 미설정 — 앱이 비밀번호 없이 누구나 접근 가능합니다")
    record("warn")

# ============================================================
#  최종 결과 요약
# ============================================================
header("📊 최종 결과")

total = results["pass"] + results["warn"] + results["fail"]
print(f"  {C.OK}✅ PASS: {results['pass']}{C.END}")
print(f"  {C.WARN}⚠️  WARN: {results['warn']}{C.END}")
print(f"  {C.FAIL}❌ FAIL: {results['fail']}{C.END}")
print(f"  {'─'*30}")

if results["fail"] == 0 and results["warn"] == 0:
    print(f"  {C.OK}{C.BOLD}🎉 모든 테스트 통과! 앱을 실행해도 됩니다.{C.END}")
elif results["fail"] == 0:
    print(f"  {C.WARN}{C.BOLD}⚠️ 경고 항목이 있지만 앱 실행은 가능합니다.{C.END}")
else:
    print(f"  {C.FAIL}{C.BOLD}⛔ 실패 항목을 해결한 후 앱을 실행하세요.{C.END}")

print()
sys.exit(1 if results["fail"] > 0 else 0)
