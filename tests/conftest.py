"""Test-session safety net: no test may touch the production submission authority.

The production authority lives under $XDG_STATE_HOME (or ~/.local/state). Every
test runs with XDG_STATE_HOME pointed at a private temporary directory, so even
a test that forgets to inject a temporary authority cannot reach the real one.

Live submission is opt-in through FINK_LSST_LIVE_SUBMISSION; it is removed
for every test so an operator's shell can never arm a test run. Tests that
exercise the armed path set it themselves and use a fake portal.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_submission_authority(tmp_path_factory, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path_factory.mktemp("xdg_state")))
    monkeypatch.delenv("FINK_LSST_LIVE_SUBMISSION", raising=False)
