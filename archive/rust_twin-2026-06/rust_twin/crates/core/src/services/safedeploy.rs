//! Safedeploy approval workflow service.

use sea_orm::{ColumnTrait, DatabaseConnection, EntityTrait, QueryFilter, Set};
use uuid::Uuid;
use chrono::Utc;
use thiserror::Error;

use crate::entities::safedeploy_approval;
use crate::entities::safedeploy_approval::Entity as ApprovalEntity;
use crate::deployment_status::{DeploymentStatus, TransitionError, transition};

#[derive(Debug, Error)]
pub enum SafedeployError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("approval not found: {0}")]
    NotFound(uuid::Uuid),
    #[error("approval already acted upon")]
    AlreadyActed,
    #[error("approval expired")]
    Expired,
    #[error("invalid transition: {0}")]
    InvalidTransition(#[from] TransitionError),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ApprovalDecision { Approved, Rejected }

pub struct SafedeployService {
    pub db: DatabaseConnection,
}

impl SafedeployService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn request_approval(
        &self,
        deployment_id: Uuid,
        approver_id: i32,
    ) -> Result<safedeploy_approval::Model, SafedeployError> {
        let expires_at = Utc::now() + chrono::Duration::hours(24);
        let new_approval = safedeploy_approval::ActiveModel {
            id: Set(Uuid::new_v4()),
            deployment_id: Set(deployment_id),
            approver_id: Set(approver_id),
            status: Set("pending".to_string()),
            reason: Set(None),
            expires_at: Set(expires_at),
            acted_at: Set(None),
            created_at: Set(Utc::now()),
        };
        Ok(new_approval.insert(&self.db).await?)
    }

    pub async fn act(
        &self,
        approval_id: Uuid,
        approver_id: i32,
        decision: ApprovalDecision,
        reason: Option<String>,
    ) -> Result<safedeploy_approval::Model, SafedeployError> {
        let approval = ApprovalEntity::find_by_id(approval_id)
            .one(&self.db).await?
            .ok_or(SafedeployError::NotFound(approval_id))?;
        if approval.status != "pending" {
            return Err(SafedeployError::AlreadyActed);
        }
        if Utc::now() > approval.expires_at {
            return Err(SafedeployError::Expired);
        }
        let now = Utc::now();
        let new_status = match decision {
            ApprovalDecision::Approved => "approved",
            ApprovalDecision::Rejected => "rejected",
        };
        let mut active: safedeploy_approval::ActiveModel = approval.into();
        active.approver_id = Set(approver_id);
        active.status = Set(new_status.to_string());
        active.reason = Set(reason);
        active.acted_at = Set(Some(now));
        active.updated_at = Set(now);
        let updated = active.update(&self.db).await?;
        Ok(updated)
    }

    pub async fn is_approved(&self, deployment_id: Uuid) -> Result<bool, SafedeployError> {
        let count = ApprovalEntity::find()
            .filter(safedeploy_approval::Column::DeploymentId.eq(deployment_id))
            .filter(safedeploy_approval::Column::Status.eq("approved"))
            .all(&self.db).await?
            .len();
        Ok(count > 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_decision_display() {
        assert_eq!(format!("{:?}", ApprovalDecision::Approved), "Approved");
    }
}
