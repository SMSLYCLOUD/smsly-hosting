use sea_orm_migration::prelude::*;

use super::m20250617_000024_create_webhooks::Webhooks;

#[derive(DeriveMigrationName)]
pub struct CreateWebhookDeliveries;

#[async_trait::async_trait]
impl MigrationTrait for CreateWebhookDeliveries {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(WebhookDeliveries::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(WebhookDeliveries::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::WebhookId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::EventType)
                            .string_len(100)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::PayloadJson)
                            .text()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::Status)
                            .string_len(20)
                            .not_null()
                            .default("pending"),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::Attempts)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::LastAttemptAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::ResponseCode)
                            .integer()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::ResponseBody)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::NextRetryAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(WebhookDeliveries::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_webhookdelivery_webhook_id")
                            .from(
                                WebhookDeliveries::Table,
                                WebhookDeliveries::WebhookId,
                            )
                            .to(Webhooks::Table, Webhooks::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("idx_webhookdelivery_webhook_created")
                    .table(WebhookDeliveries::Table)
                    .col(WebhookDeliveries::WebhookId)
                    .col(WebhookDeliveries::CreatedAt)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(WebhookDeliveries::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum WebhookDeliveries {
    #[sea_orm(iden = "notifications_webhookdelivery")]
    Table,
    Id,
    WebhookId,
    EventType,
    PayloadJson,
    Status,
    Attempts,
    LastAttemptAt,
    ResponseCode,
    ResponseBody,
    NextRetryAt,
    CreatedAt,
    UpdatedAt,
}
