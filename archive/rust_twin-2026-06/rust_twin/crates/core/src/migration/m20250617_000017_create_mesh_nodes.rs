use sea_orm_migration::prelude::*;

use super::m20250617_000016_create_clusters::Clusters;

#[derive(DeriveMigrationName)]
pub struct CreateMeshNodes;

#[async_trait::async_trait]
impl MigrationTrait for CreateMeshNodes {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(MeshNodes::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(MeshNodes::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(MeshNodes::ClusterId).uuid().not_null())
                    .col(ColumnDef::new(MeshNodes::Hostname).string_len(255).not_null())
                    .col(ColumnDef::new(MeshNodes::IpAddress).string_len(45).not_null())
                    .col(ColumnDef::new(MeshNodes::Port).integer().not_null())
                    .col(ColumnDef::new(MeshNodes::Role).string_len(20).not_null())
                    .col(ColumnDef::new(MeshNodes::Status).string_len(20).not_null())
                    .col(
                        ColumnDef::new(MeshNodes::LastSeen)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(ColumnDef::new(MeshNodes::CpuCapacity).integer().not_null())
                    .col(
                        ColumnDef::new(MeshNodes::MemoryCapacityMb)
                            .integer()
                            .not_null(),
                    )
                    .col(ColumnDef::new(MeshNodes::CurrentLoad).double().not_null())
                    .col(ColumnDef::new(MeshNodes::Version).string_len(50).not_null())
                    .col(
                        ColumnDef::new(MeshNodes::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(MeshNodes::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_mesh_node_cluster_id")
                            .from(MeshNodes::Table, MeshNodes::ClusterId)
                            .to(Clusters::Table, Clusters::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .index(
                        Index::create()
                            .name("uq_mesh_node_cluster_hostname")
                            .col(MeshNodes::ClusterId)
                            .col(MeshNodes::Hostname)
                            .unique(),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(MeshNodes::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum MeshNodes {
    #[sea_orm(iden = "deployments_meshnode")]
    Table,
    Id,
    ClusterId,
    Hostname,
    IpAddress,
    Port,
    Role,
    Status,
    LastSeen,
    CpuCapacity,
    MemoryCapacityMb,
    CurrentLoad,
    Version,
    CreatedAt,
    UpdatedAt,
}
