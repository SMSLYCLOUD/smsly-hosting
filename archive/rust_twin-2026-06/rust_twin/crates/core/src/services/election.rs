//! Node election service.

use sea_orm::DatabaseConnection;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum ElectionError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct ElectionService {
    pub db: DatabaseConnection,
}

impl ElectionService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::node_election::Model>, ElectionError> {
        Ok(crate::entities::node_election::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: i32) -> Result<crate::entities::node_election::Model, ElectionError> {
        crate::entities::node_election::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| ElectionError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(ElectionError::NotFound("x".into()).to_string(), "not found: x");
    }
}
