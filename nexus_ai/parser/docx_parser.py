"""DOCX 파서 — 조항 단위 청킹.

- 워드 표(병합 셀 포함) 와 다중 계층 목록을 plain text 로 평탄화.
- "제N조" / "제N장" / 사례집 "#NNN" 패턴을 기준으로 청크 분할.
- 한 청크가 너무 길어지면 max_chars 단위로 추가 분할.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


_RE_ARTICLE = re.compile(r"^\s*제\s*([0-9]+)\s*조")
_RE_CASE_NO = re.compile(r"#\s*([0-9]+)")


@dataclass
class Chunk:
    chunk_idx: int
    text: str
    article_no: str | None = None
    case_no: str | None = None
    categories: list[str] = field(default_factory=list)


def _iter_block_items(doc: Document):
    """문서 내 paragraph + table 을 등장 순서대로 순회."""
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            yield Table(child, doc)


def _table_to_text(tbl: Table) -> str:
    seen_cells: set[int] = set()
    rows: list[str] = []
    for row in tbl.rows:
        cells: list[str] = []
        for cell in row.cells:
            cid = id(cell._tc)
            if cid in seen_cells:
                continue
            seen_cells.add(cid)
            txt = " ".join(p.text.strip() for p in cell.paragraphs if p.text.strip())
            if txt:
                cells.append(txt)
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _flatten(doc: Document) -> str:
    parts: list[str] = []
    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            t = block.text.strip()
            if t:
                parts.append(t)
        elif isinstance(block, Table):
            t = _table_to_text(block)
            if t:
                parts.append(t)
    return "\n".join(parts)


def parse_docx(file_bytes: bytes, *, max_chars: int = 1200) -> list[Chunk]:
    doc = Document(io.BytesIO(file_bytes))
    flat = _flatten(doc)

    chunks: list[Chunk] = []
    buf: list[str] = []
    cur_article: str | None = None
    cur_case: str | None = None

    def flush():
        if not buf:
            return
        text = "\n".join(buf).strip()
        if not text:
            buf.clear(); return
        chunks.append(Chunk(
            chunk_idx=len(chunks),
            text=text,
            article_no=cur_article,
            case_no=cur_case,
        ))
        buf.clear()

    for line in flat.split("\n"):
        m_art = _RE_ARTICLE.search(line)
        m_case = _RE_CASE_NO.search(line)
        if m_art or m_case or sum(len(x) for x in buf) + len(line) > max_chars:
            flush()
            if m_art:
                cur_article = f"제{m_art.group(1)}조"
            if m_case:
                cur_case = f"#{m_case.group(1)}"
        buf.append(line)
    flush()
    return chunks


_CATEGORY_KEYWORDS = {
    "공정거래": ("표시광고", "하도급", "대리점", "가맹", "부당지원", "공정거래"),
    "정보보안": ("개인정보", "영업비밀", "보안서약", "정보보안", "IT 자산"),
    "안전":     ("산업안전", "중대재해", "사고", "시설안전", "안전관리"),
    "재무":     ("회계", "세무", "자금", "결재 권한", "재무"),
    "영업":     ("영업관리", "고객응대", "매장운영", "영업"),
    "총무":     ("자산관리", "차량", "출장", "총무", "행정"),
    "환경":     ("환경경영", "폐기물", "에너지", "친환경", "환경"),
    "CSR":      ("사회공헌", "동반성장", "기부", "ESG", "CSR"),
    "공통":     ("윤리강령", "행동규범", "임직원", "공통"),
}

# 신고절차 문서는 NEXUS DB 적재에서 제외 (인사 챗봇 전용)
HR_PROCEDURE_HINTS = (
    "신고처리", "신고 처리", "조사위원회", "신고 절차",
    "고충처리", "괴롭힘 신고", "성희롱 신고",
)


def suggest_categories(text: str) -> list[str]:
    hits: list[str] = []
    for cat, kws in _CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in kws):
            hits.append(cat)
    if not hits:
        hits = ["공통"]
    return hits


def looks_like_hr_procedure(title: str, text_sample: str) -> bool:
    blob = f"{title}\n{text_sample[:2000]}"
    return any(h in blob for h in HR_PROCEDURE_HINTS)
