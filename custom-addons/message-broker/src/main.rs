use bytes::Bytes;
use dashmap::DashMap;
use parking_lot::RwLock;
use std::sync::Arc;
use tokio::net::TcpListener;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tracing::{info, error, Level};
use tracing_subscriber;

#[derive(Debug, Clone)]
pub struct Message {
    pub payload: Bytes,
    pub offset: u64,
}

#[derive(Debug)]
pub struct Partition {
    pub id: u32,
    pub messages: RwLock<Vec<Message>>,
}

impl Partition {
    pub fn new(id: u32) -> Self {
        Self {
            id,
            messages: RwLock::new(Vec::new()),
        }
    }

    pub fn append(&self, payload: Bytes) -> u64 {
        let mut msgs = self.messages.write();
        let offset = msgs.len() as u64;
        msgs.push(Message { payload, offset });
        offset
    }

    pub fn read(&self, offset: u64) -> Option<Message> {
        let msgs = self.messages.read();
        msgs.get(offset as usize).cloned()
    }
}

#[derive(Debug)]
pub struct Topic {
    pub name: String,
    pub partitions: Vec<Arc<Partition>>,
}

impl Topic {
    pub fn new(name: String, partition_count: u32) -> Self {
        let mut partitions = Vec::with_capacity(partition_count as usize);
        for id in 0..partition_count {
            partitions.push(Arc::new(Partition::new(id)));
        }
        Self { name, partitions }
    }

    pub fn get_partition(&self, id: u32) -> Option<Arc<Partition>> {
        self.partitions.get(id as usize).cloned()
    }
}

pub struct BrokerState {
    pub topics: DashMap<String, Arc<Topic>>,
}

impl BrokerState {
    pub fn new() -> Self {
        Self {
            topics: DashMap::new(),
        }
    }

    pub fn create_topic(&self, name: String, partitions: u32) {
        if !self.topics.contains_key(&name) {
            let topic = Arc::new(Topic::new(name.clone(), partitions));
            self.topics.insert(name, topic);
        }
    }

    pub fn get_topic(&self, name: &str) -> Option<Arc<Topic>> {
        self.topics.get(name).map(|t| t.clone())
    }
}

#[tokio::main]
async fn main() -> std::io::Result<()> {
    tracing_subscriber::fmt()
        .with_max_level(Level::INFO)
        .init();

    info!("Starting Custom Message Broker...");

    let state = Arc::new(BrokerState::new());

    // Create a default topic for testing
    state.create_topic("default_topic".to_string(), 3);
    info!("Created default_topic with 3 partitions");

    let listener = TcpListener::bind("127.0.0.1:9092").await?;
    info!("Listening on 127.0.0.1:9092");

    loop {
        let (mut socket, addr) = listener.accept().await?;
        info!("Accepted connection from {}", addr);

        let state_clone = state.clone();
        tokio::spawn(async move {
            let mut buf = [0; 1024];

            // In a real broker, we'd parse AMQP/MQTT or a custom binary protocol here.
            // For this skeleton, we just read whatever the client sends and echo back a success.
            match socket.read(&mut buf).await {
                Ok(n) if n > 0 => {
                    info!("Received {} bytes from {}", n, addr);
                    let payload = Bytes::copy_from_slice(&buf[..n]);

                    if let Some(topic) = state_clone.get_topic("default_topic") {
                        if let Some(partition) = topic.get_partition(0) {
                            let offset = partition.append(payload);
                            info!("Appended message to default_topic partition 0 at offset {}", offset);

                            let response = format!("ACK offset {}\n", offset);
                            if let Err(e) = socket.write_all(response.as_bytes()).await {
                                error!("Failed to write to socket: {}", e);
                            }
                        }
                    }
                }
                Ok(_) => info!("Connection closed by {}", addr),
                Err(e) => error!("Failed to read from socket: {}", e),
            }
        });
    }
}
