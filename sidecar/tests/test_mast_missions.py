"""MAST Missions failures must surface as a clear message, not 'invalid input'."""
import astropy.units as u
from astropy.coordinates import SkyCoord

import jwst_pipeline.__main__ as main


class _ServerError(Exception):
    pass


def _fake_missions(fail_times: int, rows: int = 3):
    calls = []

    class _Conn:
        TIMEOUT = 600

    class FakeMissions:
        def __init__(self, mission):
            self._service_api_connection = _Conn()

        def query_region(self, coord, radius, limit, select_cols):
            calls.append((radius.to(u.arcmin).value, limit))
            if len(calls) <= fail_times:
                raise _ServerError("The server was unable to process the request due to invalid input.")
            return [{"program": "1", "pi_name": "x", "instrume": "NIRCAM", "opticalElements": "F200W;CLEAR",
                     "duration": 100.0}] * rows

    return FakeMissions, calls


def test_retries_once_with_half_radius_then_succeeds():
    Fake, calls = _fake_missions(fail_times=1)
    res = main._missions_query_with_fallback(Fake, SkyCoord(1, 2, unit="deg"), 5.0, u)
    assert len(res) == 3
    assert calls == [(5.0, main.MISSIONS_ROW_LIMIT), (2.5, 500)]


def test_persistent_failure_is_explained(monkeypatch):
    Fake, calls = _fake_missions(fail_times=5)
    monkeypatch.setattr("astroquery.mast.MastMissions", Fake)
    out = []
    monkeypatch.setattr(main, "emit", lambda obj: out.append(obj))
    main.query_mast_missions({"center_ra": 53.126, "center_dec": -27.802, "field_size_arcmin": 5.0}, "r1")
    payload = out[-1]["payload"]
    assert payload["type"] == "Error"
    assert "dense fields" in payload["message"]
    assert "invalid input" not in payload["message"].split("Last error")[0]
    assert len(calls) == 2


def test_timeout_is_applied_to_the_connection():
    Fake, _ = _fake_missions(fail_times=0)
    m = Fake("jwst")
    main._missions_query_with_fallback(lambda mission: m, SkyCoord(1, 2, unit="deg"), 1.0, u)
    assert m._service_api_connection.TIMEOUT == main.MISSIONS_TIMEOUT_S
