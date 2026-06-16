use sea_orm_migration::prelude::*;

use super::m20250616_000002_create_projects::Projects;

#[derive(DeriveMigrationName)]
pub struct CreateServices;

#[async_trait::async_trait]
impl MigrationTrait for CreateServices {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Services::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(Services::Id).uuid().not_null().primary_key())
                    .col(ColumnDef::new(Services::ProjectId).uuid().not_null())
                    .col(ColumnDef::new(Services::Name).string_len(100).not_null())
                    .col(
                        ColumnDef::new(Services::Slug)
                            .string_len(120)
                            .not_null()
                            .unique_key(),
                    )
                    .col(
                        ColumnDef::new(Services::DeployType)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Services::RepositoryUrl)
                            .string_len(200)
                            .null(),
                    )
                    .col(ColumnDef::new(Services::Branch).string_len(255).not_null())
                    .col(ColumnDef::new(Services::CustomDomains).json().not_null())
                    .col(
                        ColumnDef::new(Services::PublicDomainHidden)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(Services::RootDirectory)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Services::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Services::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_services_project_id")
                            .from(Services::Table, Services::ProjectId)
                            .to(Projects::Table, Projects::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Services::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Services {
    #[sea_orm(iden = "deployments_service")]
    Table,
    Id,
    ProjectId,
    Name,
    Slug,
    DeployType,
    RepositoryUrl,
    Branch,
    CustomDomains,
    PublicDomainHidden,
    RootDirectory,
    CreatedAt,
    UpdatedAt,
}
