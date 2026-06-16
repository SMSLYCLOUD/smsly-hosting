//! Webhook service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum WebhookError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct WebhookService {
    pub db: DatabaseConnection,
}

impl WebhookService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::webhook::Model>, WebhookError> {
        Ok(crate::entities::webhook::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::webhook::Model, WebhookError> {
        crate::entities::webhook::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| WebhookError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(WebhookError::NotFound("x".into()).to_string(), "not found: x");
    }
}
