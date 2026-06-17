use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "billing_usageaggregate")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub subscription_id: Uuid,
    pub period_start: DateTimeWithTimeZone,
    pub period_end: DateTimeWithTimeZone,
    pub cpu_seconds: i64,
    pub memory_mb_seconds: i64,
    pub bandwidth_bytes: i64,
    pub requests_count: i64,
    pub addons_cost_cents: i64,
    pub base_cost_cents: i64,
    pub total_cost_cents: i64,
    pub created_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::subscription::Entity",
        from = "Column::SubscriptionId",
        to = "super::subscription::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Subscription,
}

impl Related<super::subscription::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Subscription.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
