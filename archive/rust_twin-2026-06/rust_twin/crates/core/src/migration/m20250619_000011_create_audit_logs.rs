use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateAuditLogs;

#[async_trait::async_trait]
impl MigrationTrait for CreateAuditLogs {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(AuditLogs::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(AuditLogs::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::ActorId)
                            .integer()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::Action)
                            .string_len(80)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::TargetType)
                            .string_len(80)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::TargetId)
                            .uuid()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::IpAddress)
                            .string_len(64)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::UserAgent)
                            .string_len(512)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::MetadataJson)
                            .json()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(AuditLogs::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_auditlog_actor_id")
                            .from(AuditLogs::Table, AuditLogs::ActorId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::SetNull),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_auditlog_actor_created")
                    .table(AuditLogs::Table)
                    .col(AuditLogs::ActorId)
                    .col(AuditLogs::CreatedAt)
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_auditlog_target")
                    .table(AuditLogs::Table)
                    .col(AuditLogs::TargetType)
                    .col(AuditLogs::TargetId)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(AuditLogs::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum AuditLogs {
    #[sea_orm(iden = "deployments_auditlog")]
    Table,
    Id,
    ActorId,
    Action,
    TargetType,
    TargetId,
    IpAddress,
    UserAgent,
    MetadataJson,
    CreatedAt,
}
