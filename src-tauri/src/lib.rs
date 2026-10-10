mod commands;
mod error;
mod sidecar;
mod state;

use state::AppState;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    env_logger::init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            commands::pipeline_commands::start_pipeline,
            commands::pipeline_commands::run_stage,
            commands::pipeline_commands::cancel_pipeline,
            commands::pipeline_commands::get_pipeline_status,
            commands::pipeline_commands::query_mast,
            commands::pipeline_commands::check_downloads,
            commands::pipeline_commands::read_text_file,
            commands::pipeline_commands::read_binary_file_base64,
            commands::pipeline_commands::list_directory,
            commands::pipeline_commands::list_workspaces,
            commands::pipeline_commands::list_known_fields,
            commands::pipeline_commands::check_reference_catalogs,
            commands::pipeline_commands::download_reference_catalogs,
            commands::pipeline_commands::download_hlsp_mosaics,
            commands::pipeline_commands::query_mast_missions,
            commands::pipeline_commands::sidecar_status,
            commands::pipeline_commands::get_workspace_root,
        ])
        .setup(|app| {
            // Spawn the Python sidecar and store its stdin sender in AppState
            let mut manager = sidecar::manager::SidecarManager::new();
            let launch = sidecar::manager::SidecarManager::describe_launch(app.handle());
            match manager.spawn(app.handle()) {
                Ok(()) => {
                    log::info!("Python sidecar started successfully ({})", launch);
                    let state = app.state::<AppState>();
                    {
                        let mut status = state.sidecar.lock().unwrap();
                        status.alive = true;
                        status.ready = false;
                        status.launch = Some(launch);
                        status.message = None;
                    }
                    // Store the stdin sender so commands can reach the sidecar,
                    // then ping it: the Ack flips `ready` and emits sidecar:health.
                    if let Some(tx) = manager.take_sender() {
                        if let Err(e) = tx.try_send(sidecar::manager::SidecarManager::startup_ping()) {
                            log::warn!("Could not queue the startup Status ping: {}", e);
                        }
                        *state.sidecar_tx.lock().unwrap() = Some(tx);
                        log::info!("Sidecar stdin channel stored in AppState; startup Status ping queued");
                    }
                }
                Err(e) => {
                    log::warn!(
                        "Failed to start Python sidecar: {}. Pipeline features will be unavailable.",
                        e
                    );
                    let state = app.state::<AppState>();
                    let mut status = state.sidecar.lock().unwrap();
                    status.alive = false;
                    status.ready = false;
                    status.launch = Some(launch);
                    status.message = Some(e.to_string());
                }
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
