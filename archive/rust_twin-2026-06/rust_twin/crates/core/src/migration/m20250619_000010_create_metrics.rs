use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateMetrics;

#[async_trait::async_trait]
impl MigrationTrait for CreateMetrics {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Metrics::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Metrics::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(Metrics::ServiceId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Metrics::Kind)
                            .string_len(40)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Metrics::ValueFloat)
                            .double()
                            .not_null()
                            .default(0.0),
                    )
                    .col(
                        ColumnDef::new(Metrics::TagsJson)
                            .json()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Metrics::RecordedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_metric_service_id")
                            .from(Metrics::Table, Metrics::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_metric_service_recorded")
                    .table(Metrics::Table)
                    .col(Metrics::ServiceId)
                    .col(Metrics::RecordedAt)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Metrics::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Metrics {
    #[sea_orm(iden = "deployments_metric")]
    Table,
    Id,
    ServiceId,
    Kind,
    ValueFloat,
    TagsJson,
    RecordedAt,
}
