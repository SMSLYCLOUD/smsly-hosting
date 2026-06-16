//! Bridge from Celery's task format to rust_twin's `Task` enum.
//!
//! Celery v5 (with `content_type=application/json` and `content-encoding=utf-8`)
//! sends messages of the form:
//!
//! ```json
//! {
//!   "body": "<json-string-of-task-message>",
//!   "content-encoding": "utf-8",
//!   "content-type": "application/json",
//!   "headers": { "task": "apps.module.func", ... },
//!   "properties": { "delivery_tag": ..., "delivery_info": { ... }, ... }
//! }
//! ```
//!
//! The body is a JSON object like:
//! ```json
//! { "id": "...", "task": "apps.deployments.tasks.smart_deploy",
//!   "args": [...], "kwargs": { ... } }
//! ```
//!
//! We map the Celery task name (from `headers.task`) to a `rust_twin` `Task` variant.
//! Unknown task names are logged and acknowledged (skipped). Native `rust_twin` payloads
//! (sent directly to the native queue) are passed through unchanged.

use crate::tasks::Task;
use serde::Deserialize;
use tracing::warn;

#[derive(Debug, Deserialize)]
pub struct CeleryEnvelope {
    pub body: String,
    pub headers: CeleryHeaders,
}

#[derive(Debug, Deserialize)]
pub struct CeleryHeaders {
    pub task: String,
}

#[derive(Debug, Deserialize)]
pub struct CeleryBody {
    #[serde(default)]
    pub args: Vec<serde_json::Value>,
    #[serde(default)]
    pub kwargs: std::collections::HashMap<String, serde_json::Value>,
}

/// Try to convert a raw Redis payload into a `rust_twin` `Task`.
///
/// Returns `Ok(Some(task))` if the payload is a Celery message that maps to a known
/// task OR if it is a native `rust_twin` task envelope.
/// Returns `Ok(None)` if the payload is a Celery message for an unknown task (ack & skip).
/// Returns `Err` if the payload is malformed (neither format).
pub fn parse_celery_message(raw_payload: &str) -> Result<Option<Task>, String> {
    // First, try to parse as a Celery envelope.
    if let Ok(envelope) = serde_json::from_str::<CeleryEnvelope>(raw_payload) {
        let body: CeleryBody = serde_json::from_str(&envelope.body)
            .map_err(|e| format!("Celery body parse: {}", e))?;

        return match envelope.headers.task.as_str() {
            "apps.deployments.tasks.smart_deploy" => {
                let project_id = uuid_arg(&body, 0, "project_id")
                    .ok_or_else(|| "smart_deploy: missing project_id".to_string())?;
                let deployment_id = uuid_arg(&body, 1, "deployment_id")
                    .ok_or_else(|| "smart_deploy: missing deployment_id".to_string())?;
                let commit_hash = string_arg(&body, 2, "commit_hash")
                    .ok_or_else(|| "smart_deploy: missing commit_hash".to_string())?;
                Ok(Some(Task::SmartDeploy {
                    project_id,
                    deployment_id,
                    commit_hash,
                }))
            }
            "apps.addons.tasks.provision_addon" => {
                let addon_id = body
                    .kwargs
                    .get("addon_id")
                    .and_then(|v| v.as_str())
                    .and_then(|s| uuid::Uuid::parse_str(s).ok())
                    .ok_or_else(|| "provision_addon: missing addon_id".to_string())?;
                let addon_type = body
                    .kwargs
                    .get("addon_type")
                    .and_then(|v| v.as_str())
                    .ok_or_else(|| "provision_addon: missing addon_type".to_string())?
                    .to_string();
                Ok(Some(Task::ProvisionAddon { addon_id, addon_type }))
            }
            "apps.billing.tasks.collect_usage" => {
                let owner_id = body
                    .kwargs
                    .get("owner_id")
                    .and_then(|v| v.as_i64())
                    .ok_or_else(|| "collect_usage: missing owner_id".to_string())?
                    as i32;
                Ok(Some(Task::CollectUsage { owner_id }))
            }
            other => {
                warn!("Unknown Celery task, skipping: {}", other);
                Ok(None)
            }
        };
    }

    // Not a Celery envelope — try as a native rust_twin message.
    match serde_json::from_str::<Task>(raw_payload) {
        Ok(task) => Ok(Some(task)),
        Err(e) => Err(format!(
            "payload is neither Celery nor native rust_twin format: {}",
            e
        )),
    }
}

fn uuid_arg(body: &CeleryBody, idx: usize, kw: &str) -> Option<uuid::Uuid> {
    body.args
        .get(idx)
        .and_then(|v| v.as_str())
        .and_then(|s| uuid::Uuid::parse_str(s).ok())
        .or_else(|| {
            body.kwargs
                .get(kw)
                .and_then(|v| v.as_str())
                .and_then(|s| uuid::Uuid::parse_str(s).ok())
        })
}

fn string_arg(body: &CeleryBody, idx: usize, kw: &str) -> Option<String> {
    body.args
        .get(idx)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .or_else(|| {
            body.kwargs
                .get(kw)
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tasks::Task;

    #[test]
    fn test_parse_celery_smart_deploy() {
        let envelope = r#"{
            "body": "{\"args\":[\"550e8400-e29b-41d4-a716-446655440000\",\"6ba7b810-9dad-11d1-80b4-00c04fd430c8\",\"abc123\"],\"kwargs\":{}}",
            "headers": {"task": "apps.deployments.tasks.smart_deploy"}
        }"#;
        let task = parse_celery_message(envelope).unwrap().unwrap();
        match task {
            Task::SmartDeploy {
                project_id,
                deployment_id,
                commit_hash,
            } => {
                assert_eq!(project_id.to_string(), "550e8400-e29b-41d4-a716-446655440000");
                assert_eq!(
                    deployment_id.to_string(),
                    "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
                );
                assert_eq!(commit_hash, "abc123");
            }
            _ => panic!("expected SmartDeploy"),
        }
    }

    #[test]
    fn test_parse_celery_provision_addon_kwargs() {
        let envelope = r#"{
            "body": "{\"args\":[],\"kwargs\":{\"addon_id\":\"550e8400-e29b-41d4-a716-446655440000\",\"addon_type\":\"POSTGRES\"}}",
            "headers": {"task": "apps.addons.tasks.provision_addon"}
        }"#;
        let task = parse_celery_message(envelope).unwrap().unwrap();
        match task {
            Task::ProvisionAddon {
                addon_id,
                addon_type,
            } => {
                assert_eq!(addon_id.to_string(), "550e8400-e29b-41d4-a716-446655440000");
                assert_eq!(addon_type, "POSTGRES");
            }
            _ => panic!("expected ProvisionAddon"),
        }
    }

    #[test]
    fn test_parse_native_rust_twin_passthrough() {
        let native = r#"{
            "type": "CollectUsage",
            "payload": {"owner_id": 42}
        }"#;
        let task = parse_celery_message(native).unwrap().unwrap();
        match task {
            Task::CollectUsage { owner_id } => assert_eq!(owner_id, 42),
            _ => panic!("expected CollectUsage"),
        }
    }

    #[test]
    fn test_parse_unknown_celery_task_returns_none() {
        let envelope = r#"{
            "body": "{}",
            "headers": {"task": "apps.unknown.task"}
        }"#;
        let result = parse_celery_message(envelope).unwrap();
        assert!(result.is_none());
    }
}
