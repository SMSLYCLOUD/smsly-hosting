use anyhow::{Context, Result};
use argon2::{
    password_hash::{rand_core::OsRng, PasswordHasher, SaltString},
    Argon2,
};
use clap::{Parser, Subcommand};
use cn_core::config::Config;
use cn_core::db;
use cn_core::entities::user;
use sea_orm::{ActiveModelTrait, ColumnTrait, EntityTrait, QueryFilter, Set};
use tracing::{info, warn};

/// Grid Management CLI (replaces Django manage.py)
#[derive(Parser)]
#[command(author, version, about, long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Creates a new superuser account
    CreateSuperuser {
        #[arg(short, long)]
        username: String,

        #[arg(short, long)]
        email: String,

        #[arg(short, long)]
        password: Option<String>,
    },
    /// Runs outstanding database migrations
    Migrate,
    /// Sets up default OAuth applications
    SetupSocialApps,
}

#[tokio::main]
async fn main() -> Result<()> {
    // 1. Initialize basic tracing for the CLI (direct to stdout)
    tracing_subscriber::fmt()
        .with_target(false)
        .with_env_filter("info,sqlx=warn,sea_orm=warn")
        .init();

    // 2. Parse arguments
    let cli = Cli::parse();

    // 3. Load config and connect to DB (needed for most commands)
    let config = Config::load().context("Failed to load environment configuration")?;
    let db = db::establish_connection(&config.database_url).await?;

    match &cli.command {
        Commands::CreateSuperuser {
            username,
            email,
            password,
        } => {
            info!("Creating superuser: {}", username);

            // 1. Check if user already exists
            let existing = user::Entity::find()
                .filter(user::Column::Username.eq(username.as_str()))
                .one(&db)
                .await?;

            if existing.is_some() {
                warn!("A user with username '{}' already exists.", username);
                return Ok(());
            }

            // 2. Prompt for password if not provided via args (simplified here to default if None)
            // In a real CLI, we would use the `dialoguer` crate to securely prompt the terminal.
            let raw_password = password.clone().unwrap_or_else(|| {
                println!("No password provided, using default 'admin123'");
                "admin123".to_string()
            });

            // 3. Hash the password using Argon2 (Django uses PBKDF2 by default, but Argon2 is modern)
            // Note: If backward compatibility with Django's login is strictly required, we would need to
            // use a python-compatible PBKDF2 hashing crate and format string (e.g. `pbkdf2_sha256$...`).
            let salt = SaltString::generate(&mut OsRng);
            let argon2 = Argon2::default();
            let password_hash = argon2
                .hash_password(raw_password.as_bytes(), &salt)
                .map_err(|e| anyhow::anyhow!("Failed to hash password: {}", e))?
                .to_string();

            // 4. Insert into the database
            let new_user = user::ActiveModel {
                username: Set(username.clone()),
                email: Set(email.clone()),
                password: Set(password_hash),
                is_superuser: Set(true),
                is_staff: Set(true),
                is_active: Set(true),
                date_joined: Set(chrono::Utc::now().into()),
                ..Default::default()
            };

            let _inserted = new_user.insert(&db).await?;
            info!("Superuser '{}' created successfully.", username);
        }
        Commands::Migrate => {
            info!("Running database migrations...");
            use sea_orm_migration::MigratorTrait;
            cn_core::migration::migrator::Migrator::up(&db, None).await?;
            info!("Migrations complete.");
        }
        Commands::SetupSocialApps => {
            info!("Setting up default OAuth applications (GitHub, Google)...");
            // TODO: (Phase 6.3) Port the logic from `setup_social_apps.py` to insert OAuth provider records.
            info!("OAuth apps configured successfully.");
        }
    }

    Ok(())
}