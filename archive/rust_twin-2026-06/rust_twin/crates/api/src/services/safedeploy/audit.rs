//! Audit log for safedeploy actions.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use tracing::info;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SafedeployEvent {
    ApprovalRequested {
        deployment_id: String,
        requester_id: i32,
        criticality: String,
    },
    ApprovalActed {
        approval_id: String,
        approver_id: i32,
        decision: String,
        reason: Option<String>,
    },
    DeploymentStateChanged {
        deployment_id: String,
        from_state: String,
        to_state: String,
    },
}

pub fn log_event(event: SafedeployEvent, at: DateTime<Utc>) {
    let payload = serde_json::to_string(&event).unwrap_or_default();
    info!(target: "audit", event = %payload, timestamp = %at, "safedeploy");
}
