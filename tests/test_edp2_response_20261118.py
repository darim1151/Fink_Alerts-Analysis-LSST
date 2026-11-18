"""Offline tests of the helpers in scripts/edp2_response_20261118.py."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edp2_response_20261118 as rv  # noqa: E402


def test_flux_ratio_rules():
    fr = rv.flux_ratio([100.0, -50.0, 10.0, 10.0, 10.0], [100.0, 100.0, -5.0, 100.0, 100.0], [10.0, 10.0, 1.0, 25.0, 0.0])
    assert fr[0] == pytest.approx(2.0) and fr[1] == pytest.approx(0.5)
    assert np.isnan(fr[2]) and np.isnan(fr[3]) and np.isnan(fr[4])     # negative template, S/N 4, zero error


def test_mag_zero_point_and_nonpositive():
    m = rv.mag([10 ** (31.4 / 2.5), 0.0, -3.0])
    assert m[0] == pytest.approx(0.0, abs=1e-9) and np.isnan(m[1]) and np.isnan(m[2])


def test_nalert_bins():
    assert rv.nalert_bins([1, 2, 3, 4, 5, 9, 10, 19, 20, 60]).tolist() == ["1", "2", "3-4", "3-4", "5-9", "5-9", "10-19", "10-19", ">= 20", ">= 20"]
