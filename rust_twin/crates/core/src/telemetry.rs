use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter, Registry};
use tracing::info;
use anyhow::{Context, Result};

pub fn init() -> Result<()> {
    let env_filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,core=debug,api=debug"));

    let fmt_layer = tracing_subscriber::fmt::layer()
        .with_target(false)
        .with_thread_ids(true);

    Registry::default()
        .with(env_filter)
        .with(fmt_layer)
        .try_init()
        .context("Failed to initialize tracing subscriber")?;

    info!("Telemetry initialized");
    Ok(())
}
