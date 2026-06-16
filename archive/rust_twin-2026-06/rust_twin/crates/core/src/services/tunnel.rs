//! Tunnel service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum TunnelError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct TunnelService {
    pub db: DatabaseConnection,
}

impl TunnelService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::tunnel::Model>, TunnelError> {
        Ok(crate::entities::tunnel::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::tunnel::Model, TunnelError> {
        crate::entities::tunnel::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| TunnelError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(TunnelError::NotFound("x".into()).to_string(), "not found: x");
    }
}
