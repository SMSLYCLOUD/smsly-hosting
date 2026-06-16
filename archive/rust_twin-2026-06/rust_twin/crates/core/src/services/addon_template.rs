//! Addon template service.

use sea_orm::DatabaseConnection;
use thiserror::Error;
use uuid::Uuid;

#[derive(Debug, Error)]
pub enum AddonTemplateError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct AddonTemplateService {
    pub db: DatabaseConnection,
}

impl AddonTemplateService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::addon_template::Model>, AddonTemplateError> {
        Ok(crate::entities::addon_template::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: Uuid) -> Result<crate::entities::addon_template::Model, AddonTemplateError> {
        crate::entities::addon_template::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| AddonTemplateError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(AddonTemplateError::NotFound("x".into()).to_string(), "not found: x");
    }
}
