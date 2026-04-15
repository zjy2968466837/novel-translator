use anyhow::Result;
use chrono::Utc;
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskState {
    pub task_id: String,
    pub status: String,
    pub progress: f64,
    pub updated_at: String,
}

pub struct Storage {
    db_path: PathBuf,
}

impl Storage {
    pub fn new(db_path: impl AsRef<Path>) -> Result<Self> {
        let db_path = db_path.as_ref().to_path_buf();
        let s = Self { db_path };
        s.init()?;
        Ok(s)
    }

    pub fn init(&self) -> Result<()> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                progress REAL NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chapters (
                task_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                status TEXT NOT NULL,
                retry_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(task_id, chapter_id)
            );
            CREATE TABLE IF NOT EXISTS api_logs (
                request_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                duration_ms INTEGER NOT NULL,
                usage_tokens INTEGER,
                created_at TEXT NOT NULL,
                log_path TEXT NOT NULL
            );
            ",
        )?;
        Ok(())
    }

    pub fn save_task_state(&self, state: &TaskState) -> Result<()> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute(
            "INSERT INTO tasks(task_id, status, progress, updated_at) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, progress=excluded.progress, updated_at=excluded.updated_at",
            params![state.task_id, state.status, state.progress, state.updated_at],
        )?;
        Ok(())
    }

    pub fn mark_chapter(&self, task_id: &str, chapter_id: &str, status: &str, retry_count: u8) -> Result<()> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute(
            "INSERT INTO chapters(task_id, chapter_id, status, retry_count) VALUES (?1, ?2, ?3, ?4)
             ON CONFLICT(task_id, chapter_id) DO UPDATE SET status=excluded.status, retry_count=excluded.retry_count",
            params![task_id, chapter_id, status, retry_count],
        )?;
        Ok(())
    }

    pub fn record_api_log(
        &self,
        request_id: &str,
        task_id: &str,
        chapter_id: &str,
        status_code: u16,
        duration_ms: u128,
        usage_tokens: Option<u32>,
        log_path: &Path,
    ) -> Result<()> {
        let conn = Connection::open(&self.db_path)?;
        conn.execute(
            "INSERT OR REPLACE INTO api_logs(request_id, task_id, chapter_id, status_code, duration_ms, usage_tokens, created_at, log_path)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                request_id,
                task_id,
                chapter_id,
                i64::from(status_code),
                duration_ms as i64,
                usage_tokens.map(|x| x as i64),
                Utc::now().to_rfc3339(),
                log_path.to_string_lossy().to_string(),
            ],
        )?;
        Ok(())
    }

    pub fn load_task_state(&self, task_id: &str) -> Result<Option<TaskState>> {
        let conn = Connection::open(&self.db_path)?;
        let mut stmt = conn.prepare("SELECT task_id, status, progress, updated_at FROM tasks WHERE task_id=?1")?;
        let mut rows = stmt.query(params![task_id])?;
        if let Some(row) = rows.next()? {
            Ok(Some(TaskState {
                task_id: row.get(0)?,
                status: row.get(1)?,
                progress: row.get(2)?,
                updated_at: row.get(3)?,
            }))
        } else {
            Ok(None)
        }
    }
}

pub fn atomic_write(path: impl AsRef<Path>, bytes: &[u8]) -> Result<()> {
    let path = path.as_ref();
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = path.with_extension("tmp.write");
    fs::write(&tmp, bytes)?;
    fs::rename(tmp, path)?;
    Ok(())
}
