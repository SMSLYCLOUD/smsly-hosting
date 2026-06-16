use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;
use super::m20250617_000013_create_plans::Plan;

#[derive(DeriveMigrationName)]
pub struct CreateSubscriptions;

#[async_trait::async_trait]
impl MigrationTrait for CreateSubscriptions {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Subscription::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Subscription::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Subscription::UserId).integer().not_null())
                    .col(ColumnDef::new(Subscription::PlanId).integer().not_null())
                    .col(
                        ColumnDef::new(Subscription::Status)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::StartedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::CurrentPeriodStart)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::CurrentPeriodEnd)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::CancelAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::CancelledAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::StripeSubscriptionId)
                            .string_len(100)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::CryptomusSubscriptionId)
                            .string_len(100)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::PaymentProvider)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Subscription::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_subscription_user")
                            .from(Subscription::Table, Subscription::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_subscription_plan")
                            .from(Subscription::Table, Subscription::PlanId)
                            .to(Plan::Table, Plan::Id)
                            .on_delete(ForeignKeyAction::Restrict),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Subscription::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Subscription {
    #[sea_orm(iden = "billing_subscription")]
    Table,
    Id,
    UserId,
    PlanId,
    Status,
    StartedAt,
    CurrentPeriodStart,
    CurrentPeriodEnd,
    CancelAt,
    CancelledAt,
    StripeSubscriptionId,
    CryptomusSubscriptionId,
    PaymentProvider,
    CreatedAt,
    UpdatedAt,
}
