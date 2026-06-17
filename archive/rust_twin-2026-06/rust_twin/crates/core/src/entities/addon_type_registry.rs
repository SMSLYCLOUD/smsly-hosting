use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "addon_type_registry")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(50))", unique)]
    pub kind: String,
    #[sea_orm(column_type = "String(StringLen::N(200))")]
    pub name: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub description: Option<String>,
    #[sea_orm(column_type = "String(StringLen::N(50))", nullable)]
    pub default_version: Option<String>,
    #[sea_orm(column_type = "Text", nullable)]
    pub supported_versions_json: Option<String>,
    pub is_active: bool,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
