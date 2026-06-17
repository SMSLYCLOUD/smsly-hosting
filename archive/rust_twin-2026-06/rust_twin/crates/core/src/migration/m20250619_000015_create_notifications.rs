use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateNotifications;

#[async_trait::async_trait]
impl MigrationTrait for CreateNotifications {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Notifications::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Notifications::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Notifications::UserId).integer().not_null())
                    .col(
                        ColumnDef::new(Notifications::Kind)
                            .string_len(80)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Notifications::Title)
                            .string_len(200)
                            .not_null(),
                    )
                    .col(ColumnDef::new(Notifications::Body).text().not_null())
                    .col(
                        ColumnDef::new(Notifications::Link)
                            .string_len(2048)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Notifications::ReadAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Notifications::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_notification_user_id")
                            .from(Notifications::Table, Notifications::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;
        manager
            .create_index(
                Index::create()
                    .name("idx_notification_user_created")
                    .table(Notifications::Table)
                    .col(Notifications::UserId)
                    .col(Notifications::CreatedAt)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Notifications::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Notifications {
    #[sea_orm(iden = "notifications_notification")]
    Table,
    Id,
    UserId,
    Kind,
    Title,
    Body,
    Link,
    ReadAt,
    CreatedAt,
}
