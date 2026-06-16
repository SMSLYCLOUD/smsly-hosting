use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreateSocialApps;

#[async_trait::async_trait]
impl MigrationTrait for CreateSocialApps {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(SocialApps::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(SocialApps::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(SocialApps::Provider)
                            .string_len(40)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialApps::Name)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialApps::ClientId)
                            .string_len(255)
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialApps::Secret)
                            .text()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialApps::Settings)
                            .text()
                            .not_null()
                            .default("{}"),
                    )
                    .col(
                        ColumnDef::new(SocialApps::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(SocialApps::CreatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .col(
                        ColumnDef::new(SocialApps::UpdatedAt)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .to_owned(),
            )
            .await?;

        manager
            .create_index(
                Index::create()
                    .name("idx_socialapp_provider")
                    .table(SocialApps::Table)
                    .col(SocialApps::Provider)
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(SocialApps::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum SocialApps {
    #[sea_orm(iden = "socialaccount_socialapp")]
    Table,
    Id,
    Provider,
    Name,
    ClientId,
    Secret,
    Settings,
    IsActive,
    CreatedAt,
    UpdatedAt,
}
