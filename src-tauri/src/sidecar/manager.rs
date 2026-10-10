use crate::error::AppError;
use crate::sidecar::protocol::SidecarResponse;
use log::{error, info, warn};
use std::path::{Path, PathBuf};
use tauri::async_runtime;
use crate::state::AppState;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::mpsc;

/// Path of the bundled sidecar executable inside the app's resource directory
/// (`bundle.resources` in tauri.conf.json maps `src-tauri/sidecar-dist/jwst-pipeline`
/// to `sidecar/jwst-pipeline`; PyInstaller's one-dir layout puts the executable
/// and its `_internal/` folder in there).
pub const SIDECAR_RESOURCE_PATH: &str = "sidecar/jwst-pipeline/jwst-pipeline";

/// Request id of the `Status` ping sent right after spawn; its Ack marks the
/// sidecar as ready.
pub const STARTUP_PING_ID: &str = "startup-status";

/// How the sidecar process is launched.
#[derive(Debug, Clone)]
pub enum SidecarLaunch {
    /// `python -m jwst_pipeline` from the source tree.
    Python { python: String, sidecar_dir: PathBuf },
    /// A PyInstaller-built executable.  `bundled` is true when it is the one
    /// shipped in the app's resource directory.
    Binary { path: PathBuf, bundled: bool },
}

impl SidecarLaunch {
    /// Decide how to launch the sidecar from the environment and build profile.
    pub fn resolve(app: &AppHandle) -> Self {
        let dev_flag = std::env::var("JWST_DEV_SIDECAR")
            .ok()
            .map(|v| v.trim().to_ascii_lowercase())
            .filter(|v| !v.is_empty());
        let force_python = matches!(dev_flag.as_deref(), Some("1" | "true" | "yes" | "on"));
        let force_bundled = matches!(dev_flag.as_deref(), Some("0" | "false" | "no" | "off"));

        if let Some(bin) = std::env::var_os("JWST_SIDECAR_BIN").filter(|v| !v.is_empty()) {
            if force_python {
                warn!("Both JWST_SIDECAR_BIN and JWST_DEV_SIDECAR=1 are set; JWST_SIDECAR_BIN wins");
            }
            return SidecarLaunch::Binary {
                path: PathBuf::from(bin),
                bundled: false,
            };
        }

        let use_python = force_python || (cfg!(debug_assertions) && !force_bundled);
        if use_python {
            let python = std::env::var("JWST_PYTHON")
                .ok()
                .filter(|v| !v.trim().is_empty())
                .unwrap_or_else(|| "python3".to_string());
            let sidecar_dir = std::env::var("JWST_SIDECAR_DIR")
                .ok()
                .filter(|v| !v.trim().is_empty())
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("sidecar")
                });
            return SidecarLaunch::Python {
                python,
                sidecar_dir,
            };
        }

        SidecarLaunch::Binary {
            path: Self::bundled_binary_path(app),
            bundled: true,
        }
    }

    /// `<resource_dir>/sidecar/jwst-pipeline/jwst-pipeline[.exe]`: where
    /// `bundle.resources` places the one-dir PyInstaller build.
    pub fn bundled_binary_path(app: &AppHandle) -> PathBuf {
        let rel = if cfg!(windows) {
            format!("{SIDECAR_RESOURCE_PATH}.exe")
        } else {
            SIDECAR_RESOURCE_PATH.to_string()
        };
        app.path()
            .resource_dir()
            .map(|dir| dir.join(&rel))
            .unwrap_or_else(|_| PathBuf::from(rel))
    }
}

pub struct SidecarManager {
    stdin_tx: Option<mpsc::Sender<String>>,
    is_alive: bool,
}

impl SidecarManager {
    pub fn new() -> Self {
        Self {
            stdin_tx: None,
            is_alive: false,
        }
    }

    /// Spawn the Python sidecar process.
    ///
    /// Launch mode is resolved by [`SidecarLaunch::resolve`]:
    ///
    /// * `JWST_SIDECAR_BIN=/path/to/jwst-pipeline` -- run that exact binary
    ///   (useful for testing a PyInstaller build inside `tauri dev`).
    /// * `JWST_DEV_SIDECAR=1` -- run `python3 -m jwst_pipeline` from the
    ///   source tree (`JWST_SIDECAR_DIR`, default `../sidecar`), even in a
    ///   release build.  `JWST_DEV_SIDECAR=0` forces the bundled binary even
    ///   in a debug build.
    /// * otherwise debug builds use Python and release builds use the
    ///   bundled sidecar `jwst-pipeline` that sits next to the app executable
    ///   (produced by `scripts/build_sidecar.sh` and `tauri build`).
    pub fn spawn(&mut self, app: &AppHandle) -> Result<(), AppError> {
        let launch = SidecarLaunch::resolve(app);

        let (mut rx, child) = match &launch {
            SidecarLaunch::Python { python, sidecar_dir } => {
                info!(
                    "Spawning sidecar in dev mode: {} -m jwst_pipeline (cwd {})",
                    python,
                    sidecar_dir.display()
                );
                if !sidecar_dir.join("jwst_pipeline").is_dir() {
                    return Err(AppError::Sidecar(format!(
                        "Dev sidecar source tree not found: {} has no jwst_pipeline/ package. \
                         Set JWST_SIDECAR_DIR to the sidecar directory, or unset JWST_DEV_SIDECAR \
                         to use the bundled binary.",
                        sidecar_dir.display()
                    )));
                }
                app.shell()
                    .command(python)
                    .args(["-m", "jwst_pipeline"])
                    .current_dir(sidecar_dir)
                    .spawn()
                    .map_err(|e| {
                        AppError::Sidecar(format!(
                            "Failed to spawn dev sidecar `{} -m jwst_pipeline` in {}: {}. \
                             Is Python 3.11+ on PATH with the sidecar dependencies installed \
                             (pip install -e \"sidecar[dev]\")? Set JWST_PYTHON to pick an interpreter.",
                            python,
                            sidecar_dir.display(),
                            e
                        ))
                    })?
            }
            SidecarLaunch::Binary { path, bundled } => {
                info!(
                    "Spawning {} sidecar binary: {}",
                    if *bundled { "bundled" } else { "JWST_SIDECAR_BIN" },
                    path.display()
                );
                if !path.is_file() {
                    return Err(AppError::Sidecar(Self::missing_binary_message(path, *bundled)));
                }
                Self::ensure_executable(path);
                let command = app.shell().command(path);
                command.spawn().map_err(|e| {
                    AppError::Sidecar(format!(
                        "Failed to spawn sidecar binary at {}: {}. \
                         Check that the file is executable for this CPU architecture; \
                         rebuild it with scripts/build_sidecar.sh, point JWST_SIDECAR_BIN at a \
                         working build, or set JWST_DEV_SIDECAR=1 to run from the Python source tree.",
                        path.display(),
                        e
                    ))
                })?
            }
        };

        // Set up stdin writer channel
        let (tx, mut stdin_rx) = mpsc::channel::<String>(64);
        self.stdin_tx = Some(tx);
        self.is_alive = true;

        // Stdin writer task
        let mut child_stdin = child;
        async_runtime::spawn(async move {
            while let Some(msg) = stdin_rx.recv().await {
                if let Err(e) = child_stdin.write(msg.as_bytes()) {
                    error!("Failed to write to sidecar stdin: {}", e);
                    break;
                }
            }
        });

        // Stdout/stderr reader task
        let app_handle = app.clone();
        async_runtime::spawn(async move {
            while let Some(event) = rx.recv().await {
                match event {
                    CommandEvent::Stdout(line) => {
                        let line_str = String::from_utf8_lossy(&line);
                        let line_str = line_str.trim();
                        if line_str.is_empty() {
                            continue;
                        }
                        match serde_json::from_str::<SidecarResponse>(line_str) {
                            Ok(response) => {
                                // Forward to frontend as Tauri events
                                match &response.payload {
                                    crate::sidecar::protocol::SidecarPayload::Progress {
                                        stage,
                                        fraction,
                                        message,
                                    } => {
                                        let _ = app_handle.emit(
                                            "pipeline:stage-update",
                                            serde_json::json!({
                                                "stageName": stage,
                                                "status": "running",
                                                "progress": fraction,
                                                "message": message,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::StageComplete {
                                        stage,
                                        result,
                                        duration_secs,
                                    } => {
                                        let _ = app_handle.emit(
                                            "pipeline:stage-update",
                                            serde_json::json!({
                                                "stageName": stage,
                                                "status": "completed",
                                                "progress": 1.0,
                                                "message": format!("Completed in {:.1}s", duration_secs),
                                                "result": result,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::Log {
                                        level,
                                        message,
                                    } => {
                                        let _ = app_handle.emit(
                                            "pipeline:log",
                                            serde_json::json!({
                                                "level": level,
                                                "message": message,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::Error {
                                        stage,
                                        message,
                                        traceback: _,
                                    } => {
                                        // Errors outside a stage come from one-shot requests
                                        // (QueryMAST, ListKnownFields, ...). Give them their own
                                        // event so the requesting panel can stop its spinner.
                                        if stage.is_none() {
                                            let _ = app_handle.emit(
                                                "sidecar:error",
                                                serde_json::json!({
                                                    "requestId": response.id,
                                                    "message": message,
                                                }),
                                            );
                                        }
                                        let _ = app_handle.emit(
                                            "pipeline:stage-update",
                                            serde_json::json!({
                                                "stageName": stage,
                                                "status": "failed",
                                                "progress": 0.0,
                                                "message": message,
                                            }),
                                        );
                                        error!(
                                            "Sidecar error [{}]: {}",
                                            stage.as_deref().unwrap_or("unknown"),
                                            message
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::PipelineComplete {
                                        summary,
                                    } => {
                                        let _ =
                                            app_handle.emit("pipeline:complete", summary.clone());
                                    }
                                    crate::sidecar::protocol::SidecarPayload::FilterProgress {
                                        filter,
                                        status,
                                        bytes_done,
                                        bytes_total,
                                    } => {
                                        let _ = app_handle.emit(
                                            "download:filter-progress",
                                            serde_json::json!({
                                                "filter": filter,
                                                "status": status,
                                                "bytesDone": bytes_done,
                                                "bytesTotal": bytes_total,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::QueryResult {
                                        observations,
                                        programs,
                                        total_size_gb,
                                        target,
                                        cached,
                                        cached_at,
                                    } => {
                                        let _ = app_handle.emit(
                                            "mast:query-result",
                                            serde_json::json!({
                                                "observations": observations,
                                                "programs": programs,
                                                "totalSizeGb": total_size_gb,
                                                "target": target,
                                                "cached": cached,
                                                "cachedAt": cached_at,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::DownloadManifest {
                                        downloads,
                                        workspace,
                                    } => {
                                        let _ = app_handle.emit(
                                            "download:manifest",
                                            serde_json::json!({
                                                "downloads": downloads,
                                                "workspace": workspace,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::KnownFieldsList {
                                        fields,
                                    } => {
                                        let _ = app_handle.emit(
                                            "field:known-fields",
                                            fields.clone(),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::ReferenceCatalogStatus {
                                        field,
                                        catalogs,
                                        mosaics,
                                    } => {
                                        let _ = app_handle.emit(
                                            "catalog:ref-status",
                                            serde_json::json!({
                                                "field": field,
                                                "catalogs": catalogs,
                                                "mosaics": mosaics,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::MastMissionsResult {
                                        total_observations,
                                        programs,
                                    } => {
                                        let _ = app_handle.emit(
                                            "mast:missions-result",
                                            serde_json::json!({
                                                "totalObservations": total_observations,
                                                "programs": programs,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::CatalogDownloadProgress {
                                        catalog,
                                        status,
                                        detail,
                                    } => {
                                        let _ = app_handle.emit(
                                            "catalog:download-progress",
                                            serde_json::json!({
                                                "catalog": catalog,
                                                "status": status,
                                                "detail": detail,
                                            }),
                                        );
                                    }
                                    crate::sidecar::protocol::SidecarPayload::Ack { command } => {
                                        info!("Sidecar acknowledged: {}", command);
                                        if command == "Status" && response.id == STARTUP_PING_ID {
                                            info!("Sidecar ready: answered the startup Status ping");
                                            let state = app_handle.state::<AppState>();
                                            let mut status = state.sidecar.lock().unwrap();
                                            status.alive = true;
                                            status.ready = true;
                                            status.message = None;
                                            let _ = app_handle.emit("sidecar:health", status.clone());
                                        }
                                    }
                                }
                            }
                            Err(e) => {
                                warn!("Failed to parse sidecar stdout: {} (line: {})", e, line_str);
                            }
                        }
                    }
                    CommandEvent::Stderr(line) => {
                        let line_str = String::from_utf8_lossy(&line);
                        let line_str = line_str.trim();
                        if !line_str.is_empty() {
                            let _ = app_handle.emit(
                                "pipeline:log",
                                serde_json::json!({
                                    "level": "debug",
                                    "message": line_str,
                                }),
                            );
                        }
                    }
                    CommandEvent::Error(err) => {
                        error!("Sidecar process error: {}", err);
                        Self::mark_dead(&app_handle, format!("Sidecar process error: {}", err));
                    }
                    CommandEvent::Terminated(status) => {
                        warn!("Sidecar terminated with status: {:?}", status);
                        Self::mark_dead(&app_handle, format!("Sidecar exited with {:?}", status));
                    }
                    _ => {}
                }
            }
        });

        Ok(())
    }

    fn missing_binary_message(path: &Path, bundled: bool) -> String {
        if bundled {
            format!(
                "Bundled pipeline sidecar not found at {}. The app was built without the \
                 `jwst-pipeline` external binary: run scripts/build_sidecar.sh (which writes \
                 src-tauri/binaries/jwst-pipeline-<target-triple>) before `tauri build`. \
                 To work around it, set JWST_SIDECAR_BIN=/path/to/jwst-pipeline or \
                 JWST_DEV_SIDECAR=1 (runs python3 -m jwst_pipeline from the source tree).",
                path.display()
            )
        } else {
            format!(
                "JWST_SIDECAR_BIN points to {} but no such file exists.",
                path.display()
            )
        }
    }


    /// Resource copies can lose the executable bit; restore it before spawning.
    fn ensure_executable(path: &Path) {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(meta) = std::fs::metadata(path) {
                let mode = meta.permissions().mode();
                if mode & 0o111 == 0 {
                    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode | 0o755));
                }
            }
        }
        #[cfg(not(unix))]
        {
            let _ = path;
        }
    }

    /// Record a dead sidecar in `AppState` and tell the UI.
    fn mark_dead(app: &AppHandle, message: String) {
        let state = app.state::<AppState>();
        let mut status = state.sidecar.lock().unwrap();
        status.alive = false;
        status.ready = false;
        status.message = Some(message);
        let _ = app.emit("sidecar:health", status.clone());
    }

    /// The NDJSON line for the startup `Status` ping.
    pub fn startup_ping() -> String {
        let request = crate::sidecar::protocol::SidecarRequest {
            id: STARTUP_PING_ID.to_string(),
            command: crate::sidecar::protocol::SidecarCommand::Status,
        };
        let mut msg = serde_json::to_string(&request).expect("static request serializes");
        msg.push('\n');
        msg
    }

    /// Human-readable description of how the sidecar will be launched.
    pub fn describe_launch(app: &AppHandle) -> String {
        match SidecarLaunch::resolve(app) {
            SidecarLaunch::Python { python, sidecar_dir } => {
                format!("{} -m jwst_pipeline (cwd {})", python, sidecar_dir.display())
            }
            SidecarLaunch::Binary { path, bundled } => {
                format!("{} {}", if bundled { "bundled binary" } else { "JWST_SIDECAR_BIN" }, path.display())
            }
        }
    }

    /// Take the stdin sender out of the manager (to store in AppState)
    pub fn take_sender(&mut self) -> Option<mpsc::Sender<String>> {
        self.stdin_tx.take()
    }

}
