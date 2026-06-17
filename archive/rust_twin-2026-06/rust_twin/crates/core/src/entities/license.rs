use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "licensing_license")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(255))", unique)]
    pub key: String,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub kind: String, // "trial", "subscription", "perpetual", "enterprise"
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String, // "active", "expired", "revoked", "pending"
    pub max_seats: i32,
    pub activated_at: Option<DateTimeWithTimeZone>,
    pub expires_at: Option<DateTimeWithTimeZone>,
    pub organization_id: Uuid,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
