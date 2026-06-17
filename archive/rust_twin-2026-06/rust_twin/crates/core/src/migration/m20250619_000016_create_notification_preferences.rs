use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateNotificationPreferences;

#[async_trait::async_trait]
impl MigrationTrait for CreateNotificationPreferences {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(NotificationPreferences::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(NotificationPreferences::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(NotificationPreferences::UserId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(NotificationPreferences::Kind)
                            .string_len(50)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(NotificationPreferences::EmailEnabled)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(NotificationPreferences::WebhookEnabled)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(NotificationPreferences::InAppEnabled)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(NotificationPreferences::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(NotificationPreferences::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_notificationpreference_user_id")
                            .from(
                                NotificationPreferences::Table,
                                NotificationPreferences::UserId,
                            )
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("uq_notificationpreference_user_kind")
                    .table(NotificationPreferences::Table)
                    .col(NotificationPreferences::UserId)
                    .col(NotificationPreferences::Kind)
                    .unique()
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(
                Table::drop()
                    .table(NotificationPreferences::Table)
                    .to_owned(),
            )
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum NotificationPreferences {
    #[sea_orm(iden = "notifications_notificationpreference")]
    Table,
    Id,
    UserId,
    Kind,
    EmailEnabled,
    WebhookEnabled,
    InAppEnabled,
    CreatedAt,
    UpdatedAt,
}
