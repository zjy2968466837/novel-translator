use anyhow::Result;
use api_client::{ApiClient, ApiConfig, ApiRequest, ProviderKind};
use api_debug::{ApiDebugLogger, ApiLogEntry, LogLevel};
use chrono::Utc;
use correction_retry::{RetryPolicy, check_quality, decide_retry};
use epub_pipeline::{apply_translations, parse_epub, rebuild_epub, validate_output};
use glossary_context::{Glossary, build_prompt};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use storage::{ApiLogIndexRecord, Storage, TaskState};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum TaskStatus {
    Pending,
    Running,
    Retrying,
    Paused,
    Done,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChapterProgress {
    pub chapter_id: String,
    pub status: TaskStatus,
    pub retries: u8,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TranslationTaskConfig {
    pub task_id: String,
    pub input_epub: PathBuf,
    pub output_epub: PathBuf,
    pub glossary_file: Option<PathBuf>,
    pub context_lines: usize,
    pub concurrent_workers: usize,
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

impl TranslationTaskConfig {
    pub fn new(input_epub: impl AsRef<Path>, output_epub: impl AsRef<Path>) -> Self {
        Self {
            task_id: Uuid::new_v4().to_string(),
            input_epub: input_epub.as_ref().to_path_buf(),
            output_epub: output_epub.as_ref().to_path_buf(),
            glossary_file: None,
            context_lines: 5,
            concurrent_workers: 1,
            provider: ProviderKind::DeepSeekOfficial,
            api_key: String::new(),
            base_url: String::new(),
            model: "deepseek-chat".to_string(),
            timeout_secs: 120,
            max_retries: 3,
            temperature: 0.7,
            top_p: 0.9,
            max_tokens: 4096,
        }
    }
}

pub struct TranslationEngine {
    storage: Storage,
    debug_logger: ApiDebugLogger,
    api_client: ApiClient,
}

impl TranslationEngine {
    pub fn new(db_path: impl AsRef<Path>, debug_root: impl AsRef<Path>) -> Result<Self> {
        Ok(Self {
            storage: Storage::new(db_path)?,
            debug_logger: ApiDebugLogger::new(debug_root)?,
            api_client: ApiClient::new()?,
        })
    }

    pub async fn run_task(&self, cfg: TranslationTaskConfig) -> Result<()> {
        self.persist_state(&cfg.task_id, TaskStatus::Running, 0.0)?;

        let glossary = match &cfg.glossary_file {
            Some(path) => Some(Glossary::load(path)?),
            None => None,
        };

        let mut book = parse_epub(&cfg.input_epub)?;
        let mut translated = Vec::new();
        let mut context_buffer: Vec<String> = Vec::new();
        let total = book.chapters.len().max(1) as f64;
        let retry_policy = RetryPolicy {
            max_retries: cfg.max_retries,
            ..Default::default()
        };

        for (idx, chapter) in book.chapters.iter().enumerate() {
            let mut attempt: u8 = 0;
            loop {
                let prompt = build_prompt(
                    &chapter.title,
                    &chapter.html,
                    &context_buffer,
                    glossary.as_ref(),
                );
                let api_cfg = ApiConfig {
                    provider: cfg.provider.clone(),
                    api_key: cfg.api_key.clone(),
                    base_url: cfg.base_url.clone(),
                    model: cfg.model.clone(),
                    timeout_secs: cfg.timeout_secs,
                    max_retries: cfg.max_retries,
                    temperature: cfg.temperature,
                    top_p: cfg.top_p,
                    max_tokens: cfg.max_tokens,
                };

                let request_id = Uuid::new_v4().to_string();
                let started = std::time::Instant::now();
                let result = self
                    .api_client
                    .call_chat_completion(
                        &api_cfg,
                        &ApiRequest {
                            prompt,
                            chapter_id: chapter.id.clone(),
                        },
                    )
                    .await;
                let duration = started.elapsed().as_millis();

                match result {
                    Ok(resp) => {
                        let glossary_issues = glossary
                            .as_ref()
                            .map(|g| g.violations(&resp.content))
                            .unwrap_or_default();
                        let report = check_quality(&chapter.html, &resp.content, &glossary_issues);
                        let decision = decide_retry(&retry_policy, attempt, &report);

                        let entry = ApiLogEntry {
                            request_id: request_id.clone(),
                            task_id: cfg.task_id.clone(),
                            chapter_id: chapter.id.clone(),
                            provider: format!("{:?}", cfg.provider),
                            status_code: resp.status_code,
                            duration_ms: duration,
                            usage_tokens: resp.usage_tokens,
                            created_at: Utc::now(),
                            request: serde_json::json!({"chapter_id": chapter.id, "attempt": attempt}),
                            response: resp.raw.clone(),
                            error: if report.passed {
                                None
                            } else {
                                Some(report.issues.join("; "))
                            },
                        };

                        let log_path = self.debug_logger.write_entry(LogLevel::Redacted, entry)?;
                        self.storage.record_api_log(&ApiLogIndexRecord {
                            request_id: request_id.clone(),
                            task_id: cfg.task_id.clone(),
                            chapter_id: chapter.id.clone(),
                            status_code: resp.status_code,
                            duration_ms: duration,
                            usage_tokens: resp.usage_tokens,
                            log_path,
                        })?;

                        if decision.should_retry {
                            attempt += 1;
                            self.storage.mark_chapter(
                                &cfg.task_id,
                                &chapter.id,
                                "retrying",
                                attempt,
                            )?;
                            continue;
                        }

                        translated.push((chapter.id.clone(), resp.content.clone()));
                        if cfg.context_lines > 0 {
                            let lines = resp
                                .content
                                .lines()
                                .map(str::trim)
                                .filter(|x| !x.is_empty())
                                .map(ToString::to_string)
                                .collect::<Vec<_>>();
                            let keep = cfg.context_lines.min(lines.len());
                            context_buffer = lines[lines.len().saturating_sub(keep)..].to_vec();
                        }
                        self.storage
                            .mark_chapter(&cfg.task_id, &chapter.id, "done", attempt)?;
                        break;
                    }
                    Err(e) => {
                        let entry = ApiLogEntry {
                            request_id: request_id.clone(),
                            task_id: cfg.task_id.clone(),
                            chapter_id: chapter.id.clone(),
                            provider: format!("{:?}", cfg.provider),
                            status_code: 599,
                            duration_ms: duration,
                            usage_tokens: None,
                            created_at: Utc::now(),
                            request: serde_json::json!({"chapter_id": chapter.id, "attempt": attempt}),
                            response: serde_json::json!({}),
                            error: Some(e.to_string()),
                        };
                        let log_path = self.debug_logger.write_entry(LogLevel::Redacted, entry)?;
                        self.storage.record_api_log(&ApiLogIndexRecord {
                            request_id: request_id.clone(),
                            task_id: cfg.task_id.clone(),
                            chapter_id: chapter.id.clone(),
                            status_code: 599,
                            duration_ms: duration,
                            usage_tokens: None,
                            log_path,
                        })?;
                        if attempt >= cfg.max_retries {
                            self.storage.mark_chapter(
                                &cfg.task_id,
                                &chapter.id,
                                "failed",
                                attempt,
                            )?;
                            self.persist_state(
                                &cfg.task_id,
                                TaskStatus::Failed,
                                idx as f64 / total,
                            )?;
                            return Err(e);
                        }
                        attempt += 1;
                        self.storage.mark_chapter(
                            &cfg.task_id,
                            &chapter.id,
                            "retrying",
                            attempt,
                        )?;
                    }
                }
            }

            self.persist_state(
                &cfg.task_id,
                TaskStatus::Running,
                (idx as f64 + 1.0) / total,
            )?;
        }

        apply_translations(&mut book, &translated);
        rebuild_epub(&book, &cfg.output_epub)?;
        validate_output(&cfg.output_epub)?;
        self.persist_state(&cfg.task_id, TaskStatus::Done, 1.0)?;
        Ok(())
    }

    pub fn export_task_logs(&self, task_id: &str, output_zip: impl AsRef<Path>) -> Result<()> {
        self.debug_logger.export_task_zip(task_id, output_zip)
    }

    fn persist_state(&self, task_id: &str, status: TaskStatus, progress: f64) -> Result<()> {
        self.storage.save_task_state(&TaskState {
            task_id: task_id.to_string(),
            status: format!("{:?}", status),
            progress,
            updated_at: Utc::now().to_rfc3339(),
        })
    }
}
