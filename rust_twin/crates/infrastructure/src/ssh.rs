use anyhow::{Context, Result};
use ssh2::Session;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::Path;
use tracing::{info, warn};

pub struct TransferEngine {
    session: Session,
    host: String,
}

impl TransferEngine {
    /// Connects and authenticates to a remote host using a password.
    /// Note: The `ssh2` crate performs blocking I/O, so this should generally be
    /// wrapped in `tokio::task::spawn_blocking` when called from async contexts.
    pub fn connect_with_password(host: &str, port: u16, user: &str, pass: &str) -> Result<Self> {
        info!("Connecting via SSH to {}:{} as {}", host, port, user);

        let tcp = TcpStream::connect(format!("{}:{}", host, port))
            .context("Failed to establish TCP connection for SSH")?;

        let mut session = Session::new().context("Failed to create SSH session")?;
        session.set_tcp_stream(tcp);
        session.handshake().context("SSH handshake failed")?;

        session
            .userauth_password(user, pass)
            .context("SSH password authentication failed")?;

        if !session.authenticated() {
            return Err(anyhow::anyhow!("SSH authentication failed"));
        }

        info!("Successfully authenticated to {}", host);

        Ok(Self {
            session,
            host: host.to_string(),
        })
    }

    /// Executes a remote command and returns the stdout.
    pub fn execute_command(&self, command: &str) -> Result<String> {
        info!("Executing remote command on {}: {}", self.host, command);

        let mut channel = self.session.channel_session()?;
        channel.exec(command).context("Failed to execute command")?;

        let mut result = String::new();
        channel.read_to_string(&mut result)?;

        channel.wait_close()?;
        let exit_status = channel.exit_status()?;

        if exit_status != 0 {
            warn!("Remote command exited with status: {}", exit_status);
            // Optionally, we could read stderr here for better error context
        }

        Ok(result)
    }

    /// Uploads a local file to the remote host via SCP.
    pub fn upload_file(&self, local_path: &Path, remote_path: &Path) -> Result<()> {
        info!("Uploading {:?} to {}:{:?}", local_path, self.host, remote_path);

        let metadata = std::fs::metadata(local_path)
            .context("Failed to read local file metadata")?;

        let mut local_file = std::fs::File::open(local_path)
            .context("Failed to open local file")?;

        // Open SCP channel
        let mut remote_file = self.session.scp_send(
            remote_path,
            0o644,
            metadata.len(),
            None,
        ).context("Failed to initiate SCP transfer")?;

        // Buffer and copy
        let mut buffer = [0; 65536]; // 64KB chunks
        loop {
            let bytes_read = local_file.read(&mut buffer)?;
            if bytes_read == 0 {
                break;
            }
            remote_file.write_all(&buffer[..bytes_read])?;
        }

        // Close the channel gracefully
        remote_file.send_eof()?;
        remote_file.wait_eof()?;
        remote_file.close()?;
        remote_file.wait_close()?;

        info!("Upload complete.");
        Ok(())
    }

    /// Downloads a file from the remote host via SCP to a local path.
    pub fn download_file(&self, remote_path: &Path, local_path: &Path) -> Result<()> {
        info!("Downloading {}:{:?} to {:?}", self.host, remote_path, local_path);

        let (mut remote_file, stat) = self.session.scp_recv(remote_path)
            .context("Failed to initiate SCP receive")?;

        let mut local_file = std::fs::File::create(local_path)
            .context("Failed to create local file")?;

        // Buffer and copy exactly `stat.size()` bytes
        let mut remaining = stat.size();
        let mut buffer = [0; 65536]; // 64KB chunks

        while remaining > 0 {
            let to_read = std::cmp::min(remaining, buffer.len() as u64) as usize;
            let bytes_read = remote_file.read(&mut buffer[..to_read])?;
            if bytes_read == 0 {
                break;
            }
            local_file.write_all(&buffer[..bytes_read])?;
            remaining -= bytes_read as u64;
        }

        // Clean up
        remote_file.send_eof()?;
        remote_file.wait_eof()?;
        remote_file.close()?;
        remote_file.wait_close()?;

        info!("Download complete.");
        Ok(())
    }
}