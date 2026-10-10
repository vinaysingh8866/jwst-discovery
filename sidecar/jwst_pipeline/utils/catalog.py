"""Polars-based catalog I/O utilities."""
from pathlib import Path


def read_catalog(path: Path):
    """Read a Parquet catalog. Returns a Polars DataFrame."""
    import polars as pl
    return pl.read_parquet(path)

def write_catalog(df, path: Path):
    """Write a Polars DataFrame to Parquet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path, compression="zstd")
