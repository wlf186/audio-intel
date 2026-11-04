"""Read existing PCM16 ranges without materializing temporary chunk WAVs."""
from contextlib import contextmanager
from functools import wraps


@contextmanager
def chunk_reader(payload):
    descriptor = payload.get('audio_source')
    if descriptor is None:
        yield lambda items: [item['path'] for item in items]
        return
    import soundfile as sf
    with sf.SoundFile(descriptor['path']) as source:
        if (source.samplerate != 16000 or descriptor['sample_rate'] != 16000
                or source.channels != 1 or source.subtype != 'PCM_16'):
            raise ValueError('Expected saved 16 kHz mono PCM16 audio')
        def read(items):
            output = []
            for item in items:
                first, last = item['start_sample'], item['end_sample']
                if not isinstance(first, int) or not isinstance(last, int) or not 0 <= first < last <= source.frames:
                    raise ValueError('Invalid chunk sample interval')
                source.seek(first)
                samples = source.read(last - first, dtype='float32')
                if len(samples) != last - first:
                    raise ValueError('Chunk source changed during read')
                output.append((samples, 16000))
            return output
        yield read


def with_chunk_reader(function):
    @wraps(function)
    def wrapped(payload):
        with chunk_reader(payload) as reader:
            return function(payload, reader)
    return wrapped
