use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq, Serialize, Deserialize)]
#[sea_orm(table_name = "core_apitokenaudit")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub token_id: Uuid,                      // FK to deployments_apitoken
    pub actor_id: Option<i32>,               // user that triggered the action (may be null for system)
    #[sea_orm(column_type = "String(StringLen::N(40))")]
    pub action: String,                      // "create", "revoke", "use", "rotate"
    #[sea_orm(column_type = "String(StringLen::N(45))", nullable)]
    pub ip_address: Option<String>,          // IPv4 or IPv6 string
    pub created_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::api_key::Entity",
        from = "Column::TokenId",
        to = "super::api_key::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Token,
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::ActorId",
        to = "super::user::Column::Id",
        on_update = "NoAction",
        on_delete = "SetNull"
    )]
    Actor,
}

impl Related<super::api_key::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Token.def()
    }
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Actor.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
