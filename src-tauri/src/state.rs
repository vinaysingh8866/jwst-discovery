use serde::{Deserialize, Serialize};
use std::sync::Mutex;
use tokio::sync::mpsc;

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PipelineState {
    pub running: bool,
    pub current_stage: Option<String>,
    pub progress: f32,
    pub workspace: Option<String>,
}

/// Lifecycle of the Python sidecar as seen by the host. Mirrors the
/// `sidecar:health` event payload so the UI can query it after the fact.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct SidecarStatus {
    /// The process was spawned (it may still be starting up).
    pub alive: bool,
    /// The sidecar answered the startup `Status` ping over the protocol.
    pub ready: bool,
    /// How it was launched, e.g. "bundled binary …" or "python -m jwst_pipeline".
    pub launch: Option<String>,
    /// Last spawn / protocol error, if any.
    pub message: Option<String>,
}

#[derive(Default)]
pub struct AppState {
    pub pipeline: Mutex<PipelineState>,
    /// Sender for the sidecar's stdin; `None` until the sidecar has been spawned.
    pub sidecar_tx: Mutex<Option<mpsc::Sender<String>>>,
    pub sidecar: Mutex<SidecarStatus>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sidecar_status_serializes_like_the_health_event() {
        let s = SidecarStatus { alive: true, ready: true, launch: Some("bundled".into()), message: None };
        let v = serde_json::to_value(&s).unwrap();
        assert_eq!(v, serde_json::json!({"alive": true, "ready": true, "launch": "bundled", "message": null}));
        assert_eq!(serde_json::from_value::<SidecarStatus>(v).unwrap(), s);
    }

    #[test]
    fn default_status_is_not_alive() {
        assert_eq!(SidecarStatus::default(), SidecarStatus { alive: false, ready: false, launch: None, message: None });
    }
}
