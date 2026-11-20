"""Generate a repeatable lecture fixture or compare the two local picture detectors."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fixture(output, hours, audio):
    width, height, fps, cycle = 960, 540, 4, 180
    clip = output.with_suffix('.cycle.mp4')
    cmd = ['ffmpeg', '-loglevel', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
           '-s', f'{width}x{height}', '-r', str(fps), '-i', 'pipe:0',
           '-i', str(audio), '-af', 'apad', '-t', str(cycle), '-c:a', 'aac',
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-threads', '2', str(clip)]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as encoder:
        for i in range(fps * cycle):
            sec = i / fps
            im = Image.new('RGB', (width, height), '#f7f8fa')
            draw = ImageDraw.Draw(im)
            if sec < 90:
                draw.text((45, 45), 'Experiment results', fill='#263a59', font_size=40)
                draw.text((50, 150), 'Error rate   ' + ('12%' if sec < 30 else '18%'), fill='black', font_size=20)
                if sec >= 60:
                    draw.text((50, 240), 'Calibration completed', fill='black', font_size=24)
            else:
                offset = min(430, max(0, int((sec - 120) * 32)))
                for n in range(25):
                    draw.text((40, 60 + n * 44-offset), f'Section {n+1}   Experiment notes and observations', fill='#153953', font_size=22)
            draw.ellipse((810+i%65, 380, 814+i%65, 384), fill='red')
            encoder.stdin.write(im.tobytes())
        encoder.stdin.close()
        if encoder.wait():
            raise RuntimeError('Fixture encoding failed')
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-n', '-stream_loop', '-1', '-i', str(clip),
                    '-t', str(hours*3600), '-c', 'copy', str(output)], check=True)


def run(source, output):
    from audio_intel.video_detection import preview
    from audio_intel.video_frames import probe
    import av
    output.mkdir(parents=True, exist_ok=False)
    with av.open(str(source)) as container:
        duration = container.duration / av.time_base
        origin = (container.start_time or 0) / av.time_base
    with source.open('rb') as media:
        report = dict(duration=duration, source_sha256=hashlib.file_digest(media, 'sha256').hexdigest(),
                      dimensions=probe(source), runs=[])
    process = psutil.Process()
    for mode in ('custom', 'auto'):
        settings = dict(min_change_percent=3, min_stable_seconds=1) if mode == 'custom' else None
        request = dict(source=str(source), cache=str(output/'features'), roi=None, min_free=0,
                       settings=settings, mode=mode, duration=duration, time_origin=origin,
                       existing_frames=str(output/'none'))
        if mode == 'auto':
            request['video_scene_detail'] = 'balanced'
        centers = (0., max(0., duration/2-90), max(0., duration-180))
        cases = [('cold-full', None), *[(label, [t, min(t+180, duration)])
                 for label,t in zip(('start','middle','end'), centers)]]
        if mode == 'custom':
            cases += [('changed-local', cases[2][1]), ('changed-full', None)]
        for label, interval in cases:
            if label.startswith('changed'):
                request.pop('snapshot', None)
                request['settings'] = dict(min_change_percent=1, min_stable_seconds=1)
            folder = output/(mode+'-'+label)
            folder.mkdir()
            def io_writes():
                counters = process.io_counters()
                return counters.write_chars if hasattr(counters, 'write_chars') else counters.write_bytes
            before = io_writes()
            started = time.monotonic()
            result = preview({**request,'time_range':interval}, folder, lambda *a:None)
            scenes = result['scenes']
            report['runs'].append(dict(mode=mode, stage=label, seconds=time.monotonic()-started,
                                       sections=len(scenes), pictures=sum(bool(s['image']) for s in scenes),
                                       process_write_bytes=io_writes()-before,
                                       cache_hit=result['cache_hit'], cache_source=result.get('cache_source')))
            if label == 'cold-full':
                request.update(snapshot=scenes, existing_scenes=scenes, existing_frames=str(folder/'frames'))
            (output/'report.json').write_text(json.dumps(report, indent=2), encoding='utf8')


def server_writes(run_directory):
    """Linux /proc I/O of the live service trees, including waited-for children."""
    if not sys.platform.startswith('linux') or not run_directory:
        return None
    total = 0
    for path in run_directory.glob('*.pid'):
        proc = psutil.Process(int(path.read_text()))
        for child in [proc, *proc.children(recursive=True)]:
            try:
                total += child.io_counters().write_bytes
            except psutil.NoSuchProcess:
                pass
    return total


def service(source, output, url, modes, run_directory, work_directory):
    import httpx
    import tempfile
    from datetime import datetime

    output.mkdir(parents=True, exist_ok=False)
    report = []
    with tempfile.TemporaryDirectory(dir=output) as work:
        for mode in modes:
            before = server_writes(run_directory)
            peak = 0
            started = time.monotonic()
            response = subprocess.check_output(
                [sys.executable, str(ROOT/'scripts/submit_video.py'), str(source), '--state',
                 str(Path(work)/'state.json'), '--scene-mode', mode, '--device', 'gpu'],
                env={**os.environ, 'AUDIO_INTEL_BASE_URL':url}, text=True)
            submitted = time.monotonic()
            identifier = next(line[5:].strip() for line in response.splitlines() if line.startswith('Job: '))
            key = os.environ.get('AUDIO_INTEL_API_KEY')
            with httpx.Client(base_url=url, headers={'Authorization':'Bearer '+key} if key else {}) as client:
                while True:
                    reply = client.get('/api/v1/jobs/'+identifier)
                    reply.raise_for_status()
                    job = reply.json()
                    if work_directory:
                        size = 0
                        for path in work_directory.rglob('*'):
                            try:
                                if path.is_file():
                                    size += path.stat().st_size
                            except FileNotFoundError:
                                pass
                        peak = max(peak, size)
                    if job['state'] in {'succeeded', 'failed', 'cancelled'}:
                        break
                    time.sleep(3)
            report.append(dict(mode=mode, job_id=identifier, state=job['state'], error=job['error_message'],
                               upload_submit_seconds=submitted-started,
                               result_seconds=(datetime.fromisoformat(job['finished_at'])-
                                               datetime.fromisoformat(job['created_at'])).total_seconds(),
                               processing_seconds=job['processing_seconds'], upload_bytes=source.stat().st_size,
                               sampled_peak_work_bytes=peak if work_directory else None,
                               linux_storage_write_bytes=server_writes(run_directory)-before if before is not None else None))
            (output/'service.json').write_text(json.dumps(report, indent=2), encoding='utf8')
            if job['state'] != 'succeeded':
                raise RuntimeError('Benchmark transcription failed; inspect service.json')


def previews(url, identifier, output, pid_file, work_dir):
    """Time POST through first readable picture, then discard the preview."""
    import httpx
    import uuid
    output.mkdir(parents=True, exist_ok=False)
    root = '/api/v1/jobs/' + identifier
    key = os.environ.get('AUDIO_INTEL_API_KEY')
    report = []
    proc = psutil.Process(int(pid_file.read_text())) if pid_file else None

    def io_writes():
        if proc is None:
            return 0
        counters = proc.io_counters()
        return counters.write_chars if hasattr(counters, 'write_chars') else counters.write_bytes

    with httpx.Client(base_url=url, headers={'Authorization':'Bearer '+key} if key else {}, timeout=60) as client:
        reply = client.get(root)
        reply.raise_for_status()
        video = reply.json()['result']['video']
        duration = video['duration']
        auto = video['detector'] in ('presentation-v4','presentation-v5','presentation-v6','presentation-v7')
        for label, center in [('start',0.), ('middle',duration/2), ('end',duration), ('whole',None)]:
            settings = {'mode':'auto'} if auto else {'mode':'custom','settings':{'min_change_percent':1}}
            body = dict(revision=video['revision'], **settings)
            if center is not None:
                start = max(0., min(center-90, duration-180))
                body['time_range'] = [start, min(duration, start+180)]
            before = io_writes()
            storage_before = proc.io_counters().write_bytes if proc and sys.platform.startswith('linux') else None
            peak_private = 0
            started = time.monotonic()
            response = client.post(root+'/video-scene-previews',json=body,headers={'Idempotency-Key':uuid.uuid4().hex})
            response.raise_for_status()
            path = root+'/video-scene-previews/'+response.json()['id']
            try:
                while True:
                    response = client.get(path)
                    response.raise_for_status()
                    value = response.json()
                    if work_dir:
                        folder = work_dir/identifier/'scene-previews'/value['id']
                        size = 0
                        for candidate in folder.rglob('*'):
                            try:
                                if candidate.is_file():
                                    stat = candidate.stat()
                                    if stat.st_nlink == 1:
                                        size += stat.st_size
                            except FileNotFoundError:
                                pass
                        peak_private = max(peak_private, size)
                    if value['state'] != 'running':
                        break
                    time.sleep(.25)
                if value['state'] != 'ready':
                    raise RuntimeError(value.get('error'))
                first = next((s['preview_url'] for s in value['scenes'] if s['image']),None)
                if first:
                    client.get(first).raise_for_status()
                elapsed = time.monotonic()-started
            finally:
                client.delete(path).raise_for_status()
            report.append(dict(stage=label,seconds=elapsed,sections=len(value['scenes']),
                               cache_source=value.get('cache_source'),
                               sampled_peak_private_bytes=peak_private if work_dir else None,
                               linux_storage_write_bytes=proc.io_counters().write_bytes-storage_before if storage_before is not None else None,
                               server_write_counter_delta=io_writes()-before if proc else None))
            (output/'previews.json').write_text(json.dumps(report, indent=2),encoding='utf8')


def quality(source, labels, output):
    """Score observed frames against manually annotated, nonoverlapping states."""
    from audio_intel.video_auto import AutoDetector
    from audio_intel.video_detection import StableDetector, source_samples
    truth = json.loads(labels.read_text(encoding='utf8'))
    start, end = truth['range']
    states = truth['states']
    previous = start
    for state in states:
        lo, hi = state['interval']
        if not previous <= lo < hi <= end:
            raise ValueError('State intervals must be ordered and contained in the evaluation range')
        previous = hi
    output.mkdir(parents=True, exist_ok=False)
    report = []
    for name, threshold in [('custom', 3), ('sensitive', .5), ('auto', None)]:
        detector = AutoDetector(start) if threshold is None else StableDetector({'min_change_percent':threshold})
        detector.start = start
        for timestamp, rgb in source_samples(source, None, start, end, threshold is None):
            detector.feed(rgb, timestamp)
        detector.flush(end)
        frames = [s['frame_time'] for s in detector.scenes if s['frame_time'] is not None]
        hits = [[t for t in frames if lo <= t < hi] for lo,hi in (state['interval'] for state in states)]
        duplicates = sum(max(0,len(hit)-1) for hit in hits)
        report.append(dict(mode=name,hits=sum(bool(hit) for hit in hits),expected=len(states),
                           extra_in_same_state=duplicates,duplicates_per_hour=duplicates*3600/(end-start),
                           outside_labels=len(frames)-sum(map(len,hits)),missing_images=len(detector.scenes)-len(frames),
                           matches=hits,frames=frames))
    (output/'quality.json').write_text(json.dumps(report, indent=2),encoding='utf8')


def details(source, output):
    """Measure the cold selector and cross-level index reuse on a real short video."""
    import av
    from audio_intel.video_detection import preview

    output.mkdir(parents=True, exist_ok=False)
    with av.open(str(source)) as video:
        duration = video.duration / av.time_base
        origin = (video.start_time or 0) / av.time_base
    interval = [min(60., duration/4), min(duration, min(60., duration/4)+180)]
    request = dict(source=str(source), cache=str(output/'scene-features'), roi=None,
                   min_free=0, settings=None, mode='auto', duration=duration,
                   time_origin=origin, existing_frames=str(output/'none'))
    reports = []
    proc = psutil.Process()
    for label, level, scope in [('cold-range','balanced',interval), ('warm-range','detailed',interval),
                               ('cold-full','balanced',None), ('warm-full','concise',None),
                               ('warm-detailed','detailed',None)]:
        folder = output/label
        folder.mkdir()
        before = proc.io_counters().write_bytes
        started = time.monotonic()
        result = preview(dict(request, video_scene_detail=level, time_range=scope), folder, lambda *args: None)
        elapsed = time.monotonic()-started
        size = sum(f.stat().st_size for f in folder.rglob('*') if f.is_file() and f.stat().st_nlink == 1)
        reports.append(dict(stage=label, detail=level, sections=len(result['scenes']), seconds=elapsed,
                            cache_source=result['cache_source'], write_bytes=proc.io_counters().write_bytes-before,
                            private_image_bytes=size, index_bytes=sum(p.stat().st_size for p in output.glob('*.bin'))))
        if label=='cold-full':
            # Subsequent previews can reuse these pictures once the initial result is applied.
            request.update(existing_frames=str(folder/'frames'), existing_scenes=result['scenes'])
        (folder/'scenes.json').write_text(json.dumps(result['scenes']),encoding='utf8')
        (output/'details.json').write_text(json.dumps(reports,indent=2),encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('fixture')
    create.add_argument('output', type=Path)
    create.add_argument('--audio', type=Path, required=True)
    create.add_argument('--hours', type=float, default=6)
    bench = sub.add_parser('run')
    bench.add_argument('source', type=Path)
    bench.add_argument('output', type=Path)
    levels = sub.add_parser('details', help='Benchmark automatic detail selection on a local video')
    levels.add_argument('source', type=Path)
    levels.add_argument('output', type=Path)
    jobs = sub.add_parser('service', help='Submit real benchmark tasks to the specified isolated test service')
    jobs.add_argument('source', type=Path)
    jobs.add_argument('output', type=Path)
    jobs.add_argument('--url', required=True)
    jobs.add_argument('--run-dir', type=Path, help='Isolated Linux service PID directory for storage I/O accounting')
    jobs.add_argument('--work-dir', type=Path, help='Isolated service temp directory to sample')
    jobs.add_argument('--modes', choices=('custom','auto'), nargs='+', default=['custom','auto'])
    check = sub.add_parser('previews', help='Measure and discard previews on an isolated benchmark job')
    check.add_argument('job_id')
    check.add_argument('output', type=Path)
    check.add_argument('--url', required=True)
    check.add_argument('--api-pid-file', type=Path)
    check.add_argument('--work-dir', type=Path)
    score = sub.add_parser('quality', help='Compare manually annotated intervals; see the validation report')
    score.add_argument('source', type=Path)
    score.add_argument('labels', type=Path)
    score.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'fixture':
        fixture(args.output, args.hours, args.audio)
    elif args.command == 'details':
        details(args.source,args.output)
    elif args.command == 'quality':
        quality(args.source, args.labels, args.output)
    elif args.command == 'previews':
        previews(args.url, args.job_id, args.output, args.api_pid_file, args.work_dir)
    elif args.command == 'service':
        service(args.source, args.output, args.url, args.modes, args.run_dir, args.work_dir)
    else:
        run(args.source, args.output)
