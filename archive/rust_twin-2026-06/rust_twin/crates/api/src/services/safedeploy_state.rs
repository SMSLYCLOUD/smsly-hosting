//! Safedeploy state machine — 4-eyes approval workflow.
//!
//! States: PENDING -> APPROVED -> REJECTED
//!                  \-> EXPIRED
//!
//! Transitions:
//! - PENDING -> APPROVED: when an approver acts and approves
//! - PENDING -> REJECTED: when an approver acts and rejects
//! - PENDING -> EXPIRED: when the expires_at passes
//!
//! Business rules:
//! - An approver cannot approve their own deployment (segregation of duties)
//! - Approval requires at least 1 approver; for "critical" deployments, 2
//! - Once approved, the deployment can proceed
//! - Once rejected or expired, the deployment is permanently blocked
//!   (a new deployment must be created)

use std::time::Duration;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ApprovalStatus {
    Pending,
    Approved,
    Rejected,
    Expired,
}

impl ApprovalStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Pending => "pending",
            Self::Approved => "approved",
            Self::Rejected => "rejected",
            Self::Expired => "expired",
        }
    }
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "pending" => Some(Self::Pending),
            "approved" => Some(Self::Approved),
            "rejected" => Some(Self::Rejected),
            "expired" => Some(Self::Expired),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DeploymentCriticality {
    Low,
    Medium,
    Critical,
}

impl DeploymentCriticality {
    /// Number of distinct approvers required.
    pub fn required_approvers(&self) -> usize {
        match self {
            Self::Low => 1,
            Self::Medium => 1,
            Self::Critical => 2,
        }
    }
}

pub struct ApprovalContext<'a> {
    pub deployment_id: &'a str,
    pub requester_id: i32,
    pub approver_id: i32,
    pub criticality: DeploymentCriticality,
    pub existing_approvals: &'a [(i32, ApprovalStatus)],  // (approver_id, status)
    pub now: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
}

pub struct ApprovalDecision {
    pub new_status: ApprovalStatus,
    pub reason: String,
}

pub fn decide_approval(ctx: &ApprovalContext) -> ApprovalDecision {
    // Rule 1: cannot self-approve
    if ctx.requester_id == ctx.approver_id {
        return ApprovalDecision {
            new_status: ApprovalStatus::Rejected,
            reason: "self-approval is not permitted (segregation of duties)".to_string(),
        };
    }

    // Rule 2: cannot approve an expired request
    if ctx.now > ctx.expires_at {
        return ApprovalDecision {
            new_status: ApprovalStatus::Expired,
            reason: "approval window has expired".to_string(),
        };
    }

    // Rule 3: check if this approver has already acted
    if ctx.existing_approvals.iter().any(|(aid, _)| *aid == ctx.approver_id) {
        return ApprovalDecision {
            new_status: ApprovalStatus::Pending,
            reason: "this approver has already acted on this deployment".to_string(),
        };
    }

    // Rule 4: count existing approvals
    let approved_count = ctx.existing_approvals.iter()
        .filter(|(_, s)| *s == ApprovalStatus::Approved)
        .count();
    let required = ctx.criticality.required_approvers();

    if approved_count + 1 >= required {
        ApprovalDecision {
            new_status: ApprovalStatus::Approved,
            reason: format!("{}/{} approvers approved", approved_count + 1, required),
        }
    } else {
        ApprovalDecision {
            new_status: ApprovalStatus::Pending,
            reason: format!("{}/{} approvers, more required", approved_count + 1, required),
        }
    }
}

/// Default expiration window for a new approval request.
pub fn default_expiry() -> Duration {
    Duration::from_secs(24 * 60 * 60)  // 24 hours
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration as ChronoDuration;

    fn ctx(criticality: DeploymentCriticality, approver_id: i32, existing: Vec<(i32, ApprovalStatus)>) -> ApprovalContext<'static> {
        // Lifetimes: use 'static by leaking — tests only.
        let existing: &'static [(i32, ApprovalStatus)] = Box::leak(existing.into_boxed_slice());
        let deployment_id = "test-deployment";
        ApprovalContext {
            deployment_id,
            requester_id: 1,
            approver_id,
            criticality,
            existing_approvals: existing,
            now: Utc::now(),
            expires_at: Utc::now() + ChronoDuration::hours(24),
        }
    }

    #[test]
    fn test_low_requires_one_approval() {
        assert_eq!(DeploymentCriticality::Low.required_approvers(), 1);
    }

    #[test]
    fn test_critical_requires_two_approvals() {
        assert_eq!(DeploymentCriticality::Critical.required_approvers(), 2);
    }

    #[test]
    fn test_cannot_self_approve() {
        let c = ctx(DeploymentCriticality::Low, 1, vec![]);
        let d = decide_approval(&c);
        assert_eq!(d.new_status, ApprovalStatus::Rejected);
    }

    #[test]
    fn test_low_approved_with_one_approver() {
        let c = ctx(DeploymentCriticality::Low, 2, vec![]);
        let d = decide_approval(&c);
        assert_eq!(d.new_status, ApprovalStatus::Approved);
    }

    #[test]
    fn test_critical_needs_two_approvers() {
        let c = ctx(DeploymentCriticality::Critical, 2, vec![]);
        let d = decide_approval(&c);
        assert_eq!(d.new_status, ApprovalStatus::Pending);

        let c = ctx(DeploymentCriticality::Critical, 3, vec![(2, ApprovalStatus::Approved)]);
        let d = decide_approval(&c);
        assert_eq!(d.new_status, ApprovalStatus::Approved);
    }

    #[test]
    fn test_expired_rejects() {
        let mut c = ctx(DeploymentCriticality::Low, 2, vec![]);
        c.expires_at = Utc::now() - ChronoDuration::seconds(1);
        let d = decide_approval(&c);
        assert_eq!(d.new_status, ApprovalStatus::Expired);
    }

    #[test]
    fn test_double_approval_from_same_user() {
        let c = ctx(DeploymentCriticality::Low, 2, vec![(2, ApprovalStatus::Approved)]);
        let d = decide_approval(&c);
        assert_eq!(d.new_status, ApprovalStatus::Pending);
    }
}
