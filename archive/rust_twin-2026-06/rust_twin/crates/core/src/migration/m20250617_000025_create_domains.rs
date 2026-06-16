use sea_orm_migration::prelude::*;

use super::m20250616_000003_create_services::Services;

#[derive(DeriveMigrationName)]
pub struct CreateDomains;

#[async_trait::async_trait]
impl MigrationTrait for CreateDomains {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Domains::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Domains::Id)
                            .uuid()
                            .not_null()
                            .primary_key(),
                    )
                    .col(ColumnDef::new(Domains::ServiceId).uuid().not_null())
                    .col(ColumnDef::new(Domains::Domain).string_len(255).not_null())
                    .col(
                        ColumnDef::new(Domains::IsPrimary)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(Domains::SslStatus)
                            .string_len(20)
                            .not_null()
                            .default("pending"),
                    )
                    .col(
                        ColumnDef::new(Domains::SslProvider)
                            .string_len(20)
                            .not_null()
                            .default("letsencrypt"),
                    )
                    .col(
                        ColumnDef::new(Domains::SslExpiresAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Domains::SslCertificatePath)
                            .string_len(512)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Domains::VerificationMethod)
                            .string_len(20)
                            .not_null()
                            .default("dns-01"),
                    )
                    .col(
                        ColumnDef::new(Domains::VerificationToken)
                            .string_len(255)
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Domains::LastVerifiedAt)
                            .timestamp_with_time_zone()
                            .null(),
                    )
                    .col(
                        ColumnDef::new(Domains::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(Domains::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .foreign_key(
                        ForeignKey::create()
                            .name("fk_domains_service_id")
                            .from(Domains::Table, Domains::ServiceId)
                            .to(Services::Table, Services::Id)
                            .on_delete(ForeignKeyAction::Cascade),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("uq_domains_domain")
                    .table(Domains::Table)
                    .col(Domains::Domain)
                    .unique()
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Domains::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Domains {
    #[sea_orm(iden = "deployments_domain")]
    Table,
    Id,
    ServiceId,
    Domain,
    IsPrimary,
    SslStatus,
    SslProvider,
    SslExpiresAt,
    SslCertificatePath,
    VerificationMethod,
    VerificationToken,
    LastVerifiedAt,
    CreatedAt,
    UpdatedAt,
}
