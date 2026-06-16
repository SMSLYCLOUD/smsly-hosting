use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateBackupRecords;

#[async_trait::async_trait]
impl MigrationTrait for CreateBackupRecords {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(BackupRecords::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(BackupRecords::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::ServiceId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::StorageBackend)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::Path)
                            .string_len(1024)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::SizeBytes)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::Sha256)
                            .string_len(64)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::EncryptionAlgo)
                            .string_len(30)
                            .not_null()
                            .default("AES-256-GCM"),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::EncryptionKeyId)
                            .string_len(128)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::Status)
                            .string_len(20)
                            .not_null()
                            .default("pending"),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::VerifiedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(BackupRecords::ExpiresAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_backuprecord_service_id")
                            .from(BackupRecords::Table, BackupRecords::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(BackupRecords::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum BackupRecords {
    #[sea_orm(iden = "deployments_backuprecord")]
    Table,
    Id,
    ServiceId,
    StorageBackend,
    Path,
    SizeBytes,
    Sha256,
    EncryptionAlgo,
    EncryptionKeyId,
    Status,
    CreatedAt,
    VerifiedAt,
    ExpiresAt,
}
