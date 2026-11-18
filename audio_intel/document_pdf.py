"""PDF layout evidence mapped back to retained pypdf extraction offsets."""
from __future__ import annotations

import re
import statistics
from typing import Any

from .document_narration import annotation


def page_text(page: Any) -> tuple[str, list[dict[str, Any]]]:
    fragments = []
    def visit(text, cm, tm, font, size):
        if text.strip():
            # Apply the text origin through the current transformation matrix.
            x = tm[4]*cm[0] + tm[5]*cm[2] + cm[4]
            y = tm[4]*cm[1] + tm[5]*cm[3] + cm[5]
            fragments.append({'text': text.replace('\r', ''), 'x': x, 'y': y,
                              'size': abs(size)*(cm[0]**2+cm[1]**2)**.5})
    text = (page.extract_text(visitor_text=visit) or '').replace('\r', '')
    if page.rotation or float(page.mediabox.height) <= 0:
        return text, []  # Keep uncertain rotated geometry readable, never guess exclusions.
    cursor = 0; lines = []
    for item in fragments:
        value = item['text'].strip()
        at = text.find(value, cursor)
        if at < 0: continue  # Unmappable geometry must never exclude text.
        cursor = at + len(value)
        lines.append({**item, 'start': at, 'end': cursor, 'text': value,
                      'height': float(page.mediabox.height), 'bottom': float(page.mediabox.bottom)})
    return text, lines


def analyze(document: dict[str, Any], pages: list[list[dict[str, Any]]]) -> None:
    annotations = []; boundaries = []
    candidates = []
    offsets = document['page_offsets']
    text = document['text']
    for index, lines in enumerate(pages):
        for row in lines:
            at = offsets[index] + row['start']
            line_start = text.rfind('\n', 0, at) + 1
            line_end = text.find('\n', at)
            line = text[line_start:line_end if line_end >= 0 else len(text)].strip()
            vertical = (row['y'] - row['bottom']) / row['height']
            others = [r for r in lines if r is not row and not re.fullmatch(r'\d+', r['text'])]
            typical_size = statistics.median([r['size'] for r in others]) if others else 0
            separated = bool(others) and min(abs(r['y']-row['y']) for r in others) > 2*typical_size
            if 0 <= vertical <= 1 and (row['size'] < typical_size*.9 or separated) and line == row['text'] and re.fullmatch(r'\d{1,5}', line) and (vertical < .12 or vertical > .88):
                candidates.append((index, row, int(line) - index))
    # Three nearby pages corroborate a numbering run in the same page-edge band.
    for index, row, delta in candidates:
        peers = [(p, r) for p, r, d in candidates if d == delta and abs(p-index) <= 3
                 and abs((r['y']-r['bottom'])/r['height']-(row['y']-row['bottom'])/row['height']) < .025]
        if len({p for p, r in peers}) >= 3:
            annotations.append(annotation(offsets[index]+row['start'], offsets[index]+row['end'],
                                          'page_number', 'repeated_page_edge_sequence', page=index+1))
    furniture = {(a['start'], a['end']) for a in annotations}
    previous = None
    for index, lines in enumerate(pages):
        body = [r for r in lines if (offsets[index]+r['start'], offsets[index]+r['end']) not in furniture]
        typical_size = statistics.median([r['size'] for r in body]) if body else 0
        notes = []
        for pos, row in enumerate(body):
            at = offsets[index] + row['start']
            marker = re.match(r'^(\[\d{1,3}\]|[①-⑳]|\d{1,3}[.)、]?)\s+', row['text'])
            if not marker or row['size'] >= typical_size*.85 or (row['y']-row['bottom'])/row['height'] >= .25:
                continue
            preceding = text[offsets[index]:at]
            if re.search(r'参考文献|资料来源|References|Bibliography', preceding, re.I):
                continue
            label = marker[1].rstrip('.)、')
            references = []
            if label.startswith('[') or label in '①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳':
                references = [(offsets[index]+m.start(), offsets[index]+m.end())
                              for m in re.finditer(r'(?<![A-Za-z0-9_])'+re.escape(label), preceding)]
            else:
                # Bare numeric references require superscript geometry as well as
                # a matching small-print footer definition. Do not strip digits
                # simply because they also occur at the start of a note.
                for ref in body[:pos]:
                    if ref['text'] != label or ref['size'] >= typical_size*.8:
                        continue
                    if any(other['size'] >= typical_size*.9 and .15*typical_size < ref['y']-other['y'] < typical_size
                           for other in body[:pos] if other is not ref):
                        references.append((offsets[index]+ref['start'], offsets[index]+ref['end']))
            if not references:
                continue
            end = row['end']
            for following in body[pos+1:]:
                if following['size'] >= typical_size*.85 or re.match(r'^(?:\[\d+\]|[①-⑳]|\d+[.)、]?)\s+', following['text']):
                    break
                end = following['end']
            notes.append((row['start'], end))
            annotations.append(annotation(at, offsets[index]+end, 'note_body', 'linked_small_footer_note', page=index+1))
            annotations.extend(annotation(low, high, 'note_reference', 'linked_footer_note', page=index+1) for low, high in references)
        # Footnotes are not part of the paragraph/page-continuation geometry.
        body = [r for r in body if not any(low <= r['start'] < high for low, high in notes)]
        gaps = [abs(a['y']-b['y']) for a, b in zip(body, body[1:])
                if abs(a['size']-b['size']) < .5 and abs(a['y']-b['y']) > 1]
        typical = statistics.median(sorted(gaps)[:max(1, len(gaps)*2//3)]) if gaps else 0
        for pos, row in enumerate(body):
            at = offsets[index]+row['start']
            if pos:
                gap = abs(row['y']-body[pos-1]['y'])
                if (typical and gap > typical*1.35) or row['size'] > typical_size*1.25:
                    boundaries.append({'offset': at, 'kind': 'paragraph'})
            elif previous and re.search(r'[。！？.!?；;][”’」』]*\s*(?:\[\d+\])?$', previous['text']):
                boundaries.append({'offset': at, 'kind': 'paragraph'})
        if body:
            previous = body[-1]
    document['annotations'] = annotations
    document['boundaries'] = boundaries
    # Bookmarks often point only to a page. Resolve a unique title within it so
    # a mid-page chapter does not move the preceding paragraph into that chapter.
    text = document['text']
    for heading in document.get('headings', []):
        start = heading['offset']; index = offsets.index(start)
        end = offsets[index+1] if index+1 < len(offsets) else len(text)
        matches = list(re.finditer(re.escape(heading['title']), text[start:end]))
        if len(matches) == 1:
            heading['offset'] = start+matches[0].start()
