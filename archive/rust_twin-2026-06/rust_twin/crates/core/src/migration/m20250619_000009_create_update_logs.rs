use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateUpdateLogs;

#[async_trait::async_trait]
impl MigrationTrait for CreateUpdateLogs {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(UpdateLogs::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(UpdateLogs::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(UpdateLogs::ServiceId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(UpdateLogs::CommitSha)
                            .string_len(40)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(UpdateLogs::Status)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(UpdateLogs::LogText)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(UpdateLogs::StartedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(UpdateLogs::CompletedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_updatelog_service_id")
                            .from(UpdateLogs::Table, UpdateLogs::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_updatelog_service_started")
                    .table(UpdateLogs::Table)
                    .col(UpdateLogs::ServiceId)
                    .col(UpdateLogs::StartedAt)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(UpdateLogs::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum UpdateLogs {
    #[sea_orm(iden = "deployments_updatelog")]
    Table,
    Id,
    ServiceId,
    CommitSha,
    Status,
    LogText,
    StartedAt,
    CompletedAt,
}
