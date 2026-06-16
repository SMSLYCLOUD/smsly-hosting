use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateApiKeys;

#[async_trait::async_trait]
impl MigrationTrait for CreateApiKeys {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(ApiKeys::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(ApiKeys::Id).uuid().not_null().primary_key())
                    .col(ColumnDef::new(ApiKeys::UserId).integer().not_null())
                    .col(ColumnDef::new(ApiKeys::Name).string_len(100).not_null())
                    .col(ColumnDef::new(ApiKeys::Prefix).string_len(12).not_null())
                    .col(ColumnDef::new(ApiKeys::TokenHash).string_len(64).not_null())
                    .col(
                        ColumnDef::new(ApiKeys::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(ApiKeys::LastUsedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(ApiKeys::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_api_keys_user_id")
                            .from(ApiKeys::Table, ApiKeys::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(ApiKeys::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum ApiKeys {
    #[sea_orm(iden = "deployments_apitoken")]
    Table,
    Id,
    UserId,
    Name,
    Prefix,
    TokenHash,
    IsActive,
    LastUsedAt,
    CreatedAt,
}
