use sea_orm_migration::prelude::*;

use super::m20250617_000027_create_social_accounts::SocialAccounts;
use super::m20250617_000028_create_social_apps::SocialApps;

#[derive(DeriveMigrationName)]
pub struct CreateSocialTokens;

#[async_trait::async_trait]
impl MigrationTrait for CreateSocialTokens {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(SocialTokens::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(SocialTokens::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(SocialTokens::AccountId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialTokens::AppId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialTokens::Token)
                            .text()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialTokens::TokenSecret)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(SocialTokens::ExpiresAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(SocialTokens::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialTokens::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_socialtoken_account_id")
                            .from(SocialTokens::Table, SocialTokens::AccountId)
                            .to(SocialAccounts::Table, SocialAccounts::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_socialtoken_app_id")
                            .from(SocialTokens::Table, SocialTokens::AppId)
                            .to(SocialApps::Table, SocialApps::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("uq_socialtoken_account_app")
                    .table(SocialTokens::Table)
                    .col(SocialTokens::AccountId)
                    .col(SocialTokens::AppId)
                    .unique()
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(SocialTokens::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum SocialTokens {
    #[sea_orm(iden = "socialaccount_socialtoken")]
    Table,
    Id,
    AccountId,
    AppId,
    Token,
    TokenSecret,
    ExpiresAt,
    CreatedAt,
    UpdatedAt,
}
