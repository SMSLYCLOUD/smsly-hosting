use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;
use super::m20250616_000006_create_teams::Teams;

#[derive(DeriveMigrationName)]
pub struct CreateTeamMembers;

#[async_trait::async_trait]
impl MigrationTrait for CreateTeamMembers {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(TeamMembers::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(TeamMembers::Id)
                            .big_integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(TeamMembers::TeamId).uuid().not_null())
                    .col(ColumnDef::new(TeamMembers::UserId).integer().not_null())
                    .col(ColumnDef::new(TeamMembers::Role).string_len(20).not_null())
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_team_members_team_id")
                            .from(TeamMembers::Table, TeamMembers::TeamId)
                            .to(Teams::Table, Teams::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_team_members_user_id")
                            .from(TeamMembers::Table, TeamMembers::UserId)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(TeamMembers::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum TeamMembers {
    #[sea_orm(iden = "teams_teammember")]
    Table,
    Id,
    TeamId,
    UserId,
    Role,
}
