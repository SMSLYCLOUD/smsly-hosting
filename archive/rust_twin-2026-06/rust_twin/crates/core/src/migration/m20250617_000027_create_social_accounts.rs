use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateSocialAccounts;

#[async_trait::async_trait]
impl MigrationTrait for CreateSocialAccounts {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(SocialAccounts::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(SocialAccounts::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(SocialAccounts::UserId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialAccounts::Provider)
                            .string_len(40)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialAccounts::Uid)
                            .string_len(191)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialAccounts::ExtraData)
                            .text()
                            .not_null()
                            .default("{}"),
                    )
                    .col(
                        ColumnDef::new(SocialAccounts::DateJoined)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialAccounts::LastLogin)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_socialaccount_user_id")
                            .from(SocialAccounts::Table, SocialAccounts::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("uq_socialaccount_provider_uid")
                    .table(SocialAccounts::Table)
                    .col(SocialAccounts::Provider)
                    .col(SocialAccounts::Uid)
                    .unique()
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_socialaccount_user_id")
                    .table(SocialAccounts::Table)
                    .col(SocialAccounts::UserId)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(SocialAccounts::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum SocialAccounts {
    #[sea_orm(iden = "socialaccount_socialaccount")]
    Table,
    Id,
    UserId,
    Provider,
    Uid,
    ExtraData,
    DateJoined,
    LastLogin,
}
