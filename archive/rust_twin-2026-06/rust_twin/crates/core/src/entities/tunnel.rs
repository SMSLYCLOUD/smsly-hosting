use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_tunnel")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub service_id: Uuid,                  // FK
    pub local_port: i32,                   // port inside the container
    pub public_subdomain: String,          // "myapp.tunnel.smsly.cloud"
    pub public_port: i32,
    pub protocol: String,                  // "http", "https", "tcp", "ws"
    pub status: String,                    // "active", "inactive", "error"
    pub connection_count: i32,
    pub bytes_in: i64,
    pub bytes_out: i64,
    pub last_connected_at: Option<DateTimeWithTimeZone>,
    pub created_at: DateTimeWithTimeZone,
    pub updated_at: DateTimeWithTimeZone,
}

#[derive(Copy, Clone, Debug, EnumIter, DeriveRelation)]
pub enum Relation {
    #[sea_orm(
        belongs_to = "super::service::Entity",
        from = "Column::ServiceId",
        to = "super::service::Column::Id"
    )]
    Service,
}

impl ActiveModelBehavior for ActiveModel {}
