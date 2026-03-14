use anyhow::{Context, Result};
use std::process::Stdio;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tracing::{info, warn};

/// Interface to the `nixpacks` CLI builder.
pub struct NixpacksBuilder {
    binary_path: String,
}

impl NixpacksBuilder {
    /// Attempts to locate the `nixpacks` binary in the PATH.
    pub async fn new() -> Result<Self> {
        // Execute `which nixpacks` asynchronously
        let output = Command::new("which")
            .arg("nixpacks")
            .output()
            .await
            .context("Failed to execute `which nixpacks`")?;

        if !output.status.success() {
            return Err(anyhow::anyhow!(
                "Nixpacks binary not found in PATH. Please install it."
            ));
        }

        let binary_path = String::from_utf8(output.stdout)?.trim().to_string();
        info!("Found nixpacks binary at: {}", binary_path);

        Ok(Self { binary_path })
    }

    /// Executes `nixpacks build` on the target source directory, tagging the output image.
    pub async fn build_image(
        &self,
        source_dir: &str,
        image_name: &str,
        env_vars: Vec<(&str, &str)>,
    ) -> Result<()> {
        info!("Starting Nixpacks build for source: {}", source_dir);

        let mut cmd = Command::new(&self.binary_path);
        cmd.arg("build").arg(source_dir).arg("--name").arg(image_name);

        // Append environment variables to the build process
        for (key, value) in env_vars {
            cmd.arg("--env").arg(format!("{}={}", key, value));
        }

        // Capture output continuously
        cmd.stdout(Stdio::piped());
        cmd.stderr(Stdio::piped());

        let mut child = cmd.spawn().context("Failed to spawn nixpacks process")?;

        let stdout = child
            .stdout
            .take()
            .context("Failed to capture nixpacks stdout")?;
        let stderr = child
            .stderr
            .take()
            .context("Failed to capture nixpacks stderr")?;

        let mut stdout_reader = BufReader::new(stdout).lines();
        let mut stderr_reader = BufReader::new(stderr).lines();

        // Spawn a task to stream standard output
        tokio::spawn(async move {
            while let Ok(Some(line)) = stdout_reader.next_line().await {
                info!("nixpacks: {}", line);
            }
        });

        // Spawn a task to stream standard error
        tokio::spawn(async move {
            while let Ok(Some(line)) = stderr_reader.next_line().await {
                warn!("nixpacks: {}", line);
            }
        });

        // Wait for process to complete
        let status = child
            .wait()
            .await
            .context("Nixpacks build process failed to exit")?;

        if status.success() {
            info!("Nixpacks build completed successfully. Image: {}", image_name);
            Ok(())
        } else {
            Err(anyhow::anyhow!(
                "Nixpacks build failed with status: {:?}",
                status.code()
            ))
        }
    }
}