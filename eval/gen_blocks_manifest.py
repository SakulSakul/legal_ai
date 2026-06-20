#!/usr/bin/env python3
"""
gen_blocks_manifest.py — legal_blocks.json 무결성 골든 매니페스트 생성 (key-free·결정론)

각 법률 블록(토픽별 issue + 토픽 메타)을 정규화 직렬화해 sha256으로 고정한다.
git·네트워크·키 일절 미사용 → CI 얕은 체크아웃에서도 로컬과 동일하게 동작.

블록을 '의도적으로' 편집한 PR은 이 스크립트를 재실행해 매니페스트를 갱신·커밋한다:
    python eval/gen_blocks_manifest.py
PR 리뷰 시 매니페스트 diff = 바뀐 블록 목록 → "의도한 블록만 바뀌었는지" 한눈에 확인.
블록을 고치고 매니페스트를 안 갱신하면 test_blocks_integrity가 FAIL(fail-closed).
"""
import os
import json
import hashlib
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SOURCE = os.path.join(ROOT, "legal_blocks.json")
MANIFEST = os.path.join(ROOT, "legal_blocks_manifest.json")


def block_hash(block) -> str:
    """정규화 해시 — 키 순서·공백 등 표시 변화엔 안 깨지고 콘텐츠 변경에만 반응."""
    return hashlib.sha256(
        json.dumps(block, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_manifest(db: dict) -> dict:
    """{ '<topic>::<issue_id>': sha, '<topic>::__topic__': sha(메타) } 반환."""
    blocks = {}
    for topic, content in db.items():
        if topic.startswith("_") or not isinstance(content, dict):
            continue
        # 토픽 레벨 메타(label/summary/legend 등 — issues 제외)도 무결성 대상
        meta = {k: v for k, v in content.items() if k != "issues"}
        if meta:
            blocks[f"{topic}::__topic__"] = block_hash(meta)
        for issue in content.get("issues", []):
            iid = issue.get("id", "?")
            blocks[f"{topic}::{iid}"] = block_hash(issue)
    return blocks


def main():
    with open(SOURCE, "r", encoding="utf-8") as f:
        db = json.load(f)
    blocks = build_manifest(db)
    manifest = {
        "_meta": {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_blocks": len(blocks),
            "source": "legal_blocks.json",
            "note": "블록 무결성 골든 해시. 블록을 의도적으로 바꾸면 이 스크립트를 재실행해 갱신·커밋.",
        },
        "blocks": blocks,
    }
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print(f"✅ {MANIFEST} — {len(blocks)} blocks")
    for k in sorted(blocks):
        print(f"   {k}")


if __name__ == "__main__":
    main()
