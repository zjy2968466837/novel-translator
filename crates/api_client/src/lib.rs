use anyhow::{Context, Result, bail};
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use tokio::time::{Duration, sleep};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ProviderKind {
    DeepSeekOfficial,
    OpenAiCompatible,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProviderPreset {
    pub key: &'static str,
    pub display_name: &'static str,
    pub base_url: &'static str,
}

pub const PRESETS: [ProviderPreset; 2] = [
    ProviderPreset {
        key: "deepseek_official",
        display_name: "DeepSeek Official",
        base_url: "https://api.deepseek.com",
    },
    ProviderPreset {
        key: "openai_compatible",
        display_name: "OpenAI Compatible",
        base_url: "",
    },
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiConfig {
    pub provider: ProviderKind,
    pub api_key: String,
    pub base_url: String,
    pub model: String,
    pub timeout_secs: u64,
    pub max_retries: u8,
    pub temperature: f32,
    pub top_p: f32,
    pub max_tokens: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiRequest {
    pub prompt: String,
    pub chapter_id: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiResponse {
    pub content: String,
    pub status_code: u16,
    pub usage_tokens: Option<u32>,
    pub raw: serde_json::Value,
}

#[derive(Clone)]
pub struct ApiClient {
    http: Client,
}

impl ApiClient {
    pub fn new() -> Result<Self> {
        let http = Client::builder().build()?;
        Ok(Self { http })
    }

    pub async fn call_chat_completion(
        &self,
        cfg: &ApiConfig,
        req: &ApiRequest,
    ) -> Result<ApiResponse> {
        if matches!(cfg.provider, ProviderKind::OpenAiCompatible) && cfg.base_url.trim().is_empty()
        {
            bail!("openai_compatible provider requires base_url");
        }

        let base = match cfg.provider {
            ProviderKind::DeepSeekOfficial => "https://api.deepseek.com".to_string(),
            ProviderKind::OpenAiCompatible => cfg.base_url.clone(),
        };
        let url = format!("{}/v1/chat/completions", base.trim_end_matches('/'));

        let payload = serde_json::json!({
            "model": cfg.model,
            "temperature": cfg.temperature,
            "top_p": cfg.top_p,
            "max_tokens": cfg.max_tokens,
            "messages": [
                {"role": "user", "content": req.prompt}
            ]
        });

        let mut attempt: u8 = 0;
        loop {
            let resp = self
                .http
                .post(&url)
                .bearer_auth(&cfg.api_key)
                .timeout(Duration::from_secs(cfg.timeout_secs))
                .json(&payload)
                .send()
                .await;

            match resp {
                Ok(r) if r.status().is_success() => {
                    let status = r.status().as_u16();
                    let json: serde_json::Value = r
                        .json()
                        .await
                        .context("failed to parse api response json")?;
                    let content = json
                        .pointer("/choices/0/message/content")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string();
                    let usage = json
                        .pointer("/usage/total_tokens")
                        .and_then(|v| v.as_u64())
                        .map(|v| v as u32);
                    return Ok(ApiResponse {
                        content,
                        status_code: status,
                        usage_tokens: usage,
                        raw: json,
                    });
                }
                Ok(r) if should_retry_status(r.status()) && attempt < cfg.max_retries => {
                    attempt += 1;
                    let backoff = 2u64.pow(attempt as u32);
                    sleep(Duration::from_secs(backoff)).await;
                }
                Ok(r) => {
                    let code = r.status().as_u16();
                    let body = r.text().await.unwrap_or_default();
                    bail!("api request failed status={} body={}", code, body);
                }
                Err(e) if attempt < cfg.max_retries => {
                    attempt += 1;
                    let backoff = 2u64.pow(attempt as u32);
                    sleep(Duration::from_secs(backoff)).await;
                    tracing::warn!(
                        error = %e,
                        attempt,
                        chapter_id = %req.chapter_id,
                        "retrying api request"
                    );
                }
                Err(e) => return Err(e).context("api request failed without retry left"),
            }
        }
    }
}

fn should_retry_status(status: StatusCode) -> bool {
    status == StatusCode::TOO_MANY_REQUESTS || status.is_server_error()
}
