use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateEnvironmentVariables;

#[async_trait::async_trait]
impl MigrationTrait for CreateEnvironmentVariables {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(EnvironmentVariables::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(EnvironmentVariables::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(EnvironmentVariables::ServiceId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(EnvironmentVariables::Key)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(ColumnDef::new(EnvironmentVariables::Value).text().not_null())
                    .col(
                        ColumnDef::new(EnvironmentVariables::Source)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(EnvironmentVariables::IsBuildArg)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(EnvironmentVariables::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(EnvironmentVariables::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_environment_variables_service_id")
                            .from(EnvironmentVariables::Table, EnvironmentVariables::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(EnvironmentVariables::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum EnvironmentVariables {
    #[sea_orm(iden = "deployments_environmentvariable")]
    Table,
    Id,
    ServiceId,
    Key,
    Value,
    Source,
    IsBuildArg,
    CreatedAt,
    UpdatedAt,
}
