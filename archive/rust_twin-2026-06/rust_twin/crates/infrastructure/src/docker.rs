use anyhow::{Context, Result};
use bollard::{
    container::{Config, CreateContainerOptions, LogsOptions, StartContainerOptions, StopContainerOptions},
    network::CreateNetworkOptions,
    Docker,
};
use futures_util::StreamExt;
use tracing::{info, warn};

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

    /// Fetches and streams logs from a running container asynchronously.
    /// In a production environment, these logs could be broadcast to a WebSocket via Redis Pub/Sub.
    pub async fn stream_logs(&self, container_name: &str) -> Result<()> {
        info!("Fetching logs for container: {}", container_name);

        let options = Some(LogsOptions::<String> {
            stdout: true,
            stderr: true,
            follow: false, // Set to true for live streaming
            tail: "100".to_string(), // Fetch last 100 lines
            ..Default::default()
        });

        let mut log_stream = self.engine.logs(container_name, options);

        while let Some(log_result) = log_stream.next().await {
            match log_result {
                Ok(log_output) => {
                    // LogOutput implements Display, safely writing stdout/stderr to standard tracing
                    info!("[{}] {}", container_name, log_output);
                }
                Err(e) => {
                    warn!("Error reading log from {}: {}", container_name, e);
                }
            }
        }

        Ok(())
    }

    /// Stops a running container by name or id.
    ///
    /// Sends `POST /containers/{name}/stop` with a 10-second graceful
    /// shutdown window. Bollard returns `Error` (a 404 in most cases) if the
    /// container is already gone; we treat that as success so the rollback
    /// flow is idempotent — a missing container is not an error.
    pub async fn stop_container(&self, container_name: &str) -> Result<()> {
        info!("Stopping container: {}", container_name);

        let options = Some(StopContainerOptions { t: 10 });

        match self
            .engine
            .stop_container(container_name, options)
            .await
        {
            Ok(()) => {
                info!("Container {} stopped successfully.", container_name);
                Ok(())
            }
            Err(e) => {
                warn!(
                    "stop_container({}) returned: {} (treating as no-op)",
                    container_name, e
                );
                Ok(())
            }
        }
    }
}
