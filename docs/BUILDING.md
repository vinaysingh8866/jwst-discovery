# Building JWST Discovery

JWST Discovery is a Tauri 2 desktop app (React front end, Rust shell) that drives
a Python pipeline running as a **sidecar** process. The app talks to the sidecar
over NDJSON on stdin/stdout (`sidecar/jwst_pipeline/__main__.py`).

In development the sidecar is plain `python3 -m jwst_pipeline`. In a packaged
release it is a single-file PyInstaller executable, `jwst-pipeline`, that Tauri
ships inside the app bundle. This document covers both.

## Prerequisites

All platforms:

| Tool | Version | Notes |
|------|---------|-------|
| Node.js | 20+ | front end + Tauri CLI (`npm ci`) |
| Rust | stable | `rustup`; `rustc -vV` must print the host triple |
| Python | 3.11+ | sidecar; must be a **shared-library** build for PyInstaller (see below) |
| git | any | fetches the EAZY template repository the first time |

Per OS:

- **macOS**: Xcode Command Line Tools (`xcode-select --install`). Builds a `.dmg`
  and `.app`. A pyenv Python works as long as it was built with
  `PYTHON_CONFIGURE_OPTS="--enable-shared"` (the python.org installer and
  Homebrew Python already are). Check with
  `python3 -c "import sysconfig; print(sysconfig.get_config_var('Py_ENABLE_SHARED'))"`
  (must print `1`).
- **Linux (Debian/Ubuntu)**:
  `sudo apt-get install libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf build-essential curl wget file libssl-dev`.
  Builds `.deb`, `.rpm` and `.AppImage`. Python from `apt` is a shared build.
- **Windows**: Visual Studio Build Tools (C++ workload), WebView2 runtime
  (preinstalled on Windows 11). Run `scripts/build_sidecar.sh` from Git Bash;
  the sidecar is named `jwst-pipeline-x86_64-pc-windows-msvc.exe`.

## Development run

```bash
npm ci
python3 -m pip install -e "sidecar[dev]"   # pipeline deps + pytest + pyinstaller
scripts/build_sidecar.sh --stub            # placeholder, see "Why a stub?" below
npm run tauri dev
```

Debug builds launch `python3 -m jwst_pipeline` with the working directory set to
`sidecar/`, so edits to the Python code take effect on the next app start with no
rebuild. Logs from the sidecar's stderr show up in the app's log panel.

**Why a stub?** `tauri.conf.json` declares
`bundle.resources: {"sidecar-dist/jwst-pipeline": "sidecar/jwst-pipeline"}`.
tauri-build verifies that `src-tauri/sidecar-dist/jwst-pipeline/` exists at
*compile* time, so even `cargo check` and `tauri dev` fail with
`resource path sidecar-dist/jwst-pipeline doesn't exist` when it is absent.
`scripts/build_sidecar.sh --stub` writes a two-line shell script in that directory;
it is never executed in debug builds (they use Python). The CI `rust` job does the
same before `cargo check`.

## Release build

```bash
npm ci
python3 -m pip install -e "sidecar[dev]"
scripts/build_sidecar.sh          # 1. PyInstaller -> src-tauri/sidecar-dist/jwst-pipeline/
npx tauri build                   # 2. front end + Rust + bundle
```

Outputs land in `src-tauri/target/release/bundle/` (`dmg/` and `macos/` on macOS,
`deb/`, `rpm/`, `appimage/` on Linux, `msi/` and `nsis/` on Windows).

### What `scripts/build_sidecar.sh` does

1. Resolves the target triple from `rustc -vV` (override with `TARGET_TRIPLE=`).
2. Uninstalls the obsolete `typing` backport if present (PyInstaller refuses to
   run with it installed) and runs `pip install -e "sidecar[dev]"` if PyInstaller
   or any pipeline dependency is missing.
3. Shallow-clones `gbrammer/eazy-photoz` into `site-packages/eazy/data/` if the
   EAZY templates are not there yet (`JWST_SKIP_EAZY_FETCH=1` to skip).
4. Runs PyInstaller with `sidecar/jwst-pipeline.spec` (work dir
   `sidecar/build/pyinstaller`, output `sidecar/build/dist/jwst-pipeline`; both
   git-ignored).
5. Copies the result to `src-tauri/sidecar-dist/jwst-pipeline/` (Tauri's
   resources naming convention; `.exe` appended on Windows).
6. Smoke-tests it: `--version` must print `jwst-pipeline <version>`, `--selftest`
   must import every bundled scientific package, and a `Status` + `Shutdown`
   request piped over the protocol must come back with two `Ack` responses. Any
   failure aborts with a non-zero exit so a broken binary never reaches
   `tauri build`.

Set `PYTHON=/path/to/python3.11` to pick the interpreter, `JWST_SKIP_SMOKE_TEST=1`
to skip step 6, `PYINSTALLER_LOG_LEVEL=INFO` for verbose PyInstaller output.

### What the spec bundles

`sidecar/jwst-pipeline.spec` generates a small launcher (handles `--version`,
`--help`, `--selftest`, then calls `jwst_pipeline.__main__:main`) and builds a
one-dir console executable from it with:

- hidden imports: every submodule of `jwst_pipeline`, astropy, astroquery, pyvo,
  sep, photutils, reproject, polars, pyarrow, scikit-learn, scipy, scikit-image,
  eazy and requests (test packages filtered out), plus matplotlib's Agg/PDF/SVG
  backends only;
- data files: astropy config + IERS tables and matplotlib's `mpl-data` (via
  PyInstaller's own hooks), package data for astroquery/pyvo/photutils/reproject,
  eazy's in-package data, the subset of `eazy-photoz` that `characterize.py`
  uses (`filters/FILTER.RES.latest`, `templates/fsps_full/`, `TEMPLATE_ERROR*`,
  `prior_*.dat`; about 13 MB instead of the 200 MB clone) and any
  `sidecar/data/psf_kernels/*` files present at build time;
- excludes: tkinter, PyQt/PySide, wx, torch, jax, scarlet2, pcigale, numba,
  cupy, IPython/jupyter, pytest.

## Where the sidecar goes and how the app finds it

| Stage | Path |
|-------|------|
| PyInstaller output | `sidecar/build/dist/jwst-pipeline/` (executable + `_internal/`) |
| Tauri input | `src-tauri/sidecar-dist/jwst-pipeline/` (git-ignored) |
| Inside the macOS bundle | `JWST Discovery.app/Contents/Resources/sidecar/jwst-pipeline/jwst-pipeline` |
| Inside Linux/Windows bundles | `<resource dir>/sidecar/jwst-pipeline/jwst-pipeline[.exe]` |

`tauri.conf.json` maps the directory through `bundle.resources`, and
`src-tauri/src/sidecar/manager.rs` resolves
`app.path().resource_dir()/sidecar/jwst-pipeline/jwst-pipeline` at runtime,
restores the executable bit if the copy lost it, checks the file exists, and
otherwise raises `Bundled pipeline sidecar not found at <path> ...` naming the
expected path instead of a bare OS spawn error.

Right after spawning, the host sends a `Status` request with id
`startup-status`. The sidecar's `Ack` flips the app state to ready and emits a
`sidecar:health` event; the top bar shows "Sidecar: starting / ready /
unavailable" and the Data page waits for ready before querying MAST. Errors from
one-shot requests (MAST queries, catalog checks) arrive as `sidecar:error`
events and are shown in the panel that issued them.

## Content-Security-Policy (Sky Viewer)

`app.security.csp` in `src-tauri/tauri.conf.json` is enforced by the webview in
the packaged app only; `npm run tauri dev` and the Vite dev server do not apply
it. Aladin Lite (the Sky Viewer) needs these entries, and the viewer renders an
empty panel without them:

| Directive | Entry | Why |
|-----------|-------|-----|
| `script-src` | `'wasm-unsafe-eval'` | Aladin Lite v3 is a Rust/WASM core; `WebAssembly.instantiate` is refused without it. |
| `connect-src` | `data:` | `aladin.js` embeds its `.wasm` as a `data:application/wasm;base64,...` URL and loads it with `fetch()`. The webview classifies that as a connection, so `A.init` rejects with `TypeError: Failed to fetch` if `data:` is missing. This was the cause of the blank viewer in 0.1.0. |
| `connect-src` | `https://alasky.unistra.fr`, `https://alaskybis.unistra.fr` | Aladin Lite 3.6 resolves the survey id (`P/DSS2/color`) through `/MocServer/query` on these hosts (not the `.cds.unistra.fr` aliases) and fetches HiPS tiles from whichever mirror the survey's `properties` names. Without them the survey never loads ("do not contain the mandatory frame or order info"). |
| `connect-src` | `https://alasky.cds.unistra.fr`, `https://alaskybis.cds.unistra.fr`, `https://aladin.cds.unistra.fr`, `https://*.u-strasbg.fr` | Tile and metadata mirrors, Sesame name resolver. |
| `img-src` | same hosts plus `data:` and `blob:` | Tiles loaded through `<img>`, inline SVG icons of the Aladin UI, FITS blobs. |
| `worker-src` | `'self' blob:` | Reserved for Aladin's optional web workers. |

Any new survey, catalog service (SIMBAD, VizieR) or archive the front end talks
to must be added to `connect-src` here. The Playwright suite serves the page
under this exact CSP (`vite --mode e2e`, see `vite.config.ts`), so a missing
host shows up as a failing `07-viewer` test with the `Refused to connect`
console line in the report.

## Workspace root

Both the app and the `jwst-field` CLI read `JWST_WORKSPACE_ROOT`; unset, it is
`~/.jwst-discovery/workspaces`. Point it at an external drive to keep the app and
the CLI looking at the same data.

## Overriding the sidecar at runtime

`manager.rs` picks the launch mode in this order:

| Setting | Effect |
|---------|--------|
| `JWST_SIDECAR_BIN=/path/to/jwst-pipeline` | Run that exact executable (works in debug and release). Highest priority. |
| `JWST_DEV_SIDECAR=1` | Run `python3 -m jwst_pipeline` from the source tree, even in a release build. |
| `JWST_DEV_SIDECAR=0` | Use the bundled binary even in a debug build. |
| (unset) | Debug build: Python. Release build: bundled binary. |

Companions for Python mode: `JWST_SIDECAR_DIR` (source directory, default
`<repo>/sidecar` as recorded at compile time) and `JWST_PYTHON` (interpreter,
default `python3`).

Examples:

```bash
# test the PyInstaller build inside the dev loop
JWST_SIDECAR_BIN=$PWD/src-tauri/sidecar-dist/jwst-pipeline-aarch64-apple-darwin npm run tauri dev

# debug a packaged app against editable Python sources
JWST_DEV_SIDECAR=1 JWST_SIDECAR_DIR=$PWD/sidecar \
  "src-tauri/target/release/bundle/macos/JWST Discovery.app/Contents/MacOS/jwst-discovery"
```

## Size and startup

Measured on an Apple Silicon Mac (aarch64-apple-darwin, Python 3.11.13,
PyInstaller 6.22.3), 2026-10-09:

| Metric | Value |
|--------|-------|
| `src-tauri/sidecar-dist/jwst-pipeline/` | 390 MB (2,821 files; pyarrow, polars, scipy, sklearn, skimage, eazy data, matplotlib, astropy + IERS) |
| `jwst-pipeline --version` | 0.2 s |
| First `Status` reply after app launch (packaged app) | 3 s (measured: spawn at T+0, Ack at T+3 s) |
| Same round-trip with `python3 -m jwst_pipeline` | ~3 s |

One-dir mode ships the files already unpacked, so a launch only has to load
them. The earlier one-file build unpacked ~2,800 files into a temp directory on
every launch and took 30 to 90 s before the first reply; that is why the layout
changed.

## CI

`.github/workflows/ci.yml`:

- `sidecar`, `frontend`, `rust` run on every push/PR. `rust` writes the stub
  sidecar before `cargo check` (see "Why a stub?").
- `release-build` runs only on `workflow_dispatch` or tags matching `v*`, on
  `macos-latest` (Apple Silicon): it runs `scripts/build_sidecar.sh`, then
  `npx tauri build --ci`, and uploads the `.dmg` as the
  `jwst-discovery-macos-<ref>` artifact.

## Known issues and caveats

- **`cargo check` / `tauri dev` need the sidecar directory to exist.** tauri-build
  validates `bundle.resources` at compile time. Use
  `scripts/build_sidecar.sh --stub` on machines without PyInstaller.
- **Obsolete `typing` backport.** PyInstaller aborts with
  `The 'typing' package is an obsolete backport ...` if `pip install typing` was
  ever run in the interpreter. `scripts/build_sidecar.sh` uninstalls it
  automatically; this was the case on the development machine.
- **Non-shared Python builds** (pyenv without `--enable-shared`, some conda
  envs) fail with `Python library not found`. Rebuild Python with
  `PYTHON_CONFIGURE_OPTS="--enable-shared" pyenv install 3.11.x` or use the
  python.org installer.
- **EAZY templates.** `characterize.py` calls `eazy.fetch_eazy_photoz()` and
  builds paths from `os.path.dirname(eazy.__file__)`. In the frozen binary that
  is the temporary unpack directory, so the spec bundles the ~13 MB subset of
  eazy-photoz the stage uses (fsps_full templates, FILTER.RES.latest, template
  error and prior files). Other template sets (sfhz, spline_templates_*) are
  not bundled; a stage that needs them would trigger a `git clone` into the
  temp dir on every launch. If templates are missing at build time the script
  shallow-clones them into site-packages first.
- **`import eazy` prints to stdout** (`Failed to import dust_attenuation ...`)
  when the optional `dust_attenuation` package is absent. The Rust side logs a
  parse warning for that line and carries on; it is harmless but noisy. Fix
  belongs in `characterize.py` (redirect stdout around the import) or by
  installing `dust_attenuation` in the build environment.
- **PSF kernels.** `sidecar/data/psf_kernels/*.npy` are git-ignored. Whatever
  is present at build time is bundled; with none present the deblend stage
  falls back to Gaussian PSFs (its normal behaviour).
- **Excluded packages.** The spec excludes cv2, catboost, av, imageio, plotly,
  lxml, pandas, h5py, cryptography and a few others that scikit-image,
  scikit-learn, astropy and eazy import only optionally (they were 290 MB of
  the first build). The whole pipeline was import-tested with each of them
  blocked. If a future stage needs one, delete it from `EXCLUDES` in the spec.
  `keyring` (astroquery) and `dask` (reproject) are hard dependencies and stay.
- **Code signing / notarization.** The sidecar is ad-hoc signed by PyInstaller.
  For a notarized app set `JWST_CODESIGN_IDENTITY` when running the build
  script (passed to PyInstaller's `codesign_identity`) and configure
  `bundle.macOS.signingIdentity` in `tauri.conf.json`; PyInstaller one-dir
  binaries under the hardened runtime may additionally need the
  `com.apple.security.cs.allow-unsigned-executable-memory` /
  `disable-library-validation` entitlements (`JWST_ENTITLEMENTS_FILE`).
  Unsigned builds need `xattr -cr "JWST Discovery.app"` on other Macs.
- **One architecture per build.** The sidecar is built for the host triple
  only; a universal macOS app needs two sidecar builds (x86_64 and aarch64) and
  `tauri build --target universal-apple-darwin`. CI's `macos-latest` runner is
  Apple Silicon, so the uploaded `.dmg` is aarch64 only.
- **Windows** is untested. The script handles the `.exe` suffix, but the spec
  and the pipeline's `multiprocessing` fork assumptions have only been exercised
  on macOS.

## App icon

The source image is `docs/images/app-icon.png` (1024 × 1024, transparent corners). Regenerate every size in `src-tauri/icons/` with:

```bash
npx tauri icon docs/images/app-icon.png
rm -rf src-tauri/icons/android src-tauri/icons/ios   # desktop only
```
