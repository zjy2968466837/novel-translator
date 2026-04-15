use anyhow::Result;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fs::{self, File};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use zip::ZipWriter;
use zip::write::SimpleFileOptions;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum LogLevel {
    Raw,
    Redacted,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApiLogEntry {
    pub request_id: String,
    pub task_id: String,
    pub chapter_id: String,
    pub provider: String,
    pub status_code: u16,
    pub duration_ms: u128,
    pub usage_tokens: Option<u32>,
    pub created_at: DateTime<Utc>,
    pub request: serde_json::Value,
    pub response: serde_json::Value,
    pub error: Option<String>,
}

pub struct ApiDebugLogger {
    root: PathBuf,
}

impl ApiDebugLogger {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(&root)?;
        Ok(Self { root })
    }

    pub fn write_entry(&self, level: LogLevel, mut entry: ApiLogEntry) -> Result<PathBuf> {
        let task_dir = self.root.join(&entry.task_id);
        fs::create_dir_all(&task_dir)?;

        if matches!(level, LogLevel::Redacted) {
            redact_json(&mut entry.request);
            redact_json(&mut entry.response);
            if let Some(err) = entry.error.as_mut() {
                *err = redact_text(err);
            }
        }

        let file_name = format!("{}_{}.json", entry.created_at.timestamp_millis(), entry.request_id);
        let path = task_dir.join(file_name);
        fs::write(&path, serde_json::to_vec_pretty(&entry)?)?;
        Ok(path)
    }

    pub fn export_task_zip(&self, task_id: &str, output_zip: impl AsRef<Path>) -> Result<()> {
        let task_dir = self.root.join(task_id);
        let zip_file = File::create(output_zip)?;
        let mut zip = ZipWriter::new(zip_file);
        let options = SimpleFileOptions::default();

        for item in fs::read_dir(task_dir)? {
            let item = item?;
            let path = item.path();
            if path.extension().and_then(|x| x.to_str()) != Some("json") {
                continue;
            }
            let mut content = Vec::new();
            File::open(&path)?.read_to_end(&mut content)?;
            let name = path.file_name().and_then(|x| x.to_str()).unwrap_or("entry.json").to_string();
            zip.start_file(name, options)?;
            zip.write_all(&content)?;
        }

        zip.finish()?;
        Ok(())
    }
}

fn redact_json(value: &mut serde_json::Value) {
    match value {
        serde_json::Value::Object(map) => {
            for (k, v) in map.iter_mut() {
                if is_sensitive_key(k) {
                    *v = serde_json::Value::String("***REDACTED***".to_string());
                } else {
                    redact_json(v);
                }
            }
        }
        serde_json::Value::Array(arr) => {
            for v in arr {
                redact_json(v);
            }
        }
        serde_json::Value::String(s) => {
            *s = redact_text(s);
        }
        _ => {}
    }
}

fn redact_text(input: &str) -> String {
    if input.len() <= 8 {
        "***REDACTED***".to_string()
    } else {
        format!("{}***", &input[..8])
    }
}

fn is_sensitive_key(k: &str) -> bool {
    matches!(k.to_ascii_lowercase().as_str(), "authorization" | "api_key" | "apikey" | "token")
}
