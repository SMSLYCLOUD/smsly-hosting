//! Transfer log service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum TransferError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct TransferService {
    pub db: DatabaseConnection,
}

impl TransferService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::transfer_log::Model>, TransferError> {
        Ok(crate::entities::transfer_log::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::transfer_log::Model, TransferError> {
        crate::entities::transfer_log::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| TransferError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(TransferError::NotFound("x".into()).to_string(), "not found: x");
    }
}
