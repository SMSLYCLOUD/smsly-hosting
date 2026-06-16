use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;
use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateWebhooks;

#[async_trait::async_trait]
impl MigrationTrait for CreateWebhooks {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Webhooks::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Webhooks::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Webhooks::UserId).integer().not_null())
                    .col(ColumnDef::new(Webhooks::ServiceId).uuid().null())
                    .col(
                        ColumnDef::new(Webhooks::Url)
                            .string_len(2048)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Webhooks::Secret)
                            .string_len(128)
                            .not_null(),
                    )
                    .col(ColumnDef::new(Webhooks::Events).text().not_null())
                    .col(
                        ColumnDef::new(Webhooks::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(Webhooks::LastTriggeredAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Webhooks::LastResponseCode)
                            .integer()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Webhooks::FailureCount)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Webhooks::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Webhooks::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_webhooks_user_id")
                            .from(Webhooks::Table, Webhooks::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_webhooks_service_id")
                            .from(Webhooks::Table, Webhooks::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::SetNull),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Webhooks::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Webhooks {
    #[sea_orm(iden = "notifications_webhook")]
    Table,
    Id,
    UserId,
    ServiceId,
    Url,
    Secret,
    Events,
    IsActive,
    LastTriggeredAt,
    LastResponseCode,
    FailureCount,
    CreatedAt,
    UpdatedAt,
}
