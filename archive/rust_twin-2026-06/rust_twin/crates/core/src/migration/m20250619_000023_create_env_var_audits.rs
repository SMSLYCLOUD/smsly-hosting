use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;
use super::m20250616_000010_create_environment_variables::EnvironmentVariables;

#[derive(DeriveMigrationName)]
pub struct CreateEnvVarAudits;

#[async_trait::async_trait]
impl MigrationTrait for CreateEnvVarAudits {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(EnvVarAudits::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(EnvVarAudits::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(EnvVarAudits::EnvVarId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(EnvVarAudits::ActorId)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(EnvVarAudits::Action)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(EnvVarAudits::OldValueEncrypted)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(EnvVarAudits::NewValueEncrypted)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(EnvVarAudits::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_env_var_audits_env_var_id")
                            .from(EnvVarAudits::Table, EnvVarAudits::EnvVarId)
                            .to(EnvironmentVariables::Table, EnvironmentVariables::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_env_var_audits_actor_id")
                            .from(EnvVarAudits::Table, EnvVarAudits::ActorId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(EnvVarAudits::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
pub enum EnvVarAudits {
    Table,
    Id,
    EnvVarId,
    ActorId,
    Action,
    OldValueEncrypted,
    NewValueEncrypted,
    CreatedAt,
}
