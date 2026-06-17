use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "billing_paymentmethod")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub user_id: i32,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub provider: String, // "stripe", "paystack", "paypal"
    #[sea_orm(column_type = "String(StringLen::N(255))")]
    pub token: String,
    #[sea_orm(column_type = "String(StringLen::N(4))")]
    pub last4: String,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub brand: String, // "visa", "mastercard", "amex"
    pub expires_at: Option<DateTimeWithTimeZone>,
    pub is_default: bool,
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
