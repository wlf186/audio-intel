"""Synthetic progress only: never use private media or transcript fixtures."""
import json
import sys

import pytest

from audio_intel import video_detection as detection


def snapshots(folder):
    return [json.loads(p.read_text(encoding='utf-8'))
            for p in sorted((folder / '.progress').glob('progress-*.json'))]


def test_boundaries_throttle_duplicates_and_flush(tmp_path, monkeypatch):
    clock = [0.]
    monkeypatch.setattr(detection.time, 'monotonic', lambda: clock[0])
    progress = detection.PreviewProgress(tmp_path)
    progress('scanning', 0., 10.)  # First publication even near monotonic zero.
    progress('scanning', .1, 10.)
    assert len(snapshots(tmp_path)) == 1
    progress('detecting', .2, 10.)
    progress('scanning', .3, 10.)  # Returning to a phase is also a boundary.
    progress('selecting', 1., 1.)
    progress('images', 1., 2.)
    progress('images', 2., 2.)  # Short stage completes within the throttle.
    progress('images', 2., 2.)
    assert len(snapshots(tmp_path)) == 6
    clock[0] = .3
    progress('scanning', 4., 10.)
    clock[0] = .6
    progress('scanning', 5., 10.)
    progress('scanning', 6., 10.)
    assert snapshots(tmp_path)[-1]['completed'] == 5.
    progress.flush()
    progress.flush()
    assert len(snapshots(tmp_path)) == 9
    assert snapshots(tmp_path)[-1]['completed'] == 6.


def test_failed_publication_retries_and_open_reader_is_immutable(tmp_path, monkeypatch):
    original = detection.publish_snapshot
    calls = []
    def publish(path, value):
        calls.append(path)
        if len(calls) == 1:
            raise OSError('synthetic failure')
        original(path, value)
    monkeypatch.setattr(detection, 'publish_snapshot', publish)
    progress = detection.PreviewProgress(tmp_path)
    progress('scanning', 0., 10.)
    assert progress.published is None
    progress('scanning', 0., 10.)
    with calls[-1].open('rb') as reader:
        before = reader.read()
        progress('selecting', .1, 10.)
        reader.seek(0)
        assert reader.read() == before
    assert [p.name for p in calls] == [f'progress-{i:08d}.json' for i in (1, 2, 3)]
    assert len(snapshots(tmp_path)) == 2


@pytest.mark.parametrize('failure', [None, RuntimeError, KeyboardInterrupt])
def test_main_flushes_observed_state_on_exit(tmp_path, monkeypatch, failure):
    (tmp_path / 'request.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(sys, 'argv', ['preview', str(tmp_path)])
    monkeypatch.setattr(detection.time, 'monotonic', lambda: 0.)
    def preview(request, folder, progress):
        progress('scanning', 0., 10.)
        progress('scanning', .2, 10.)
        if failure:
            raise failure('synthetic exit')
        return {'scenes': []}
    monkeypatch.setattr(detection, 'preview', preview)
    if failure:
        with pytest.raises(failure):
            detection.main()
        assert not (tmp_path / 'result.json').exists()
    else:
        detection.main()
    assert snapshots(tmp_path)[-1] == {'phase': 'scanning', 'completed': .2, 'total': 10.}


def test_fast_child_final_drain_after_exit(tmp_path, monkeypatch):
    import asyncio
    from dataclasses import replace
    from audio_intel import video_previews
    from audio_intel.config import settings

    config = replace(settings, temp_dir=tmp_path, data_dir=tmp_path / 'data')
    manager = video_previews.PreviewManager(config)
    item = {'folder': tmp_path, 'job_id': 'synthetic', 'state': 'running'}
    monkeypatch.setattr(video_previews, 'completed', lambda _: None)
    original = video_previews.subprocess.Popen
    # Publish only at child exit, after the parent's last while-loop drain.
    class Child:
        def __init__(self, process):
            self.process = process
            self.pid = process.pid
            self.returncode = None
        def poll(self):
            self.process.wait(timeout=10)
            progress = detection.PreviewProgress(tmp_path)
            progress('images', 1., 1.)
            (tmp_path / 'result.json').write_text('{"scenes":[],"cache_hit":false}', encoding='utf-8')
            self.returncode = 0
            return 0
        def wait(self):
            return self.process.wait()
    def spawn(command, **kwargs):
        return Child(original([sys.executable, '-c', 'import time; time.sleep(.1)'], **kwargs))
    monkeypatch.setattr(video_previews.subprocess, 'Popen', spawn)
    asyncio.run(manager._run(item))
    assert item['state'] == 'ready'
    assert item['progress'] == {'phase': 'images', 'completed': 1., 'total': 1.}
    assert not list((tmp_path / '.progress').glob('*.json'))
    assert not (tmp_path / 'process.json').exists()
