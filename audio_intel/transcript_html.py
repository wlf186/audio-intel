"""Standalone, offline ASR reading document."""
import base64
from hashlib import sha256
from html import escape
from pathlib import Path
from threading import Lock

from .ui_theme import stylesheet
from .reading_outline import sections, export_headings
from .transcript import comparison_source_text, insight_label, paragraphs, join_text
from .transcript_diff import text_changes
from .utils import timecode

_assets = Path(__file__).parent / 'assets' / 'reader'
_render_lock = Lock()
_max_text_chars = 4_000_000
_max_image_bytes = 128 * 1024**2
_max_bytes = 256 * 1024**2


def render(title: str, result: dict, output_dir: Path, locale: str = 'zh-CN') -> bytes:
    def tr(zh: str, en: str) -> str:
        return en if locale == 'en-US' else zh

    def period(item: dict) -> str:
        return f"{timecode(item['start'])} – {timecode(item['end'])}"

    if sum(len(p.get('text', '')) + len(p.get('original_text', '')) for p in result.get('segments', [])) > _max_text_chars:
        raise ValueError('Transcript exceeds export text budget')
    def short_time(seconds: float) -> str:
        minutes, second = divmod(max(0, int(seconds)), 60)
        hour, minute = divmod(minutes, 60)
        return f'{hour}:{minute:02}:{second:02}' if hour else f'{minute:02}:{second:02}'

    parts = []
    size = 0

    def add(fragment: str) -> None:
        nonlocal size
        data = fragment.encode('utf-8')
        size += len(data)
        if size > _max_bytes:
            raise ValueError('Transcript exceeds export size budget')
        parts.append(data)

    nav = []
    blocks = sections(result)
    headings = export_headings(result, blocks)
    anchors = {s.get('id'): f'section-{i}' for i, s in enumerate(blocks)}
    hidden = set((result.get('video') or {}).get('export_hidden_scene_ids', []))
    image_bytes = 0
    next_time = 0
    has_summary = False

    def heading(label: str, anchor: str, level: int) -> None:
        safe = escape(label)
        nav.append(f'<a class="level-{level}" href="#{anchor}">{safe}</a>')
        add(f'<h{level} id="{anchor}">{safe}</h{level}>')

    def topic_heading(label: str, anchor: str, summary: str) -> None:
        nonlocal has_summary
        add('<div class="topic-row">')
        heading(label, anchor, 2)
        if summary:
            has_summary = True
            add(f'<aside class="margin-note"><small>{tr("议题概述","Topic overview")}</small><p>{escape(summary)}</p></aside>')
        add('</div>')

    if not result.get('reading_outline'):
        for i, topic in enumerate(result.get('topics', [])):
            topic_heading(topic['title'], f'topic-{i}', topic['summary'])
            for span in topic.get('ranges', []):
                add(f'<time>{period(span)}</time>')
        if not result.get('video') and not result.get('_optimized'):
            blocks = [{'paragraphs': paragraphs(result.get('segments', []), include_sources=True)}]
    for i, section in enumerate(blocks):
        anchor = f'section-{i}'
        if 'topic_heading' in section:
            topic_heading(headings[section['id']], anchor, section.get('topic_summary', ''))
            for link in section['related_item_ids']:
                if link in anchors:
                    add(f'<a href="#{anchors[link]}">{escape(headings[link])}</a>')
            continue
        label = headings.get(section.get('id')) or (section.get('title_summary') or {}).get('title')
        if not label and section.get('image'):
            label = tr('画面 ', 'Frame ') + str(section['frame_number'])
        if label:
            heading(label, anchor, 3)
            method = insight_label(section.get("title_summary") or {})
            if method:
                add(f"<small>{escape(method)}</small>")
        if section.get('image'):
            add('<figure>')
            if section.get('frame_scene_id', section['id']) in hidden:
                add(tr('画面已隐藏', 'Picture hidden'))
            else:
                folder = (output_dir / 'frames').resolve()
                name = section['image']
                path = (folder / name).resolve()
                if Path(name).name != name or path.parent != folder:
                    raise ValueError('Invalid frame name')
                image_bytes += path.stat().st_size
                if image_bytes > _max_image_bytes:
                    raise ValueError('Images exceed export budget')
                image = base64.b64encode(path.read_bytes()).decode('ascii')
                add(f'<img loading="lazy" alt="{tr("转写画面","Transcript frame")}" src="data:image/jpeg;base64,{image}">')
            caption = tr('画面 ', 'Frame ') + str(section['frame_number'])
            add(f'<figcaption>{escape(caption)} · {period(section)}</figcaption></figure>')
        previous_speaker = None
        for j, p in enumerate(section['paragraphs']):
            if not label and not result.get('video') and p['start'] >= next_time:
                target = f'p-{i}-{j}'
                nav.append(f'<a class="level-3" href="#{target}">{short_time(p["start"])}</a>')
                add(f'<span class="anchor" id="{target}"></span>')
                previous_speaker = None
                next_time = p['start'] + 300
            summary = p.get('group_summary') or {}
            speaker = p.get('speaker')
            repeated = bool(speaker and speaker == previous_speaker)
            previous_speaker = speaker
            speech_id = f'speech-{i}-{j}'
            add(f'<section class="speech" id="{speech_id}"><div class="passage">')
            name = escape(str(p.get('speaker_label') or speaker or ''))
            name_class = 'continued' if repeated else 'speaker'
            add(f'<header><strong class="{name_class}">{name}</strong>'
                f'<details class="clock"><summary>{short_time(p["start"])}</summary><time>{period(p)}</time></details>')
            add(f'<a class="summary-jump" href="#{speech_id}">{short_time(p["start"])}</a>')
            if p.get('review_status') == 'needs_review':
                add(f' <span class="warn">{tr("待核对","Needs review")}</span>')
            if p.get('merged_source_count', 0) > 1:
                add(f' · {tr("合并来源", "Merged sources")} {int(p["merged_source_count"])}')
            if p.get('generation_method') in ('basic_cleanup','mixed'):
                add(tr(' · 基础整理',' · Basic cleanup'))
            add('</header><div class="body">')
            content = escape(p['text'])
            if p.get('sources') and not result.get('_optimized') and not result.get('video'):
                # Insert anchors at genuine source boundaries inside a long merged speech.
                fragments = []
                for k, source in enumerate(p['sources']):
                    if source['start'] >= next_time:
                        target = f'time-{i}-{j}-{k}'
                        stamp = short_time(source['start'])
                        nav.append(f'<a class="level-3" href="#{target}">{stamp}</a>')
                        fragments.append(f'<span id="{target}"></span>')
                        next_time = source['start'] + 300
                    text = source['text']
                    if k:
                        joined = join_text(p['sources'][k-1]['text'], text)
                        if joined != p['sources'][k-1]['text'] + text:
                            fragments.append(' ')
                    if len(p['sources']) > 1:
                        fragments.append(f'<time class="stamp">{period(source)} </time>')
                    fragments.append(escape(text))
                content = ''.join(fragments)
            if 'original_text' in p:
                source = escape(comparison_source_text(p))
                if result.get('optimization_policy', 0) < 6:
                    left, right = text_changes(p['original_text'], p['text'])
                    def changed(tokens, tag):
                        return ''.join(f'<{tag}>{escape(t)}</{tag}>' if flag else escape(t) for t, flag in tokens)
                    source, content = changed(left, 'del'), changed(right, 'ins')
                add(f'<div class="pair"><details class="source" open><summary>{tr("对应原文","Original")}</summary><p>{source}</p></details><div><b>{tr("优化稿","Optimized")}</b><p>{content}</p></div></div>')
            else:
                add(f'<p>{content}</p>')
            if p.get('review_note'):
                add(f'<details open><summary>{tr("核对说明","Review notes")}</summary><p>{escape(p["review_note"])}</p></details>')
            if 'class="stamp"' in content:
                add(f'<button class="timing" aria-expanded="false">{tr("逐句时间","Source timing")}</button>')
            add('</div></div>')
            notes = [item for item in [summary, *summary.get('subgroups', [])]
                     if item.get('state') == 'ready' and any(item.get('sentences', []))]
            if notes:
                has_summary = True
                add('<aside class="margin-note">')
                for note in notes:
                    whole = note is summary
                    label = tr('本段小结', 'Passage summary') if whole else tr('分段小结', 'Segment summary')
                    stamp = short_time(p['start']) if whole else period(note)
                    method = insight_label(note)
                    label_class = 'passage-label' if whole else 'segment-label'
                    add(f'<small class="{label_class}">{label} <a href="#{speech_id}">{stamp}</a></small>')
                    if method:
                        add(f'<small>{escape(method)}</small>')
                    add(f'<p>{escape(" ".join(note["sentences"]))}</p>')
                add('</aside>')
            add('</section>')
        for link in section.get('linked_paragraphs', []):
            add(f'<p>{tr("相关发言","Related passage")} · {timecode(link["start"])}</p>')
    body = b''.join(parts)
    parts.clear()
    version = {'original':tr('原文','Original'), 'optimized':tr('优化稿','Optimized'), 'comparison':tr('对照版','Comparison')}[result.get('text_version','original')]
    comparison_class = 'comparison' if result.get("text_version") == "comparison" else 'reading'
    if has_summary:
        comparison_class += ' has-notes'
    title = escape(title)
    summary_control = (f'<button id="brief" data-full="{tr("返回全文","Full text")}" '
                       f'aria-pressed="false">{tr("小结","Summaries")}</button>') if has_summary else ''
    css = stylesheet() + (_assets / 'reader.css').read_text(encoding='utf-8')
    js = (_assets / 'reader.js').read_text(encoding='utf-8')
    script_hash = base64.b64encode(sha256(js.encode()).digest()).decode()
    duration = ' · ' + short_time(result['duration']) if result.get('duration') is not None else ''
    def icon(paths: str) -> str:
        return f'<svg aria-hidden="true" viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.6">{paths}</svg>'

    search_icon = icon('<circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6"/>')
    menu_icon = icon('<path d="M3 6h18M3 12h18M3 18h18"/>')
    print_icon = icon('<path d="M7 8V3h10v5M7 16H4V9h16v7h-3M7 14h10v7H7z"/>')
    add(f'''<!doctype html><html lang="{locale}"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'sha256-{script_hash}'; base-uri 'none'; form-action 'none'">
<title>{title}</title><style>{css}</style></head><body class="{comparison_class}">
<nav id="directory" data-hierarchical="{int(bool(result.get('reading_outline')))}"><b>{tr("目录","Contents")}</b><button id="closemenu" hidden>{tr("关闭","Close")}</button>{''.join(nav)}</nav>
<main>
<div class="tools" hidden>
<button id="menu" aria-controls="directory" aria-expanded="false">{menu_icon}{tr("目录","Contents")}</button>
<button id="find" aria-expanded="false" aria-controls="searchbox">{search_icon}{tr("搜索","Search")}</button>
<div id="searchbox">{search_icon}<input id="query" type="search" aria-label="{tr("搜索文稿","Search transcript")}" placeholder="{tr("搜索文稿","Search transcript")}">
<output id="count"></output><button id="prev" hidden aria-label="{tr("上一处","Previous match")}">↑</button><button id="next" hidden aria-label="{tr("下一处","Next match")}">↓</button></div>
{summary_control}<button id="more" aria-expanded="false" aria-controls="options">··· {tr("更多","More")}</button><div id="options" hidden><button id="print">{print_icon}{tr("打印","Print")}<span> / {tr("另存为 PDF","Save as PDF")}</span></button></div></div>
<div class="masthead"><h1>{title}</h1><small>{version}{duration}</small></div>
<article>{escape(result.get("review_notice") or "")}''')
    parts.append(body)
    add(f'</article></main><dialog id="lightbox" aria-label="{tr("查看画面","View frame")}"><div class="viewer-tools"><button id="frame-close" autofocus>{tr("关闭","Close")}</button><button id="zoom" data-fit="{tr("适应屏幕","Fit to screen")}" data-actual="{tr("原始尺寸","Actual size")}"></button></div><div class="frame-view"><img alt=""></div></dialog><script>{js}</script></html>')
    return b''.join(parts)


def download(title: str, result: dict, output_dir: Path, locale: str = 'zh-CN') -> bytes:
    if not _render_lock.acquire(blocking=False):
        raise BlockingIOError('HTML renderer busy')
    try:
        return render(title, result, output_dir, locale)
    finally:
        _render_lock.release()
