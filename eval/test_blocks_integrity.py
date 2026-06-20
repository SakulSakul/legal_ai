"""
test_blocks_integrity.py — legal_blocks.json 무결성 가드 (매니페스트 해시 대조)

"의도한 블록만 바뀌고 다른 법률 블록은 그대로"를 CI에서 확실히 게이트한다.
git·네트워크·키 일절 미사용 → CI 얕은 체크아웃에서도 skip 없이 로컬과 동일 동작
(과거 git show origin/main 의존 테스트의 graceful-skip 구멍을 대체).

블록을 의도적으로 편집한 PR은 `python eval/gen_blocks_manifest.py`로 매니페스트를
갱신·커밋해야 한다. 안 하면 이 테스트가 FAIL(fail-closed) → 무단 변경이 초록불로
빠져나가지 못한다.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# 생성 스크립트의 해시 로직을 그대로 재사용(단일 진실원 → drift 차단)
import gen_blocks_manifest as G  # noqa: E402


def _load(name):
    with open(os.path.join(ROOT, name), "r", encoding="utf-8") as f:
        return json.load(f)


def _current_blocks():
    return G.build_manifest(_load("legal_blocks.json"))


def _manifest_blocks():
    return _load("legal_blocks_manifest.json")["blocks"]


def test_manifest_exists_and_nonempty():
    m = _load("legal_blocks_manifest.json")
    assert m.get("blocks"), "매니페스트에 블록이 없음"
    assert m["_meta"]["source"] == "legal_blocks.json"


def test_block_key_set_matches():
    """블록 추가/삭제 감지 — 키 집합이 매니페스트와 일치해야 함."""
    cur = set(_current_blocks())
    man = set(_manifest_blocks())
    added = cur - man
    removed = man - cur
    assert not added and not removed, (
        f"블록 키 불일치 — 추가:{sorted(added)} 삭제:{sorted(removed)}. "
        f"의도된 변경이면 `python eval/gen_blocks_manifest.py` 재실행·커밋."
    )


def test_block_hashes_match():
    """내용 변경 감지 — 모든 블록 해시가 매니페스트와 일치해야 함."""
    cur = _current_blocks()
    man = _manifest_blocks()
    mismatched = sorted(k for k in cur if k in man and cur[k] != man[k])
    assert not mismatched, (
        f"블록 내용 변경 감지 — {mismatched}. 의도된 편집이면 "
        f"`python eval/gen_blocks_manifest.py` 재실행·커밋(매니페스트 diff=바뀐 블록)."
    )


def test_guard_logic_is_git_and_key_free():
    """가드 해시 로직(gen_blocks_manifest)이 git/네트워크/키에 의존하지 않음
    (CI 얕은 체크아웃에서도 skip 없이 동작 — 과거 graceful-skip 구멍 방지)."""
    src = open(os.path.join(HERE, "gen_blocks_manifest.py"), encoding="utf-8").read()
    for forbidden in ("import subprocess", "import requests", "import streamlit", "get_secret", "origin/main"):
        assert forbidden not in src, f"gen_blocks_manifest.py: '{forbidden}' 의존 — CI 불안정 위험"
