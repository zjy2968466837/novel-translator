use anyhow::Result;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EpubChapter {
    pub id: String,
    pub title: String,
    pub html: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EpubResource {
    pub path: String,
    pub media_type: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EpubBook {
    pub input_path: PathBuf,
    pub chapters: Vec<EpubChapter>,
    pub resources: Vec<EpubResource>,
    pub metadata_title: Option<String>,
}

pub fn parse_epub(input: impl AsRef<Path>) -> Result<EpubBook> {
    let input_path = input.as_ref().to_path_buf();
    // Skeleton pipeline aligned to auto-novel stages:
    // 1) normalize archive
    // 2) segment chapters
    // 3) map resources (images/css/fonts)
    // 4) rebuild nav/metadata
    // Here we keep a minimal parsing contract for incremental implementation.
    Ok(EpubBook {
        input_path,
        chapters: vec![],
        resources: vec![],
        metadata_title: None,
    })
}

pub fn apply_translations(book: &mut EpubBook, translated_chapters: &[(String, String)]) {
    for (chapter_id, translated_html) in translated_chapters {
        if let Some(ch) = book.chapters.iter_mut().find(|x| &x.id == chapter_id) {
            ch.html = translated_html.clone();
        }
    }
}

pub fn rebuild_epub(book: &EpubBook, output: impl AsRef<Path>) -> Result<()> {
    // Placeholder write-back path to keep full flow executable in early phases.
    fs::copy(&book.input_path, output)?;
    Ok(())
}

pub fn validate_output(_output: impl AsRef<Path>) -> Result<()> {
    // Hook for structural validation checks (nav, spine, media references)
    Ok(())
}
