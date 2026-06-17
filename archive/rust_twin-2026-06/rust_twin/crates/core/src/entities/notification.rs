use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "notifications_notification")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub user_id: i32,                        // FK to auth_user
    #[sea_orm(column_type = "String(StringLen::N(80))")]
    pub kind: String,                        // "deploy_success", "deploy_failed", "health_alert", ...
    #[sea_orm(column_type = "String(StringLen::N(200))")]
    pub title: String,
    #[sea_orm(column_type = "Text")]
    pub body: String,
    #[sea_orm(column_type = "String(StringLen::N(2048))", nullable)]
    pub link: Option<String>,
    pub read_at: Option<DateTimeWithTimeZone>,
    pub created_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::UserId",
        to = "super::user::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    User,
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::User.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
