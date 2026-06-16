use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "socialaccount_socialapp")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: i32,
    pub provider: String,
    pub name: String,
    pub client_id: String,
    pub secret: String,
    pub settings: String,
    pub is_active: bool,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {}

impl ActiveModelBehavior for ActiveModel {}
