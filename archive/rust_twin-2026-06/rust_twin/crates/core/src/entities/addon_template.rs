use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_addontemplate")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub slug: String,
    pub name: String,
    pub description: String,
    pub category: String,
    pub image: String,
    pub default_port: i32,
    pub env_schema: String,
    pub volumes: String,
    pub ports: String,
    pub healthcheck: Option<String>,
    pub documentation_url: Option<String>,
    pub is_active: bool,
    pub tier: String,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
