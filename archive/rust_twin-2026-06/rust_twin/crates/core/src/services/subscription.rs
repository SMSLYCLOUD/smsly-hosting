//! Subscription service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum SubscriptionError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct SubscriptionService {
    pub db: DatabaseConnection,
}

impl SubscriptionService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::subscription::Model>, SubscriptionError> {
        Ok(crate::entities::subscription::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::subscription::Model, SubscriptionError> {
        crate::entities::subscription::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| SubscriptionError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(SubscriptionError::NotFound("x".into()).to_string(), "not found: x");
    }
}
