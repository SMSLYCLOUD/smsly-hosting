use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;
use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateTransferLogs;

#[async_trait::async_trait]
impl MigrationTrait for CreateTransferLogs {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(TransferLogs::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(TransferLogs::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::SourceServerId)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::TargetServerId)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::ServiceId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::Status)
                            .string_len(20)
                            .not_null()
                            .default("pending"),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::Phase)
                            .string_len(30)
                            .not_null()
                            .default("pre_transfer"),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::Progress)
                            .double()
                            .not_null()
                            .default(0.0),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::BytesTransferred)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::TotalBytes)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::ErrorMessage)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::StartedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::CompletedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(TransferLogs::OperatorId)
                            .integer()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_transferlog_service_id")
                            .from(TransferLogs::Table, TransferLogs::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_transferlog_operator_id")
                            .from(TransferLogs::Table, TransferLogs::OperatorId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::NoAction),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_transferlog_service_started")
                    .table(TransferLogs::Table)
                    .col(TransferLogs::ServiceId)
                    .col(TransferLogs::StartedAt)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(TransferLogs::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum TransferLogs {
    #[sea_orm(iden = "deployments_transferlog")]
    Table,
    Id,
    SourceServerId,
    TargetServerId,
    ServiceId,
    Status,
    Phase,
    Progress,
    BytesTransferred,
    TotalBytes,
    ErrorMessage,
    StartedAt,
    CompletedAt,
    OperatorId,
}
