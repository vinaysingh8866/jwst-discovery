"""Every registered stage must construct against an empty workspace and declare a name."""
from threading import Event

import pytest

from jwst_pipeline.stages import STAGE_ORDER, STAGE_REGISTRY

EXPECTED_ORDER = ["ingest", "preprocess", "detect", "deblend", "measure",
                  "characterize", "crossmatch", "anomaly", "report"]


def test_stage_order_is_the_documented_nine():
    assert STAGE_ORDER == EXPECTED_ORDER


@pytest.mark.parametrize("name", EXPECTED_ORDER)
def test_stage_constructs_and_creates_workspace_layout(name, tmp_path):
    stage = STAGE_REGISTRY[name](
        workspace=tmp_path / "ws",
        config={"target_field": "T", "filters": ["F200W"]},
        progress_callback=lambda *_: None,
        cancel_event=Event(),
    )
    assert stage.name == name
    for sub in ("raw", "processed", "catalogs", "segmentation", "report"):
        assert (tmp_path / "ws" / sub).is_dir()
