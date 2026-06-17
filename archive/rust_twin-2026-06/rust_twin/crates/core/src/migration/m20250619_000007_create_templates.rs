use sea_orm_migration::prelude::*;

use super::m20250616_000001_create_users::Users;

#[derive(DeriveMigrationName)]
pub struct CreateTemplates;

#[async_trait::async_trait]
impl MigrationTrait for CreateTemplates {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Templates::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Templates::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(Templates::Slug)
                            .string_len(120)
                            .not_null()
                            .unique_key(),
                    )
                    .col(
                        ColumnDef::new(Templates::Name)
                            .string_len(150)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Templates::Description)
                            .text()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Templates::Kind)
                            .string_len(40)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Templates::ContentJson)
                            .json()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Templates::IsOfficial)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(Templates::CreatedById)
                            .integer()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Templates::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Templates::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_template_created_by_id")
                            .from(Templates::Table, Templates::CreatedById)
                            .to(Users::Table, Users::Id)
                            .on_delete(ForeignKeyAction::SetNull),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Templates::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Templates {
    #[sea_orm(iden = "deployments_template")]
    Table,
    Id,
    Slug,
    Name,
    Description,
    Kind,
    ContentJson,
    IsOfficial,
    CreatedById,
    CreatedAt,
    UpdatedAt,
}
