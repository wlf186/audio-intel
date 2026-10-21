"""Offline PDF downloads. Bounded concurrency and an in-memory LRU, no disk cache."""
from __future__ import annotations

import hashlib
import html
import io
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .transcript import paragraphs, scene_transcripts
from .utils import timecode

_lock = threading.Lock()
_render_lock = threading.Lock()
_cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_cache_limit = 64 * 1024**2
_font_ready = False
_max_image_bytes = 128 * 1024**2
_max_text_chars = 4_000_000


def render(title: str, result: dict[str, Any], output_dir: Path) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Flowable

    if sum(len(segment.get('text', '')) for segment in result.get('segments', [])) > _max_text_chars:
        raise ValueError('Transcript exceeds PDF text budget; split the source')
    image_bytes = 0
    global _font_ready
    if not _font_ready:
        pdfmetrics.registerFont(TTFont('Transcript', str(Path(__file__).parent / 'assets/fonts/NotoSansSC.ttf')))
        _font_ready = True
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=48, leftMargin=48,
                            topMargin=44, bottomMargin=44, title=title, author='Sandevistan-Audio')
    body = ParagraphStyle('body', fontName='Transcript', fontSize=10.5, leading=17,
                          wordWrap='CJK', spaceAfter=12, alignment=TA_LEFT)
    heading = ParagraphStyle('heading', parent=body, fontSize=17, leading=24, spaceAfter=18)
    label = ParagraphStyle('label', parent=body, fontSize=9, leading=14,
                           textColor=colors.HexColor('#4c5967'), spaceAfter=4, keepWithNext=True)
    class HiddenPicture(Flowable):
        def __init__(self, text='画面已隐藏 / Picture hidden'):
            super().__init__()
            self.width, self.height = doc.width, 120
            self.text = text

        def draw(self):
            self.canv.setFillColor(colors.HexColor('#f0f2f4'))
            self.canv.rect(0, 0, self.width, self.height, stroke=0, fill=1)
            self.canv.setFillColor(colors.HexColor('#4c5967'))
            self.canv.setFont('Transcript', 12)
            self.canv.drawCentredString(self.width / 2, self.height / 2, self.text)

    hidden = set((result.get('video') or {}).get('export_hidden_scene_ids') or [])
    story: list[Any] = [Paragraph(html.escape(title), heading)]
    sections = scene_transcripts(result) if result.get('video') else [{'paragraphs': paragraphs(result.get('segments', []))}]
    for index, section in enumerate(sections):
        if result.get('video'):
            story.append(Spacer(1, 14))
            story.append(Paragraph(f"画面 {index + 1} · {timecode(section['start'])} – {timecode(section['end'])}", label))
            if not section.get('image'):
                story.extend([HiddenPicture('无稳定画面 / No stable picture'), Spacer(1, 12)])
            elif section['id'] in hidden:
                story.extend([HiddenPicture(), Spacer(1, 12)])
            else:
                name = section['image']
                if Path(name).name != name:
                    raise ValueError('Invalid frame name')
                path = (output_dir / 'frames' / name).resolve()
                if not path.is_relative_to((output_dir / 'frames').resolve()):
                    raise ValueError('Invalid frame path')
                image_bytes += path.stat().st_size
                if image_bytes > _max_image_bytes:
                    raise ValueError('Images exceed PDF resource budget; split the source')
                picture = Image(str(path))
                scale = min(doc.width / picture.imageWidth, 260 / picture.imageHeight)
                picture.drawWidth = picture.imageWidth * scale
                picture.drawHeight = picture.imageHeight * scale
                story.extend([picture, Spacer(1, 12)])
        for paragraph in section['paragraphs']:
            identity = html.escape(str(paragraph.get('speaker_label') or paragraph['speaker']))
            story.append(Paragraph(f"{identity}　{timecode(paragraph['start'])} – {timecode(paragraph['end'])}", label))
            story.append(Paragraph(html.escape(paragraph['text']).replace('\n', '<br/>'), body))
        if not section['paragraphs']:
            story.append(Paragraph('此段无识别语音 / No recognized speech', body))

    def page_number(canvas: Any, document: Any) -> None:
        canvas.setFont('Transcript', 8)
        canvas.setFillColor(colors.HexColor('#66717b'))
        canvas.drawRightString(A4[0] - 48, 24, str(document.page))

    doc.build(story, onFirstPage=page_number, onLaterPages=page_number)
    return stream.getvalue()


def download(title: str, result: dict[str, Any], output_dir: Path, cache_variant: str = "") -> bytes:
    key = hashlib.sha256(json.dumps([str(output_dir), title, result, cache_variant], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    now = time.monotonic()
    with _lock:
        for old in [k for k, (expiry, _) in _cache.items() if expiry <= now]:
            del _cache[old]
        cached = _cache.get(key)
        if cached:
            _cache.move_to_end(key)
            return cached[1]
    if not _render_lock.acquire(blocking=False):
        raise BlockingIOError('PDF renderer is busy; retry shortly')
    try:
        payload = render(title, result, output_dir)
        with _lock:
            if len(payload) <= _cache_limit:
                while _cache and sum(len(data) for _, data in _cache.values()) + len(payload) > _cache_limit:
                    _cache.popitem(last=False)
                _cache[key] = (time.monotonic() + 300, payload)
        return payload
    finally:
        _render_lock.release()
