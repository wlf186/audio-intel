"""Offline, editable concise transcripts with bounded in-memory generation."""
from __future__ import annotations

import hashlib
import io
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .transcript import comparison_source_text, reading_sections, insight_label, presentation_sections, group_insight_lines
from .transcript_diff import text_changes
from .utils import timecode

_lock = threading.Lock()
_render_lock = threading.Lock()
_cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_cache_generation = 0
_cache_limit = 64 * 1024**2
_max_image_bytes = 128 * 1024**2
_max_text_chars = 4_000_000


def render(title: str, result: dict[str, Any], output_dir: Path) -> bytes:
    from docx import Document
    from docx.image.image import Image
    from docx.enum.style import WD_STYLE_TYPE
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Mm, Pt, RGBColor

    if sum(len(segment.get('text', '')) + len(segment.get('original_text', '')) for segment in result.get('segments', [])) > _max_text_chars:
        raise ValueError('Transcript exceeds DOCX text budget; split the source')
    document = Document()
    properties = document.core_properties
    for name in ('author', 'last_modified_by', 'subject', 'keywords', 'comments', 'category', 'identifier'):
        setattr(properties, name, '')
    properties.title = title
    section = document.sections[0]
    comparison = result.get('text_version') == 'comparison'
    section.orientation = WD_ORIENT.LANDSCAPE if comparison else WD_ORIENT.PORTRAIT
    section.page_width, section.page_height = (Mm(297), Mm(210)) if comparison else (Mm(210), Mm(297))
    section.left_margin = section.right_margin = Mm(18)
    section.top_margin = section.bottom_margin = Mm(18)
    for name in ('Normal', 'Title', 'Heading 1', 'Heading 2'):
        style = document.styles[name]
        style.font.name = 'Calibri'
        style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), '宋体')
    normal = document.styles['Normal']
    normal.font.size = Pt(11)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = 1.2
    ink = RGBColor.from_string('232321')
    muted = RGBColor.from_string('64645E')
    for name, color, bold in [('Heading 1', ink, True), ('Heading 2', muted, False)]:
        font = document.styles[name].font
        font.color.rgb = color
        font.bold = bold
    summary_style = document.styles.add_style('Transcript Summary', WD_STYLE_TYPE.PARAGRAPH)
    summary_style.base_style = normal
    summary_style.font.color.rgb = RGBColor.from_string('28654B')
    document.add_heading(title, 0)
    hidden = set((result.get('video') or {}).get('export_hidden_scene_ids') or [])
    from .reading_outline import export_headings, sections as outline_sections
    sections = outline_sections(result)
    titles = export_headings(result, sections)
    image_bytes = 0
    frame_bytes = {}
    if result.get('review_notice'):
        document.add_paragraph(result['review_notice'])
    if result.get('topics') and not result.get('reading_outline'):
        document.add_heading('议题目录 / Topics', 1)
        for topic in result['topics']:
            topic_run = document.add_paragraph(style='Heading 2').add_run(topic['title'])
            topic_run.font.color.rgb = ink
            topic_run.bold = True
            document.add_paragraph(('原文要点 / Source excerpt: ' if topic.get('summary_method') == 'source_excerpt' else '') + topic['summary'], style=summary_style)
            document.add_paragraph(' / '.join(f"{timecode(r['start'])} – {timecode(r['end'])}" for r in topic['ranges']))
    def anchor_for(value): return 'a' + hashlib.sha256(str(value).encode()).hexdigest()[:32]
    def bookmark(paragraph, value, number):
        start = OxmlElement('w:bookmarkStart'); start.set(qn('w:id'), str(number)); start.set(qn('w:name'), anchor_for(value))
        end = OxmlElement('w:bookmarkEnd'); end.set(qn('w:id'), str(number))
        paragraph._p.insert(1 if paragraph._p.pPr is not None else 0, start)
        paragraph._p.append(end)
    def field_marker(paragraph, kind):
        marker = OxmlElement('w:fldChar')
        marker.set(qn('w:fldCharType'), kind)
        paragraph.add_run()._r.append(marker)

    def instruction(paragraph, value):
        node = OxmlElement('w:instrText')
        node.set(qn('xml:space'), 'preserve')
        node.text = value
        paragraph.add_run()._r.append(node)

    def add_link(paragraph, text, identity):
        element = OxmlElement('w:hyperlink')
        element.set(qn('w:anchor'), anchor_for(identity))
        run = paragraph.add_run(text)
        element.append(run._r)
        paragraph._p.append(element)

    if titles:
        from docx.enum.text import WD_TAB_ALIGNMENT, WD_TAB_LEADER
        document.add_paragraph('目录 / Contents', style='TOC Heading')
        for level in (1, 2):
            style = document.styles.add_style(f'TOC {level}', WD_STYLE_TYPE.PARAGRAPH)
            style.base_style = normal
            style.font.bold = level == 1
            style.paragraph_format.left_indent = Mm(6 if level == 2 else 0)
            style.paragraph_format.tab_stops.add_tab_stop(
                section.page_width - section.left_margin - section.right_margin,
                WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        first = True
        for item in sections:
            identity = item['id']
            if identity not in titles:
                continue
            paragraph = document.add_paragraph(style='TOC 1' if 'topic_heading' in item else 'TOC 2')
            if first:
                field_marker(paragraph, 'begin')
                instruction(paragraph, r' TOC \o "1-2" \h ')
                field_marker(paragraph, 'separate')
                first = False
            add_link(paragraph, titles[identity], identity)
            paragraph.add_run().add_tab()
            field_marker(paragraph, 'begin')
            instruction(paragraph, ' PAGEREF ' + anchor_for(identity) + r' \h ')
            field_marker(paragraph, 'end')
        field_marker(paragraph, 'end')
        update = OxmlElement('w:updateFields')
        update.set(qn('w:val'), 'true')
        document.settings.element.append(update)
        document.add_page_break()
    frame_root = (output_dir / 'frames').resolve()
    for index, item in enumerate(sections):
        if 'topic_heading' in item:
            paragraph = document.add_heading(titles[item['id']], 1)
            bookmark(paragraph, item['id'], index+1)
            document.add_paragraph(item['topic_summary'], style=summary_style)
            for related in item['related_item_ids']:
                add_link(document.add_paragraph(), '相关内容 / Related: ' + titles[related], related)
            continue
        if result.get('reading_outline') and not item.get('reading_resource'):
            paragraph = document.add_heading(titles[item['id']], 2)
            bookmark(paragraph, item.get('id', str(index)), index+1)
        if result.get('video'):
            title = (item.get('title_summary') or {}).get('title')
            if item.get('image') and not result.get('reading_outline'):
                frame = document.add_heading(level=1).add_run(f"画面 {item['frame_number']}" + (f" · {title}" if title else ''))
                frame.font.color.rgb = muted
                frame.bold = False
            elif title and not result.get('reading_outline'):
                document.add_paragraph().add_run(title).font.color.rgb = muted
            document.add_paragraph(f"{timecode(item['start'])} – {timecode(item['end'])}")
            if item.get('reading_block') and item.get('frame_number'):
                document.add_paragraph(f"关联画面 / Associated frame {item['frame_number']}")
            method = insight_label(item.get('title_summary') or {})
            if method:
                document.add_paragraph(method)
            if not item.get('image'):
                pass
            elif item.get('frame_scene_id', item['id']) in hidden:
                document.add_paragraph('画面已隐藏 / Picture hidden')
            else:
                name = item['image']
                path = (frame_root / name).resolve()
                if Path(name).name != name or not path.is_relative_to(frame_root):
                    raise ValueError('Invalid frame path')
                image_bytes += 0 if name in frame_bytes else path.stat().st_size
                if image_bytes > _max_image_bytes:
                    raise ValueError('Images exceed DOCX resource budget; split the source')
                # Read the validated bytes, avoiding filename metadata in the package.
                if name not in frame_bytes: frame_bytes[name] = path.read_bytes()
                stream = io.BytesIO(frame_bytes[name])
                picture = Image.from_file(stream)
                scale = min(int(section.page_width - section.left_margin - section.right_margin) / picture.width, int(Mm(100)) / picture.height)
                stream.seek(0)
                document.add_picture(stream, width=int(picture.width * scale), height=int(picture.height * scale))
        if item.get('related_start') is not None:
            document.add_paragraph(f"相关文字 / Related text: {timecode(item['related_start'])}")
        for paragraph in item['paragraphs']:
            label = document.add_paragraph()
            label.paragraph_format.keep_with_next = True
            label.add_run(f"{paragraph.get('speaker_label') or paragraph['speaker']}　{timecode(paragraph['start'])} – {timecode(paragraph['end'])}").bold = True
            if paragraph.get('merged_source_count', 0) > 1:
                label.add_run(f"　合并 {paragraph['merged_source_count']} 条来源 / Merged sources")
            if paragraph.get('generation_method') in ('basic_cleanup', 'mixed'):
                label.add_run('　包含基础整理 / Includes basic cleanup').italic = True
            if paragraph.get('review_status') == 'needs_review':
                label.add_run('　待核对 / Needs review').italic = True
            if paragraph.get('review_note'):
                document.add_paragraph(paragraph['review_note'])
            if 'original_text' in paragraph:
                table = document.add_table(rows=2, cols=2)
                table.autofit = False
                column_width = int((section.page_width - section.left_margin - section.right_margin) / 2)
                for column in table.columns:
                    column.width = column_width
                for row in table.rows:
                    for cell in row.cells:
                        cell.width = column_width
                        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                table.rows[0]._tr.get_or_add_trPr().append(OxmlElement('w:tblHeader'))
                for cell, title in zip(table.rows[0].cells, ('原文 / Original', '改写 / Rewritten')):
                    cell.paragraphs[0].add_run(title).bold = True
                    shade = OxmlElement('w:shd'); shade.set(qn('w:fill'), 'F0F2F4')
                    cell._tc.get_or_add_tcPr().append(shade)
                changes = ([(comparison_source_text(paragraph), False)], [(paragraph['text'], False)]) if result.get('optimization_policy', 0) >= 6 else text_changes(paragraph['original_text'], paragraph['text'])
                for index, (cell, parts) in enumerate(zip(table.rows[1].cells, changes)):
                    content = cell.paragraphs[0]
                    content.paragraph_format.keep_together = False
                    content.paragraph_format.keep_with_next = False
                    for text, changed in parts:
                        run = content.add_run(text)
                        if changed:
                            run.font.color.rgb = RGBColor.from_string('9C3A32' if index == 0 else '12634B')
                            run.font.strike = index == 0
                document.add_paragraph().paragraph_format.space_after = Pt(4)
            else:
                document.add_paragraph(paragraph['text'])
            for text in group_insight_lines(paragraph.get('group_summary') or {}):
                content = document.add_paragraph(text, style=summary_style)
                content.paragraph_format.left_indent = Mm(3)
                for run in content.runs:
                    run.font.size = Pt(10)
        for link in item.get('linked_paragraphs', []):
            document.add_paragraph(f"本段文字已并入 {('画面 ' + str(link['frame_number'])) if link.get('frame_number') else '相关段落'} 的成稿（{timecode(link['start'])}） / Text included in the earlier group")

    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def download(title: str, result: dict[str, Any], output_dir: Path, cache_variant: str = "") -> bytes:
    key = hashlib.sha256(json.dumps([str(output_dir), title, result, cache_variant], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        generation = _cache_generation
        for old in [k for k, (expiry, _) in _cache.items() if expiry <= now]:
            del _cache[old]
        cached = _cache.get(key)
        if cached:
            _cache.move_to_end(key)
            return cached[1]
    if not _render_lock.acquire(blocking=False):
        raise BlockingIOError('DOCX renderer is busy; retry shortly')
    try:
        payload = render(title, result, output_dir)
        with _lock:
            if generation == _cache_generation and len(payload) <= _cache_limit:
                while _cache and sum(len(data) for _, data in _cache.values()) + len(payload) > _cache_limit:
                    _cache.popitem(last=False)
                _cache[key] = (time.monotonic() + 300, payload)
        return payload
    finally:
        _render_lock.release()
