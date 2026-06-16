//! Safedeploy HTTP routes.
//!
//! Mirrors backend/apps/deployments/views_safedeploy.py.

use axum::{extract::{Path, State}, http::StatusCode, response::IntoResponse, routing::{get, post}, Json, Router};
use serde::Deserialize;
use std::sync::Arc;

use super::approval_dispatcher::{ApprovalDispatcher, DispatcherError};
use super::audit::{log_event, SafedeployEvent};
use cn_core::deployment_status::{transition, DeploymentStatus, TransitionError};
use cn_core::entities::{deployment, safedeploy_approval, user};
use crate::services::safedeploy_state::{ApprovalStatus, DeploymentCriticality};

#[derive(Clone)]
pub struct AppState {
    pub dispatcher: Arc<ApprovalDispatcher>,
}

pub fn create_router() -> Router<AppState> {
    Router::new()
        .route("/api/v1/deployments/:id/request-approval", post(request_approval))
        .route("/api/v1/approvals/:id/act", post(act_on_approval))
        .route("/api/v1/approvals/:id", get(get_approval))
}

#[derive(Deserialize)]
pub struct RequestApprovalBody {
    pub criticality: String,
}

pub async fn request_approval(
    State(state): State<AppState>,
    Path(deployment_id): Path<uuid::Uuid>,
    Json(body): Json<RequestApprovalBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let requester_id: i32 = 0;
    let criticality = match body.criticality.as_str() {
        "low" => DeploymentCriticality::Low,
        "medium" => DeploymentCriticality::Medium,
        "critical" => DeploymentCriticality::Critical,
        _ => return Err((StatusCode::BAD_REQUEST, "invalid criticality".to_string())),
    };
    state.dispatcher.request_approval(deployment_id, requester_id, criticality)
        .await.map(|a| (StatusCode::CREATED, Json(a)))
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))
}

#[derive(Deserialize)]
pub struct ActOnApprovalBody {
    pub approver_id: i32,
    pub decision: String,
    pub reason: Option<String>,
}

pub async fn act_on_approval(
    State(state): State<AppState>,
    Path(approval_id): Path<uuid::Uuid>,
    Json(body): Json<ActOnApprovalBody>,
) -> Result<impl IntoResponse, (StatusCode, String)> {
    let decision = match body.decision.as_str() {
        "approved" => ApprovalStatus::Approved,
        "rejected" => ApprovalStatus::Rejected,
        _ => return Err((StatusCode::BAD_REQUEST, "invalid decision".to_string())),
    };
    let dec = state.dispatcher.act_on_approval(
        approval_id, body.approver_id, decision, body.reason.clone()
    ).await.map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    let reason = body.reason.clone();
    state.dispatcher.apply_decision(approval_id, body.approver_id, dec.new_status, body.reason).await
        .map_err(|e| (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()))?;
    log_event(SafedeployEvent::ApprovalActed {
        approval_id: approval_id.to_string(),
        approver_id: body.approver_id,
        decision: dec.new_status.as_str().to_string(),
        reason: reason,
    }, chrono::Utc::now());
    Ok(Json(serde_json::json!({ "status": dec.new_status.as_str(), "reason": dec.reason })))
}

pub async fn get_approval(
    State(_state): State<AppState>,
    Path(_approval_id): Path<uuid::Uuid>,
) -> impl IntoResponse {
    (StatusCode::NOT_IMPLEMENTED, "not yet implemented")
}
