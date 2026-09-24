"""Parse acquired source documents into intact sections and structured tables.

No fixed-size chunking: a section runs from one detected heading to the next and keeps its heading
path. Financial tables keep their rows, header rows, caption, surrounding context and unit hints.
Only an oversized section is divided, at block boundaries, into numbered parts that keep the heading.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PARSER_VERSION = "1.0.0"
MAX_SECTION_CHARS = 20_000

BLOCK_TAGS = {"p", "div", "table", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "section", "article", "blockquote", "center", "tr", "td", "th"}
SKIP_TAGS = {"script", "style", "head", "title", "noscript", "ix:header", "svg", "button", "nav"}
NUMBER = re.compile(r"\(?\$?\s?\d[\d,]*(\.\d+)?\)?%?")
YEARLIKE = re.compile(r"^(19|20)\d\d$|^[A-Z][a-z]+\.? \d{1,2},? (19|20)\d\d$")
PAGE_FURNITURE = re.compile(r"^(F-\d{1,3}|\d{1,3}|Table of Contents|Page \d+ of \d+)$|^Field: ", re.I)
ARTICLE_HEADING = re.compile(r"^\d{1,2}\.\s+[A-Z][^.:]{2,80}$")  # "4. Repayment of the Loan; Authorizations"
SUBCLAUSE = re.compile(r"^\d+(\.\d+)+\.?\s")  # "3.2. Business Account. You agree ..." is body text
STATEMENT_HEADING = re.compile(
    r"(Consolidated|Condensed).{0,40}(Balance Sheets?|Statements? of)", re.I)
NOTE_HEADING = re.compile(r"^Note \d+\s*[—–-]\s*.{3,100}$")
UNIT_HINT = re.compile(r"in (thousands|millions)|\(\s*in [^)]*\)|U\.?S\.? dollars|USD|\$|%", re.I)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


@dataclass
class Block:
    kind: str  # "para" | "heading" | "table"
    text: str = ""
    level: int = 0
    rows: list[list[str]] = field(default_factory=list)
    header_rows: int = 0
    page: int | None = None


# --- HTML --------------------------------------------------------------------------------------

def _hidden(tag: Tag) -> bool:
    return "display:none" in (tag.get("style") or "").replace(" ", "").lower()


def _is_bold(node: NavigableString, stop: Tag) -> bool:
    for parent in node.parents:
        if parent.name in ("b", "strong"):
            return True
        m = re.search(r"font-weight:\s*(\w+)", (parent.get("style") or "").lower())
        if m:
            return m.group(1) in ("bold", "bolder", "700", "800", "900")
        if parent is stop:
            return False
    return False


def _classify(text: str, bold_share: float) -> Block | None:
    if not text or PAGE_FURNITURE.match(text):
        return None
    if NOTE_HEADING.match(text):  # includes "Note 11 — Notes Payable (cont.)" after a page break
        return Block("heading", text.removesuffix("(cont.)").strip(), level=2)
    if ARTICLE_HEADING.match(text):
        return Block("heading", text, level=2)
    if SUBCLAUSE.match(text):
        return Block("para", text)
    letters = re.sub(r"[^A-Za-z]", "", text)
    if letters and len(text) <= 300 and bold_share >= 0.9:
        top = (letters.isupper() and len(text) <= 120) or STATEMENT_HEADING.search(text)
        level = 1 if top else 2
        return Block("heading", text, level=level)
    return Block("para", text)


def _paragraph(tag: Tag) -> Block | None:
    strings = [s for s in tag.find_all(string=True) if norm(s)]
    total = sum(len(norm(s)) for s in strings)
    bold = sum(len(norm(s)) for s in strings if _is_bold(s, tag))
    return _classify(norm(" ".join(strings)), bold / total if total else 0.0)


def _table(tag: Tag) -> list[Block]:
    grid: list[list[str]] = []
    for tr in tag.find_all("tr"):
        row: list[str] = []
        for cell in tr.find_all(["td", "th"], recursive=False):
            span = str(cell.get("colspan") or "1")
            row.append(norm(cell.get_text(" ")))
            row.extend([""] * (int(span) - 1 if span.isdigit() else 0))
        grid.append(row)
    width = max((len(r) for r in grid), default=0)
    grid = [r + [""] * (width - len(r)) for r in grid]

    # Merge currency and closing-paren/percent cells into the number they belong to.
    for r in grid:
        for i, v in enumerate(r):
            if v in ("$", "US$") and i + 1 < width:
                j = next((k for k in range(i + 1, width) if r[k]), None)
                if j is not None:
                    r[j], r[i] = f"{v}{r[j]}", ""
            elif v in (")", "%", ")%") and i > 0:
                j = next((k for k in range(i - 1, -1, -1) if r[k]), None)
                if j is not None:
                    r[j], r[i] = r[j] + v, ""
    keep = [c for c in range(width) if any(r[c] for r in grid)]
    rows = [[r[c] for c in keep] for r in grid if any(r[c] for c in keep)]

    data = len(rows) >= 2 and len(keep) >= 2 and any(
        NUMBER.fullmatch(v) and not YEARLIKE.match(v) for r in rows for v in r[1:])
    if not data:  # layout table (bullets, signature blocks, footnote grids): keep as paragraphs
        texts = (" ".join(v for v in r if v) for r in rows)
        return [b for t in texts if (b := _classify(t, 0.0))]

    header = 0
    for r in rows:
        numeric = [v for v in r[1:] if v and NUMBER.fullmatch(v) and not YEARLIKE.match(v)]
        if r[0] and numeric:
            break
        header += 1
    header = min(header, len(rows))

    # A spanning header ("June 30, 2024" over the $ and amount cells) lands one column away from
    # its values. Merge two adjacent value columns when no row fills both and one of them holds
    # only header text. The label column is never merged.
    c = 1
    while c + 1 < len(rows[0]):
        a = [r[c] for r in rows]
        b = [r[c + 1] for r in rows]
        disjoint = not any(x and y for x, y in zip(a, b, strict=True))
        header_only = not any(a[header:]) or not any(b[header:])
        if disjoint and header_only:
            for r in rows:
                r[c] = r[c] or r[c + 1]
                del r[c + 1]
        else:
            c += 1
    return [Block("table", rows=rows, header_rows=header)]


def _walk(node: Tag, out: list[Block]) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            if norm(child) and node.name not in SKIP_TAGS:
                out.append(Block("para", norm(child)))
            continue
        if not isinstance(child, Tag) or child.name in SKIP_TAGS or _hidden(child):
            continue
        if child.name == "table":
            out.extend(_table(child))
        elif re.fullmatch(r"h[1-6]", child.name):
            if text := norm(child.get_text(" ")):
                out.append(Block("heading", text, level=1 if child.name in ("h1", "h2") else 2))
        elif child.name in BLOCK_TAGS and not child.find(BLOCK_TAGS):
            if block := _paragraph(child):
                out.append(block)
        else:
            _walk(child, out)


def html_blocks(path: Path) -> list[Block]:
    soup = BeautifulSoup(path.read_bytes(), "lxml")
    root = soup.select_one("[itemprop=articleBody]") or soup.body or soup
    out: list[Block] = []
    _walk(root, out)
    return out


# --- PDF ---------------------------------------------------------------------------------------

def pdf_blocks(path: Path) -> list[Block]:
    from pypdf import PdfReader

    out: list[Block] = []
    for n, page in enumerate(PdfReader(path).pages, start=1):
        out.append(Block("heading", f"Page {n}", level=1, page=n))
        text = page.extract_text() or ""
        # Paragraphs end at blank lines or at lines closing a sentence.
        para: list[str] = []
        for line in text.splitlines():
            line = norm(line)
            if not line:
                if para:
                    out.append(Block("para", " ".join(para), page=n))
                    para = []
                continue
            para.append(line)
            if line.endswith((".", ":", ";")) and len(line) < 60:
                out.append(Block("para", " ".join(para), page=n))
                para = []
        if para:
            out.append(Block("para", " ".join(para), page=n))
    return out


def blocks_for(path: Path) -> list[Block]:
    return pdf_blocks(path) if path.suffix.lower() == ".pdf" else html_blocks(path)


# --- Sections ----------------------------------------------------------------------------------

@dataclass
class ParsedTable:
    ordinal: int
    rows: list[list[str]]
    header_rows: int
    caption: str
    context_before: str
    context_after: str
    units_hint: str | None

    def rendered(self) -> str:
        return "\n".join("| " + " | ".join(r) + " |" for r in self.rows)


@dataclass
class ParsedSection:
    ordinal: int
    heading: str
    heading_path: list[str]
    page: int | None
    part: int = 1
    parts: int = 1
    items: list[str | ParsedTable] = field(default_factory=list)  # paragraphs and tables in order

    def text(self, table_ids: dict[int, str] | None = None) -> str:
        chunks = []
        for item in self.items:
            if isinstance(item, ParsedTable):
                tid = (table_ids or {}).get(item.ordinal, f"table {item.ordinal}")
                chunks.append(f"[{tid}]\n{item.rendered()}")
            else:
                chunks.append(item)
        return "\n\n".join(chunks)


def sectionize(blocks: list[Block]) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    top: str | None = None  # current level-1 heading
    current = ParsedSection(0, "Document start", [], None)
    table_n = 0

    def flush() -> None:
        if current.items:
            sections.append(current)

    for i, b in enumerate(blocks):
        if b.kind == "heading":
            if current.items:
                flush()
            if b.level == 1:
                top = b.text
            path = [b.text] if b.level == 1 else [p for p in (top, b.text) if p]
            current = ParsedSection(len(sections), b.text, path, b.page)
            continue
        if b.kind == "para":
            current.items.append(b.text)
            if current.page is None:
                current.page = b.page
            continue
        prev = [x for x in current.items[-3:] if isinstance(x, str)]
        after = next((x.text for x in blocks[i + 1:i + 3] if x.kind == "para"), "")
        head_text = " ".join(" ".join(r) for r in b.rows[:max(b.header_rows, 1)])
        unit = UNIT_HINT.search(" ".join(prev[-1:]) + " " + head_text)
        current.items.append(ParsedTable(
            ordinal=table_n, rows=b.rows, header_rows=b.header_rows,
            caption=prev[-1] if prev else current.heading, context_before="\n".join(prev),
            context_after=after,
            units_hint=unit.group(0) if unit else None))
        table_n += 1
    flush()

    # Split oversized sections at block boundaries; every part keeps its heading path.
    out: list[ParsedSection] = []
    for s in sections:
        groups: list[list] = [[]]
        size = 0
        for item in s.items:
            n = len(item.rendered() if isinstance(item, ParsedTable) else item)
            if groups[-1] and size + n > MAX_SECTION_CHARS:
                groups.append([])
                size = 0
            groups[-1].append(item)
            size += n
        for k, g in enumerate(groups, start=1):
            out.append(ParsedSection(len(out), s.heading, s.heading_path, s.page, k, len(groups), g))
    return out
