use sea_orm_migration::prelude::*;

use super::m20250616_000008_create_api_keys::ApiKeys;
use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateApiTokenAudits;

#[async_trait::async_trait]
impl MigrationTrait for CreateApiTokenAudits {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(ApiTokenAudits::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(ApiTokenAudits::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(ApiTokenAudits::TokenId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(ApiTokenAudits::ActorId)
                            .integer()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(ApiTokenAudits::Action)
                            .string_len(40)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(ApiTokenAudits::IpAddress)
                            .string_len(45)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(ApiTokenAudits::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_apitokenaudit_token_id")
                            .from(ApiTokenAudits::Table, ApiTokenAudits::TokenId)
                            .to(ApiKeys::Table, ApiKeys::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_apitokenaudit_actor_id")
                            .from(ApiTokenAudits::Table, ApiTokenAudits::ActorId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::SetNull),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("idx_apitokenaudit_token_created")
                    .table(ApiTokenAudits::Table)
                    .col(ApiTokenAudits::TokenId)
                    .col(ApiTokenAudits::CreatedAt)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(ApiTokenAudits::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum ApiTokenAudits {
    #[sea_orm(iden = "core_apitokenaudit")]
    Table,
    Id,
    TokenId,
    ActorId,
    Action,
    IpAddress,
    CreatedAt,
}
