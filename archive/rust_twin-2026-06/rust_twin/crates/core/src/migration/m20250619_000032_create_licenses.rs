use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreateLicenses;

#[async_trait::async_trait]
impl MigrationTrait for CreateLicenses {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Licenses::Table)
                    .if_not_exists()
                    .col(ColumnDef::new(Licenses::Id).uuid().not_null().primary_key())
                    .col(
                        ColumnDef::new(Licenses::Key)
                            .string_len(255)
                            .not_null()
                            .unique_key(),
                    )
                    .col(
                        ColumnDef::new(Licenses::Kind)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Licenses::Status)
                            .string_len(20)
                            .not_null()
                            .default("pending"),
                    )
                    .col(
                        ColumnDef::new(Licenses::MaxSeats)
                            .integer()
                            .not_null()
                            .default(1),
                    )
                    .col(
                        ColumnDef::new(Licenses::ActivatedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Licenses::ExpiresAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Licenses::OrganizationId)
                            .uuid()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Licenses::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Licenses::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_license_organization_id")
                    .table(Licenses::Table)
                    .col(Licenses::OrganizationId)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Licenses::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Licenses {
    #[sea_orm(iden = "licensing_license")]
    Table,
    Id,
    Key,
    Kind,
    Status,
    MaxSeats,
    ActivatedAt,
    ExpiresAt,
    OrganizationId,
    CreatedAt,
    UpdatedAt,
}
