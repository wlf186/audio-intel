from audio_intel.optimization_sources import scene_reading_groups


def test_scene_sources_follow_original_assignment_and_keep_offsets():
    result = {'segments': [{'id': 1, 'speaker': 's0', 'text': '你好世界', 'start': 0, 'end': 4,
                            'words': [{'text': '你好', 'start': 0, 'end': 2},
                                      {'text': '世界', 'start': 2, 'end': 4}]}],
              'video': {'scenes': [{'id': 'a', 'start': 0, 'end': 2},
                                   {'id': 'b', 'start': 2, 'end': 4}]}}
    groups = scene_reading_groups(result)
    assert [g['scene_ids'] for g in groups] == [['a'], ['b']]
    assert [g['original_text'] for g in groups] == ['你好', '世界']
    assert [(g['sources'][0]['start_offset'], g['sources'][0]['end_offset']) for g in groups] == [(0, 2), (2, 4)]


def test_unaligned_long_source_is_owned_once_and_partitioned_losslessly():
    text = '这是一段没有可靠字词时间的长发言。' * 100
    result = {'segments': [{'id': 1, 'speaker': 's0', 'text': text, 'start': 0, 'end': 30}],
              'video': {'scenes': [{'id': 'a', 'start': 0, 'end': 10},
                                   {'id': 'b', 'start': 10, 'end': 30}]}}
    groups = scene_reading_groups(result)
    assert all(g['scene_ids'] == ['b'] for g in groups)
    assert ''.join(g['original_text'] for g in groups) == text
    assert all(len(g['original_text']) <= 400 for g in groups)
    spans = [s for g in groups for s in g['sources']]
    assert spans[0]['start_offset'] == 0 and spans[-1]['end_offset'] == len(text)
    assert all(a['end_offset'] == b['start_offset'] for a, b in zip(spans, spans[1:]))


def test_groups_respect_speaker_and_pause_within_a_scene():
    result = {'segments': [{'id': i, 'speaker': speaker, 'text': '发言', 'start': start, 'end': start + 1}
                            for i, (speaker, start) in enumerate([('a', 0), ('a', 2), ('a', 30), ('b', 32)])],
              'video': {'scenes': [{'id': 'a', 'start': 0, 'end': 40}]}}
    groups = scene_reading_groups(result)
    assert [g['segment_ids'] for g in groups] == [[0, 1], [2], [3]]


def test_engine_uses_scene_scoped_sources_and_fingerprint():
    from copy import deepcopy
    from audio_intel.optimization_engine import prepare_units, source_hash
    result = {'segments': [{'id': 1, 'speaker': 's0', 'speaker_label': 'Mike', 'text': '第一句', 'start': 0, 'end': 4},
                           {'id': 2, 'speaker': 's0', 'text': '第二句', 'start': 4, 'end': 8}],
              'video': {'scenes': [{'id': 'a', 'start': 0, 'end': 4}, {'id': 'b', 'start': 4, 'end': 8}]}}
    assert [g['segment_ids'] for g in prepare_units(result)] == [[1], [2]]
    original = source_hash(result)
    changed = deepcopy(result)
    changed['segments'][0]['speaker_label'] = 'New name'
    changed['video']['scenes'][0]['image'] = 'replacement.jpg'
    changed['video']['export_hidden_scene_ids'] = ['b']
    assert source_hash(changed) == original
    changed['video']['scenes'][0]['end'] = 3
    assert source_hash(changed) != original
    changed = deepcopy(result)
    changed['video']['scene_detection'] = {'min_stable_seconds': 5}
    assert source_hash(changed) != original
