use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;
use super::m20250616_000002_create_projects::Projects;

#[derive(DeriveMigrationName)]
pub struct CreateTeams;

#[async_trait::async_trait]
impl MigrationTrait for CreateTeams {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Teams::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(Teams::Id).uuid().not_null().primary_key())
                    .col(ColumnDef::new(Teams::OwnerId).integer().not_null())
                    .col(ColumnDef::new(Teams::Name).string_len(255).not_null())
                    .col(
                        ColumnDef::new(Teams::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_teams_owner_id")
                            .from(Teams::Table, Teams::OwnerId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_foreign_key(
                ForeignKey::create()
                    .name("fk_projects_team_id")
                    .from(Projects::Table, Projects::TeamId)
                    .to(Teams::Table, Teams::Id)
                    .on_delete(ForeignKeyAction::SetNull)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_foreign_key(
                ForeignKey::drop()
                    .name("fk_projects_team_id")
                    .table(Projects::Table)
                    .to_owned(),
            )
            .await?;
        manager
            .drop_table(Table::drop().table(Teams::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Teams {
    #[sea_orm(iden = "teams_team")]
    Table,
    Id,
    OwnerId,
    Name,
    CreatedAt,
}
