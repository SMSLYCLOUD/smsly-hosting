//! Canary rollout state machine.
//!
//! A canary deployment progresses through three stages:
//! - `Canary10`  — 10% of traffic routed to the new revision
//! - `Canary50`  — 50% of traffic routed to the new revision
//! - `Canary100` — 100% of traffic routed to the new revision (terminal for the
//!                 canary state machine; the deployment record transitions to
//!                 `Running` at this point)
//!
//! Between stages, the worker runs periodic HTTP health checks against the
//! service. Advancement requires:
//! 1. The dwell time at the current stage has elapsed (`stage_dwell_secs`).
//! 2. A configurable number of consecutive successful health checks
//!    (`health_check_consecutive_required`).
//!
//! Auto-abort triggers when consecutive health failures reach
//! `health_failure_threshold`. On abort, the deployment is marked with the
//! `ABORTED` status string.
//!
//! ## Persistence
//!
//! The canary state is persisted in Redis under the key
//! `canary:run:{deployment_id}` (JSON-encoded [`CanaryRun`]). The deployment
//! entity's `status` column holds the stage string. The deployment entity
//! schema is owned by another agent; if a dedicated `canary_stage` column
//! becomes available, the string-in-status approach can be replaced.
//!
//! TODO: add a `canary_stage` column to the deployment entity to decouple
//! canary progress from the deployment lifecycle status.

use chrono::{DateTime, Utc};
use sea_orm::prelude::Uuid;
use serde::{Deserialize, Serialize};

/// Wire/DB representation of the canary stages. These strings are stored in
/// the deployment entity's `status` column.
pub const CANARY_STATUS_10: &str = "CANARY10";
pub const CANARY_STATUS_50: &str = "CANARY50";
pub const CANARY_STATUS_100: &str = "CANARY100";
pub const CANARY_STATUS_ABORTED: &str = "ABORTED";

/// Stages in the canary state machine.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum CanaryStage {
    Disabled,
    Canary10,
    Canary50,
    Canary100,
    Aborted,
}

impl CanaryStage {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Disabled => "DISABLED",
            Self::Canary10 => CANARY_STATUS_10,
            Self::Canary50 => CANARY_STATUS_50,
            Self::Canary100 => CANARY_STATUS_100,
            Self::Aborted => CANARY_STATUS_ABORTED,
        }
    }

    /// Lenient parser: returns `None` for non-canary status strings.
    pub fn from_status(s: &str) -> Option<Self> {
        match s {
            CANARY_STATUS_10 => Some(Self::Canary10),
            CANARY_STATUS_50 => Some(Self::Canary50),
            CANARY_STATUS_100 => Some(Self::Canary100),
            CANARY_STATUS_ABORTED => Some(Self::Aborted),
            _ => None,
        }
    }

    /// Return the next stage in the canary progression, or `None` if the
    /// current stage is terminal for the canary state machine.
    pub fn next(&self) -> Option<Self> {
        match self {
            Self::Canary10 => Some(Self::Canary50),
            Self::Canary50 => Some(Self::Canary100),
            Self::Canary100 | Self::Disabled | Self::Aborted => None,
        }
    }
}

/// Tunable parameters for the canary rollout.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CanaryConfig {
    pub health_check_path: String,
    pub health_check_timeout_secs: u64,
    pub health_check_consecutive_required: u32,
    pub stage_dwell_secs: u64,
    pub health_failure_threshold: u32,
}

impl Default for CanaryConfig {
    fn default() -> Self {
        Self {
            health_check_path: "/health".to_string(),
            health_check_timeout_secs: 10,
            health_check_consecutive_required: 3,
            stage_dwell_secs: 60,
            health_failure_threshold: 5,
        }
    }
}

/// Mutable state for a single canary deployment.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CanaryRun {
    pub deployment_id: Uuid,
    pub stage: CanaryStage,
    pub started_at: DateTime<Utc>,
    pub stage_entered_at: DateTime<Utc>,
    pub health_failures: u32,
    pub consecutive_passes: u32,
}

impl CanaryRun {
    pub fn new(deployment_id: Uuid, now: DateTime<Utc>) -> Self {
        Self {
            deployment_id,
            stage: CanaryStage::Canary10,
            started_at: now,
            stage_entered_at: now,
            health_failures: 0,
            consecutive_passes: 0,
        }
    }

    /// Advance to the next stage if the dwell time has elapsed and enough
    /// consecutive health checks have passed. Returns the new stage on
    /// success. Resets health counters on advance.
    pub fn advance_if_ready(
        &mut self,
        cfg: &CanaryConfig,
        now: DateTime<Utc>,
    ) -> Option<CanaryStage> {
        let dwell_secs = (now - self.stage_entered_at).num_seconds().max(0) as u64;
        if dwell_secs < cfg.stage_dwell_secs {
            return None;
        }
        if self.consecutive_passes < cfg.health_check_consecutive_required {
            return None;
        }
        let next = self.stage.next()?;
        self.stage = next;
        self.stage_entered_at = now;
        self.consecutive_passes = 0;
        self.health_failures = 0;
        Some(next)
    }

    /// Record the outcome of a single health check.
    pub fn record_health_check(&mut self, passed: bool) {
        if passed {
            self.consecutive_passes = self.consecutive_passes.saturating_add(1);
            self.health_failures = 0;
        } else {
            self.consecutive_passes = 0;
            self.health_failures = self.health_failures.saturating_add(1);
        }
    }

    /// True when consecutive failures have reached the abort threshold.
    pub fn should_abort(&self, cfg: &CanaryConfig) -> bool {
        self.health_failures >= cfg.health_failure_threshold
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> CanaryConfig {
        CanaryConfig {
            stage_dwell_secs: 60,
            health_check_consecutive_required: 3,
            health_failure_threshold: 5,
            ..CanaryConfig::default()
        }
    }

    #[test]
    fn advance_requires_dwell_time() {
        let dep_id = Uuid::new_v4();
        let t0 = Utc::now();
        let mut run = CanaryRun::new(dep_id, t0);
        run.consecutive_passes = 3;
        // 30s < 60s dwell
        let result = run.advance_if_ready(&cfg(), t0 + chrono::Duration::seconds(30));
        assert_eq!(result, None);
    }

    #[test]
    fn advance_requires_consecutive_passes() {
        let dep_id = Uuid::new_v4();
        let t0 = Utc::now();
        let mut run = CanaryRun::new(dep_id, t0);
        run.consecutive_passes = 2; // < 3 required
        let result = run.advance_if_ready(&cfg(), t0 + chrono::Duration::seconds(120));
        assert_eq!(result, None);
    }

    #[test]
    fn advance_canary10_to_canary50() {
        let dep_id = Uuid::new_v4();
        let t0 = Utc::now();
        let mut run = CanaryRun::new(dep_id, t0);
        run.consecutive_passes = 3;
        let result = run.advance_if_ready(&cfg(), t0 + chrono::Duration::seconds(120));
        assert_eq!(result, Some(CanaryStage::Canary50));
        assert_eq!(run.stage, CanaryStage::Canary50);
        assert_eq!(run.consecutive_passes, 0);
    }

    #[test]
    fn advance_canary50_to_canary100() {
        let dep_id = Uuid::new_v4();
        let t0 = Utc::now();
        let mut run = CanaryRun::new(dep_id, t0);
        run.stage = CanaryStage::Canary50;
        run.consecutive_passes = 3;
        let result = run.advance_if_ready(&cfg(), t0 + chrono::Duration::seconds(120));
        assert_eq!(result, Some(CanaryStage::Canary100));
    }

    #[test]
    fn advance_from_canary100_returns_none() {
        let dep_id = Uuid::new_v4();
        let t0 = Utc::now();
        let mut run = CanaryRun::new(dep_id, t0);
        run.stage = CanaryStage::Canary100;
        run.consecutive_passes = 10;
        let result = run.advance_if_ready(&cfg(), t0 + chrono::Duration::seconds(120));
        assert_eq!(result, None);
    }

    #[test]
    fn record_pass_increments_consecutive() {
        let dep_id = Uuid::new_v4();
        let mut run = CanaryRun::new(dep_id, Utc::now());
        run.record_health_check(true);
        run.record_health_check(true);
        assert_eq!(run.consecutive_passes, 2);
        assert_eq!(run.health_failures, 0);
    }

    #[test]
    fn record_fail_resets_consecutive_and_increments_failures() {
        let dep_id = Uuid::new_v4();
        let mut run = CanaryRun::new(dep_id, Utc::now());
        run.record_health_check(true);
        run.record_health_check(true);
        run.record_health_check(false);
        assert_eq!(run.consecutive_passes, 0);
        assert_eq!(run.health_failures, 1);
    }

    #[test]
    fn pass_resets_failures() {
        let dep_id = Uuid::new_v4();
        let mut run = CanaryRun::new(dep_id, Utc::now());
        run.record_health_check(false);
        run.record_health_check(false);
        assert_eq!(run.health_failures, 2);
        run.record_health_check(true);
        assert_eq!(run.health_failures, 0);
    }

    #[test]
    fn should_abort_at_threshold() {
        let dep_id = Uuid::new_v4();
        let mut run = CanaryRun::new(dep_id, Utc::now());
        for _ in 0..4 {
            run.record_health_check(false);
        }
        assert!(!run.should_abort(&cfg()));
        run.record_health_check(false);
        assert!(run.should_abort(&cfg()));
    }

    #[test]
    fn stage_roundtrip() {
        assert_eq!(CanaryStage::Canary10.as_str(), CANARY_STATUS_10);
        assert_eq!(
            CanaryStage::from_status(CANARY_STATUS_50),
            Some(CanaryStage::Canary50)
        );
        assert_eq!(CanaryStage::from_status("RUNNING"), None);
    }
}
