"""Lossless formatted alignment and picture-only presentation, without migrations."""
from copy import deepcopy
from pathlib import Path
import io
import json

import pytest
from test_video_transcription import client, local
from docx import Document
from pypdf import PdfReader

from audio_intel.transcript import scene_transcripts, presentation_sections
from audio_intel.optimization_engine import source_hash, prepare_units
from audio_intel.transcript_docx import render as docx
from audio_intel.transcript_pdf import render as pdf


def result_for(text, tokens):
    return {'segments': [{'id': 0, 'speaker': 's', 'speaker_label': 'Speaker',
            'start': 0, 'end': len(tokens), 'text': text,
            'words': [{'text': token, 'start': i, 'end': i + 1} for i, token in enumerate(tokens)]}],
            'video': {'scenes': [{'id': str(i), 'start': i, 'end': i + 1,
                                  'image': None, 'frame_time': None} for i in range(len(tokens))]}}


@pytest.mark.parametrize('text,tokens', [
    ('Fable 5.2 works.', ['Fable', '52', 'works']),
    ('A seven-day trial.', ['A', 'sevenday', 'trial']),
    ('At two a.m. today.', ['At', 'two', 'am', 'today']),
    ('Spend $5,000 today.', ['Spend', '5000', 'today']),
    ('中文，测试。', ['中', '文', '测', '试']),
])
def test_formatted_alignment_preserves_offsets_and_historical_policy(text, tokens):
    result = result_for(text, tokens)
    before = deepcopy(result)
    scenes = scene_transcripts(result, include_sources=True)
    spans = [source for scene in scenes for p in scene['paragraphs'] for source in p['sources']]
    assert ''.join(source['text'] for source in spans) == text
    assert all(source['text'] == text[source['start_offset']:source['end_offset']] for source in spans)
    assert all(a['end_offset'] == b['start_offset'] for a, b in zip(spans, spans[1:]))
    assert all(scene['paragraphs'] for scene in scenes)
    assert result == before


def test_real_content_difference_still_falls_back_and_policies_are_frozen():
    result = result_for('Version 5.3 works.', ['Version', '52', 'works'])
    assert sum(bool(s['paragraphs']) for s in scene_transcripts(result)) == 1
    result = result_for('Version 5.2 works.', ['Version', '52', 'works'])
    old = source_hash(result, source_assignment_version=1)
    assert old != source_hash(result, source_assignment_version=2)
    assert len(prepare_units(result, source_assignment_version=1)) == 1
    assert len(prepare_units(result, source_assignment_version=2)) == 3
    assert source_hash(result, source_assignment_version=1) == old


def test_shared_presentation_contract():
    fixture = json.loads((Path(__file__).parent / 'fixtures/video_presentation.json').read_text(encoding='utf-8'))
    assert [(s['id'], s['frame_number']) for s in presentation_sections(fixture['scenes'])] == [tuple(x) for x in fixture['expected']]


def test_exports_only_pictures_have_headings_and_text_stays_independent(tmp_path):
    from PIL import Image
    frames = tmp_path / 'frames';frames.mkdir()
    Image.new('RGB', (320, 180), 'white').save(frames / 'a.jpg')
    result = result_for('First.Second.Third.', ['First', 'Second', 'Third'])
    scenes = result['video']['scenes']
    scenes[0].update(image='a.jpg', frame_time=0)
    scenes[2].update(image='a.jpg', frame_time=2)
    scenes.insert(2, {'id': 'empty', 'start': 1.8, 'end': 1.9, 'image': None, 'frame_time': None})
    before = deepcopy(result)
    word = Document(io.BytesIO(docx('Reading', result, tmp_path)))
    headings = [p.text for p in word.paragraphs if p.style.name == 'Heading 1']
    assert headings == ['画面 1', '画面 2']
    text = '\n'.join(p.text for p in word.paragraphs)
    assert 'Second.' in text and '无稳定画面' not in text and '无识别语音' not in text
    pdf_text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(pdf('Reading', result, tmp_path))).pages)
    assert 'Second.' in pdf_text and 'No stable picture' not in pdf_text
    assert result == before


def test_preview_decodes_without_writing_job_files(client, local):
    from test_video_detection import completed_video
    from audio_intel import db
    job = completed_video(client, local)
    folder = local.jobs_dir / job['id']
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    response = client.get(f"/api/v1/jobs/{job['id']}/video-preview", params={'time': 1})
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.headers['cache-control'] == 'no-store'
    assert before == {p.relative_to(folder): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    assert db.get_job(job['id'])['result'] == job['result']

