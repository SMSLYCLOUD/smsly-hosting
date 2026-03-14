use anyhow::{Context, Result};
use reqwest::Client;
use serde_json::json;
use tracing::{info, warn};

pub struct GeminiClient {
    api_key: String,
    model: String,
    client: Client,
}

impl GeminiClient {
    /// Initialize a new Gemini API client.
    /// Default model if none provided is `gemini-2.0-flash`.
    pub fn new(api_key: String, model_override: Option<String>) -> Self {
        Self {
            api_key,
            model: model_override.unwrap_or_else(|| "gemini-2.0-flash".to_string()),
            client: Client::new(),
        }
    }

    /// Performs an AI root-cause analysis based on provided context (logs, errors, metrics).
    pub async fn analyze_root_cause(&self, context_data: &str) -> Result<String> {
        info!("Sending root cause analysis request to Gemini ({})", self.model);

        let url = format!(
            "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent?key={}",
            self.model, self.api_key
        );

        // Construct the prompt
        let prompt = format!(
            "You are an expert DevOps AI. Analyze the following system data, logs, or metrics \
            and provide a concise root cause analysis and a recommended remediation step.\n\n\
            DATA:\n{}\n\nANALYSIS:",
            context_data
        );

        let payload = json!({
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 800
            }
        });

        let response = self
            .client
            .post(&url)
            .header("Content-Type", "application/json")
            .json(&payload)
            .send()
            .await
            .context("Failed to send request to Gemini API")?;

        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            warn!("Gemini API error ({}): {}", status, body);
            return Err(anyhow::anyhow!("Gemini API returned error status: {}", status));
        }

        let resp_json: serde_json::Value = response
            .json()
            .await
            .context("Failed to parse Gemini API JSON response")?;

        // Extract the text from the heavily nested Gemini response structure
        let analysis_text = resp_json["candidates"][0]["content"]["parts"][0]["text"]
            .as_str()
            .unwrap_or("Failed to extract text from Gemini response")
            .to_string();

        Ok(analysis_text)
    }
}