use sea_orm_migration::prelude::*;

use super::m20250617_000017_create_mesh_nodes::MeshNodes;

#[derive(DeriveMigrationName)]
pub struct CreateHeartbeatLogs;

#[async_trait::async_trait]
impl MigrationTrait for CreateHeartbeatLogs {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(HeartbeatLogs::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(HeartbeatLogs::Id)
                            .big_integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(HeartbeatLogs::NodeId).uuid().not_null())
                    .col(ColumnDef::new(HeartbeatLogs::Term).big_integer().not_null())
                    .col(ColumnDef::new(HeartbeatLogs::CpuUsage).double().not_null())
                    .col(ColumnDef::new(HeartbeatLogs::MemoryUsageMb).integer().not_null())
                    .col(
                        ColumnDef::new(HeartbeatLogs::ActiveDeployments)
                            .integer()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(HeartbeatLogs::IsMaster)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(HeartbeatLogs::ReceivedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_heartbeat_log_node_id")
                            .from(HeartbeatLogs::Table, HeartbeatLogs::NodeId)
                            .to(MeshNodes::Table, MeshNodes::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .index(
                        Index::create()
                            .name("idx_heartbeat_node_time")
                            .col(HeartbeatLogs::NodeId)
                            .col(HeartbeatLogs::ReceivedAt),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(HeartbeatLogs::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum HeartbeatLogs {
    #[sea_orm(iden = "deployments_heartbeatlog")]
    Table,
    Id,
    NodeId,
    Term,
    CpuUsage,
    MemoryUsageMb,
    ActiveDeployments,
    IsMaster,
    ReceivedAt,
}
