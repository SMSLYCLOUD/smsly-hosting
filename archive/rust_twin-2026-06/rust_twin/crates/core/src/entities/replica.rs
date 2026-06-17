use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "deployments_replica")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub source_service_id: Uuid,
    pub target_region_id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub status: String,
    pub last_synced_at: Option<DateTimeWithTimeZone>,
    pub lag_seconds: i32,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::service::Entity",
        from = "Column::SourceServiceId",
        to = "super::service::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    SourceService,
}

impl Related<super::service::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::SourceService.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
