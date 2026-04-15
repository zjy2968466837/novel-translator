use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum RetryStage {
    LightFix,
    StrictRetranslate,
    Fallback,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryPolicy {
    pub max_retries: u8,
    pub base_backoff_secs: u64,
}

impl Default for RetryPolicy {
    fn default() -> Self {
        Self {
            max_retries: 3,
            base_backoff_secs: 2,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RetryDecision {
    pub should_retry: bool,
    pub stage: RetryStage,
    pub reasons: Vec<String>,
    pub wait_seconds: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QualityReport {
    pub passed: bool,
    pub issues: Vec<String>,
    pub checked_at: DateTime<Utc>,
}

pub fn check_quality(source: &str, translated: &str, glossary_violations: &[String]) -> QualityReport {
    let mut issues = Vec::new();

    if translated.trim().is_empty() {
        issues.push("translated output is empty".to_string());
    }
    if translated.len() < source.len() / 5 {
        issues.push("translated output too short".to_string());
    }
    if translated.contains('�') {
        issues.push("mojibake character found".to_string());
    }
    if !translated.contains('<') && source.contains('<') {
        issues.push("html structure likely broken".to_string());
    }
    issues.extend(glossary_violations.iter().cloned());

    QualityReport {
        passed: issues.is_empty(),
        issues,
        checked_at: Utc::now(),
    }
}

pub fn decide_retry(policy: &RetryPolicy, attempt: u8, report: &QualityReport) -> RetryDecision {
    if report.passed || attempt >= policy.max_retries {
        return RetryDecision {
            should_retry: false,
            stage: RetryStage::Fallback,
            reasons: report.issues.clone(),
            wait_seconds: 0,
        };
    }

    let stage = match attempt {
        0 => RetryStage::LightFix,
        1 => RetryStage::StrictRetranslate,
        _ => RetryStage::Fallback,
    };

    RetryDecision {
        should_retry: true,
        stage,
        reasons: report.issues.clone(),
        wait_seconds: policy.base_backoff_secs * (2u64.pow(attempt as u32)),
    }
}
