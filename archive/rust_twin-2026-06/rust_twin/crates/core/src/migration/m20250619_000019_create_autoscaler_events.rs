use sea_orm_migration::prelude::*;

use super::m20250619_000018_create_autoscaler_configs::AutoscalerConfigs;

#[derive(DeriveMigrationName)]
pub struct CreateAutoscalerEvents;

#[async_trait::async_trait]
impl MigrationTrait for CreateAutoscalerEvents {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(AutoscalerEvents::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(AutoscalerEvents::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(AutoscalerEvents::ConfigId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AutoscalerEvents::FromReplicas)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AutoscalerEvents::ToReplicas)
                            .integer()
                            .not_null(),
                    )
                    .col(ColumnDef::new(AutoscalerEvents::Reason).text().null())
                    .col(
                        ColumnDef::new(AutoscalerEvents::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_autoscaler_events_config_id")
                            .from(AutoscalerEvents::Table, AutoscalerEvents::ConfigId)
                            .to(AutoscalerConfigs::Table, AutoscalerConfigs::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(AutoscalerEvents::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
pub enum AutoscalerEvents {
    Table,
    Id,
    ConfigId,
    FromReplicas,
    ToReplicas,
    Reason,
    CreatedAt,
}
