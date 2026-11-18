"""Source-backed blocks split at topics before prose; display never changes evidence."""
from collections import defaultdict

from audio_intel.reading_plan import units, outline
from audio_intel.reading_outline import sections


def recording():
    return {'duration': 12, 'segments': [{'id': 0, 'start': 0, 'end': 12, 'speaker':'s0',
            'text': '讨论设计。随后测试。回到设计。', 'words': []}],
            'video': {'scenes': [{'id': 's1', 'start':0, 'end':12, 'image':'1.jpg'}]}}


def catalogue():
    def ref(a, b):
        return {'start':0, 'end':12, 'source_refs':[{'segment_id':0, 'start_offset':a, 'end_offset':b}]}
    return {'topics':[{'id':'a', 'title':'设计', 'summary':'设计讨论', 'ranges':[ref(0,5),ref(10,15)]},
                      {'id':'b', 'title':'测试', 'summary':'测试讨论', 'ranges':[ref(5,10)]}]}


def test_source_partition_occurrences_and_shared_frames():
    raw, topics = recording(), catalogue()
    plan = units(raw, topics)
    assert [u['reading_topic_id'] for u in plan] == ['a','b','a']
    assert len({u['occurrence_id'] for u in plan}) == 3
    coverage = defaultdict(list)
    for unit in plan:
        for span in unit['sources']:
            assert span['text'] == raw['segments'][0]['text'][span['start_offset']:span['end_offset']]
            coverage[span['segment_id']].extend(range(span['start_offset'], span['end_offset']))
    assert coverage[0] == list(range(len(raw['segments'][0]['text'])))
    projected = outline(raw, topics, plan, {})
    assert [g['topic_id'] for g in projected['groups']] == ['a','b','a']
    assert [g['recurring'] for g in projected['groups']] == [False,False,True]
    assert [i['frame_numbers'] for i in projected['items']] == [[1],[1],[1]]
    assert [g['topic_id'] for g in projected['merged_groups']] == ['a','b']
    assert units(raw, topics) == plan
    export = {**raw,'segments':[{'id':i,'reading_block_id':u['reading_block_id'],'text':u['original_text']} for i,u in enumerate(plan)],'reading_outline':projected}
    children = lambda: [s['id'] for s in sections(export) if 'topic_heading' not in s]
    assert children() == [u['reading_block_id'] for u in plan]
    projected['merge_repeated_topics'] = True
    assert children() == [plan[i]['reading_block_id'] for i in (0,2,1)]
    assert all(s.get('reading_block') for s in sections(export) if 'topic_heading' not in s)


def test_recurring_export_labels_follow_identity():
    from audio_intel.reading_outline import export_headings

    raw, topics = recording(), catalogue()
    raw.pop('video')
    topics['topics'][1]['title'] = topics['topics'][0]['title']
    plan = units(raw, topics)
    result = {**raw, 'reading_outline': outline(raw, topics, plan, {}),
              'segments': [{'reading_block_id': unit['reading_block_id']} for unit in plan]}
    for merge, expected in [(False, ['设计', '设计', '设计（续 1）']), (True, ['设计', '设计'])]:
        result['reading_outline']['merge_repeated_topics'] = merge
        rows = sections(result)
        titles = export_headings(result, rows)
        assert [titles[row['id']] for row in rows if 'topic_heading' in row] == expected
