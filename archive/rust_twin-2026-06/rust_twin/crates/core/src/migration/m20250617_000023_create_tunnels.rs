use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateTunnels;

#[async_trait::async_trait]
impl MigrationTrait for CreateTunnels {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Tunnels::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Tunnels::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Tunnels::ServiceId).uuid().not_null())
                    .col(ColumnDef::new(Tunnels::LocalPort).integer().not_null())
                    .col(
                        ColumnDef::new(Tunnels::PublicSubdomain)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(ColumnDef::new(Tunnels::PublicPort).integer().not_null())
                    .col(
                        ColumnDef::new(Tunnels::Protocol)
                            .string_len(10)
                            .not_null()
                            .default("http"),
                    )
                    .col(
                        ColumnDef::new(Tunnels::Status)
                            .string_len(20)
                            .not_null()
                            .default("inactive"),
                    )
                    .col(
                        ColumnDef::new(Tunnels::ConnectionCount)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Tunnels::BytesIn)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Tunnels::BytesOut)
                            .big_integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(Tunnels::LastConnectedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Tunnels::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Tunnels::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_tunnels_service_id")
                            .from(Tunnels::Table, Tunnels::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("uq_tunnels_public_subdomain")
                    .table(Tunnels::Table)
                    .col(Tunnels::PublicSubdomain)
                    .unique()
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Tunnels::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Tunnels {
    #[sea_orm(iden = "deployments_tunnel")]
    Table,
    Id,
    ServiceId,
    LocalPort,
    PublicSubdomain,
    PublicPort,
    Protocol,
    Status,
    ConnectionCount,
    BytesIn,
    BytesOut,
    LastConnectedAt,
    CreatedAt,
    UpdatedAt,
}
