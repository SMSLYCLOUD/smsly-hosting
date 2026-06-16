//! Deployment status enum and state machine.
//!
//! Mirrors the Django backend's `Deployment.Status` choices. Each state
//! has a defined set of valid transitions; attempts to transition to an
//! invalid state return an error.
//!
//! States (matching the Django backend):
//! - AWAITING_APPROVAL: needs human approval before proceeding (4-eyes)
//! - QUEUED: waiting for a worker slot
//! - BUILDING: Nixpacks building the image
//! - BUILD_FAILED: build step failed
//! - DEPLOYING: deploying to the target node
//! - DEPLOY_FAILED: deploy step failed
//! - RUNNING: healthy, serving traffic
//! - UNHEALTHY: running but health check failing
//! - STOPPING: graceful shutdown in progress
//! - STOPPED: deliberately stopped by user
//! - ROLLING_OUT: in a rolling-update phase
//! - ROLLED_BACK: rolled back to previous version
//! - REMOVED: deleted (tombstone)
//!
//! Valid transitions:
//! - AWAITING_APPROVAL -> APPROVED (then QUEUED) | REJECTED (then BLOCKED)
//! - QUEUED -> BUILDING | CANCELLED
//! - BUILDING -> DEPLOYING | BUILD_FAILED
//! - BUILD_FAILED -> (terminal; requires new deployment)
//! - DEPLOYING -> RUNNING | DEPLOY_FAILED
//! - DEPLOY_FAILED -> (terminal)
//! - RUNNING -> STOPPING | ROLLING_OUT | UNHEALTHY
//! - UNHEALTHY -> RUNNING (recovered) | STOPPING
//! - STOPPING -> STOPPED
//! - STOPPED -> QUEUED (restart)
//! - ROLLING_OUT -> RUNNING | ROLLED_BACK
//! - ROLLED_BACK -> (terminal)

use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum DeploymentStatus {
    AwaitingApproval,
    Queued,
    Building,
    BuildFailed,
    Deploying,
    DeployFailed,
    Running,
    Unhealthy,
    Stopping,
    Stopped,
    RollingOut,
    RolledBack,
    Removed,
}

impl DeploymentStatus {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::AwaitingApproval => "AWAITING_APPROVAL",
            Self::Queued => "QUEUED",
            Self::Building => "BUILDING",
            Self::BuildFailed => "BUILD_FAILED",
            Self::Deploying => "DEPLOYING",
            Self::DeployFailed => "DEPLOY_FAILED",
            Self::Running => "RUNNING",
            Self::Unhealthy => "UNHEALTHY",
            Self::Stopping => "STOPPING",
            Self::Stopped => "STOPPED",
            Self::RollingOut => "ROLLING_OUT",
            Self::RolledBack => "ROLLED_BACK",
            Self::Removed => "REMOVED",
        }
    }
    pub fn from_str(s: &str) -> Option<Self> {
        Some(match s {
            "AWAITING_APPROVAL" => Self::AwaitingApproval,
            "QUEUED" => Self::Queued,
            "BUILDING" => Self::Building,
            "BUILD_FAILED" => Self::BuildFailed,
            "DEPLOYING" => Self::Deploying,
            "DEPLOY_FAILED" => Self::DeployFailed,
            "RUNNING" => Self::Running,
            "UNHEALTHY" => Self::Unhealthy,
            "STOPPING" => Self::Stopping,
            "STOPPED" => Self::Stopped,
            "ROLLING_OUT" => Self::RollingOut,
            "ROLLED_BACK" => Self::RolledBack,
            "REMOVED" => Self::Removed,
            _ => return None,
        })
    }
    pub fn is_terminal(&self) -> bool {
        matches!(self, Self::BuildFailed | Self::DeployFailed | Self::RolledBack | Self::Removed)
    }
    pub fn is_running(&self) -> bool {
        matches!(self, Self::Running | Self::Unhealthy | Self::RollingOut)
    }
}

impl fmt::Display for DeploymentStatus {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum TransitionError {
    #[error("cannot transition from {from} to {to}")]
    Invalid { from: String, to: String },
    #[error("unknown status string: {0}")]
    Unknown(String),
}

pub fn transition(from: DeploymentStatus, to: DeploymentStatus) -> Result<(), TransitionError> {
    use DeploymentStatus::*;
    let ok = matches!(
        (from, to),
        (AwaitingApproval, Queued)
            | (AwaitingApproval, Removed)  // rejected
            | (Queued, Building)
            | (Queued, Stopped)             // cancelled
            | (Queued, Removed)
            | (Building, Deploying)
            | (Building, BuildFailed)
            | (Building, Queued)            // retry
            | (BuildFailed, Removed)
            | (Deploying, Running)
            | (Deploying, DeployFailed)
            | (Deploying, RollingOut)       // blue-green partial
            | (DeployFailed, Removed)
            | (Running, Stopping)
            | (Running, RollingOut)
            | (Running, Unhealthy)
            | (Running, Removed)
            | (Unhealthy, Running)
            | (Unhealthy, Stopping)
            | (Unhealthy, Removed)
            | (Stopping, Stopped)
            | (Stopping, Removed)
            | (Stopped, Queued)
            | (Stopped, Removed)
            | (RollingOut, Running)
            | (RollingOut, RolledBack)
            | (RollingOut, Removed)
            | (RolledBack, Removed)
    );
    if ok {
        Ok(())
    } else {
        Err(TransitionError::Invalid {
            from: from.to_string(),
            to: to.to_string(),
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_valid_transitions() {
        assert!(transition(DeploymentStatus::Queued, DeploymentStatus::Building).is_ok());
        assert!(transition(DeploymentStatus::Building, DeploymentStatus::Deploying).is_ok());
        assert!(transition(DeploymentStatus::Deploying, DeploymentStatus::Running).is_ok());
        assert!(transition(DeploymentStatus::Running, DeploymentStatus::Stopping).is_ok());
        assert!(transition(DeploymentStatus::Stopping, DeploymentStatus::Stopped).is_ok());
        assert!(transition(DeploymentStatus::Stopped, DeploymentStatus::Queued).is_ok());
    }

    #[test]
    fn test_invalid_transitions() {
        // Cannot go from BUILDING back to AWAITING_APPROVAL
        assert!(transition(DeploymentStatus::Building, DeploymentStatus::AwaitingApproval).is_err());
        // Cannot go from RUNNING directly to BUILDING
        assert!(transition(DeploymentStatus::Running, DeploymentStatus::Building).is_err());
        // Terminal states cannot transition
        assert!(transition(DeploymentStatus::BuildFailed, DeploymentStatus::Running).is_err());
    }

    #[test]
    fn test_terminal_states() {
        assert!(DeploymentStatus::BuildFailed.is_terminal());
        assert!(DeploymentStatus::DeployFailed.is_terminal());
        assert!(DeploymentStatus::RolledBack.is_terminal());
        assert!(DeploymentStatus::Removed.is_terminal());
        assert!(!DeploymentStatus::Running.is_terminal());
    }

    #[test]
    fn test_running_states() {
        assert!(DeploymentStatus::Running.is_running());
        assert!(DeploymentStatus::Unhealthy.is_running());
        assert!(DeploymentStatus::RollingOut.is_running());
        assert!(!DeploymentStatus::Building.is_running());
    }

    #[test]
    fn test_round_trip() {
        for s in [
            DeploymentStatus::AwaitingApproval, DeploymentStatus::Queued,
            DeploymentStatus::Building, DeploymentStatus::BuildFailed,
            DeploymentStatus::Deploying, DeploymentStatus::DeployFailed,
            DeploymentStatus::Running, DeploymentStatus::Unhealthy,
            DeploymentStatus::Stopping, DeploymentStatus::Stopped,
            DeploymentStatus::RollingOut, DeploymentStatus::RolledBack,
            DeploymentStatus::Removed,
        ] {
            assert_eq!(DeploymentStatus::from_str(s.as_str()), Some(s));
        }
    }
}
