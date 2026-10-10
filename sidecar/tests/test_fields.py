"""The field registry must stay well-formed: the CLI and docs are generated from it."""
import pytest

from jwst_pipeline.fields import DEFAULT_STAGES, FIELDS, get_field
from jwst_pipeline.stages import STAGE_REGISTRY

REQUIRED = {"target_field", "program_id", "center_ra", "center_dec", "filters",
            "detection_threshold", "min_area", "crossmatch_radius_arcsec",
            "anomaly_contamination", "top_candidates"}


@pytest.mark.parametrize("key", sorted(FIELDS))
def test_field_entry_is_complete(key):
    f = FIELDS[key]
    cfg = f["config"]
    assert REQUIRED <= set(cfg), f"{key} missing {REQUIRED - set(cfg)}"
    assert cfg["target_field"] == key
    assert -360 <= cfg["center_ra"] <= 360 and -90 <= cfg["center_dec"] <= 90
    assert cfg["filters"] and all(flt.startswith("F") for flt in cfg["filters"])
    for stage in f.get("stages", DEFAULT_STAGES):
        assert stage in STAGE_REGISTRY


def test_lookup_is_case_insensitive():
    assert get_field("smacs-0723")["key"] == "SMACS-0723"
    with pytest.raises(KeyError):
        get_field("not-a-field")


def test_cli_list_runs():
    from jwst_pipeline.cli import main
    assert main(["list"]) == 0
