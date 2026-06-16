//! Domain service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum DomainError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct DomainService {
    pub db: DatabaseConnection,
}

impl DomainService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::domain::Model>, DomainError> {
        Ok(crate::entities::domain::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::domain::Model, DomainError> {
        crate::entities::domain::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| DomainError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(DomainError::NotFound("x".into()).to_string(), "not found: x");
    }
}
