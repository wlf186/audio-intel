from copy import deepcopy

import pytest

from audio_intel.optimization_sources import source_groups


def segment(identifier, speaker, text, start, end):
    return dict(id=identifier, speaker=speaker, speaker_label=speaker, text=text, start=start, end=end)


def test_mike_sources_merge_across_picture_boundary_without_mutation():
    result = {'segments': [segment(1, 'Mike', '那个那个只能就只能布一个模型，不止', 0, 4),
                           segment(2, 'Mike', '不止不止阿特拉斯', 4, 8)],
              'video': {'scenes': [dict(id='a', start=0, end=4), dict(id='b', start=4, end=8)]}}
    original = deepcopy(result)
    groups = source_groups(result)
    assert len(groups) == 1
    assert groups[0]['segment_ids'] == [1, 2]
    assert groups[0]['scene_ids'] == ['a', 'b']
    assert groups[0]['merged_source_count'] == 2
    assert groups[0]['original_text'] == ''.join(s['text'] for s in result['segments'])
    assert result == original
    for source, value in zip(groups[0]['sources'], result['segments']):
        assert value['text'][source['start_offset']:source['end_offset']] == source['text']


def test_another_speakers_brief_agreement_is_a_hard_boundary():
    groups = source_groups({'segments': [segment(1, 'Mike', '只能部署', 0, 1),
                                        segment(2, 'Jane', '嗯', 1, 2),
                                        segment(3, 'Mike', '一个模型。', 2, 3)]})
    assert [g['segment_ids'] for g in groups] == [[1], [2], [3]]


@pytest.mark.parametrize('gap,count', [(14.9, 1), (15, 1), (15.1, 2)])
def test_pause_boundary(gap, count):
    groups = source_groups({'segments': [segment(1, 'Mike', '前一句。', 0, 1),
                                        segment(2, 'Mike', '后一句。', 1 + gap, 2 + gap)]})
    assert len(groups) == count


def test_names_do_not_override_speaker_identity():
    segments = [segment(1, 'a', '前一句。', 0, 1), segment(2, 'b', '后一句。', 1, 2)]
    for value in segments:
        value['speaker_label'] = 'Mike'
    assert len(source_groups({'segments': segments})) == 2


def test_long_turn_is_not_cut_at_request_character_limit():
    text = '完整的长句' * 300
    groups = source_groups({'segments': [segment(1, 'Mike', text, 0, 30)]})
    assert groups[0]['original_text'] == text
    assert groups[0]['sources'][0]['end_offset'] == len(text)
