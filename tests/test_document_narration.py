from __future__ import annotations

import hashlib
import json

import pytest

from audio_intel.document_narration import (NarrationOptions, annotation, chunks_for,
    finish_analysis, options_for, preview, skipped_ranges, spoken_slice)
from audio_intel.tts_text import split_text


def document(text, **extra):
    value = {'text': text, 'text_hash': hashlib.sha256(text.encode()).hexdigest(), 'version': 3,
             'headings': [], **extra}
    finish_analysis(value)
    return value


def test_numeric_citations_need_evidence_and_bibliography_stays_spoken():
    text = '收入 2026 元，增长 85%，数组 a[1]，区间 [11]。正文。[11]\n\n[11] Author. Book.\n[12] Another book.'
    d = document(text)
    ranges = skipped_ranges(d, NarrationOptions())
    assert [text[a:b] for a,b in ranges] == ['[11]']
    clean = spoken_slice(text, 0, len(text), ranges, reflow=False)
    assert 'a[1]' in clean and '区间 [11]' in clean and '[11] Author' in clean
    assert '2026' in clean and '85%' in clean


def test_explicit_note_and_unicode_override():
    text = '🙂正文[^a]。\n\n[^a]: 注释 85 元。\n\n尾声。'
    d = document(text)
    assert {a['kind'] for a in d['annotations']} == {'note_reference', 'note_body'}
    marker = next(a for a in d['annotations'] if a['kind'] == 'note_reference')
    options = NarrationOptions(analysis_revision=d['analysis_revision'], overrides=[{**{k:marker[k] for k in ('start','end')}, 'action':'speak'}])
    assert '[^a]' in spoken_slice(text,0,len(text),skipped_ranges(d,options),reflow=False)
    assert '注释' not in spoken_slice(text,0,len(text),skipped_ranges(d,options),reflow=False)
    with pytest.raises(ValueError):
        NarrationOptions(overrides=[{'start':1,'end':3,'action':'skip'}])
    with pytest.raises(ValueError):
        NarrationOptions(analysis_revision='x',overrides=[{'start':1,'end':3,'action':'skip'},{'start':2,'end':4,'action':'speak'}])
    with pytest.raises(LookupError):
        options_for(d,NarrationOptions(analysis_revision='stale'))
    with pytest.raises(ValueError):
        options_for(d,NarrationOptions(analysis_revision=d['analysis_revision'],overrides=[{'start':0,'end':len(text)+1,'action':'skip'}]))


def test_pauses_and_cross_page_continuation():
    text = '第一段。\n\n我认为，最\n85\n\n聪明的人可以讲清楚。\n\n第二章\n正文。'
    page = text.index('85');chapter=text.index('第二章')
    d=document(text, source_format='pdf', annotations=[annotation(page,page+2,'page_number','test')],
               boundaries=[{'offset':text.index('我认为'),'kind':'paragraph'}],
               headings=[{'offset':chapter,'title':'第二章','level':1,'basis':'heading'}])
    chunks=chunks_for(d,{'start':0,'end':len(text)},NarrationOptions(),split_text)
    assert [c['pause_before_ms'] for c in chunks] == [0,350,700,350]
    assert '最聪明的人' in chunks[1]['text'] and '85' not in chunks[1]['text']
    off=chunks_for(d,{'start':0,'end':len(text)},NarrationOptions(natural_pauses=False),split_text)
    assert len(off)==1


def test_preview_ids_stable_options_revision_changes_and_empty_sections():
    text='[^x]: only a note'
    d=document(text)
    first=preview(d,'auto',1000,2000)
    second=preview(d,'auto',1000,2000,NarrationOptions(skip_enabled=False))
    assert first['sections'][0]['id']==second['sections'][0]['id']
    assert first['preview_revision']!=second['preview_revision']
    assert not first['sections'][0]['speakable'] and second['sections'][0]['speakable']
    old={k:v for k,v in d.items() if k not in {'narration_version','analysis_revision'}}
    assert preview(old,'auto',1000,2000)['analysis_required']
    with pytest.raises(ValueError):options_for(old,NarrationOptions())


def test_skipped_ranges_union_and_override_precedence():
    d=document('0123456789',annotations=[annotation(1,7,'note_body','test'),annotation(2,4,'note_reference','test')])
    o=NarrationOptions(analysis_revision=d['analysis_revision'],overrides=[{'start':3,'end':5,'action':'speak'},{'start':8,'end':9,'action':'skip'}])
    assert skipped_ranges(d,o)==[(1,3),(5,7),(8,9)]
    assert spoken_slice(d['text'],0,10,skipped_ranges(d,o),reflow=False)=='03479'


def test_markdown_footnotes_remain_in_preview(tmp_path):
    from audio_intel.document_text import extract
    path=tmp_path/'notes.md'
    path.write_text('正文[^a]。\n\n[^a]: 保留可见的脚注\n    第二行。\n\n结尾。',encoding='utf-8')
    d=extract(path,10000,10000)
    assert '保留可见的脚注 第二行。' in d['text']
    assert {a['kind'] for a in d['annotations']} == {'note_body','note_reference'}
    spoken=spoken_slice(d['text'],0,len(d['text']),skipped_ranges(d,NarrationOptions()),reflow=False)
    assert '正文' in spoken and '结尾' in spoken and '脚注' not in spoken


def test_epub_html_semantic_notes():
    from audio_intel.document_text import HtmlText
    parser=HtmlText()
    parser.feed('<p>Text<a epub:type="noteref" href="#n">1</a>.</p><aside epub:type="footnote" id="n"><p>A note.</p></aside>')
    d=document(''.join(parser.parts),annotations=parser.annotations)
    assert [a['kind'] for a in d['annotations']]==['note_reference','note_body']
    assert spoken_slice(d['text'],0,len(d['text']),skipped_ranges(d,NarrationOptions()),reflow=False)=='Text.'


def test_pdf_layout_footer_evidence_does_not_drop_body_numbers():
    from audio_intel.document_pdf import analyze
    pages=[];parts=[];offsets=[];length=0
    for number in range(51,55):
        text=f'正文 85 元。\n{number}\n\n';offsets.append(length);length+=len(text);parts.append(text)
        pages.append([{'start':0,'end':len('正文 85 元。'),'text':'正文 85 元。','x':40,'y':400,'size':12,'height':600,'bottom':0},
                      {'start':len('正文 85 元。\n'),'end':len(text)-2,'text':str(number),'x':300,'y':25,'size':9,'height':600,'bottom':0}])
    d={'text':''.join(parts),'headings':[],'page_offsets':offsets}
    analyze(d,pages)
    assert len(d['annotations'])==4
    clean=spoken_slice(d['text'],0,len(d['text']),skipped_ranges(d,NarrationOptions()),reflow=True)
    assert clean.count('85')==4 and '51' not in clean


def test_docx_native_footnote_is_retained_and_annotated(tmp_path):
    import zipfile
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from audio_intel.document_text import extract
    path=tmp_path/'notes.docx'
    doc=Document();p=doc.add_paragraph('正文价格 85 元')
    ref=OxmlElement('w:footnoteReference');ref.set(qn('w:id'),'1');p.add_run()._r.append(ref)
    doc.add_paragraph('尾声。');doc.save(path)
    with zipfile.ZipFile(path,'a') as archive:
        archive.writestr('word/footnotes.xml','<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:footnote w:id="1"><w:p><w:r><w:t>脚注文字，不朗读。</w:t></w:r></w:p></w:footnote></w:footnotes>')
    d=extract(path,10000,1000000)
    assert '脚注文字' in d['text'] and len(d['annotations'])==2
    clean=spoken_slice(d['text'],0,len(d['text']),skipped_ranges(d,NarrationOptions()),reflow=False)
    assert '脚注文字' not in clean and '正文价格 85 元' in clean and '尾声' in clean


def test_pdf_note_does_not_break_a_cross_page_sentence():
    from audio_intel.document_pdf import analyze
    first='开头。\n继续说明。\n我认为，最[1]\n[1] 注释文字。\n\n'
    second='聪明的人可以讲清楚。\n\n'
    def rows(text, specs):
        return [{'text':value,'start':text.index(value),'end':text.index(value)+len(value),
                 'x':40,'y':y,'size':size,'height':600,'bottom':0} for value,y,size in specs]
    pages=[rows(first,[('开头。',500,12),('继续说明。',480,12),('我认为，最[1]',460,12),('[1] 注释文字。',40,7)]),
           rows(second,[('聪明的人可以讲清楚。',500,12)])]
    d={'text':first+second,'headings':[],'page_offsets':[0,len(first)]}
    analyze(d,pages)
    assert {a['kind'] for a in d['annotations']}=={'note_reference','note_body'}
    d=document(d['text'],headings=d['headings'],annotations=d['annotations'],boundaries=d['boundaries'],source_format='pdf')
    chunks=chunks_for(d,{'start':0,'end':len(d['text'])},NarrationOptions(),split_text)
    assert any('最聪明的人' in c['text'] for c in chunks)
    assert '注释' not in ''.join(c['text'] for c in chunks)


def test_audiobook_highlights_skip_annotations_and_preserve_repeated_unicode():
    from audio_intel.audiobook import marked_text
    html = marked_text('😀甲[1] 甲', 10, ['😀甲', '甲'], [[0,24],[32,56]], 24,
                       {12,13,14}, [(12,15)])
    assert 'data-at="0.000000" data-end="1.000000">😀甲</span>' in html
    assert 'data-at="1.333333" data-end="2.333333">甲</span>' in html
    assert '<summary>原书注释</summary>[1]</details>' in html
    assert 'data-at' not in marked_text('甲',0,['错'],[[0,24]],24,set())
