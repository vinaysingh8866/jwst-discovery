# jwst-pipeline

Python sidecar for [JWST Discovery](https://github.com/vinaysingh8866/jwst-discovery): the nine-stage NIRCam pipeline (ingest, preprocess, detect, deblend, measure, characterize, crossmatch, anomaly, report), the NDJSON protocol the desktop app drives it with, and the `jwst-field` command for running fields headless.

```bash
pip install -e ".[dev]"
jwst-field list
jwst-field run SMACS-0723
pytest -q
```

Full documentation is in the repository README and `docs/`.
