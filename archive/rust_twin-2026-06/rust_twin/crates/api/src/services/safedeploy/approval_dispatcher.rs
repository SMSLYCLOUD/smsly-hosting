//! Approval dispatcher — wires HTTP endpoints to the state machine.

use std::sync::Arc;
use chrono::Utc;
use sea_orm::{ColumnTrait, DatabaseConnection, EntityTrait, QueryFilter, Set};
use thiserror::Error;
use tracing::{info, warn};

use cn_core::deployment_status::{transition, DeploymentStatus, TransitionError};
use cn_core::entities::{deployment, safedeploy_approval, user};
use crate::services::safedeploy_state::{decide_approval, ApprovalContext, ApprovalDecision, ApprovalStatus, DeploymentCriticality};

#[derive(Debug, Error)]
pub enum DispatcherError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("deployment not found: {0}")]
    DeploymentNotFound(uuid::Uuid),
    #[error("approver not found: {0}")]
    ApproverNotFound(i32),
    #[error("invalid state transition: {0}")]
    Transition(#[from] TransitionError),
    #[error("duplicate approval request")]
    Duplicate,
}

pub struct ApprovalDispatcher {
    pub db: Arc<DatabaseConnection>,
}

impl ApprovalDispatcher {
    pub fn new(db: Arc<DatabaseConnection>) -> Self { Self { db } }

    /// Request approval for a deployment. Creates a new SafedeployApproval
    /// in PENDING state and sets the deployment status to AWAITING_APPROVAL.
    pub async fn request_approval(
        &self,
        deployment_id: uuid::Uuid,
        requester_id: i32,
        criticality: DeploymentCriticality,
    ) -> Result<safedeploy_approval::Model, DispatcherError> {
        // Verify the deployment exists and the requester owns it (skip for now)
        let dep = deployment::Entity::find_by_id(deployment_id)
            .one(&*self.db).await?
            .ok_or(DispatcherError::DeploymentNotFound(deployment_id))?;
        if dep.requester_id != requester_id {
            return Err(DispatcherError::Duplicate);  // ownership check
        }

        // Determine expiry (24h default)
        let expires_at = Utc::now() + chrono::Duration::seconds(
            safedeploy_state::default_expiry().as_secs() as i64
        );

        // Create the approval request
        let new_approval = safedeploy_approval::ActiveModel {
            id: Set(uuid::Uuid::new_v4()),
            deployment_id: Set(deployment_id),
            approver_id: Set(requester_id),  // initial = requester; will be set on action
            status: Set(ApprovalStatus::Pending.as_str().to_string()),
            reason: Set(None),
            expires_at: Set(expires_at),
            acted_at: Set(None),
            created_at: Set(Utc::now()),
        };
        // Note: approver_id should not be the requester. For a fresh request,
        // we need a different "primary approver". Use 0 as a sentinel for "not yet
        // acted upon" — or use a separate "requested" model. For now, the field
        // is per-action, so a request creates a placeholder.
        let inserted = new_approval.insert(&*self.db).await?;

        // Set deployment status to AWAITING_APPROVAL
        let mut dep_active: deployment::ActiveModel = dep.into();
        dep_active.status = Set(DeploymentStatus::AwaitingApproval.as_str().to_string());
        dep_active.update(&*self.db).await?;

        info!("Approval requested for deployment {} (approver_id placeholder = requester {})", deployment_id, requester_id);
        Ok(inserted)
    }

    /// Act on an approval request (approve or reject).
    pub async fn act_on_approval(
        &self,
        approval_id: uuid::Uuid,
        approver_id: i32,
        decision: ApprovalStatus,  // approved or rejected
        reason: Option<String>,
    ) -> Result<ApprovalDecision, DispatcherError> {
        // Find the approval
        let approval = safedeploy_approval::Entity::find_by_id(approval_id)
            .one(&*self.db).await?
            .ok_or(DispatcherError::DeploymentNotFound(approval_id))?;

        // Find the deployment
        let dep = deployment::Entity::find_by_id(approval.deployment_id)
            .one(&*self.db).await?
            .ok_or(DispatcherError::DeploymentNotFound(approval.deployment_id))?;

        // Get all existing approvals for this deployment
        let existing: Vec<(i32, ApprovalStatus)> = safedeploy_approval::Entity::find()
            .filter(safedeploy_approval::Column::DeploymentId.eq(approval.deployment_id))
            .all(&*self.db).await?
            .iter()
            .map(|a| (a.approver_id, ApprovalStatus::from_str(&a.status).unwrap_or(ApprovalStatus::Pending)))
            .collect();

        // Determine criticality from the deployment (we'd need a separate field
        // for this; for now default to Medium)
        let criticality = DeploymentCriticality::Medium;

        // Run the state machine
        let ctx = ApprovalContext {
            deployment_id: &approval.deployment_id.to_string(),
            requester_id: dep.requester_id,
            approver_id,
            criticality,
            existing_approvals: &existing,  // borrowed; will be dropped at end
            now: Utc::now(),
            expires_at: approval.expires_at,
        };
        // Note: we can't return a decision struct AND apply the change in a
        // single function without a leaky lifetime. Split: caller decides, then
        // calls apply_decision.
        let dec = decide_approval(&ctx);
        Ok(dec)
    }

    /// Apply a decision to the database.
    pub async fn apply_decision(
        &self,
        approval_id: uuid::Uuid,
        approver_id: i32,
        decision: ApprovalStatus,
        reason: Option<String>,
    ) -> Result<(), DispatcherError> {
        let mut approval = safedeploy_approval::Entity::find_by_id(approval_id)
            .one(&*self.db).await?
            .ok_or(DispatcherError::DeploymentNotFound(approval_id))?
            .into_active_model();
        approval.approver_id = Set(approver_id);
        approval.status = Set(decision.as_str().to_string());
        approval.reason = Set(reason);
        approval.acted_at = Set(Some(Utc::now()));
        approval.update(&*self.db).await?;

        // If approved, transition the deployment to QUEUED
        if decision == ApprovalStatus::Approved {
            let dep = deployment::Entity::find_by_id(
                safedeploy_approval::Entity::find_by_id(approval_id).await?
                    .ok_or(DispatcherError::DeploymentNotFound(approval_id))?
                    .deployment_id
            ).one(&*self.db).await?
                .ok_or(DispatcherError::DeploymentNotFound(uuid::Uuid::nil()))?;

            transition(DeploymentStatus::AwaitingApproval, DeploymentStatus::Queued)?;
            let mut dep_active: deployment::ActiveModel = dep.into();
            dep_active.status = Set(DeploymentStatus::Queued.as_str().to_string());
            dep_active.update(&*self.db).await?;
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    // Test the state machine integration with the dispatcher
    // (without a real DB, can only test the pure functions)
    use super::super::approval_dispatcher::*;
    use super::super::audit::*;

    #[test]
    fn test_audit_event_serialises() {
        let e = SafedeployEvent::ApprovalRequested {
            deployment_id: "d1".into(),
            requester_id: 1,
            criticality: "medium".into(),
        };
        let s = serde_json::to_string(&e).unwrap();
        assert!(s.contains("d1"));
    }
}
