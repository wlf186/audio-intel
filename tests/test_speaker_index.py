import random

import pytest

from asr.pipeline import speaker_at
from asr.speaker_index import SpeakerIndex


@pytest.mark.parametrize('seed', range(12))
def test_index_matches_legacy_for_overlaps_gaps_ties_and_shuffled_intervals(seed):
    rng = random.Random(seed)
    intervals = []
    for _ in range(100):
        start = rng.randrange(-10, 100) / 3
        intervals.append(dict(start=start, end=start + rng.randrange(0, 15) / 3,
                              speaker=f'Speaker_{rng.randrange(5)}'))
    rng.shuffle(intervals)
    index = SpeakerIndex(intervals)
    queries = [(x['start'], x['start']) for x in intervals]
    queries += [(x['end'], x['end']) for x in intervals]
    for _ in range(300):
        start = rng.uniform(-20, 60)
        queries.append((start, start + rng.uniform(0, 15)))
    for start, end in queries:
        assert index.speaker_at(start, end) == speaker_at(start, end, intervals)


def test_index_empty_and_invalid_geometry_fallback():
    assert SpeakerIndex([]).speaker_at(0, 1) == 'Speaker_0'
    intervals = [dict(start=5, end=1, speaker='A'), dict(start=2, end=3, speaker='B')]
    for start, end in [(1, 2), (4, 2), (10, 11)]:
        assert SpeakerIndex(intervals).speaker_at(start, end) == speaker_at(start, end, intervals)


def test_index_nearest_endpoint_ties_follow_original_order():
    intervals = [dict(start=5, end=6, speaker='right'), dict(start=0, end=1, speaker='left')]
    assert SpeakerIndex(intervals).speaker_at(3, 3) == 'right'
