use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreateAddonTemplates;

#[async_trait::async_trait]
impl MigrationTrait for CreateAddonTemplates {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.create_table(
            Table::create()
                .table(AddonTemplate::Table)
                .if_not_exists()
                .col(ColumnDef::new(AddonTemplate::Id).uuid().not_null().primary_key())
                .col(ColumnDef::new(AddonTemplate::Slug).string_len(100).not_null().unique_key())
                .col(ColumnDef::new(AddonTemplate::Name).string_len(200).not_null())
                .col(ColumnDef::new(AddonTemplate::Description).text().not_null())
                .col(ColumnDef::new(AddonTemplate::Category).string_len(50).not_null())
                .col(ColumnDef::new(AddonTemplate::Image).string_len(500).not_null())
                .col(ColumnDef::new(AddonTemplate::DefaultPort).integer().not_null())
                .col(ColumnDef::new(AddonTemplate::EnvSchema).text().not_null())
                .col(ColumnDef::new(AddonTemplate::Volumes).text().not_null())
                .col(ColumnDef::new(AddonTemplate::Ports).text().not_null())
                .col(ColumnDef::new(AddonTemplate::Healthcheck).text())
                .col(ColumnDef::new(AddonTemplate::DocumentationUrl).string_len(500))
                .col(ColumnDef::new(AddonTemplate::IsActive).boolean().not_null().default(true))
                .col(ColumnDef::new(AddonTemplate::Tier).string_len(50).not_null().default("community"))
                .col(ColumnDef::new(AddonTemplate::CreatedAt).timestamp_with_time_zone().not_null())
                .col(ColumnDef::new(AddonTemplate::UpdatedAt).timestamp_with_time_zone().not_null())
                .to_owned(),
        ).await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager.drop_table(Table::drop().table(AddonTemplate::Table).to_owned()).await
    }
}

#[derive(DeriveIden)]
pub enum AddonTemplate {
    Table,
    Id, Slug, Name, Description, Category, Image, DefaultPort,
    EnvSchema, Volumes, Ports, Healthcheck, DocumentationUrl,
    IsActive, Tier, CreatedAt, UpdatedAt,
}
