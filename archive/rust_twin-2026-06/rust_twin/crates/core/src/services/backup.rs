//! Backup record service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum BackupError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct BackupService {
    pub db: DatabaseConnection,
}

impl BackupService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::backup_record::Model>, BackupError> {
        Ok(crate::entities::backup_record::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::backup_record::Model, BackupError> {
        crate::entities::backup_record::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| BackupError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(BackupError::NotFound("x".into()).to_string(), "not found: x");
    }
}
