use sea_orm_migration::prelude::*;

use super::m20250616_000009_create_crons::Crons;

#[derive(DeriveMigrationName)]
pub struct CreateCronRuns;

#[async_trait::async_trait]
impl MigrationTrait for CreateCronRuns {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(CronRuns::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(CronRuns::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(CronRuns::CronId).uuid().not_null())
                    .col(
                        ColumnDef::new(CronRuns::StartedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(CronRuns::CompletedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(CronRuns::Status)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(ColumnDef::new(CronRuns::ExitCode).integer().null())
                    .col(ColumnDef::new(CronRuns::LogText).text().null())
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_cron_runs_cron_id")
                            .from(CronRuns::Table, CronRuns::CronId)
                            .to(Crons::Table, Crons::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(CronRuns::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
pub enum CronRuns {
    Table,
    Id,
    CronId,
    StartedAt,
    CompletedAt,
    Status,
    ExitCode,
    LogText,
}
