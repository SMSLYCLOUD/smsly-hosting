//! `SeaORM` Entity for `billing_plan`.

use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "billing_plan")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: i32,
    #[sea_orm(column_type = "String(StringLen::N(50))", unique)]
    pub code: String,
    #[sea_orm(column_type = "String(StringLen::N(100))")]
    pub name: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub description: Option<String>,
    pub max_services: i32,
    pub max_team_members: i32,
    pub max_domains_per_service: i32,
    pub monthly_price_cents: i32,
    pub yearly_price_cents: i32,
    #[sea_orm(column_type = "String(StringLen::N(100))", nullable)]
    pub stripe_price_id: Option<String>,
    #[sea_orm(column_type = "String(StringLen::N(100))", nullable)]
    pub cryptomus_plan_id: Option<String>,
    pub is_active: bool,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(has_many = "super::subscription::Entity")]
    Subscriptions,
}

impl Related<super::subscription::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Subscriptions.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
