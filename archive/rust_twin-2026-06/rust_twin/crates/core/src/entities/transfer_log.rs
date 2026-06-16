use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "deployments_transferlog")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub source_server_id: String,          // server slug/ID
    pub target_server_id: String,
    pub service_id: Uuid,                  // FK
    pub status: String,                    // "pending", "running", "completed", "failed"
    pub phase: String,                     // "pre_transfer", "transferring", "post_transfer", "verification"
    pub progress: i32,                     // 0..100 percent, avoids f64
    pub bytes_transferred: i64,
    pub total_bytes: i64,
    pub error_message: Option<String>,
    pub started_at: DateTimeWithTimeZone,
    pub completed_at: Option<DateTimeWithTimeZone>,
    pub operator_id: i32,                  // FK to user
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::service::Entity",
        from = "Column::ServiceId",
        to = "super::service::Column::Id"
    )]
    Service,
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::OperatorId",
        to = "super::user::Column::Id"
    )]
    Operator,
}

impl ActiveModelBehavior for ActiveModel {}
