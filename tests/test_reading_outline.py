import io
import pytest
from docx import Document
from audio_intel.reading_outline import build
from audio_intel import transcript_export, transcript_docx, transcript_html

def fixture():
    result={'segments':[{'id':n,'text':'Topic source text.','start':n*10,'end':n*10+10,'speaker':'a'} for n in range(3)],'video':{'scenes':[{'id':str(n),'start':n*10,'end':n*10+10,'image':None,'title_summary':{'state':'ready','title':f'Scene {n}'}} for n in range(3)]}}
    catalogue={'topics':[{'id':key,'title':title,'summary':'Discuss source text.','ranges':[{'start':i*10,'end':i*10+10,'source_refs':[{'segment_id':i,'start_offset':0,'end_offset':18}]} for i in ids]} for key,title,ids in [('a','First topic',[0,2]),('b','Second topic',[1])]]}
    return result,catalogue

def test_outline_recurring_topics_and_source_ownership():
    result,catalogue=fixture();outline=build(result,catalogue)
    assert outline['groups'][0]['primary_item_ids']==['0','2']
    assert outline['groups'][1]['primary_item_ids']==['1']
    assert all(i['assignment_method']=='source' for i in outline['items'])
    catalogue['topics'][1]['ranges'].append(catalogue['topics'][0]['ranges'][0])
    shared=build(result,catalogue)['items'][0]
    assert shared['primary_topic_id']=='a' and shared['related_topic_ids']==['b']

def test_hierarchy_documents_have_real_structure_and_links(tmp_path):
    result,catalogue=fixture();result['reading_outline']=build(result,catalogue)
    safe=transcript_export.export_result(result,transcript_export.TranscriptExportRequest(replacements=[{'source':'First','target':'Public'}]))
    assert safe['reading_outline']['groups'][0]['title']=='Public topic'
    doc=Document(io.BytesIO(transcript_docx.render('Test',safe,tmp_path)))
    headings=[p.text for p in doc.paragraphs if p.style.name=='Heading 1']
    assert headings.index('Public topic')<headings.index('Second topic')
    assert doc._element.xpath('.//w:hyperlink') and doc._element.xpath('.//w:bookmarkStart')
    pdf = transcript_html.render('Test',safe,tmp_path).decode()
    assert 'href="#section-0"' in pdf and 'id="section-0"' in pdf

def test_organization_contract():
    from pydantic import ValidationError
    assert transcript_export.TranscriptExportRequest(format='docx',organization='topic',optimization_id='a'*32).text_version=='original'
    for data in ({'organization':'topic'},{'organization':'topic','format':'srt','optimization_id':'a'*32}):
        with pytest.raises(ValidationError):transcript_export.TranscriptExportRequest(**data)

def test_merged_audio_is_grouped_once_with_valid_related_links(tmp_path):
    from audio_intel.reading_outline import sections
    result, catalogue = fixture()
    result.pop('video')
    catalogue['topics'][1]['ranges'].append(catalogue['topics'][0]['ranges'][0])
    result['reading_outline'] = build(result, catalogue)
    # Export ordinals differ from original IDs; merged original segments stay intact.
    result['segments'] = [dict(result['segments'][0], id=99, text='Merged text.', segment_ids=[0, 2], end=30),
                          dict(result['segments'][1], id=100, segment_ids=[1])]
    safe = transcript_export.export_result(result, transcript_export.TranscriptExportRequest())
    assert safe['segments'][0]['segment_ids'] == [0, 2]
    rows = sections(safe)
    assert [p['id'] for row in rows for p in row['paragraphs']] == [99, 100]
    anchors = {row['id'] for row in rows}
    assert all(ref in anchors for row in rows for ref in row.get('related_item_ids', []))
    doc = Document(io.BytesIO(transcript_docx.render('Audio', safe, tmp_path)))
    assert len([p for p in doc.paragraphs if p.style.name == 'Heading 2']) == 2
    bookmarks = {n.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}name') for n in doc._element.xpath('.//w:bookmarkStart')}
    assert all(link.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}anchor') in bookmarks for link in doc._element.xpath('.//w:hyperlink'))
    assert b'href="#section-' in transcript_html.render('Audio', safe, tmp_path)


def test_legacy_contents_match_body_links_and_page_numbers(tmp_path):
    from docx.oxml.ns import qn

    result, catalogue = fixture()
    for scene in result['video']['scenes']:
        scene.pop('title_summary')
    catalogue['topics'][1]['ranges'].append(catalogue['topics'][0]['ranges'][0])
    result['reading_outline'] = build(result, catalogue)
    result.update(_optimized=True, optimization_policy=6)
    result['segments'][0]['scene_ids'] = ['0', '2']
    expected = ['First topic', '片段 1', '片段 3', 'Second topic', '片段 2']
    doc = Document(io.BytesIO(transcript_docx.render('Test', result, tmp_path)))
    contents = [p for p in doc.paragraphs if p.style.name in ('TOC 1', 'TOC 2')]
    assert [p.text.strip() for p in contents] == expected
    assert [p.text for p in doc.paragraphs if p.style.name.startswith('Heading ')] == expected
    anchors = {node.get(qn('w:name')) for node in doc._element.xpath('.//w:bookmarkStart')}
    assert all(link.get(qn('w:anchor')) in anchors for link in doc._element.xpath('.//w:hyperlink'))
    fields = doc._element.xpath('.//w:instrText/text()')
    assert {field.split()[1] for field in fields if field.strip().startswith('PAGEREF')} == anchors
    assert any(field.strip().startswith('TOC ') for field in fields)
    pdf = transcript_html.render('Test', result, tmp_path).decode()
    from lxml import html
    page = html.fromstring(pdf)
    assert page.xpath('//nav/a/text()') == expected
    for href in page.xpath('//nav/a/@href'):
        assert len(page.xpath('//*[@id=$target]', target=href[1:])) == 1


def test_multi_page_contents_with_long_titles(tmp_path):
    result, catalogue = fixture()
    for n in range(3, 65):
        result['segments'].append(dict(result['segments'][0], id=n, start=n*10, end=n*10+10))
        result['video']['scenes'].append(dict(result['video']['scenes'][0], id=str(n),
            start=n*10, end=n*10+10, title_summary={'title': f'{n} ' + '多行标题' * 24}))
    result['reading_outline'] = build(result, catalogue)
    pdf = transcript_html.render('Test', result, tmp_path).decode()
    assert pdf.count('class="level-3"') == 65
