use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;
use super::m20250617_000014_create_subscriptions::Subscription;

#[derive(DeriveMigrationName)]
pub struct CreateInvoices;

#[async_trait::async_trait]
impl MigrationTrait for CreateInvoices {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Invoice::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Invoice::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Invoice::UserId).integer().not_null())
                    .col(ColumnDef::new(Invoice::SubscriptionId).uuid().null())
                    .col(
                        ColumnDef::new(Invoice::AmountCents)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::Currency)
                            .string_len(10)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::Status)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::InvoiceNumber)
                            .string_len(50)
                            .not_null()
                            .unique_key(),
                    )
                    .col(ColumnDef::new(Invoice::Description).text().null())
                    .col(
                        ColumnDef::new(Invoice::PeriodStart)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::PeriodEnd)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::DueDate)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::PaidAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::StripeInvoiceId)
                            .string_len(100)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::CryptomusInvoiceId)
                            .string_len(100)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Invoice::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_invoice_user")
                            .from(Invoice::Table, Invoice::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_invoice_subscription")
                            .from(Invoice::Table, Invoice::SubscriptionId)
                            .to(Subscription::Table, Subscription::Id)
                            .on_delete(ForeignKeyAction::SetNull),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Invoice::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Invoice {
    #[sea_orm(iden = "billing_invoice")]
    Table,
    Id,
    UserId,
    SubscriptionId,
    AmountCents,
    Currency,
    Status,
    InvoiceNumber,
    Description,
    PeriodStart,
    PeriodEnd,
    DueDate,
    PaidAt,
    StripeInvoiceId,
    CryptomusInvoiceId,
    CreatedAt,
    UpdatedAt,
}
