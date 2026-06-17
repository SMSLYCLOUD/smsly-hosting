use cn_core::auth::DrfTokenStore;
use cn_core::config::Config;
use sea_orm::DatabaseConnection;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use crate::middleware::ratelimit::RateLimitState;
use crate::middleware::hmac::HmacState;
use crate::services::webhooks::dispatcher::WebhookDispatcher;

pub mod handlers;
pub mod routes;
pub mod middleware;
pub mod services;
pub mod openapi;

/// Cumulative sum of HTTP request durations (in nanoseconds) since process
/// start. Updated by the `request_duration_middleware` on every served
/// request. Exposed through the `smsly_http_request_duration_seconds_avg`
/// gauge in the `/metrics` output.
pub static TOTAL_REQUEST_DURATION_NANOS: AtomicU64 = AtomicU64::new(0);

/// Total number of HTTP requests served since process start. Updated by the
/// `request_duration_middleware` on every served request.
pub static TOTAL_REQUESTS: AtomicU64 = AtomicU64::new(0);

/// Record another request's wall-clock duration. Truncates to `u64` nanoseconds
/// (sufficient for ~584 years of cumulative time before overflow).
pub fn inc_request_duration(nanos: u128) {
    TOTAL_REQUEST_DURATION_NANOS.fetch_add(nanos as u64, Ordering::Relaxed);
}

/// Record that another request completed. Increments `TOTAL_REQUESTS`.
pub fn inc_total_requests() {
    TOTAL_REQUESTS.fetch_add(1, Ordering::Relaxed);
}

pub struct AppState {
    pub db: DatabaseConnection,
    pub config: Config,
    pub redis: redis::Client,
    pub rate_limit: RateLimitState,
    pub hmac: HmacState,
    /// Process-local store of DRF-style 40-char hex auth tokens -> user id.
    pub drf_tokens: DrfTokenStore,
    /// Webhook dispatcher (Gap 7). Shared across all handlers; spawns
    /// non-blocking dispatch tasks for subscribed webhook events.
    /// The dispatcher is stateless w.r.t. the db — handlers pass
    /// `&state.db` to `publish` / `record_delivery` as needed.
    pub webhook_dispatcher: Arc<WebhookDispatcher>,
}

impl AppState {
    pub fn new(db: DatabaseConnection, config: Config, redis: redis::Client) -> Self {
        let auth_secret = Arc::new(config.auth_secret.clone());
        Self {
            db,
            config,
            redis,
            rate_limit: RateLimitState::with_defaults(),
            hmac: HmacState {
                secret: auth_secret,
                max_clock_skew_secs: 60,
                nonce_cache_size: 10000,
            },
            drf_tokens: DrfTokenStore::new(),
            webhook_dispatcher: Arc::new(WebhookDispatcher::new()),
        }
    }
}
