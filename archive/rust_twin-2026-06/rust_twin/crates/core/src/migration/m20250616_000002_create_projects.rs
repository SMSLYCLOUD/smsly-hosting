use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateProjects;

#[async_trait::async_trait]
impl MigrationTrait for CreateProjects {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Projects::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(Projects::Id).uuid().not_null().primary_key())
                    .col(ColumnDef::new(Projects::OwnerId).integer().not_null())
                    .col(ColumnDef::new(Projects::TeamId).uuid().null())
                    .col(ColumnDef::new(Projects::Name).string_len(100).not_null())
                    .col(ColumnDef::new(Projects::Slug).string_len(120).not_null())
                    .col(ColumnDef::new(Projects::Description).text().not_null())
                    .col(
                        ColumnDef::new(Projects::IconEmoji)
                            .string_len(10)
                            .not_null(),
                    )
                    .col(ColumnDef::new(Projects::Color).string_len(7).not_null())
                    .col(
                        ColumnDef::new(Projects::IsDefault)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(Projects::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Projects::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_projects_owner_id")
                            .from(Projects::Table, Projects::OwnerId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Projects::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Projects {
    #[sea_orm(iden = "deployments_project")]
    Table,
    Id,
    OwnerId,
    TeamId,
    Name,
    Slug,
    Description,
    IconEmoji,
    Color,
    IsDefault,
    CreatedAt,
    UpdatedAt,
}
