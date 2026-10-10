use serde::Serialize;
use thiserror::Error;

#[derive(Debug, Error, Serialize)]
pub enum AppError {
    #[error("Sidecar error: {0}")]
    Sidecar(String),

    #[error("IO error: {0}")]
    Io(String),

    #[error("Config error: {0}")]
    Config(String),
}

impl From<std::io::Error> for AppError {
    fn from(e: std::io::Error) -> Self {
        AppError::Io(e.to_string())
    }
}

impl From<serde_json::Error> for AppError {
    fn from(e: serde_json::Error) -> Self {
        AppError::Config(e.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn io_error_converts_to_io_variant_with_message() {
        let io = std::io::Error::new(std::io::ErrorKind::NotFound, "no such file");
        let err: AppError = io.into();
        assert!(matches!(err, AppError::Io(ref m) if m == "no such file"));
        assert_eq!(err.to_string(), "IO error: no such file");
    }

    #[test]
    fn serde_json_error_converts_to_config_variant() {
        let bad = serde_json::from_str::<serde_json::Value>("{not json");
        let err: AppError = bad.unwrap_err().into();
        match err {
            AppError::Config(msg) => assert!(!msg.is_empty()),
            other => panic!("expected Config, got {other:?}"),
        }
    }

    #[test]
    fn question_mark_operator_uses_the_from_impls() {
        fn read() -> Result<String, AppError> {
            let text = std::fs::read_to_string("/definitely/not/a/real/path")?;
            Ok(text)
        }
        fn parse() -> Result<serde_json::Value, AppError> {
            Ok(serde_json::from_str("[")?)
        }
        assert!(matches!(read(), Err(AppError::Io(_))));
        assert!(matches!(parse(), Err(AppError::Config(_))));
    }

    #[test]
    fn display_prefixes_each_variant() {
        assert_eq!(AppError::Sidecar("x".into()).to_string(), "Sidecar error: x");
        assert_eq!(AppError::Config("y".into()).to_string(), "Config error: y");
    }

    #[test]
    fn serializes_as_externally_tagged_for_the_frontend() {
        // Tauri sends the serialized error to `invoke().catch(...)`.
        let v = serde_json::to_value(AppError::Sidecar("Sidecar not running".into())).unwrap();
        assert_eq!(v, serde_json::json!({ "Sidecar": "Sidecar not running" }));
        let v = serde_json::to_value(AppError::Io("eperm".into())).unwrap();
        assert_eq!(v, serde_json::json!({ "Io": "eperm" }));
    }
}
