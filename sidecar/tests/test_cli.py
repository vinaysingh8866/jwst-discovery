"""``jwst-field`` CLI behaviour that does not need data or network."""
from __future__ import annotations

import pytest

from jwst_pipeline.cli import main
from jwst_pipeline.fields import FIELDS

FIELD_WITH_RECIPE = next(k for k, f in FIELDS.items() if f.get("download_script"))


def test_run_on_empty_workspace_returns_1_with_hint(tmp_path, capsys):
    rc = main(["run", FIELD_WITH_RECIPE, "--workspace-root", str(tmp_path)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "No FITS files" in out
    assert f"jwst-field download {FIELD_WITH_RECIPE}" in out
    assert str(tmp_path / FIELD_WITH_RECIPE / "raw") in out


def test_run_stages_filter_only_runs_requested_stages(tmp_path, capsys):
    raw = tmp_path / FIELD_WITH_RECIPE / "raw"
    raw.mkdir(parents=True)
    (raw / "placeholder_i2d.fits").write_bytes(b"")  # enough to get past the empty-workspace check
    rc = main(["run", FIELD_WITH_RECIPE, "--workspace-root", str(tmp_path), "--stages", "detect"])
    # detect has no detection image to work on, so it fails -> 1, but only detect ran.
    assert rc == 1
    out = capsys.readouterr().out
    assert "=== detect ===" in out
    assert "=== preprocess ===" not in out and "=== measure ===" not in out
    assert "failed stages: ['detect']" in out


def test_run_rejects_unknown_stage(tmp_path, capsys):
    rc = main(["run", FIELD_WITH_RECIPE, "--workspace-root", str(tmp_path), "--stages", "detect", "bogus"])
    assert rc == 2
    assert "bogus" in capsys.readouterr().err


@pytest.mark.parametrize("cmd", ["run", "download"])
def test_unknown_field_returns_2(cmd, capsys):
    rc = main([cmd, "Not-A-Field"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "Unknown field" in err and "Not-A-Field" in err


def test_download_points_at_field_recipe_without_network(capsys):
    rc = main(["download", FIELD_WITH_RECIPE])
    assert rc == 2
    out = capsys.readouterr().out
    assert FIELDS[FIELD_WITH_RECIPE]["download_script"] in out
    assert "--force-mast" in out


def test_list_prints_every_field(capsys):
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    for key in FIELDS:
        assert key in out
