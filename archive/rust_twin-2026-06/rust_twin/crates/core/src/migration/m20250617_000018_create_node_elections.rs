use sea_orm_migration::prelude::*;

use super::m20250617_000016_create_clusters::Clusters;

#[derive(DeriveMigrationName)]
pub struct CreateNodeElections;

#[async_trait::async_trait]
impl MigrationTrait for CreateNodeElections {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(NodeElections::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(NodeElections::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(NodeElections::ClusterId).uuid().not_null())
                    .col(ColumnDef::new(NodeElections::MasterNodeId).uuid().null())
                    .col(ColumnDef::new(NodeElections::Term).big_integer().not_null())
                    .col(
                        ColumnDef::new(NodeElections::LastElectionAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(NodeElections::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(NodeElections::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_node_election_cluster_id")
                            .from(NodeElections::Table, NodeElections::ClusterId)
                            .to(Clusters::Table, Clusters::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(NodeElections::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum NodeElections {
    #[sea_orm(iden = "deployments_nodeelection")]
    Table,
    Id,
    ClusterId,
    MasterNodeId,
    Term,
    LastElectionAt,
    CreatedAt,
    UpdatedAt,
}
