use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_meshnode")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub cluster_id: Uuid,                   // FK
    pub hostname: String,                   // unique within cluster
    pub ip_address: String,
    pub port: i32,
    pub role: String,                       // "master" or "worker"
    pub status: String,                     // "online", "offline", "syncing"
    pub last_seen: Option<DateTimeWithTimeZone>,
    pub cpu_capacity: i32,                  // for scheduling
    pub memory_capacity_mb: i32,
    pub current_load: i32,                // 0..100 percent, avoids f64 (Eq not impl)
    pub version: String,                    // smsly version
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

impl Related<super::cluster::Entity> for Entity {
    fn to() -> RelationDef { Relation::Cluster.def() }
}

impl ActiveModelBehavior for ActiveModel {}
