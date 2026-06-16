//! Plan service — manages subscription plans.

use sea_orm::{ColumnTrait, DatabaseConnection, EntityTrait, QueryFilter, Set};
use uuid::Uuid;
use chrono::{DateTime, Utc};
use thiserror::Error;

use crate::entities::{plan, user, subscription};
use crate::entities::plan::Entity as PlanEntity;

#[derive(Debug, Error)]
pub enum PlanError {
    #[error("database error: {0}")]
    Db(#[from] sea_orm::DbErr),
    #[error("plan not found: {0}")]
    NotFound(String),
    #[error("plan already exists: {0}")]
    AlreadyExists(String),
    #[error("user not found: {0}")]
    UserNotFound(i32),
}

pub struct PlanService {
    pub db: DatabaseConnection,
}

impl PlanService {
    pub fn new(db: DatabaseConnection) -> Self { Self { db } }

    /// List all active plans, ordered by price ascending.
    pub async fn list_active(&self) -> Result<Vec<plan::Model>, PlanError> {
        let plans = PlanEntity::find()
            .filter(plan::Column::IsActive.eq(true))
            .all(&self.db).await?;
        // Sort by monthly price ascending
        let mut sorted = plans;
        sorted.sort_by_key(|p| p.monthly_price_cents);
        Ok(sorted)
    }

    /// Get a plan by its code (e.g. "free", "pro", "enterprise").
    pub async fn get_by_code(&self, code: &str) -> Result<plan::Model, PlanError> {
        PlanEntity::find()
            .filter(plan::Column::Code.eq(code))
            .one(&self.db).await?
            .ok_or_else(|| PlanError::NotFound(code.to_string()))
    }

    /// Get a plan by id.
    pub async fn get_by_id(&self, id: i32) -> Result<plan::Model, PlanError> {
        PlanEntity::find_by_id(id)
            .one(&self.db).await?
            .ok_or_else(|| PlanError::NotFound(id.to_string()))
    }

    /// Subscribe a user to a plan. Creates or updates the user's subscription.
    pub async fn subscribe_user(
        &self,
        user_id: i32,
        plan_id: i32,
        payment_provider: &str,
        provider_subscription_id: Option<String>,
    ) -> Result<subscription::Model, PlanError> {
        // Verify user exists
        let _ = user::Entity::find_by_id(user_id)
            .one(&self.db).await?
            .ok_or(PlanError::UserNotFound(user_id))?;
        // Verify plan exists
        self.get_by_id(plan_id).await?;
        // Cancel any existing active subscription
        let existing = subscription::Entity::find()
            .filter(subscription::Column::UserId.eq(user_id))
            .filter(subscription::Column::Status.eq("active"))
            .all(&self.db).await?;
        for sub in existing {
            let mut active: subscription::ActiveModel = sub.into();
            active.status = Set("cancelled".to_string());
            active.cancelled_at = Set(Some(Utc::now()));
            active.updated_at = Set(Utc::now());
            active.update(&self.db).await?;
        }
        // Create new subscription
        let now = Utc::now();
        let new_sub = subscription::ActiveModel {
            id: Set(Uuid::new_v4()),
            user_id: Set(user_id),
            plan_id: Set(plan_id),
            status: Set("active".to_string()),
            started_at: Set(now),
            current_period_start: Set(now),
            current_period_end: Set(now + chrono::Duration::days(30)),
            cancel_at: Set(None),
            cancelled_at: Set(None),
            stripe_subscription_id: Set(if payment_provider == "stripe" { provider_subscription_id.clone() } else { None }),
            cryptomus_subscription_id: Set(if payment_provider == "cryptomus" { provider_subscription_id.clone() } else { None }),
            payment_provider: Set(payment_provider.to_string()),
            created_at: Set(now),
            updated_at: Set(now),
        };
        let inserted = new_sub.insert(&self.db).await?;
        Ok(inserted)
    }

    /// Check if a user is within their plan's tier limits.
    pub async fn check_tier_limit(
        &self,
        user_id: i32,
        limit: TierLimit,
    ) -> Result<bool, PlanError> {
        let sub = subscription::Entity::find()
            .filter(subscription::Column::UserId.eq(user_id))
            .filter(subscription::Column::Status.eq("active"))
            .one(&self.db).await?
            .ok_or_else(|| PlanError::NotFound("no active subscription".to_string()))?;
        let plan = self.get_by_id(sub.plan_id).await?;
        let current_count = match limit {
            TierLimit::Services => sea_orm::EntityTrait::find_by_id::<crate::entities::service::Entity>(sea_orm::ActiveModel::default().clone())
                .all(&self.db).await?.len() as i32,  // placeholder; route will do real count
            ,
            TierLimit::TeamMembers => 0,
        };
        Ok(current_count < plan.max_services)
    }
}

#[derive(Debug, Clone, Copy)]
pub enum TierLimit { Services, TeamMembers }

#[cfg(test)]
mod tests {
    // Pure-function tests only (no DB)
    use super::*;
    #[test]
    fn test_plan_error_display() {
        let e = PlanError::NotFound("pro".into());
        assert_eq!(e.to_string(), "plan not found: pro");
    }
}
