use serde::{Deserialize, Serialize};

/// Message sent from Rust to the Python sidecar via stdin.
///
/// Wire format: `{"id": "<uuid>", "command": {"type": "<Variant>", "params": {...}}}`
/// (unit variants omit `params`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SidecarRequest {
    pub id: String,
    pub command: SidecarCommand,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", content = "params")]
pub enum SidecarCommand {
    RunStage {
        stage: String,
        workspace: String,
        config: serde_json::Value,
    },
    RunPipeline {
        workspace: String,
        config: serde_json::Value,
    },
    QueryMAST {
        config: serde_json::Value,
        force_refresh: bool,
    },
    CheckDownloads {
        workspace: String,
    },
    ListKnownFields,
    CheckReferenceCatalogs {
        field: String,
        workspace: String,
    },
    DownloadReferenceCatalogs {
        field: String,
        force: bool,
    },
    DownloadHLSPMosaics {
        field: String,
        workspace: String,
        filters: Option<Vec<String>>,
        force: bool,
    },
    QueryMastMissions {
        config: serde_json::Value,
    },
    Cancel,
    /// Understood by the sidecar; not yet issued by any Tauri command.
    #[allow(dead_code)]
    Status,
    /// Understood by the sidecar; not yet issued by any Tauri command.
    #[allow(dead_code)]
    Shutdown,
}

/// Message sent from Python sidecar to Rust via stdout.
///
/// Wire format: `{"id": "<request id>", "payload": {"type": "<Variant>", ...}}`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SidecarResponse {
    /// Correlation id of the originating request (`"unknown"` when unsolicited).
    /// The manager currently dispatches on payload type alone.
    #[allow(dead_code)]
    pub id: String,
    pub payload: SidecarPayload,
}

#[derive(Debug, Clone, PartialEq, Deserialize, Serialize)]
#[serde(tag = "type")]
pub enum SidecarPayload {
    StageComplete {
        stage: String,
        result: serde_json::Value,
        duration_secs: f64,
    },
    Progress {
        stage: String,
        fraction: f32,
        message: String,
    },
    Log {
        level: String,
        message: String,
    },
    Error {
        stage: Option<String>,
        message: String,
        traceback: Option<String>,
    },
    PipelineComplete {
        summary: serde_json::Value,
    },
    FilterProgress {
        filter: String,
        status: String,
        bytes_done: u64,
        bytes_total: u64,
    },
    QueryResult {
        observations: u32,
        #[serde(default)]
        programs: Vec<serde_json::Value>,
        #[serde(default)]
        total_size_gb: f64,
        #[serde(default)]
        target: String,
        #[serde(default)]
        cached: bool,
        #[serde(default)]
        cached_at: Option<String>,
    },
    DownloadManifest {
        downloads: serde_json::Value,
        workspace: String,
    },
    KnownFieldsList {
        fields: serde_json::Value,
    },
    ReferenceCatalogStatus {
        field: String,
        catalogs: serde_json::Value,
        #[serde(default)]
        mosaics: serde_json::Value,
    },
    MastMissionsResult {
        total_observations: u32,
        #[serde(default)]
        programs: Vec<serde_json::Value>,
    },
    CatalogDownloadProgress {
        catalog: String,
        status: String,
        detail: String,
    },
    Ack {
        command: String,
    },
}

#[cfg(test)]
mod tests {
    //! Round-trip tests pinned to the exact JSON shapes produced by
    //! `sidecar/jwst_pipeline/protocol.py` and consumed by `__main__.py`.
    use super::*;
    use serde_json::{json, Value};

    /// Deserialize `json`, re-serialize it, and assert both the typed value and
    /// the JSON are unchanged.
    fn roundtrip_response(json: Value) -> SidecarResponse {
        let parsed: SidecarResponse = serde_json::from_value(json.clone()).expect("deserialize");
        let reserialized = serde_json::to_value(&parsed).expect("serialize");
        assert_eq!(reserialized, json, "payload did not round-trip losslessly");
        let reparsed: SidecarResponse = serde_json::from_value(reserialized).unwrap();
        assert_eq!(reparsed, parsed);
        parsed
    }

    fn roundtrip_request(request: SidecarRequest, expected: Value) {
        let serialized = serde_json::to_value(&request).expect("serialize");
        assert_eq!(serialized, expected, "request JSON does not match the Python wire format");
        let parsed: SidecarRequest = serde_json::from_value(serialized).expect("deserialize");
        assert_eq!(parsed, request);
    }

    // ---- responses (Python -> Rust), shapes from protocol.py -----------------

    #[test]
    fn progress_matches_format_progress() {
        let r = roundtrip_response(json!({
            "id": "req-1",
            "payload": {"type": "Progress", "stage": "detect", "fraction": 0.25, "message": "Detecting sources"}
        }));
        assert_eq!(r.id, "req-1");
        match r.payload {
            SidecarPayload::Progress { stage, fraction, message } => {
                assert_eq!(stage, "detect");
                assert_eq!(fraction, 0.25);
                assert_eq!(message, "Detecting sources");
            }
            other => panic!("unexpected payload {other:?}"),
        }
    }

    #[test]
    fn stage_complete_matches_format_stage_complete() {
        let r = roundtrip_response(json!({
            "id": "req-2",
            "payload": {
                "type": "StageComplete",
                "stage": "measure",
                "result": {"n_sources": 1234, "filters": ["F200W", "F444W"]},
                "duration_secs": 12.5
            }
        }));
        match r.payload {
            SidecarPayload::StageComplete { stage, result, duration_secs } => {
                assert_eq!(stage, "measure");
                assert_eq!(result["n_sources"], 1234);
                assert_eq!(duration_secs, 12.5);
            }
            other => panic!("unexpected payload {other:?}"),
        }
    }

    #[test]
    fn log_matches_format_log() {
        let r = roundtrip_response(json!({
            "id": "req-3",
            "payload": {"type": "Log", "level": "warning", "message": "SIMBAD slow"}
        }));
        assert_eq!(
            r.payload,
            SidecarPayload::Log { level: "warning".into(), message: "SIMBAD slow".into() }
        );
    }

    #[test]
    fn filter_progress_matches_format_filter_progress() {
        let r = roundtrip_response(json!({
            "id": "req-4",
            "payload": {
                "type": "FilterProgress",
                "filter": "F200W",
                "status": "downloading",
                "bytes_done": 1048576,
                "bytes_total": 4194304
            }
        }));
        assert_eq!(
            r.payload,
            SidecarPayload::FilterProgress {
                filter: "F200W".into(),
                status: "downloading".into(),
                bytes_done: 1_048_576,
                bytes_total: 4_194_304,
            }
        );
    }

    #[test]
    fn error_with_stage_and_traceback() {
        let r = roundtrip_response(json!({
            "id": "req-5",
            "payload": {
                "type": "Error",
                "stage": "crossmatch",
                "message": "timeout",
                "traceback": "Traceback (most recent call last): ..."
            }
        }));
        assert_eq!(
            r.payload,
            SidecarPayload::Error {
                stage: Some("crossmatch".into()),
                message: "timeout".into(),
                traceback: Some("Traceback (most recent call last): ...".into()),
            }
        );
    }

    #[test]
    fn error_with_null_stage_and_traceback_as_emitted_by_format_error_defaults() {
        // format_error(request_id=None, ...) yields id "unknown" and null optionals.
        let r = roundtrip_response(json!({
            "id": "unknown",
            "payload": {"type": "Error", "stage": null, "message": "Unknown command type: Foo", "traceback": null}
        }));
        assert_eq!(r.id, "unknown");
        assert_eq!(
            r.payload,
            SidecarPayload::Error { stage: None, message: "Unknown command type: Foo".into(), traceback: None }
        );
    }

    #[test]
    fn pipeline_complete_carries_summary() {
        let r = roundtrip_response(json!({
            "id": "req-6",
            "payload": {"type": "PipelineComplete", "summary": {"stages_completed": 9}}
        }));
        assert!(matches!(r.payload, SidecarPayload::PipelineComplete { summary } if summary["stages_completed"] == 9));
    }

    #[test]
    fn query_result_fills_defaults_for_omitted_fields() {
        let minimal: SidecarResponse = serde_json::from_value(json!({
            "id": "q", "payload": {"type": "QueryResult", "observations": 3}
        }))
        .unwrap();
        assert_eq!(
            minimal.payload,
            SidecarPayload::QueryResult {
                observations: 3,
                programs: vec![],
                total_size_gb: 0.0,
                target: String::new(),
                cached: false,
                cached_at: None,
            }
        );

        roundtrip_response(json!({
            "id": "q",
            "payload": {
                "type": "QueryResult",
                "observations": 3,
                "programs": [{"program_id": "1210"}],
                "total_size_gb": 1.5,
                "target": "JADES",
                "cached": true,
                "cached_at": "2026-01-01T00:00:00Z"
            }
        }));
    }

    #[test]
    fn remaining_response_variants_round_trip() {
        roundtrip_response(json!({
            "id": "a",
            "payload": {"type": "DownloadManifest", "downloads": {"1210:F200W": {"exists": true}}, "workspace": "/ws"}
        }));
        roundtrip_response(json!({
            "id": "b",
            "payload": {"type": "KnownFieldsList", "fields": [{"field": "abell2744"}]}
        }));
        roundtrip_response(json!({
            "id": "c",
            "payload": {"type": "ReferenceCatalogStatus", "field": "abell2744", "catalogs": [], "mosaics": []}
        }));
        roundtrip_response(json!({
            "id": "d",
            "payload": {"type": "MastMissionsResult", "total_observations": 7, "programs": []}
        }));
        roundtrip_response(json!({
            "id": "e",
            "payload": {"type": "CatalogDownloadProgress", "catalog": "dja_abell2744", "status": "ok", "detail": "1234 rows"}
        }));
        roundtrip_response(json!({ "id": "f", "payload": {"type": "Ack", "command": "Cancel"} }));
    }

    #[test]
    fn reference_catalog_status_defaults_mosaics_when_absent() {
        let r: SidecarResponse = serde_json::from_value(json!({
            "id": "c", "payload": {"type": "ReferenceCatalogStatus", "field": "x", "catalogs": []}
        }))
        .unwrap();
        assert!(matches!(r.payload, SidecarPayload::ReferenceCatalogStatus { mosaics: Value::Null, .. }));
    }

    #[test]
    fn unknown_payload_type_is_rejected() {
        let err = serde_json::from_value::<SidecarResponse>(json!({
            "id": "z", "payload": {"type": "NotAThing"}
        }));
        assert!(err.is_err());
    }

    // ---- requests (Rust -> Python), shapes consumed by __main__.py -----------

    #[test]
    fn run_stage_request_uses_type_and_params_envelope() {
        roundtrip_request(
            SidecarRequest {
                id: "r1".into(),
                command: SidecarCommand::RunStage {
                    stage: "detect".into(),
                    workspace: "/ws/field".into(),
                    config: json!({"detection_threshold": 2.0}),
                },
            },
            json!({
                "id": "r1",
                "command": {
                    "type": "RunStage",
                    "params": {"stage": "detect", "workspace": "/ws/field", "config": {"detection_threshold": 2.0}}
                }
            }),
        );
    }

    #[test]
    fn run_pipeline_and_query_mast_requests() {
        roundtrip_request(
            SidecarRequest {
                id: "r2".into(),
                command: SidecarCommand::RunPipeline { workspace: "/ws".into(), config: json!({}) },
            },
            json!({"id": "r2", "command": {"type": "RunPipeline", "params": {"workspace": "/ws", "config": {}}}}),
        );
        roundtrip_request(
            SidecarRequest {
                id: "r3".into(),
                command: SidecarCommand::QueryMAST { config: json!({"target_field": "X"}), force_refresh: true },
            },
            json!({
                "id": "r3",
                "command": {"type": "QueryMAST", "params": {"config": {"target_field": "X"}, "force_refresh": true}}
            }),
        );
    }

    #[test]
    fn catalog_and_mosaic_requests() {
        roundtrip_request(
            SidecarRequest { id: "r4".into(), command: SidecarCommand::CheckDownloads { workspace: "/ws".into() } },
            json!({"id": "r4", "command": {"type": "CheckDownloads", "params": {"workspace": "/ws"}}}),
        );
        roundtrip_request(
            SidecarRequest {
                id: "r5".into(),
                command: SidecarCommand::CheckReferenceCatalogs { field: "abell2744".into(), workspace: "/ws".into() },
            },
            json!({
                "id": "r5",
                "command": {"type": "CheckReferenceCatalogs", "params": {"field": "abell2744", "workspace": "/ws"}}
            }),
        );
        roundtrip_request(
            SidecarRequest {
                id: "r6".into(),
                command: SidecarCommand::DownloadReferenceCatalogs { field: "abell2744".into(), force: false },
            },
            json!({
                "id": "r6",
                "command": {"type": "DownloadReferenceCatalogs", "params": {"field": "abell2744", "force": false}}
            }),
        );
        roundtrip_request(
            SidecarRequest {
                id: "r7".into(),
                command: SidecarCommand::DownloadHLSPMosaics {
                    field: "abell2744".into(),
                    workspace: "/ws".into(),
                    filters: None,
                    force: false,
                },
            },
            json!({
                "id": "r7",
                "command": {
                    "type": "DownloadHLSPMosaics",
                    "params": {"field": "abell2744", "workspace": "/ws", "filters": null, "force": false}
                }
            }),
        );
        roundtrip_request(
            SidecarRequest {
                id: "r8".into(),
                command: SidecarCommand::DownloadHLSPMosaics {
                    field: "abell2744".into(),
                    workspace: "/ws".into(),
                    filters: Some(vec!["F200W".into()]),
                    force: true,
                },
            },
            json!({
                "id": "r8",
                "command": {
                    "type": "DownloadHLSPMosaics",
                    "params": {"field": "abell2744", "workspace": "/ws", "filters": ["F200W"], "force": true}
                }
            }),
        );
        roundtrip_request(
            SidecarRequest { id: "r9".into(), command: SidecarCommand::QueryMastMissions { config: json!({}) } },
            json!({"id": "r9", "command": {"type": "QueryMastMissions", "params": {"config": {}}}}),
        );
    }

    #[test]
    fn unit_variants_serialize_without_params() {
        for (cmd, name) in [
            (SidecarCommand::ListKnownFields, "ListKnownFields"),
            (SidecarCommand::Cancel, "Cancel"),
            (SidecarCommand::Status, "Status"),
            (SidecarCommand::Shutdown, "Shutdown"),
        ] {
            roundtrip_request(
                SidecarRequest { id: "u".into(), command: cmd },
                json!({"id": "u", "command": {"type": name}}),
            );
        }
    }

    #[test]
    fn request_line_is_single_line_json_for_the_ndjson_transport() {
        let request = SidecarRequest {
            id: "line".into(),
            command: SidecarCommand::RunStage {
                stage: "ingest".into(),
                workspace: "/ws".into(),
                config: json!({"filters": ["F090W", "F150W"]}),
            },
        };
        let line = serde_json::to_string(&request).unwrap();
        assert!(!line.contains('\n'));
        let back: SidecarRequest = serde_json::from_str(&line).unwrap();
        assert_eq!(back, request);
    }
}
