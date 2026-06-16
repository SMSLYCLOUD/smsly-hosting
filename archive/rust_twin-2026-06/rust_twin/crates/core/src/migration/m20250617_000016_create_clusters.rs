use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreateClusters;

#[async_trait::async_trait]
impl MigrationTrait for CreateClusters {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Clusters::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Clusters::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Clusters::Name).string_len(100).not_null())
                    .col(ColumnDef::new(Clusters::Region).string_len(50).not_null())
                    .col(
                        ColumnDef::new(Clusters::MeshToken)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Clusters::WireguardPublicKey)
                            .string_len(64)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Clusters::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(Clusters::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Clusters::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Clusters::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Clusters {
    #[sea_orm(iden = "deployments_cluster")]
    Table,
    Id,
    Name,
    Region,
    MeshToken,
    WireguardPublicKey,
    IsActive,
    CreatedAt,
    UpdatedAt,
}
