use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "autoscaler_config")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub service_id: Uuid,
    pub min_replicas: i32,
    pub max_replicas: i32,
    pub target_cpu_percent: i32,
    pub target_memory_percent: i32,
    pub scale_up_cooldown_secs: i32,
    pub scale_down_cooldown_secs: i32,
    pub is_enabled: bool,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::service::Entity",
        from = "Column::ServiceId",
        to = "super::service::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Service,
}

impl Related<super::service::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Service.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
