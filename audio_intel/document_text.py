"""Offline document extraction and lossless, versioned text partitioning.

Offsets always address the canonical spoken text, never the source byte stream.
Splitting does not strip slices: joining all sections reproduces that text exactly.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import posixpath
import re
import unicodedata
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

VERSION = 2
FORMATS = {".epub", ".txt", ".pdf", ".md", ".markdown", ".docx", ".xlsx", ".pptx"}
CHAPTER = re.compile(r"^(?:第[〇零一二三四五六七八九十百千万两\d]+[章节回卷部篇]|(?:chapter|part|section)\s+(?:\d+|[ivxlcdm]+)\b|\d+(?:\.\d+){0,3}[、.．\s]+\S)", re.I)
ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "fig", "no", "e.g", "i.e"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16")
    else:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("gb18030")
    if "\x00" in text:
        raise ValueError("Text encoding is unsupported; save as UTF-8 / 请将文本保存为 UTF-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class HtmlText(HTMLParser):
    """Preserve block boundaries without splitting inline spans into paragraphs."""
    blocks = {"p", "div", "section", "article", "li", "blockquote", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.length = 0
        self.anchors: dict[str, int] = {}
        self.headings: list[dict[str, Any]] = []
        self.current_heading: tuple[str, int, int] | None = None
        self.skip = 0
        self.omitted = 0

    def append(self, text: str) -> None:
        self.parts.append(text)
        self.length += len(text)

    def boundary(self) -> None:
        if self.parts and not self.parts[-1].endswith("\n\n"):
            self.append("\n\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag in {"script", "style", "head", "nav", "pre"}:
            self.skip += 1
            if tag in {"nav", "pre"}:
                self.omitted += 1
        if self.skip:
            return
        if tag in self.blocks:
            self.boundary()
        if tag == "br":
            self.append("\n")
        if tag in {"td", "th"}:
            self.append(" ")
        if tag == "img":
            self.omitted += 1
        anchor = attributes.get("id") or attributes.get("name")
        if anchor:
            self.anchors.setdefault(anchor, self.length)
        if re.fullmatch(r"h[1-6]", tag):
            self.current_heading = tag, self.length, len(self.parts)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "head", "nav", "pre"} and self.skip:
            self.skip -= 1
            return
        if self.skip:
            return
        if self.current_heading and self.current_heading[0] == tag:
            _, start, part_index = self.current_heading
            title = "".join(self.parts[part_index:]).strip()
            self.headings.append({"offset": start, "title": title[:200], "level": int(tag[1]), "basis": "heading"})
            self.current_heading = None
        if tag in self.blocks:
            self.boundary()

    def handle_data(self, data: str) -> None:
        if not self.skip:
            # Whitespace in XHTML source is layout, not an extra paragraph.
            self.append(re.sub(r"\s+", " ", data))


def _xml(data: bytes) -> Any:
    from defusedxml import ElementTree
    return ElementTree.fromstring(data)


def _member(base: str, reference: str) -> tuple[str, str]:
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or parsed.query:
        raise ValueError("External EPUB resources are unsupported")
    name = posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parsed.path)) if parsed.path else base)
    if name.startswith(("/", "../")) or "\\" in name:
        raise ValueError("Unsafe EPUB member path")
    return name, unquote(parsed.fragment)


def _epub(path: Path, max_chars: int, archive_limit: int) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > archive_limit:
            raise ValueError("EPUB expanded size or member count exceeds the limit")
        if len({i.filename for i in infos}) != len(infos):
            raise ValueError("EPUB contains duplicate members")
        container = _xml(archive.read("META-INF/container.xml"))
        opf = next(n.attrib["full-path"] for n in container.iter() if n.tag.endswith("}rootfile"))
        opf, _ = _member("root", opf)
        package = _xml(archive.read(opf))
        manifest = {n.attrib["id"]: n.attrib for n in package.iter() if n.tag.endswith("}item")}
        title = next((n.text for n in package.iter() if n.tag.endswith("}title") and n.text), path.stem)
        parts: list[str] = []
        headings: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        anchors: dict[tuple[str, str], int] = {}
        warnings: list[str] = []
        length = 0
        seen: set[str] = set()
        for ref in (n for n in package.iter() if n.tag.endswith("}itemref")):
            name, _ = _member(opf, manifest[ref.attrib["idref"]]["href"])
            if name in seen:
                continue
            seen.add(name)
            parser = HtmlText()
            parser.feed(decode_text(archive.read(name)))
            text = "".join(parser.parts)
            anchors[name, ""] = length
            anchors.update({(name, key): length + offset for key, offset in parser.anchors.items()})
            if not text.strip():
                # Image-only chapter title pages still locate TOC boundaries at
                # the next spoken character; keep their anchors in reading order.
                warnings.append(f"{name}: no spoken text; images omitted / 此文件无可读文字，图片未朗读")
                continue
            files.append({"offset": length, "title": parser.headings[0]["title"] if parser.headings else f"Part {len(files) + 1}", "basis": "spine", "level": 1})
            headings.extend({**h, "offset": length + h["offset"]} for h in parser.headings)
            if parser.omitted:
                warnings.append(f"{name}: {parser.omitted} image/navigation/code elements omitted / 图片、导航或代码未朗读")
            parts.append(text + "\n\n")
            length += len(text) + 2
            if length > max_chars:
                raise ValueError("Document text exceeds the configured limit")
        toc: list[dict[str, Any]] = []
        ncx = next((i for i in manifest.values() if i.get("media-type") == "application/x-dtbncx+xml"), None)
        nav = next((i for i in manifest.values() if "nav" in i.get("properties", "").split()), None)

        def add(reference: str, label: str, level: int, base: str) -> None:
            try:
                name, fragment = _member(base, reference)
                offset = anchors.get((name, fragment))
                if offset is None:
                    warnings.append(f"Unresolved TOC entry / 无法定位目录项: {label[:100]}")
                else:
                    toc.append({"offset": offset, "title": label[:200], "level": level, "basis": "toc"})
            except ValueError:
                warnings.append("External or unsafe TOC entry ignored / 已忽略外部或无效目录链接")

        if nav:
            name, _ = _member(opf, nav["href"])
            root = _xml(archive.read(name))
            nav_roots = [n for n in root.iter() if n.tag.endswith("}nav") and "toc" in " ".join(n.attrib.values()).split()]

            def walk_nav(node: Any, level: int = 0) -> None:
                if node.tag.endswith("}ol"):
                    level += 1
                if node.tag.endswith("}a") and node.attrib.get("href"):
                    add(node.attrib["href"], "".join(node.itertext()).strip(), max(1, level), name)
                for child in node:
                    walk_nav(child, level)

            for node in nav_roots:
                walk_nav(node)
        if not toc and ncx:
            name, _ = _member(opf, ncx["href"])

            def walk_ncx(node: Any, level: int = 0) -> None:
                if node.tag.endswith("}navPoint"):
                    level += 1
                    content = next((n for n in node if n.tag.endswith("}content")), None)
                    label = next((n for n in node if n.tag.endswith("}navLabel")), None)
                    if content is not None:
                        add(content.attrib.get("src", ""), "".join(label.itertext()).strip() if label is not None else "", level, name)
                for child in node:
                    walk_ncx(child, level)

            walk_ncx(_xml(archive.read(name)))
        return {"title": title, "text": "".join(parts), "headings": toc or headings or files, "warnings": warnings}


def _markdown(text: str) -> dict[str, Any]:
    from markdown_it import MarkdownIt
    text, citation_count = re.subn(r"\ue200cite\ue202[^\ue200\ue201\n]*\ue201", "", text)
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    parts: list[str] = []
    headings: list[dict[str, Any]] = []
    warnings: list[str] = ["Exported citation control markers omitted / 导出引用控制标记未朗读"] if citation_count else []
    length = 0
    heading_level: int | None = None

    def inline(children: list[Any]) -> str:
        result = []
        for child in children:
            if child.type in {"text", "code_inline"}:
                result.append(child.content)
            elif child.type in {"softbreak", "hardbreak"}:
                result.append("\n")
            elif child.type == "image":
                warnings.append("Image omitted / 图片未朗读")
            elif child.type == "html_inline":
                parser = HtmlText(); parser.feed(child.content)
                result.append("".join(parser.parts))
        return "".join(result)

    for token in tokens:
        if token.type == "heading_open":
            heading_level = int(token.tag[1])
        elif token.type == "inline":
            value = inline(token.children or [])
            if heading_level is not None:
                headings.append({"offset": length, "title": value[:200], "level": heading_level, "basis": "heading"})
                heading_level = None
            parts.append(value + "\n\n"); length += len(value) + 2
        elif token.type in {"fence", "code_block"}:
            warnings.append("Code block omitted / 代码块未朗读")
        elif token.type == "html_block":
            parser = HtmlText(); parser.feed(token.content)
            value = "".join(parser.parts)
            headings.extend({**h, "offset": length + h["offset"]} for h in parser.headings)
            parts.append(value + "\n\n"); length += len(value) + 2
    return {"text": "".join(parts), "headings": headings, "warnings": warnings}


def _plain_headings(text: str, *, pdf: bool = False) -> list[dict[str, Any]]:
    items = []
    for match in re.finditer(r"(?m)^[^\n]+", text):
        line = match[0].strip()
        numbered_bar = pdf and re.match(r"^\d{1,3}\s*[｜|]\s*[^\d\s]", line)
        if numbered_bar and re.search(r"[=|｜]", line[numbered_bar.end():]):
            numbered_bar = None  # Absolute-value expressions are not headings.
        if pdf and re.match(r"^\d", line) and not numbered_bar:
            # PDF text extraction does not retain heading styles. Numeric list
            # items, equations and page furniture must not become chapters.
            continue
        if len(line) <= 100 and (CHAPTER.match(line) or numbered_bar) and not re.search(r"[.·…]{2,}\s*\d+\s*$", line):
            if numbered_bar and items and items[-1]["title"] == line:
                continue
            items.append({"offset": match.start(), "title": line, "level": 1, "basis": "heading"})
    return items if len(items) > 1 else []


def extract(path: Path, max_chars: int, archive_limit: int) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix not in FORMATS:
        raise ValueError("Unsupported document format")
    if suffix in {".docx", ".xlsx", ".pptx"}:
        from .document_office import extract_office
        result = extract_office(path, max_chars, archive_limit)
    elif suffix == ".epub":
        result = _epub(path, max_chars, archive_limit)
    elif suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are unsupported / 不支持加密 PDF")
        pages = []; offsets = []; length = 0; warnings = []
        for number, page in enumerate(reader.pages, 1):
            offsets.append(length)
            text = (page.extract_text() or "").replace("\r", "") + "\n\n"
            text = re.sub(r"[\u2f00-\u2fdf]", lambda m: unicodedata.normalize("NFKC", m[0]), text)
            if not text.strip():
                warnings.append(f"Page {number}: no text; OCR may be required / 此页无文字，可能需要 OCR")
            if "\ufffd" in text:
                warnings.append(f"Page {number}: replacement characters / 此页有乱码")
            pages.append(text); length += len(text)
            if length > max_chars:
                raise ValueError("Document text exceeds the configured limit")
        headings = []

        def outline(items: list[Any], level: int = 1) -> None:
            for item in items:
                if isinstance(item, list):
                    outline(item, level + 1)
                else:
                    try:
                        page = reader.get_destination_page_number(item)
                        if page is not None and 0 <= page < len(offsets):
                            headings.append({"offset": offsets[page], "title": str(item.title)[:200], "level": level, "basis": "toc"})
                    except (ValueError, KeyError):
                        warnings.append("Invalid PDF bookmark / 无效 PDF 书签")
        outline(reader.outline)
        text = "".join(pages)
        result = {"text": text, "headings": headings or _plain_headings(text, pdf=True), "warnings": warnings, "page_offsets": offsets}
    else:
        text = decode_text(path.read_bytes())
        result = _markdown(text) if suffix in {".md", ".markdown"} else {"text": text, "headings": _plain_headings(text), "warnings": []}
    if not result["text"].strip():
        raise ValueError("No readable text; scanned documents require OCR / 无可读正文，请先进行 OCR")
    if len(result["text"]) > max_chars:
        raise ValueError("Document text exceeds the configured limit")
    result.setdefault("title", path.stem)
    result["version"] = VERSION
    result["text_hash"] = hashlib.sha256(result["text"].encode()).hexdigest()
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result


def _boundaries(text: str) -> tuple[list[list[int]], list[int]]:
    paragraphs = [m.end() for m in re.finditer(r"\n[^\S\n]*\n+", text)]
    sentences = []; sentence_lines = []
    for match in re.finditer(r"[。！？!?;；.]([\"'”’」』）》】]*)(\s*)", text):
        at = match.start()
        if text[at] == ".":
            if at and at + 1 < len(text) and text[at - 1].isdigit() and text[at + 1].isdigit():
                continue
            prefix = re.search(r"[\w.]+$", text[max(0, at - 30):at])
            word = prefix[0].lower() if prefix else ""
            if word in ABBREVIATIONS or len(word) == 1 or (at + 1 < len(text) and not text[at + 1].isspace()):
                continue
        (sentence_lines if "\n" in match[2] else sentences).append(match.end())
    lines = [m.end() for m in re.finditer(r"\n", text)]
    words = [m.end() for m in re.finditer(r"[^\S\n]+", text)]
    return [paragraphs, sentence_lines, sentences, lines], words


def _near(points: list[int], low: int, high: int, target: int) -> int | None:
    start, end = bisect.bisect_left(points, low), bisect.bisect_right(points, high)
    if start == end:
        return None
    middle = bisect.bisect_left(points, target, start, end)
    return min(points[max(start, middle - 1):min(end, middle + 1)], key=lambda p: (abs(p - target), p))


def length_sections(text: str, target: int, base: int = 0) -> list[dict[str, Any]]:
    boundaries, words = _boundaries(text)
    names = ["paragraph", "sentence", "sentence", "newline"]
    pieces = []; start = 0
    while start < len(text):
        if len(text) - start <= int(1.2 * target):
            cut, basis = len(text), "remainder"
        else:
            low, high, goal = start + int(.8 * target), start + int(1.2 * target), start + target
            cut = None; basis = "forced"
            for candidates, name in zip(boundaries, names):
                cut = _near(candidates, low, high, goal)
                if cut is not None:
                    basis = name; break
            if cut is None:
                following = [(p[bisect.bisect_right(p, high)], name) for p, name in zip(boundaries, names) if bisect.bisect_right(p, high) < len(p) and p[bisect.bisect_right(p, high)] <= start + 2 * target]
                if following:
                    cut, basis = min(following)
            if cut is None:
                cut = _near(words, low, high, goal)
                if cut is not None:
                    basis = "word"
            if cut is None:
                cut = min(start + 2 * target, len(text))
                while cut > start + target and cut < len(text) and (unicodedata.combining(text[cut]) or text[cut] == "\u200d" or text[cut - 1] == "\u200d"):
                    cut -= 1
                if cut == len(text):
                    basis = "remainder"
        pieces.append({"start": start + base, "end": cut + base, "basis": basis})
        start = cut
    if len(pieces) > 1 and pieces[-1]["end"] - pieces[-1]["start"] < .2 * target and pieces[-1]["end"] - pieces[-2]["start"] <= 2 * target:
        pieces[-2]["end"] = pieces[-1]["end"]; pieces.pop()
    return pieces


def segment(document: dict[str, Any], mode: str = "auto", target: int = 10000, max_sections: int = 2000) -> dict[str, Any]:
    if mode not in {"auto", "length"} or not 1000 <= target <= 50000:
        raise ValueError("Use auto/length and a target of 1000–50000 characters")
    text = document["text"]
    headings = sorted(document.get("headings", []), key=lambda h: (h["offset"], h["level"]))
    # A global title is a wrapper; the shallowest repeated level is the main level.
    levels = sorted({h["level"] for h in headings})
    level = next((n for n in levels if sum(h["level"] == n for h in headings) > 1), levels[0] if levels else 1)
    selected = {h["offset"]: h for h in reversed(headings) if h["level"] == level and 0 <= h["offset"] < len(text)}
    if len(selected) < 2:
        selected = {}  # A lone document title is not a reliable chapter structure.
    pieces = []
    if mode == "auto" and selected:
        positions = sorted(selected)
        if positions[0] > 0:
            prefix = text[:positions[0]]
            # Keep wrapper titles with the first chapter; preserve a real preface.
            wrapper_text = "\n".join(h["title"] for h in headings if h["offset"] < positions[0])
            if not prefix.strip() or re.sub(r"\s", "", prefix) == re.sub(r"\s", "", wrapper_text):
                selected[0] = selected.pop(positions[0]); positions[0] = 0
            else:
                pieces.extend({**p, "title": "前置正文 / Preface"} for p in length_sections(prefix, target))
        for index, start in enumerate(positions):
            end = positions[index + 1] if index + 1 < len(positions) else len(text)
            pieces.append({"start": start, "end": end, "title": selected[start]["title"], "basis": selected[start]["basis"]})
    else:
        pieces = length_sections(text, target)
    # Whitespace remains in the canonical text, but must never become a TTS item.
    nonempty = []
    pending_start = None
    for piece in pieces:
        if not text[piece["start"]:piece["end"]].strip():
            if pending_start is None:
                pending_start = piece["start"]
            continue
        if pending_start is not None:
            piece["start"] = pending_start
            pending_start = None
        nonempty.append(piece)
    if not nonempty:
        raise ValueError("Document contains no speakable text / 文档没有可朗读正文")
    if pending_start is not None:
        nonempty[-1]["end"] = len(text)
    merged = len(nonempty) != len(pieces)
    pieces = nonempty
    if len(pieces) > max_sections:
        raise ValueError("Too many sections; increase the target length / 分段过多，请增大目标字数")
    revision = digest([document.get("version", 1), document["text_hash"], mode, target])
    if merged:
        revision = digest([revision, "nonempty-v1", [(p["start"], p["end"]) for p in pieces]])
    for index, piece in enumerate(pieces, 1):
        piece.update({"id": digest([revision, piece["start"], piece["end"]])[:24], "index": index, "char_count": piece["end"] - piece["start"]})
        piece.setdefault("title", f"第{index}段 / Section {index}")
        if document.get("page_offsets"):
            piece["page_start"] = bisect.bisect_right(document["page_offsets"], piece["start"])
            piece["page_end"] = bisect.bisect_right(document["page_offsets"], max(piece["start"], piece["end"] - 1))
    assert "".join(text[p["start"]:p["end"]] for p in pieces) == text
    return {"preview_revision": revision, "segmentation_mode": mode, "target_section_chars": target, "sections": pieces, "total_chars": len(text), "section_count": len(pieces)}
