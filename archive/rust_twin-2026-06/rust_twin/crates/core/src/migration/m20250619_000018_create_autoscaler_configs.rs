use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateAutoscalerConfigs;

#[async_trait::async_trait]
impl MigrationTrait for CreateAutoscalerConfigs {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(AutoscalerConfigs::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(AutoscalerConfigs::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::ServiceId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::MinReplicas)
                            .integer()
                            .not_null()
                            .default(1),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::MaxReplicas)
                            .integer()
                            .not_null()
                            .default(10),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::TargetCpuPercent)
                            .integer()
                            .not_null()
                            .default(70),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::TargetMemoryPercent)
                            .integer()
                            .not_null()
                            .default(75),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::ScaleUpCooldownSecs)
                            .integer()
                            .not_null()
                            .default(60),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::ScaleDownCooldownSecs)
                            .integer()
                            .not_null()
                            .default(300),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::IsEnabled)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AutoscalerConfigs::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_autoscaler_configs_service_id")
                            .from(
                                AutoscalerConfigs::Table,
                                AutoscalerConfigs::ServiceId,
                            )
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(AutoscalerConfigs::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
pub enum AutoscalerConfigs {
    Table,
    Id,
    ServiceId,
    MinReplicas,
    MaxReplicas,
    TargetCpuPercent,
    TargetMemoryPercent,
    ScaleUpCooldownSecs,
    ScaleDownCooldownSecs,
    IsEnabled,
    CreatedAt,
    UpdatedAt,
}
