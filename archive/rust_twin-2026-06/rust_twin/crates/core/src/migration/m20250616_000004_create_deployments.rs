use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateDeployments;

#[async_trait::async_trait]
impl MigrationTrait for CreateDeployments {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Deployments::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(Deployments::Id).uuid().not_null().primary_key())
                    .col(ColumnDef::new(Deployments::ServiceId).uuid().not_null())
                    .col(
                        ColumnDef::new(Deployments::CommitHash)
                            .string_len(40)
                            .not_null(),
                    )
                    .col(ColumnDef::new(Deployments::Status).string_len(20).not_null())
                    .col(
                        ColumnDef::new(Deployments::StartedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Deployments::FinishedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Deployments::IsRollback)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(Deployments::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_deployments_service_id")
                            .from(Deployments::Table, Deployments::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Deployments::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Deployments {
    #[sea_orm(iden = "deployments_deployment")]
    Table,
    Id,
    ServiceId,
    CommitHash,
    Status,
    StartedAt,
    FinishedAt,
    IsRollback,
    CreatedAt,
}
