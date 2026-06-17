use sea_orm_migration::prelude::*;

use super::m20250616_000005_create_addons::Addons;

#[derive(DeriveMigrationName)]
pub struct CreateAddonInstanceMetrics;

#[async_trait::async_trait]
impl MigrationTrait for CreateAddonInstanceMetrics {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(AddonInstanceMetrics::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(AddonInstanceMetrics::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(AddonInstanceMetrics::AddonId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AddonInstanceMetrics::Kind)
                            .string_len(50)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AddonInstanceMetrics::Value)
                            .double()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AddonInstanceMetrics::RecordedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_addon_instance_metrics_addon_id")
                            .from(
                                AddonInstanceMetrics::Table,
                                AddonInstanceMetrics::AddonId,
                            )
                            .to(Addons::Table, Addons::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(
                Table::drop()
                    .table(AddonInstanceMetrics::Table)
                    .to_owned(),
            )
            .await
    }
}

#[derive(DeriveIden)]
pub enum AddonInstanceMetrics {
    Table,
    Id,
    AddonId,
    Kind,
    Value,
    RecordedAt,
}
