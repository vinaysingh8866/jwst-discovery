use crate::error::AppError;
use crate::sidecar::protocol::{SidecarCommand, SidecarRequest};
use crate::state::{AppState, PipelineState};
use base64::{engine::general_purpose::STANDARD, Engine};
use std::path::PathBuf;
use tauri::State;
use uuid::Uuid;

#[tauri::command]
pub async fn start_pipeline(
    workspace: String,
    config: serde_json::Value,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    // Update pipeline state
    {
        let mut pipeline = state.pipeline.lock().unwrap();
        pipeline.running = true;
        pipeline.current_stage = Some("ingest".to_string());
        pipeline.progress = 0.0;
        pipeline.workspace = Some(workspace.clone());
    }

    // Send command to sidecar
    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::RunPipeline { workspace, config },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');

        let tx = tx.clone();
        tokio::spawn(async move {
            let _ = tx.send(msg).await;
        });
    } else {
        return Err(AppError::Sidecar(
            "Sidecar not running. Please restart the application.".to_string(),
        ));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn run_stage(
    stage: String,
    workspace: String,
    config: serde_json::Value,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    {
        let mut pipeline = state.pipeline.lock().unwrap();
        pipeline.running = true;
        pipeline.current_stage = Some(stage.clone());
        pipeline.progress = 0.0;
    }

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::RunStage {
            stage,
            workspace,
            config,
        },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');

        let tx = tx.clone();
        tokio::spawn(async move {
            let _ = tx.send(msg).await;
        });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn cancel_pipeline(state: State<'_, AppState>) -> Result<(), AppError> {
    let request = SidecarRequest {
        id: Uuid::new_v4().to_string(),
        command: SidecarCommand::Cancel,
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');

        let tx = tx.clone();
        tokio::spawn(async move {
            let _ = tx.send(msg).await;
        });
    }

    {
        let mut pipeline = state.pipeline.lock().unwrap();
        pipeline.running = false;
    }

    Ok(())
}

#[tauri::command]
pub async fn query_mast(
    config: serde_json::Value,
    force_refresh: bool,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::QueryMAST { config, force_refresh },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');

        let tx = tx.clone();
        tokio::spawn(async move {
            let _ = tx.send(msg).await;
        });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn check_downloads(
    workspace: String,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::CheckDownloads { workspace },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');

        let tx = tx.clone();
        tokio::spawn(async move {
            let _ = tx.send(msg).await;
        });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn get_pipeline_status(
    state: State<'_, AppState>,
) -> Result<PipelineState, AppError> {
    let pipeline = state.pipeline.lock().unwrap();
    Ok(pipeline.clone())
}

/// Read a text file from the workspace (for JSON, CSV, etc.)
#[tauri::command]
pub async fn read_text_file(path: String) -> Result<String, AppError> {
    std::fs::read_to_string(&path)
        .map_err(|e| AppError::Io(format!("{}: {}", path, e)))
}

/// Read a binary file and return as base64 (for images)
#[tauri::command]
pub async fn read_binary_file_base64(path: String) -> Result<String, AppError> {
    let bytes = std::fs::read(&path)
        .map_err(|e| AppError::Io(format!("{}: {}", path, e)))?;
    Ok(STANDARD.encode(&bytes))
}

/// Root directory holding one workspace per field.
///
/// `JWST_WORKSPACE_ROOT` overrides the default `~/.jwst-discovery/workspaces`
/// (the same variable the `jwst-field` CLI honours), so the app and the CLI
/// always look in the same place. A leading `~` is expanded.
pub fn workspace_root() -> Result<std::path::PathBuf, AppError> {
    if let Some(raw) = std::env::var_os("JWST_WORKSPACE_ROOT").filter(|v| !v.is_empty()) {
        let raw = raw.to_string_lossy().to_string();
        if let Some(rest) = raw.strip_prefix("~/") {
            let home = dirs::home_dir().ok_or_else(|| AppError::Io("Cannot find home dir".into()))?;
            return Ok(home.join(rest));
        }
        return Ok(std::path::PathBuf::from(raw));
    }
    let home = dirs::home_dir().ok_or_else(|| AppError::Io("Cannot find home dir".into()))?;
    Ok(home.join(".jwst-discovery").join("workspaces"))
}

/// The workspace root as a string, for the frontend's path resolver.
#[tauri::command]
pub fn get_workspace_root() -> Result<String, AppError> {
    Ok(workspace_root()?.to_string_lossy().to_string())
}

/// List available workspaces (subdirectories of the workspace root)
#[tauri::command]
pub async fn list_workspaces() -> Result<Vec<serde_json::Value>, AppError> {
    let ws_root = workspace_root()?;
    if !ws_root.is_dir() {
        return Ok(vec![]);
    }
    let mut workspaces = Vec::new();
    for entry in std::fs::read_dir(&ws_root)
        .map_err(|e| AppError::Io(format!("{}: {}", ws_root.display(), e)))?
    {
        let entry = match entry {
            Ok(e) => e,
            Err(_) => continue,
        };
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        let has_report = path.join("report").join("candidates.json").exists();
        let has_raw = path.join("raw").is_dir();
        let n_raw = if has_raw {
            std::fs::read_dir(path.join("raw"))
                .map(|rd| rd.filter_map(|e| e.ok()).filter(|e| {
                    e.path().extension().map(|ext| ext == "fits").unwrap_or(false)
                }).count())
                .unwrap_or(0)
        } else {
            0
        };
        workspaces.push(serde_json::json!({
            "name": name,
            "path": path.to_string_lossy(),
            "has_report": has_report,
            "has_raw": has_raw,
            "n_fits": n_raw,
        }));
    }
    workspaces.sort_by(|a, b| a["name"].as_str().cmp(&b["name"].as_str()));
    Ok(workspaces)
}

#[tauri::command]
pub async fn list_known_fields(
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::ListKnownFields,
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');
        let tx = tx.clone();
        tokio::spawn(async move { let _ = tx.send(msg).await; });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn check_reference_catalogs(
    field: String,
    workspace: String,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::CheckReferenceCatalogs { field, workspace },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');
        let tx = tx.clone();
        tokio::spawn(async move { let _ = tx.send(msg).await; });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn download_reference_catalogs(
    field: String,
    force: bool,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::DownloadReferenceCatalogs { field, force },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');
        let tx = tx.clone();
        tokio::spawn(async move { let _ = tx.send(msg).await; });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn download_hlsp_mosaics(
    field: String,
    workspace: String,
    filters: Option<Vec<String>>,
    force: bool,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::DownloadHLSPMosaics { field, workspace, filters, force },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');
        let tx = tx.clone();
        tokio::spawn(async move { let _ = tx.send(msg).await; });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

#[tauri::command]
pub async fn query_mast_missions(
    config: serde_json::Value,
    state: State<'_, AppState>,
) -> Result<String, AppError> {
    let request_id = Uuid::new_v4().to_string();

    let request = SidecarRequest {
        id: request_id.clone(),
        command: SidecarCommand::QueryMastMissions { config },
    };

    let tx = state.sidecar_tx.lock().unwrap();
    if let Some(tx) = tx.as_ref() {
        let mut msg = serde_json::to_string(&request)
            .map_err(|e| AppError::Sidecar(format!("Serialize error: {}", e)))?;
        msg.push('\n');
        let tx = tx.clone();
        tokio::spawn(async move { let _ = tx.send(msg).await; });
    } else {
        return Err(AppError::Sidecar("Sidecar not running".to_string()));
    }

    Ok(request_id)
}

/// List files in a workspace subdirectory
#[tauri::command]
pub async fn list_directory(path: String) -> Result<Vec<String>, AppError> {
    let dir = PathBuf::from(&path);
    if !dir.is_dir() {
        return Ok(vec![]);
    }
    let mut entries = Vec::new();
    for entry in std::fs::read_dir(&dir)
        .map_err(|e| AppError::Io(format!("{}: {}", path, e)))?
        .flatten()
    {
        if let Some(name) = entry.file_name().to_str() {
            entries.push(name.to_string());
        }
    }
    entries.sort();
    Ok(entries)
}

/// Current sidecar lifecycle state (spawned? answered the startup ping? last error?).
/// The UI calls this on mount because `sidecar:health` events emitted before the
/// webview loaded are lost.
#[tauri::command]
pub fn sidecar_status(state: State<'_, AppState>) -> crate::state::SidecarStatus {
    state.sidecar.lock().unwrap().clone()
}

#[cfg(test)]
mod workspace_root_tests {
    use super::workspace_root;

    #[test]
    fn env_override_wins_and_tilde_expands() {
        // Serialise env mutation across tests in this module.
        std::env::set_var("JWST_WORKSPACE_ROOT", "/data/jwst");
        assert_eq!(workspace_root().unwrap(), std::path::PathBuf::from("/data/jwst"));
        std::env::set_var("JWST_WORKSPACE_ROOT", "~/jwst-ws");
        let expanded = workspace_root().unwrap();
        assert!(expanded.ends_with("jwst-ws") && !expanded.to_string_lossy().contains('~'));
        std::env::remove_var("JWST_WORKSPACE_ROOT");
        assert!(workspace_root().unwrap().ends_with(".jwst-discovery/workspaces"));
    }
}
