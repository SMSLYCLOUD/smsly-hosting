use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateUsages;

#[async_trait::async_trait]
impl MigrationTrait for CreateUsages {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Usages::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Usages::Id)
                            .big_integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Usages::ServiceId).uuid().not_null())
                    .col(ColumnDef::new(Usages::CpuCores).double().not_null())
                    .col(ColumnDef::new(Usages::MemoryMb).integer().not_null())
                    .col(ColumnDef::new(Usages::DurationSeconds).integer().not_null())
                    .col(ColumnDef::new(Usages::Cost).double().not_null())
                    .col(
                        ColumnDef::new(Usages::Timestamp)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_usages_service_id")
                            .from(Usages::Table, Usages::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Usages::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Usages {
    #[sea_orm(iden = "billing_usagerecord")]
    Table,
    Id,
    ServiceId,
    CpuCores,
    MemoryMb,
    DurationSeconds,
    Cost,
    Timestamp,
}
