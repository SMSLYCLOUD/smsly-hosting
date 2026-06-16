use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateCrons;

#[async_trait::async_trait]
impl MigrationTrait for CreateCrons {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Crons::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(Crons::Id).uuid().not_null().primary_key())
                    .col(ColumnDef::new(Crons::ServiceId).uuid().not_null())
                    .col(ColumnDef::new(Crons::Name).string_len(255).not_null())
                    .col(ColumnDef::new(Crons::Schedule).string_len(100).not_null())
                    .col(ColumnDef::new(Crons::Command).string_len(500).not_null())
                    .col(
                        ColumnDef::new(Crons::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(Crons::LastRunAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Crons::NextRunAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Crons::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Crons::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_crons_service_id")
                            .from(Crons::Table, Crons::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Crons::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Crons {
    #[sea_orm(iden = "deployments_cronjob")]
    Table,
    Id,
    ServiceId,
    Name,
    Schedule,
    Command,
    IsActive,
    LastRunAt,
    NextRunAt,
    CreatedAt,
    UpdatedAt,
}
