import math
import tracemalloc

import numpy as np
import pytest

from audio_intel.utils import waveform_peaks


def reference(samples, bins):
    width = max(1, math.ceil(len(samples) / bins))
    return [round(max(abs(float(x)) for x in samples[i:i + width]), 4)
            for i in range(0, len(samples), width)]


@pytest.mark.parametrize('count,bins', [(0, 240), (4, 240), (500, 240), (131_077, 1), (131_077, 3)])
@pytest.mark.parametrize('dtype', ['float32', 'float64', 'int16'])
def test_legacy_waveform_values_and_boundaries(count, bins, dtype):
    values = (np.arange(count) % 31 - 15).astype(dtype)
    if dtype != 'int16':
        values /= 7
    expected = reference(values, bins)
    assert waveform_peaks(values, bins) == expected
    assert waveform_peaks(values.tolist(), bins) == expected


@pytest.mark.parametrize('values', [[float('nan')], [float('inf')], [[1, 2]], iter([1, 2])])
def test_invalid_waveform_samples_are_rejected(values):
    with pytest.raises((TypeError, ValueError)):
        waveform_peaks(values, 240)


@pytest.mark.parametrize('bins', [0, -1, True, 1.5])
def test_invalid_bins_are_rejected(bins):
    with pytest.raises(ValueError):
        waveform_peaks([1, 2], bins)


@pytest.mark.parametrize('bins', [1, 240])
def test_recording_13_hours_uses_bounded_auxiliary_memory(bins):
    class VirtualAudio:
        maximum_slice = 0

        def __len__(self):
            return 779_695_104

        def __iter__(self):
            raise AssertionError('The recording must not be materialized')

        def __getitem__(self, index):
            assert isinstance(index, slice)
            size = index.stop - index.start
            assert size <= 65_536
            self.maximum_slice = max(self.maximum_slice, size)
            return np.full(size, -.25, dtype='float32')

    waveform_peaks([0, 1], 1)  # Warm imports before measuring allocations.
    audio = VirtualAudio()
    tracemalloc.start()
    try:
        result = waveform_peaks(audio, bins)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result == [.25] * bins
    assert audio.maximum_slice == 65_536
    assert peak < 8 * 1024 * 1024
