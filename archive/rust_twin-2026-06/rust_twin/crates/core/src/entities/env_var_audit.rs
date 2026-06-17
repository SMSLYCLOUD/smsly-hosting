use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "env_var_audit")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub env_var_id: i32,
    pub actor_id: i32,
    #[sea_orm(column_type = "String(StringLen::N(20))")]
    pub action: String,
    #[sea_orm(column_type = "Text", nullable)]
    pub old_value_encrypted: Option<String>,
    #[sea_orm(column_type = "Text", nullable)]
    pub new_value_encrypted: Option<String>,
    pub created_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::environment_variable::Entity",
        from = "Column::EnvVarId",
        to = "super::environment_variable::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    EnvVar,
    #[sea_orm(
        belongs_to = "super::user::Entity",
        from = "Column::ActorId",
        to = "super::user::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Actor,
}

impl Related<super::environment_variable::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::EnvVar.def()
    }
}

impl Related<super::user::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Actor.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
