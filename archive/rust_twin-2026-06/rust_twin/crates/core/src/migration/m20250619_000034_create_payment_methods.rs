use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreatePaymentMethods;

#[async_trait::async_trait]
impl MigrationTrait for CreatePaymentMethods {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(PaymentMethods::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(PaymentMethods::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::UserId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::Provider)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::Token)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::Last4)
                            .string_len(4)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::Brand)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::ExpiresAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::IsDefault)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PaymentMethods::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_paymentmethod_user_id")
                            .from(PaymentMethods::Table, PaymentMethods::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_paymentmethod_user_id")
                    .table(PaymentMethods::Table)
                    .col(PaymentMethods::UserId)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(PaymentMethods::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum PaymentMethods {
    #[sea_orm(iden = "billing_paymentmethod")]
    Table,
    Id,
    UserId,
    Provider,
    Token,
    Last4,
    Brand,
    ExpiresAt,
    IsDefault,
    CreatedAt,
    UpdatedAt,
}
