use sea_orm::entity::prelude::*;

#[derive(Clone, Debug, PartialEq, DeriveEntityModel, Eq)]
#[sea_orm(table_name = "deployments_backuprecord")]
pub struct Model {
    #[sea_orm(primary_key)]
    pub id: Uuid,
    pub service_id: Uuid,                  // FK
    pub storage_backend: String,           // "s3", "local", "ftp"
    pub path: String,                      // S3 key or local path
    pub size_bytes: i64,
    pub sha256: String,                    // 64 hex chars
    pub encryption_algo: String,           // "AES-256-GCM"
    pub encryption_key_id: String,         // FK to key in key store
    pub status: String,                    // "pending", "uploading", "completed", "verified", "failed"
    pub created_at: DateTimeWithTimeZone,
    pub verified_at: Option<DateTimeWithTimeZone>,
    pub expires_at: Option<DateTimeWithTimeZone>,
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
