use serde::Deserialize;
use anyhow::{Context, Result};

#[derive(Debug, Deserialize, Clone)]
pub struct Config {
    // Required Security
    pub secret_key: String,
    pub field_encryption_key: String,

    // Database
    pub database_url: String,

    // Redis
    #[serde(default = "default_redis_host")]
    pub redis_host: String,
    #[serde(default = "default_redis_port")]
    pub redis_port: u16,
    pub redis_password: Option<String>,

    // App Settings
    #[serde(default)]
    pub debug: bool,
    #[serde(default = "default_domain")]
    pub domain: String,
    #[serde(default = "default_use_ssl")]
    pub use_ssl: bool,

    // Server setup
    #[serde(default = "default_host")]
    pub host: String,
    #[serde(default = "default_port")]
    pub port: u16,
}

fn default_redis_host() -> String {
    "redis".to_string()
}

fn default_redis_port() -> u16 {
    6379
}

fn default_domain() -> String {
    "localhost".to_string()
}

fn default_use_ssl() -> bool {
    false
}

fn default_host() -> String {
    "0.0.0.0".to_string()
}

fn default_port() -> u16 {
    8000
}

impl Config {
    /// Loads the configuration from the environment and/or `.env` file.
    pub fn load() -> Result<Self> {
        // Attempt to load .env file, ignore error if it doesn't exist
        let _ = dotenvy::dotenv();

        envy::from_env::<Config>().context("Failed to parse environment variables")
    }

    /// Constructs the Redis URL based on individual parameters
    pub fn get_redis_url(&self) -> String {
        if let Some(password) = &self.redis_password {
            format!("redis://:{}@{}:{}", password, self.redis_host, self.redis_port)
        } else {
            format!("redis://{}:{}", self.redis_host, self.redis_port)
        }
    }
}
