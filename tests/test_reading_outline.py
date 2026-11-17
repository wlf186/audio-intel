import io
import pytest
from docx import Document
from pypdf import PdfReader
from audio_intel.reading_outline import build
from audio_intel import transcript_export, transcript_docx, transcript_pdf

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
    pdf=PdfReader(io.BytesIO(transcript_pdf.render('Test',safe,tmp_path)))
    assert pdf.outline and any(page.get('/Annots') for page in pdf.pages)

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
    assert PdfReader(io.BytesIO(transcript_pdf.render('Audio', safe, tmp_path))).outline
