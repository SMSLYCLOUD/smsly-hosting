//! SSO (social auth) service — covers social_account, social_app, social_token.

use sea_orm::DatabaseConnection;
use thiserror::Error;

#[derive(Debug, Error)]
pub enum SsoError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("not found: {0}")]
    NotFound(String),
}

pub struct SocialAccountService {
    pub db: DatabaseConnection,
}

impl SocialAccountService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::social_account::Model>, SsoError> {
        Ok(crate::entities::social_account::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: i32) -> Result<crate::entities::social_account::Model, SsoError> {
        crate::entities::social_account::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| SsoError::NotFound(id.to_string()))
    }
}

pub struct SocialAppService {
    pub db: DatabaseConnection,
}

impl SocialAppService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::social_app::Model>, SsoError> {
        Ok(crate::entities::social_app::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: i32) -> Result<crate::entities::social_app::Model, SsoError> {
        crate::entities::social_app::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| SsoError::NotFound(id.to_string()))
    }
}

pub struct SocialTokenService {
    pub db: DatabaseConnection,
}

impl SocialTokenService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    pub async fn list(&self) -> Result<Vec<crate::entities::social_token::Model>, SsoError> {
        Ok(crate::entities::social_token::Entity::find().all(&self.db).await?)
    }

    pub async fn get_by_id(&self, id: i32) -> Result<crate::entities::social_token::Model, SsoError> {
        crate::entities::social_token::Entity::find_by_id(id).one(&self.db).await?
            .ok_or_else(|| SsoError::NotFound(id.to_string()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_error_display() {
        assert_eq!(SsoError::NotFound("x".into()).to_string(), "not found: x");
    }
}
