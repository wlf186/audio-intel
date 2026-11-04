import numpy as np
import pytest
import soundfile as sf

from asr.chunk_input import chunk_reader


@pytest.mark.parametrize('first,last', [(0, 1600), (32159, 992160), (0, 1100000)])
def test_ranges_preserve_pcm_quantization(tmp_path, first, last):
    samples = np.random.default_rng(7).uniform(-1, 1, 1100000).astype('float32')
    source, chunk = tmp_path / 'source.wav', tmp_path / 'chunk.wav'
    sf.write(source, samples, 16000, subtype='PCM_16')
    sf.write(chunk, samples[first:last], 16000, subtype='PCM_16')
    with chunk_reader({'audio_source': {'path': str(source), 'sample_rate': 16000}}) as read:
        actual, rate = read([{'start_sample': first, 'end_sample': last}])[0]
        expected, _ = sf.read(chunk, dtype='float32')
        assert rate == 16000
        assert np.array_equal(actual, expected)
        with pytest.raises(ValueError):
            read([{'start_sample': 0, 'end_sample': len(samples) + 1}])
    source.unlink()  # The context releases the handle on Windows too.


def test_legacy_paths():
    with chunk_reader({}) as read:
        assert read([{'path': 'legacy.wav'}]) == ['legacy.wav']
