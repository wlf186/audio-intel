"""Stream independent, offline reading documents from immutable job snapshots."""
import base64
import hashlib
import json
import zipfile
from bisect import bisect_right
from html import escape
from pathlib import Path
from urllib.parse import quote

from .document_download import Sink
from .document_narration import NarrationOptions, skipped_ranges
from .tts_text import split_text

ASSETS = Path(__file__).parent / 'assets' / 'audiobook'


class OmittedRanges:
    def __init__(self, ranges):
        self.edges = [edge for pair in ranges for edge in pair]

    def __contains__(self, position):
        return bisect_right(self.edges, position) % 2 == 1


def marked_text(text, offset, chunks, times, rate, skipped, notes=(), headings=()):
    """Walk source code points in order, ignoring only omitted text and whitespace."""
    cursor = 0
    ranges = []
    if times and len(times) == len(chunks):
        for chunk, (start, end) in zip(chunks, times):
            for char in chunk:
                if char.isspace():
                    continue
                omitted = False
                while cursor < len(text) and (text[cursor].isspace() or offset + cursor in skipped):
                    omitted |= offset + cursor in skipped
                    cursor += 1
                if cursor == len(text) or text[cursor] != char:
                    return marked_text(text, offset, [], [], rate, skipped, notes, headings)
                if ranges and ranges[-1][2] == start and not omitted:
                    ranges[-1][1] = cursor + 1
                else:
                    ranges.append([cursor, cursor+1, start, end])
                cursor += 1
    # Render text once, splitting spans at both editorial and audio boundaries.
    cuts = {0, len(text)}
    notes = sorted((max(0,a-offset), min(len(text),b-offset)) for a,b in notes
                   if a < offset+len(text) and b > offset)
    titles = {}
    for position, (at, title) in enumerate(headings):
        at -= offset
        if text.startswith(title, at):
            titles[at] = (at+len(title), f'h{offset}-{position}')
    for a,b,*_ in [*ranges,*notes]:
        cuts.update((a,b))
    for a,(b,_) in titles.items():
        cuts.update((a,b))
    cuts = sorted(cuts)
    parts = []
    cue = note = 0
    closing = None
    for a,b in zip(cuts,cuts[1:]):
        if a in titles:
            closing, anchor = titles[a]
            parts.append(f'<h3 id="{anchor}">')
        value = escape(text[a:b])
        while cue < len(ranges) and ranges[cue][1] <= a:
            cue += 1
        if cue < len(ranges) and ranges[cue][0] <= a:
            _,_,start,end = ranges[cue]
            value = f'<span tabindex="0" data-at="{start/rate:.6f}" data-end="{end/rate:.6f}">{value}</span>'
        while note < len(notes) and notes[note][1] <= a:
            note += 1
        if note < len(notes) and notes[note][0] <= a:
            if offset+a in skipped:
                value = '<details class="note"><summary>原书注释</summary>'+value+'</details>'
            else:
                value = '<small class="note">'+value+'</small>'
        parts.append(value)
        if b == closing:
            parts.append('</h3>')
    return ''.join(parts)


class Book:
    def __init__(self, job, root, files, section_id=None):
        self.id = job['id']
        self.title = job['request']['document']['title']
        self.text = (root/'input/document.txt').read_text(encoding='utf-8')
        if hashlib.sha256(self.text.encode()).hexdigest() != job['request']['document']['text_hash']:
            raise ValueError('Document snapshot checksum mismatch')
        self.manifest = json.loads((root/'input/document.json').read_text(encoding='utf-8'))
        self.artifacts = {a['name']:a for a in job['result']['artifacts']}
        self.sections = [s for s in job['result']['document']['sections'] if not section_id or s['id']==section_id]
        self.paths = dict(files)
        self.source = {s['id']:s for s in self.manifest['sections']}
        self.index_dir = root / 'reading'
        options = self.manifest.get('narration_options')
        self.skips = OmittedRanges(skipped_ranges(self.manifest, NarrationOptions.model_validate(options)) if options else [])
        self.notes = sorted((a['start'],a['end']) for a in self.manifest.get('annotations',[]) if a['kind']=='note_body')

    def html(self, sections, *, online=False, single=False):
        title = sections[0]['title'] if single else self.title
        js = (ASSETS/'book.js').read_text(encoding='utf-8')
        css = (ASSETS/'book.css').read_text(encoding='utf-8')
        digest = base64.b64encode(hashlib.sha256(js.encode()).digest()).decode()
        media = "'self'" if online else "data: blob:"
        nav_items = []
        subheads = {}
        for i, section in enumerate(sections):
            source = self.source[section['id']]
            low, high = source['start'], source['end']
            subheads[i] = [(h['offset'],h['title']) for h in self.manifest.get('headings',[])
                           if low < h['offset'] < high and self.text.startswith(h['title'], h['offset'])]
            nav_items.append(f'<a class="section-link" href="#s{i}">{escape(section["title"])}</a>')
            for j, (_, label) in enumerate(subheads[i]):
                nav_items.append(f'<a class="subhead" href="#h{low}-{j}">{escape(label)}</a>')
        nav = ''.join(nav_items)
        heading = '<small>'+escape(self.title)+'</small>' if single and title!=self.title else ''
        first = self.source[sections[0]['id']]
        show_title = not single or self.text[first['start']:first['end']].split('\n',1)[0].strip() != title
        masthead = f'<h1>{escape(title)}</h1>' if show_title else ''
        seconds = round(sum(s['duration'] for s in sections))
        yield f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-{digest}'; media-src {media}; base-uri 'none'; form-action 'none'"><title>{escape(title)}</title><style>{css}</style><body data-book="{escape(self.id)}" data-single="{int(single)}"><header><button id="menu" aria-expanded="false" aria-controls="toc">☰ 目录</button></header><nav id="toc" aria-label="章节目录"><strong>目录</strong>{nav}</nav><main>{heading}{masthead}<small>{len(sections)} 段 · {seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}</small>'''.encode()
        for i,s in enumerate(sections):
            name = s['artifact_name']
            source = self.source[s['id']]
            low, high = source['start'], source['end']
            text = self.text[low:high]
            plan = self.manifest.get('plans',{}).get(s['id'])
            chunks = [c['text'] for c in plan] if plan else split_text(text)
            times = None
            try:
                checkpoint = json.loads((self.index_dir / (s['id']+'.json')).read_text(encoding='utf-8'))
                if checkpoint['version']==1 and checkpoint['sha256']==self.artifacts[name].get('sha256'):
                    samples = checkpoint['samples']
                    if isinstance(samples,list) and all(isinstance(pair,list) and len(pair)==2 and
                            all(type(n) is int for n in pair) and 0<=pair[0]<pair[1] for pair in samples):
                        if all(b <= c for (_, b), (c, _) in zip(samples,samples[1:])) and (not samples or samples[-1][1] <= (s['duration']+.001)*s['sample_rate']):
                            times = samples
            except (OSError, ValueError, TypeError, KeyError):
                pass
            chapter_heading = text.split('\n',1)[0].strip() == s['title']
            heads = list(subheads[i])
            if chapter_heading:
                heads.append((low + text.index(s['title']), s['title']))
            body = marked_text(text,low,chunks,times,s['sample_rate'],self.skips,self.notes,heads)
            if chapter_heading:
                tag = 'h1' if single else 'h2'
                original = f'<h3 id="h{low}-{len(heads)-1}">'
                before, _, after = body.partition(original)
                title_html, _, rest = after.partition('</h3>')
                body = before + f'<{tag}>{title_html}</{tag}>' + rest.lstrip('\n')
            src = f'/api/v1/jobs/{self.id}/artifacts/{quote(name,safe="")}' if online else ''
            subtitle = '' if single or chapter_heading else f'<h2>{escape(s["title"])}</h2>'
            yield f'<section id="s{i}" data-id="{escape(s["id"])}" data-src="{src}" data-title="{escape(s["title"])}">{subtitle}<button class="chapter">从本段听</button><article>{body}</article></section>'.encode()
            if not online:
                yield b'<script type="application/octet-stream" id="audio'+str(i).encode()+b'">'
                with self.paths[name].open('rb') as audio:
                    while block := audio.read(3*65536):
                        yield base64.b64encode(block)
                yield b'</script>'
        yield ('''</main><footer><div class="now"><span id="status" role="status"></span><button id="follow" hidden>回到正在朗读</button></div><input type="range" id="seek" step=".1" min="0" max="0" value="0" aria-label="播放位置"><div class="buttons"><button id="play">开始听</button><button id="back">回听 10 秒</button><select id="speed" aria-label="倍速">'''+''.join(f'<option value="{n}" {"selected" if n==1 else ""}>{n}×</option>' for n in (.75,1,1.25,1.5,1.75,2))+'''</select><time></time><button id="more" aria-expanded="false">更多</button></div><div id="extras" hidden><button id="prev" disabled>上一片段</button><button id="restart" disabled>重播本片段</button><button id="next" disabled>下一片段</button><button id="undo" disabled>返回跳播前</button></div></footer><audio preload="metadata"></audio><script>'''+js+'</script></body></html>').encode()

    def archive(self):
        sink = Sink()
        with zipfile.ZipFile(sink,'w',allowZip64=True) as archive:
            for section in self.sections:
                name = Path(section['artifact_name']).with_suffix('.html').name
                with archive.open(name,'w',force_zip64=True) as entry:
                    for chunk in self.html([section],single=True):
                        entry.write(chunk)
                        yield sink.drain()
                yield sink.drain()
        yield sink.drain()
