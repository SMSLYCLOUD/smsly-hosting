//! Loads addon templates from JSON (parity with Django's fixtures).

use serde::Deserialize;

#[derive(Debug, Deserialize, Clone)]
pub struct Template {
    pub slug: String,
    pub name: String,
    pub description: String,
    pub category: String,
    pub image: String,
    pub default_port: u16,
    pub env_schema: serde_json::Value,
    pub volumes: serde_json::Value,
    pub ports: serde_json::Value,
    pub healthcheck: Option<serde_json::Value>,
    pub documentation_url: Option<String>,
    pub tier: String,
}

pub struct TemplateLoader;

impl TemplateLoader {
    pub fn load_from_str(_content: &str) -> Result<Vec<Template>, serde_json::Error> {
        // Stub: would parse the Django fixtures JSON
        Ok(vec![])
    }
}
