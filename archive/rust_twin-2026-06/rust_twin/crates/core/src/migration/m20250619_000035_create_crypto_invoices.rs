use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateCryptoInvoices;

#[async_trait::async_trait]
impl MigrationTrait for CreateCryptoInvoices {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(CryptoInvoices::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(CryptoInvoices::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::InvoiceId)
                            .string_len(100)
                            .not_null()
                            .unique_key(),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::UserId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::Currency)
                            .string_len(10)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::AmountCents)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::WalletAddress)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::Status)
                            .string_len(20)
                            .not_null()
                            .default("pending"),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::PaidAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(CryptoInvoices::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_cryptoinvoice_user_id")
                            .from(CryptoInvoices::Table, CryptoInvoices::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_cryptoinvoice_user_id")
                    .table(CryptoInvoices::Table)
                    .col(CryptoInvoices::UserId)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(CryptoInvoices::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum CryptoInvoices {
    #[sea_orm(iden = "billing_cryptoinvoice")]
    Table,
    Id,
    InvoiceId,
    UserId,
    Currency,
    AmountCents,
    WalletAddress,
    Status,
    PaidAt,
    CreatedAt,
    UpdatedAt,
}
