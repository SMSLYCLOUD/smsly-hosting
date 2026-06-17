use sea_orm_migration::prelude::*;

use super::m20250617_000021_create_transfer_logs::TransferLogs;

#[derive(DeriveMigrationName)]
pub struct AddTransferLogRegionIds;

#[async_trait::async_trait]
impl MigrationTrait for AddTransferLogRegionIds {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .alter_table(
                Table::alter()
                    .table(TransferLogs::Table)
                    .add_column_if_not_exists(
                        ColumnDef::new(TransferLogs::SourceRegionId)
                            .uuid()
                            .null(),
                    )
                    .add_column_if_not_exists(
                        ColumnDef::new(TransferLogs::TargetRegionId)
                            .uuid()
                            .null(),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .alter_table(
                Table::alter()
                    .table(TransferLogs::Table)
                    .drop_column(TransferLogs::SourceRegionId)
                    .drop_column(TransferLogs::TargetRegionId)
                    .to_owned(),
            )
            .await
    }
}
