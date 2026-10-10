#!/usr/bin/env bash
# Build the Python pipeline sidecar into a one-dir PyInstaller bundle and drop
# it where Tauri's `bundle.resources` picks it up:
#
#     src-tauri/sidecar-dist/jwst-pipeline/jwst-pipeline[.exe]   (+ _internal/)
#
# The app finds it under <resource_dir>/sidecar/jwst-pipeline/ at runtime.
#
# Usage:
#     scripts/build_sidecar.sh            # full PyInstaller build + smoke test
#     scripts/build_sidecar.sh --stub     # write a placeholder so `tauri build`
#                                         # / CI work without PyInstaller
#
# Environment overrides:
#     PYTHON                 interpreter to use (default: python3 on PATH)
#     JWST_SKIP_EAZY_FETCH=1 do not git-clone eazy-photoz templates if missing
#     JWST_SKIP_SMOKE_TEST=1 skip the --version / protocol smoke test
#     JWST_CODESIGN_IDENTITY macOS signing identity passed to PyInstaller
#
# See docs/BUILDING.md for the full story.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SIDECAR_DIR="$ROOT/sidecar"
SPEC="$SIDECAR_DIR/jwst-pipeline.spec"
WORK_DIR="$SIDECAR_DIR/build/pyinstaller"   # sidecar/build/ is git-ignored
DIST_DIR="$SIDECAR_DIR/build/dist"
OUT_DIR="$ROOT/src-tauri/sidecar-dist/jwst-pipeline"
PYTHON="${PYTHON:-python3}"

log()  { printf '\033[1;34m[build_sidecar]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[build_sidecar] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

EXT=""
case "$(uname -s 2>/dev/null)" in MINGW*|MSYS*|CYGWIN*|Windows_NT) EXT=".exe" ;; esac
OUT="$OUT_DIR/jwst-pipeline${EXT}"

# ---------------------------------------------------------------------------
# --stub: placeholder so tauri-build's resource check passes without PyInstaller
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--stub" ]]; then
    rm -rf "$OUT_DIR" && mkdir -p "$OUT_DIR"
    cat > "$OUT" <<'STUB'
#!/bin/sh
echo "jwst-pipeline STUB: this placeholder was written by scripts/build_sidecar.sh --stub" >&2
echo "Run scripts/build_sidecar.sh (without --stub) to build the real sidecar, or set JWST_DEV_SIDECAR=1." >&2
exit 1
STUB
    chmod +x "$OUT"
    log "wrote stub sidecar at $OUT"
    exit 0
fi

# ---------------------------------------------------------------------------
# Python environment
# ---------------------------------------------------------------------------
command -v "$PYTHON" >/dev/null 2>&1 || die "python interpreter '$PYTHON' not found (set PYTHON=...)"
log "python: $("$PYTHON" -c 'import sys; print(sys.executable, sys.version.split()[0])')"
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python >= 3.11 required"

# PyInstaller refuses to run if the obsolete `typing` backport is installed.
if "$PYTHON" -m pip show typing >/dev/null 2>&1; then
    log "removing obsolete 'typing' backport (incompatible with PyInstaller)"
    "$PYTHON" -m pip uninstall -y typing >/dev/null
fi

needs_install=0
if ! (cd "$SIDECAR_DIR" && "$PYTHON" - <<'PY' 2>/dev/null
import importlib
for mod in ("PyInstaller", "jwst_pipeline", "astropy", "astroquery", "pyvo", "sep",
            "photutils", "reproject", "polars", "pyarrow", "sklearn", "scipy",
            "matplotlib", "eazy", "skimage", "requests"):
    importlib.import_module(mod)
PY
); then
    needs_install=1
fi

if [[ $needs_install -eq 1 ]]; then
    log "installing sidecar + dev extras: pip install -e \"$SIDECAR_DIR[dev]\""
    "$PYTHON" -m pip install -e "$SIDECAR_DIR[dev]"
else
    log "sidecar dependencies and PyInstaller already importable; skipping pip install"
fi

"$PYTHON" -m PyInstaller --version >/dev/null 2>&1 || die "PyInstaller still not importable after install"
log "PyInstaller $("$PYTHON" -m PyInstaller --version)"

# ---------------------------------------------------------------------------
# EAZY templates (eazy-photoz repo subset gets bundled by the spec)
# ---------------------------------------------------------------------------
EAZY_DATA="$("$PYTHON" -c 'import os, eazy; print(os.path.join(os.path.dirname(eazy.__file__), "data"))' 2>/dev/null | tail -1)"
if [[ -n "$EAZY_DATA" && ! -d "$EAZY_DATA/eazy-photoz/templates" ]]; then
    if [[ "${JWST_SKIP_EAZY_FETCH:-0}" == "1" ]]; then
        log "eazy-photoz templates missing and JWST_SKIP_EAZY_FETCH=1; binary will clone them at runtime"
    else
        log "fetching eazy-photoz templates into $EAZY_DATA (shallow clone)"
        command -v git >/dev/null 2>&1 || die "git required to fetch eazy-photoz (or set JWST_SKIP_EAZY_FETCH=1)"
        rm -rf "$EAZY_DATA/eazy-photoz"
        git clone --depth 1 --quiet https://github.com/gbrammer/eazy-photoz.git "$EAZY_DATA/eazy-photoz"
    fi
fi

# ---------------------------------------------------------------------------
# PyInstaller
# ---------------------------------------------------------------------------
log "building one-dir sidecar"
rm -rf "$DIST_DIR"
(cd "$SIDECAR_DIR" && "$PYTHON" -m PyInstaller \
    --noconfirm --clean \
    --workpath "$WORK_DIR" \
    --distpath "$DIST_DIR" \
    --log-level "${PYINSTALLER_LOG_LEVEL:-WARN}" \
    "$SPEC")

BUILT_DIR="$DIST_DIR/jwst-pipeline"
[[ -f "$BUILT_DIR/jwst-pipeline${EXT}" ]] || die "PyInstaller did not produce $BUILT_DIR/jwst-pipeline${EXT}"

rm -rf "$OUT_DIR" && mkdir -p "$(dirname "$OUT_DIR")"
cp -R "$BUILT_DIR" "$OUT_DIR"
chmod +x "$OUT"

# ---------------------------------------------------------------------------
# Smoke test: the binary must start, answer --version, and speak the protocol
# ---------------------------------------------------------------------------
if [[ "${JWST_SKIP_SMOKE_TEST:-0}" != "1" ]]; then
    log "smoke test: $OUT --version"
    if ! version_out="$("$OUT" --version 2>&1)"; then
        printf '%s\n' "$version_out" >&2
        die "sidecar binary failed to start with --version (see output above)"
    fi
    case "$version_out" in
        jwst-pipeline*) log "ok: $version_out" ;;
        *) printf '%s\n' "$version_out" >&2; die "unexpected --version output" ;;
    esac

    log "smoke test: --selftest (imports every bundled package)"
    "$OUT" --selftest >/dev/null || die "sidecar --selftest failed"

    log "smoke test: Status/Shutdown round-trip over the NDJSON protocol"
    "$PYTHON" - "$OUT" <<'PY' || die "protocol smoke test failed"
import json, subprocess, sys
binary = sys.argv[1]
req = (json.dumps({"id": "1", "command": {"type": "Status"}}) + "\n"
       + json.dumps({"id": "2", "command": {"type": "Shutdown"}}) + "\n")
proc = subprocess.run([binary], input=req, capture_output=True, text=True, timeout=300)
acks = {}
for line in proc.stdout.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        print(f"  non-JSON stdout line: {line!r}", file=sys.stderr)
        continue
    payload = msg.get("payload", {})
    if payload.get("type") == "Ack":
        acks[msg.get("id")] = payload.get("command")
print(f"  stdout acks: {acks}", file=sys.stderr)
if proc.returncode != 0 or acks.get("1") != "Status" or acks.get("2") != "Shutdown":
    print(proc.stderr[-4000:], file=sys.stderr)
    sys.exit(f"  exit={proc.returncode}; expected Ack for Status and Shutdown")
PY
fi

SIZE="$(du -sh "$OUT_DIR" | cut -f1)"
log "sidecar ready: $OUT_DIR ($SIZE, $(find "$OUT_DIR" -type f | wc -l | tr -d ' ') files)"
log "next: npm run tauri build   (bundles it as <resources>/sidecar/jwst-pipeline/)"
