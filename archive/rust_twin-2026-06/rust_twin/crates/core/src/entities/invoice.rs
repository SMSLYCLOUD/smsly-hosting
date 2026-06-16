//! `SeaORM` Entity for `billing_invoice`.

use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "billing_invoice")]
pub struct Model {
    #[sea_orm(primary_key, auto_increment = false)]
    pub id: Uuid,
    pub user_id: i32,
    pub subscription_id: Option<Uuid>,
    pub amount_cents: i32,
    #[sea_orm(column_type = "String(StringLen::N(10))")]
    pub currency: String, // "USD", "EUR"
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String, // draft, open, paid, void, uncollectible
    #[sea_orm(column_type = "String(StringLen::N(50))", unique)]
    pub invoice_number: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub description: Option<String>,
    pub period_start: Option<DateTimeWithTimeZone>,
    pub period_end: Option<DateTimeWithTimeZone>,
    pub due_date: Option<DateTimeWithTimeZone>,
    pub paid_at: Option<DateTimeWithTimeZone>,
    #[sea_orm(column_type = "String(StringLen::N(100))", nullable)]
    pub stripe_invoice_id: Option<String>,
    #[sea_orm(column_type = "String(StringLen::N(100))", nullable)]
    pub cryptomus_invoice_id: Option<String>,
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
        belongs_to = "super::subscription::Entity",
        from = "Column::SubscriptionId",
        to = "super::subscription::Column::Id",
        on_update = "NoAction",
        on_delete = "SetNull"
    )]
    Subscription,
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::User.def()
    }
}

impl Related<super::subscription::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Subscription.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
