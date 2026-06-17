use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Serialize, Deserialize)]
#[sea_orm(table_name = "deployments_metric")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub service_id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(40))")]
    pub kind: String,
    pub value_float: f64,
    #[sea_orm(column_type = "Json", nullable)]
    pub tags_json: Option<serde_json::Value>,
    pub recorded_at: DateTimeWithTimeZone,
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
