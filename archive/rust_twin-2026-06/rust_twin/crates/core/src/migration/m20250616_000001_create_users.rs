// NOTE: This schema is INTENTIONALLY DIVERGENT from Django's User model.
// Django has separate auth_user and the rest of the app uses a different
// shape. To make this polarity-compatible, the FK constraints and column
// names would need to match. See archive/.../docs/RUST_TWIN_POLARITY.md
// (or similar) for the full gap analysis.
//
// This migration creates a "ground up" schema that suits the rust_twin
// entities. It is NOT a clone of the Django schema.

use sea_orm_migration::prelude::*;

#[derive(DeriveMigrationName)]
pub struct CreateUsers;

#[async_trait::async_trait]
impl MigrationTrait for CreateUsers {
    async fn up(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .create_table(
                Table::create()
                    .table(Users::Table)
                    .if_not_exists()
                    .col(
                        ColumnDef::new(Users::Id)
                            .integer()
                            .not_null()
                            .auto_increment()
                            .primary_key(),
                    )
                    .col(
                        ColumnDef::new(Users::Username)
                            .string_len(150)
                            .not_null()
                            .unique_key(),
                    )
                    .col(ColumnDef::new(Users::Email).string_len(254).not_null())
                    .col(ColumnDef::new(Users::Password).string_len(128).not_null())
                    .col(
                        ColumnDef::new(Users::IsSuperuser)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(Users::IsStaff)
                            .boolean()
                            .not_null()
                            .default(false),
                    )
                    .col(
                        ColumnDef::new(Users::IsActive)
                            .boolean()
                            .not_null()
                            .default(true),
                    )
                    .col(
                        ColumnDef::new(Users::DateJoined)
                            .timestamp_with_time_zone()
                            .not_null(),
                    )
                    .to_owned(),
            )
            .await
    }

    async fn down(&self, manager: &SchemaManager) -> Result<(), DbErr> {
        manager
            .drop_table(Table::drop().table(Users::Table).to_owned())
            .await
    }
}

#[derive(DeriveIden)]
#[sea_orm(rename_all = "snake_case")]
pub enum Users {
    #[sea_orm(iden = "auth_user")]
    Table,
    Id,
    Username,
    Email,
    Password,
    IsSuperuser,
    IsStaff,
    IsActive,
    DateJoined,
}
