use sea_orm::entity::prelude::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Serialize, Deserialize)]
#[sea_orm(table_name = "addon_instance_metric")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub addon_id: Uuid,
    #[sea_orm(column_type = "String(StringLen::N(50))")]
    pub kind: String,
    #[sea_orm(column_type = "Double")]
    pub value: f64,
    pub recorded_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::addon::Entity",
        from = "Column::AddonId",
        to = "super::addon::Column::Id",
        on_update = "NoAction",
        on_delete = "Cascade"
    )]
    Addon,
}

impl Related<super::addon::Entity> for Entity {
    fn to() -> RelationDef {
        Relation::Addon.def()
    }
}

impl ActiveModelBehavior for ActiveModel {}
