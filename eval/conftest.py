"""pytest 공용 설정 — sys.path 주입 + 커스텀 마커 등록."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "embedding: 임베딩(GEMINI_API_KEY) 필요 — 의미매칭 게이트. 키 없으면 skip.",
    )
