"""Abstract base class for all pipeline stages."""
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from threading import Event


class CancellationError(Exception):
    pass


class PipelineStage(ABC):
    """Base class for pipeline stages."""

    name: str = "base"

    def __init__(
        self,
        workspace: Path,
        config: dict,
        progress_callback: Callable[[float, str], None],
        cancel_event: Event,
        emit_fn: Callable[..., None] | None = None,
    ):
        self.workspace = workspace
        self.config = config
        self.progress = progress_callback
        self.cancel = cancel_event
        self.emit = emit_fn or (lambda *args, **kwargs: None)

        # Ensure workspace directories exist
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "raw").mkdir(exist_ok=True)
        (self.workspace / "processed").mkdir(exist_ok=True)
        (self.workspace / "catalogs").mkdir(exist_ok=True)
        (self.workspace / "segmentation").mkdir(exist_ok=True)
        (self.workspace / "report").mkdir(exist_ok=True)
        (self.workspace / "report" / "cutouts").mkdir(exist_ok=True)
        (self.workspace / "report" / "seds").mkdir(exist_ok=True)

    def check_cancel(self):
        if self.cancel.is_set():
            raise CancellationError(f"Stage {self.name} cancelled by user")

    @abstractmethod
    def run(self) -> dict:
        """Execute the stage. Returns a result summary dict."""
        ...

    def validate_inputs(self) -> bool:
        """Check that required input files exist. Override in subclasses."""
        return True
