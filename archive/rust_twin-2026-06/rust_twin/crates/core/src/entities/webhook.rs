use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "notifications_webhook")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub user_id: i32,                      // FK (owner)
    pub service_id: Option<Uuid>,          // FK (scope, null = account-wide)
    pub url: String,                       // destination URL (validated for SSRF)
    pub secret: String,                    // HMAC signing key
    pub events: String,                    // JSON array of subscribed events
    pub is_active: bool,
    pub last_triggered_at: Option<DateTimeWithTimeZone>,
    pub last_response_code: Option<i32>,
    pub failure_count: i32,                // consecutive failures
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::UserId",
        to = "super::user::Column::Id"
    )]
    User,
    #[sea_orm(
        belongs_to = "super::service::Entity",
        from = "Column::ServiceId",
        to = "super::service::Column::Id"
    )]
    Service,
}

impl ActiveModelBehavior for ActiveModel {}
