use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "deployments_template")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(120))", unique)]
    pub slug: String,
    #[sea_orm(column_type = "String(StringLen::N(150))")]
    pub name: String,
    #[sea_orm(column_type = "Text")]
    pub description: String,
    #[sea_orm(column_type = "String(StringLen::N(40))")]
    pub kind: String,
    #[sea_orm(column_type = "Json")]
    pub content_json: serde_json::Value,
    pub is_official: bool,
    pub created_by_id: Option<i32>,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::CreatedById",
        to = "super::user::Column::Id",
        on_update = "NoAction",
        on_delete = "SetNull"
    )]
    CreatedBy,
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::CreatedBy.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
