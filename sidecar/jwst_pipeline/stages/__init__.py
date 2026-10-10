"""Pipeline stage registry."""
from jwst_pipeline.stages.anomaly import AnomalyStage
from jwst_pipeline.stages.base import PipelineStage
from jwst_pipeline.stages.characterize import CharacterizeStage
from jwst_pipeline.stages.crossmatch import CrossmatchStage
from jwst_pipeline.stages.deblend import DeblendStage
from jwst_pipeline.stages.detect import DetectStage
from jwst_pipeline.stages.ingest import IngestStage
from jwst_pipeline.stages.measure import MeasureStage
from jwst_pipeline.stages.preprocess import PreprocessStage
from jwst_pipeline.stages.report import ReportStage

STAGE_REGISTRY: dict[str, type[PipelineStage]] = {
    "ingest": IngestStage,
    "preprocess": PreprocessStage,
    "detect": DetectStage,
    "deblend": DeblendStage,
    "measure": MeasureStage,
    "characterize": CharacterizeStage,
    "crossmatch": CrossmatchStage,
    "anomaly": AnomalyStage,
    "report": ReportStage,
}

STAGE_ORDER = list(STAGE_REGISTRY.keys())
