use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "notifications_webhookdelivery")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub webhook_id: Uuid,                    // FK to notifications_webhook
    #[sea_orm(column_type = "String(StringLen::N(100))")]
    pub event_type: String,                  // e.g. "deploy.success", "deploy.failed"
    #[sea_orm(column_type = "Text")]
    pub payload_json: String,                // serialized JSON payload sent to the endpoint
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String,                      // "pending", "success", "failed", "dead"
    pub attempts: i32,                       // total delivery attempts
    pub last_attempt_at: Option<DateTimeWithTimeZone>,
    pub response_code: Option<i32>,          // HTTP status code of the last attempt
    #[sea_orm(column_type = "Text", nullable)]
    pub response_body: Option<String>,       // truncated body of the last response
    pub next_retry_at: Option<DateTimeWithTimeZone>,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::webhook::Entity",
        from = "Column::WebhookId",
        to = "super::webhook::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Webhook,
}

impl Related<super::webhook::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Webhook.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
