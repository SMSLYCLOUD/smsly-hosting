use sea_orm_migration::prelude::*;

use super::m20250617_000014_create_subscriptions::Subscription;

#[derive(DeriveMigrationName)]
pub struct CreateUsageAggregates;

#[async_trait::async_trait]
impl MigrationTrait for CreateUsageAggregates {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(UsageAggregates::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(UsageAggregates::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::SubscriptionId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::PeriodStart)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::PeriodEnd)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::CpuSeconds)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::MemoryMbSeconds)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::BandwidthBytes)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::RequestsCount)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::AddonsCostCents)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::BaseCostCents)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::TotalCostCents)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(UsageAggregates::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_usage_aggregate_subscription_id")
                            .from(UsageAggregates::Table, UsageAggregates::SubscriptionId)
                            .to(Subscription::Table, Subscription::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_usage_aggregate_subscription_id")
                    .table(UsageAggregates::Table)
                    .col(UsageAggregates::SubscriptionId)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(UsageAggregates::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum UsageAggregates {
    #[sea_orm(iden = "billing_usageaggregate")]
    Table,
    Id,
    SubscriptionId,
    PeriodStart,
    PeriodEnd,
    CpuSeconds,
    MemoryMbSeconds,
    BandwidthBytes,
    RequestsCount,
    AddonsCostCents,
    BaseCostCents,
    TotalCostCents,
    CreatedAt,
}
