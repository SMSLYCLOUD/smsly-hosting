use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "autoscaler_event")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub config_id: Uuid,
    pub from_replicas: i32,
    pub to_replicas: i32,
    #[sea_orm(column_type = "Text", nullable)]
    pub reason: Option<String>,
    pub created_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::autoscaler_config::Entity",
        from = "Column::ConfigId",
        to = "super::autoscaler_config::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Config,
}

impl Related<super::autoscaler_config::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Config.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
