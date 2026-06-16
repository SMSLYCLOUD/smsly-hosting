use cn_core::config::Config;
use sea_orm::DatabaseConnection;
use std::sync::Arc;
use crate::middleware::ratelimit::RateLimitState;
use crate::middleware::hmac::HmacState;

pub mod handlers;
pub mod routes;
pub mod middleware;
pub mod services;

pub struct AppState {
    pub db: DatabaseConnection,
    pub config: Config,
    pub redis: redis::Client,
    pub rate_limit: RateLimitState,
    pub hmac: HmacState,
}

impl AppState {
    pub fn new(db: DatabaseConnection, config: Config, redis: redis::Client) -> Self {
        Self {
            db,
            config,
            redis,
            rate_limit: RateLimitState::with_defaults(),
            hmac: HmacState {
                secret: Arc::new(String::new()),
                max_clock_skew_secs: 60,
                nonce_cache_size: 10000,
            },
        }
    }
}
