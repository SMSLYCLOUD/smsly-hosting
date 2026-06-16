use cn_core::config::Config;
use sea_orm::DatabaseConnection;

pub mod handlers;
pub mod routes;
pub mod middleware;
pub mod services;

pub struct AppState {
    pub db: DatabaseConnection,
    pub config: Config,
    pub redis: redis::Client,
}
