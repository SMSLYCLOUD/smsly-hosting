use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "billing_cryptoinvoice")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(100))", unique)]
    pub invoice_id: String,
    pub user_id: i32,
    #[sea_orm(column_type = "String(StringLen::N(10))")]
    pub currency: String, // "BTC", "ETH", "USDT"
    pub amount_cents: i64,
    #[sea_orm(column_type = "String(StringLen::N(255))")]
    pub wallet_address: String,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String, // "pending", "paid", "expired", "failed"
    pub paid_at: Option<DateTimeWithTimeZone>,
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
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::User.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
