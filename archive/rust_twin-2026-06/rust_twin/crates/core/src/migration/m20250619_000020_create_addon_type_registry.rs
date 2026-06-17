use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreateAddonTypeRegistry;

#[async_trait::async_trait]
impl MigrationTrait for CreateAddonTypeRegistry {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(AddonTypeRegistry::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(AddonTypeRegistry::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(AddonTypeRegistry::Kind)
                            .string_len(50)
                            .not_null()
                            .unique_key(),
                    )
                    .col(
                        ColumnDef::new(AddonTypeRegistry::Name)
                            .string_len(200)
                            .not_null(),
                    )
                    .col(ColumnDef::new(AddonTypeRegistry::Description).text().null())
                    .col(
                        ColumnDef::new(AddonTypeRegistry::DefaultVersion)
                            .string_len(50)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(AddonTypeRegistry::SupportedVersionsJson)
                            .text()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(AddonTypeRegistry::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(
                Table::drop()
                    .table(AddonTypeRegistry::Table)
                    .to_owned(),
            )
            .await
    }
}

#[derive(DeriveIden)]
pub enum AddonTypeRegistry {
    Table,
    Id,
    Kind,
    Name,
    Description,
    DefaultVersion,
    SupportedVersionsJson,
    IsActive,
}
