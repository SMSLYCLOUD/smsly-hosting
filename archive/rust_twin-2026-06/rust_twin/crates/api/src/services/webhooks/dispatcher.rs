//! Webhook dispatcher — signs, validates, retries.

use std::sync::Arc;
use std::time::Duration;
use chrono::Utc;
use sea_orm::DatabaseConnection;
use serde::Serialize;
use thiserror::Error;
use tokio::time::sleep;
use tracing::{error, info, warn};

use super::hmac;
use super::retry::RetryPolicy;
use super::ssrf_guard::{validate_url, SsrfError};
use cn_core::entities::webhook;

#[derive(Debug, Serialize, Clone)]
pub struct WebhookEvent {
    pub event_type: String,
    pub timestamp: chrono::DateTime<Utc>,
    pub data: serde_json::Value,
}

#[derive(Debug, Error)]
pub enum DispatchError {
    #[error("SSRF guard rejected URL: {0}")]
    Ssrf(#[from] SsrfError),
    #[error("HTTP request failed: {0}")]
    Http(String),
    #[error("non-success status: {0}")]
    Status(u16),
}

pub struct WebhookDispatcher {
    pub db: Arc<DatabaseConnection>,
    pub retry_policy: RetryPolicy,
    pub http_client: reqwest::Client,
}

impl WebhookDispatcher {
    pub fn new(db: Arc<DatabaseConnection>) -> Self {
        let http_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .user_agent("smsly-webhook/1.0")
            .build()
            .expect("reqwest::Client::builder is infallible");
        Self { db, retry_policy: RetryPolicy::default(), http_client }
    }

    pub fn new_for_test() -> Self {
        let http_client = reqwest::Client::builder()
            .timeout(Duration::from_secs(30))
            .user_agent("smsly-webhook/1.0")
            .build()
            .expect("reqwest::Client::builder is infallible");
        let db = Arc::new(sea_orm::DatabaseConnection::Disconnected);
        Self { db, retry_policy: RetryPolicy::default(), http_client }
    }

    pub async fn dispatch(&self, hook: &webhook::Model, event: &WebhookEvent) -> Result<(), DispatchError> {
        // SSRF guard
        validate_url(&hook.url)?;

        // Sign the payload
        let payload = serde_json::to_vec(event).map_err(|e| DispatchError::Http(e.to_string()))?;
        let signature = hmac::sign(&hook.secret, &payload);

        // Send with retry
        for attempt in 0..self.retry_policy.max_attempts {
            let delay = self.retry_policy.delay_for_attempt(attempt);
            let response = self.http_client
                .post(&hook.url)
                .header("Content-Type", "application/json")
                .header("X-Smsly-Signature", &signature)
                .header("X-Smsly-Event", &event.event_type)
                .header("X-Smsly-Delivery", &uuid::Uuid::new_v4().to_string())
                .body(payload.clone())
                .send().await;

            match response {
                Ok(resp) => {
                    let status = resp.status().as_u16();
                    if resp.status().is_success() {
                        info!("Webhook delivered: hook={} event={} status={}", hook.id, event.event_type, status);
                        return Ok(());
                    }
                    if !self.retry_policy.should_retry(attempt, Some(status as i32)) {
                        warn!("Webhook permanent failure: hook={} status={}", hook.id, status);
                        return Err(DispatchError::Status(status));
                    }
                    warn!("Webhook transient failure: hook={} status={}, retrying in {:?}", hook.id, status, delay);
                }
                Err(e) => {
                    if !self.retry_policy.should_retry(attempt, None) {
                        error!("Webhook permanent network failure: hook={} err={}", hook.id, e);
                        return Err(DispatchError::Http(e.to_string()));
                    }
                    warn!("Webhook transient network failure: hook={} err={}, retrying in {:?}", hook.id, e, delay);
                }
            }
            sleep(delay).await;
        }
        Err(DispatchError::Status(0))  // exhausted retries
    }
}
