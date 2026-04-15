use anyhow::Result;
use api_client::PRESETS;
use core_engine::{TranslationEngine, TranslationTaskConfig};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BridgeStartTaskRequest {
    pub input_epub: String,
    pub output_epub: String,
    pub db_path: String,
    pub debug_root: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BridgeStartTaskResponse {
    pub task_id: String,
}

pub fn provider_presets_json() -> Result<String> {
    Ok(serde_json::to_string_pretty(&PRESETS)?)
}

pub async fn start_task(req: BridgeStartTaskRequest) -> Result<BridgeStartTaskResponse> {
    let engine = TranslationEngine::new(PathBuf::from(req.db_path), PathBuf::from(req.debug_root))?;
    let cfg = TranslationTaskConfig::new(req.input_epub, req.output_epub);
    let task_id = cfg.task_id.clone();
    engine.run_task(cfg).await?;
    Ok(BridgeStartTaskResponse { task_id })
}
