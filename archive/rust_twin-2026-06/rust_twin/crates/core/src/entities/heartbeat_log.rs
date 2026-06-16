use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_heartbeatlog")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: i64,                            // auto-increment
    pub node_id: Uuid,                      // FK
    pub term: i64,                          // election term
    pub cpu_usage: i32,                    // 0..100 percent, avoids f64
    pub memory_usage_mb: i32,
    pub active_deployments: i32,
    pub is_master: bool,                    // claimed master for this term
    pub received_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::mesh_node::Entity",
        from = "Column::NodeId",
        to = "super::mesh_node::Column::Id"
    )]
    Node,
}

impl Related<super::mesh_node::Entity> for Entity {
    fn to() -> RelationDef { Relation::Node.def() }
}

impl ActiveModelBehavior for ActiveModel {}
