use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreatePlatformLicenses;

#[async_trait::async_trait]
impl MigrationTrait for CreatePlatformLicenses {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(PlatformLicenses::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(PlatformLicenses::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(PlatformLicenses::LicenseKey).text().not_null())
                    .col(ColumnDef::new(PlatformLicenses::Tier).string_len(20).not_null())
                    .col(
                        ColumnDef::new(PlatformLicenses::LicenseData)
                            .text()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::IsValid)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::LastValidated)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::ValidationError)
                            .text()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::LicensedTo)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::InstanceId)
                            .string_len(64)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::ExpiresAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::MaxServices)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::MaxTeamMembers)
                            .integer()
                            .not_null()
                            .default(0),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::PaymentProvider)
                            .string_len(20)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::SubscriptionId)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(PlatformLicenses::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(PlatformLicenses::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum PlatformLicenses {
    #[sea_orm(iden = "licensing_platformlicense")]
    Table,
    Id,
    LicenseKey,
    Tier,
    LicenseData,
    IsValid,
    LastValidated,
    ValidationError,
    LicensedTo,
    InstanceId,
    ExpiresAt,
    MaxServices,
    MaxTeamMembers,
    PaymentProvider,
    SubscriptionId,
    CreatedAt,
    UpdatedAt,
}
