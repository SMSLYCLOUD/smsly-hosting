use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "deployments_updatelog")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub service_id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(40))")]
    pub commit_sha: String,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub log_text: Option<String>,
    pub started_at: DateTimeWithTimeZone,
    pub completed_at: Option<DateTimeWithTimeZone>,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::service::Entity",
        from = "Column::ServiceId",
        to = "super::service::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Service,
}

impl Related<super::service::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Service.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
