use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct AddDeploymentRequesterId;

#[async_trait::async_trait]
impl MigrationTrait for AddDeploymentRequesterId {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.alter_table(
            Table::alter()
                .table(Deployment::Table)
                .add_column_if_not_exists(
                    ColumnDef::new(Deployment::RequesterId).integer().null()
                )
                .to_owned(),
        ).await
    }
    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.alter_table(
            Table::alter()
                .table(Deployment::Table)
                .drop_column(Deployment::RequesterId)
                .to_owned(),
        ).await
    }
}

#[derive(DeriveIden)]
pub enum Deployment {
    #[sea_orm(iden = "deployments_deployment")]
    Table,
    RequesterId,
}
