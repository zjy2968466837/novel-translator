use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Glossary {
    pub terms: BTreeMap<String, String>,
}

impl Glossary {
    pub fn load(path: impl AsRef<Path>) -> Result<Self> {
        let data = fs::read(path)?;
        let terms: BTreeMap<String, String> = serde_json::from_slice(&data)?;
        Ok(Self { terms })
    }

    pub fn as_rules_text(&self) -> String {
        self.terms
            .iter()
            .map(|(k, v)| format!("- {} => {}", k, v))
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub fn violations(&self, translated_text: &str) -> Vec<String> {
        self.terms
            .iter()
            .filter_map(|(src, target)| {
                if translated_text.contains(src) && !translated_text.contains(target) {
                    Some(format!("term mismatch: {} => {}", src, target))
                } else {
                    None
                }
            })
            .collect()
    }
}

pub fn build_prompt(
    chapter_title: &str,
    source_text: &str,
    context_lines: &[String],
    glossary: Option<&Glossary>,
) -> String {
    let mut sections = Vec::new();
    sections.push("你是轻小说翻译助手，请保持语义准确、保留段落结构。".to_string());
    if let Some(gls) = glossary {
        sections.push(format!("术语强制规则:\n{}", gls.as_rules_text()));
    }
    if !context_lines.is_empty() {
        sections.push(format!("前文上下文:\n{}", context_lines.join("\n")));
    }
    sections.push(format!("章节标题: {}", chapter_title));
    sections.push(format!("待翻译正文:\n{}", source_text));
    sections.join("\n\n")
}
