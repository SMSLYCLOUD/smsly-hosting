use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateReplicas;

#[async_trait::async_trait]
impl MigrationTrait for CreateReplicas {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Replicas::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Replicas::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(Replicas::SourceServiceId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Replicas::TargetRegionId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Replicas::Status)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Replicas::LastSyncedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Replicas::LagSeconds)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Replicas::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Replicas::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_replica_source_service_id")
                            .from(Replicas::Table, Replicas::SourceServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_replica_source_status")
                    .table(Replicas::Table)
                    .col(Replicas::SourceServiceId)
                    .col(Replicas::Status)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Replicas::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Replicas {
    #[sea_orm(iden = "deployments_replica")]
    Table,
    Id,
    SourceServiceId,
    TargetRegionId,
    Status,
    LastSyncedAt,
    LagSeconds,
    CreatedAt,
    UpdatedAt,
}
