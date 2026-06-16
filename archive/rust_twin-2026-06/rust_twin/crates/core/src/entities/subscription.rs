//! `SeaORM` Entity for `billing_subscription`.

use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "billing_subscription")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub id: Uuid,
    pub user_id: i32,
    pub plan_id: i32,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String, // active, cancelled, past_due, trial
    pub started_at: DateTimeWithTimeZone,
    pub current_period_start: DateTimeWithTimeZone,
    pub current_period_end: DateTimeWithTimeZone,
    pub cancel_at: Option<DateTimeWithTimeZone>,
    pub cancelled_at: Option<DateTimeWithTimeZone>,
    #[sea_orm(column_type = "String(StringLen::N(100))", nullable)]
    pub stripe_subscription_id: Option<String>,
    #[sea_orm(column_type = "String(StringLen::N(100))", nullable)]
    pub cryptomus_subscription_id: Option<String>,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub payment_provider: String, // "stripe" or "cryptomus"
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::UserId",
        to = "super::user::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    User,
    #[sea_orm(
        belongs_to = "super::plan::Entity",
        from = "Column::PlanId",
        to = "super::plan::Column::Id",
        on_update = "NoAction",
        on_delete = "Restrict"
    )]
    Plan,
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::User.def()
    }
}

impl Related<super::plan::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Plan.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
