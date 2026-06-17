use cn_core::config::Config;
use sea_orm::DatabaseConnection;

pub mod celery_bridge;
pub mod rollback;
pub mod tasks;

pub struct WorkerState {
    pub db: DatabaseConnection,
    pub config: Config,
    pub redis: redis::Client,
}
