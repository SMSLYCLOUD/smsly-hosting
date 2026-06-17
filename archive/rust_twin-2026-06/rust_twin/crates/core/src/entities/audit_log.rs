use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "deployments_auditlog")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub actor_id: Option<i32>,
    #[sea_orm(column_type = "String(StringLen::N(80))")]
    pub action: String,
    #[sea_orm(column_type = "String(StringLen::N(80))")]
    pub target_type: String,
    pub target_id: Option<Uuid>,
    #[sea_orm(column_type = "String(StringLen::N(64))", nullable)]
    pub ip_address: Option<String>,
    #[sea_orm(column_type = "String(StringLen::N(512))", nullable)]
    pub user_agent: Option<String>,
    #[sea_orm(column_type = "Json", nullable)]
    pub metadata_json: Option<serde_json::Value>,
    pub created_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::ActorId",
        to = "super::user::Column::Id",
        on_update = "NoAction",
        on_delete = "SetNull"
    )]
    Actor,
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Actor.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
