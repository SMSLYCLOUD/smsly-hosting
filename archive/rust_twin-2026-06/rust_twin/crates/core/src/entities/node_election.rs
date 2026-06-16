use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_nodeelection")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: i32,                            // auto-increment, singleton per cluster
    pub cluster_id: Uuid,                   // FK
    pub master_node_id: Option<Uuid>,       // FK to mesh_node
    pub term: i64,                          // Raft-like term number
    pub last_election_at: DateTimeWithTimeZone,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::cluster::Entity",
        from = "Column::ClusterId",
        to = "super::cluster::Column::Id"
    )]
    Cluster,
}

impl ActiveModelBehavior for ActiveModel {}
