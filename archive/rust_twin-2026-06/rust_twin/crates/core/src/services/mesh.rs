//! Mesh node service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum MeshError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct MeshService {
    pub db: DatabaseConnection,
}

impl MeshService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::mesh_node::Model>, MeshError> {
        Ok(crate::entities::mesh_node::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::mesh_node::Model, MeshError> {
        crate::entities::mesh_node::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| MeshError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(MeshError::NotFound("x".into()).to_string(), "not found: x");
    }
}
