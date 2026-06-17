//! Service-replication orchestration (Gap 10).
//!
//! Manages per-service replication targets (e.g. async warm standbys in
//! other regions). The `replication_target` entity is owned by Agent 1; if
//! it has not been generated yet the public functions return
//! `DbErr::Custom` so the API surface compiles and the routes can be wired
//! up cleanly.
//!
//! TODO: requires `replication_target` entity from Agent 1.

use sea_orm::DatabaseConnection;
use uuid::Uuid;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ReplicationError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("replication_target entity not yet defined")]
    NotDefined,
}

/// Placeholder model. The real type lives in
/// `crate::entities::replication_target` once Agent 1 lands the entity.
#[derive(Debug, Clone)]
pub struct ReplicationTarget {
    pub id: Uuid,
    pub service_id: Uuid,
    pub target_region: String,
    pub last_sync_at: Option<chrono::DateTime<chrono::Utc>>,
    pub lag_seconds: i32,
    pub status: String,
}

pub struct ReplicationService {
    #[allow(dead_code)]
    pub db: DatabaseConnection,
}

impl ReplicationService {
    #[allow(dead_code)]
    pub fn new(db: DatabaseConnection) -> Self {
        Self { db }
    }
}

pub async fn create_replication_target(
    _db: &DatabaseConnection,
    _source_service_id: Uuid,
    _target_region: String,
) -> Result<ReplicationTarget, sea_orm::DbErr> {
    // TODO: requires replication_target entity from Agent 1
    Err(sea_orm::DbErr::Custom(
        "replication_target entity not yet defined".into(),
    ))
}

pub async fn list_replication_targets(
    _db: &DatabaseConnection,
    _service_id: Uuid,
) -> Result<Vec<ReplicationTarget>, sea_orm::DbErr> {
    // TODO: requires replication_target entity from Agent 1
    Err(sea_orm::DbErr::Custom(
        "replication_target entity not yet defined".into(),
    ))
}

pub async fn record_sync(
    _db: &DatabaseConnection,
    _target_id: Uuid,
    _lag_seconds: i32,
) -> Result<(), sea_orm::DbErr> {
    // TODO: requires replication_target entity from Agent 1
    Err(sea_orm::DbErr::Custom(
        "replication_target entity not yet defined".into(),
    ))
}

pub async fn delete_replication_target(
    _db: &DatabaseConnection,
    _id: Uuid,
) -> Result<(), sea_orm::DbErr> {
    // TODO: requires replication_target entity from Agent 1
    Err(sea_orm::DbErr::Custom(
        "replication_target entity not yet defined".into(),
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        let e = ReplicationError::NotDefined;
        assert_eq!(e.to_string(), "replication_target entity not yet defined");
    }
}
