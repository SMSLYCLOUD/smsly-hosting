use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "socialaccount_socialtoken")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: i32,
    pub account_id: i32,
    pub app_id: i32,
    pub token: String,
    pub token_secret: Option<String>,
    pub expires_at: Option<DateTimeWithTimeZone>,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::social_account::Entity",
        from = "Column::AccountId",
        to = "super::social_account::Column::Id"
    )]
    Account,
    #[sea_orm(
        belongs_to = "super::social_app::Entity",
        from = "Column::AppId",
        to = "super::social_app::Column::Id"
    )]
    App,
}

impl Related<super::social_account::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Account.def()
    }
}

impl Related<super::social_app::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::App.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
