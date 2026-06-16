use sea_orm_migration::prelude::*;

use super::m20250616_000002_create_projects::Projects;
use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateAddons;

#[async_trait::async_trait]
impl MigrationTrait for CreateAddons {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Addons::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(Addons::Id).uuid().not_null().primary_key())
                    .col(ColumnDef::new(Addons::ProjectId).uuid().null())
                    .col(ColumnDef::new(Addons::ServiceId).uuid().not_null())
                    .col(ColumnDef::new(Addons::Name).string_len(100).not_null())
                    .col(ColumnDef::new(Addons::AddonType).string_len(20).not_null())
                    .col(ColumnDef::new(Addons::Status).string_len(20).not_null())
                    .col(ColumnDef::new(Addons::ConnectionUrl).text().null())
                    .col(ColumnDef::new(Addons::ContainerId).string_len(64).null())
                    .col(
                        ColumnDef::new(Addons::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Addons::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_addons_project_id")
                            .from(Addons::Table, Addons::ProjectId)
                            .to(Projects::Table, Projects::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_addons_service_id")
                            .from(Addons::Table, Addons::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Addons::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Addons {
    #[sea_orm(iden = "deployments_addon")]
    Table,
    Id,
    ProjectId,
    ServiceId,
    Name,
    AddonType,
    Status,
    ConnectionUrl,
    #[sea_orm(iden = "coolify_uuid")]
    ContainerId,
    CreatedAt,
    UpdatedAt,
}
