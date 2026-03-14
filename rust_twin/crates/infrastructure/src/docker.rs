use anyhow::{Context, Result};
use bollard::{
    container::{Config, CreateContainerOptions, StartContainerOptions},
    network::CreateNetworkOptions,
    Docker,
};
use tracing::info;

/// A robust async wrapper around the Docker daemon via `bollard`.
pub struct DockerClient {
    engine: Docker,
}

impl DockerClient {
    /// Attempts to connect to the local Docker socket (or named pipe on Windows).
    pub fn new() -> Result<Self> {
        info!("Connecting to local Docker socket...");
        let engine = Docker::connect_with_socket_defaults()
            .context("Failed to connect to the Docker socket. Is Docker running?")?;

        Ok(Self { engine })
    }

    /// Verifies the daemon is reachable and logs its version.
    pub async fn ping(&self) -> Result<()> {
        let version = self.engine.version().await.context("Docker ping failed")?;
        info!("Successfully connected to Docker engine v{}", version.version.unwrap_or_default());
        Ok(())
    }

    /// Idempotently creates a Docker network with the given name.
    pub async fn ensure_network(&self, network_name: &str) -> Result<()> {
        let inspect_result = self.engine.inspect_network(network_name, None::<bollard::network::InspectNetworkOptions<String>>).await;

        if inspect_result.is_ok() {
            info!("Docker network '{}' already exists.", network_name);
            return Ok(());
        }

        info!("Creating Docker network: {}", network_name);
        let options = CreateNetworkOptions {
            name: network_name,
            check_duplicate: true,
            driver: "bridge",
            ..Default::default()
        };

        self.engine
            .create_network(options)
            .await
            .context(format!("Failed to create Docker network '{}'", network_name))?;

        Ok(())
    }

    /// Creates and starts a container with the given image and name.
    pub async fn run_container(
        &self,
        image: &str,
        name: &str,
        env: Vec<String>,
        network: &str,
    ) -> Result<String> {
        info!("Creating container '{}' from image '{}'", name, image);

        // Bollard requires Vec<&str> for envs, so we map our Strings
        let env_refs: Vec<&str> = env.iter().map(AsRef::as_ref).collect();

        // 1. Define container spec
        let config = Config {
            image: Some(image),
            env: Some(env_refs),
            host_config: Some(bollard::models::HostConfig {
                network_mode: Some(network.to_string()),
                restart_policy: Some(bollard::models::RestartPolicy {
                    name: Some(bollard::models::RestartPolicyNameEnum::UNLESS_STOPPED),
                    ..Default::default()
                }),
                ..Default::default()
            }),
            ..Default::default()
        };

        // 2. Create the container
        let options = Some(CreateContainerOptions {
            name,
            platform: None,
        });

        let container = self
            .engine
            .create_container(options, config)
            .await
            .context("Failed to create container")?;

        let id = container.id;
        info!("Container created successfully: {}", &id[..12]);

        // 3. Start the container
        info!("Starting container {}...", name);
        self.engine
            .start_container(&id, None::<StartContainerOptions<String>>)
            .await
            .context("Failed to start container")?;

        info!("Container {} is running.", name);

        Ok(id)
    }
}