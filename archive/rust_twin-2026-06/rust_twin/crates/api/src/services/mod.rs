//! API services — feature modules that expose business logic via Axum.

pub mod safedeploy;
pub mod webhooks;
pub mod sso;
pub mod addon_template;

// State machine for the safedeploy approval workflow (used by services/safedeploy/*)
pub mod safedeploy_state;
