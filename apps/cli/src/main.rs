use anyhow::Result;
use api_client::ProviderKind;
use clap::{Parser, Subcommand, ValueEnum};
use core_engine::{TranslationEngine, TranslationTaskConfig};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "novel-translator")]
#[command(about = "Novel translator CLI (Rust rebuild)")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    Translate {
        #[arg(long)]
        input: PathBuf,
        #[arg(long)]
        output: PathBuf,
        #[arg(long)]
        api_key: String,
        #[arg(long)]
        model: String,
        #[arg(long, value_enum, default_value_t = ProviderArg::DeepseekOfficial)]
        provider: ProviderArg,
        #[arg(long, default_value = "")]
        base_url: String,
        #[arg(long, default_value_t = 120)]
        timeout: u64,
        #[arg(long, default_value_t = 3)]
        max_retries: u8,
        #[arg(long)]
        glossary: Option<PathBuf>,
        #[arg(long, default_value_t = 1)]
        workers: usize,
        #[arg(long, default_value = "./.data/state.db")]
        db_path: PathBuf,
        #[arg(long, default_value = "./.data/api_logs")]
        debug_root: PathBuf,
    },
    Download {
        #[arg(long)]
        url: String,
        #[arg(long)]
        output: PathBuf,
    },
}

#[derive(Clone, ValueEnum)]
enum ProviderArg {
    DeepseekOfficial,
    OpenaiCompatible,
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::fmt().with_env_filter("info").init();
    let cli = Cli::parse();
    match cli.command {
        Commands::Translate {
            input,
            output,
            api_key,
            model,
            provider,
            base_url,
            timeout,
            max_retries,
            glossary,
            workers,
            db_path,
            debug_root,
        } => {
            let engine = TranslationEngine::new(db_path, debug_root)?;
            let mut cfg = TranslationTaskConfig::new(input, output);
            cfg.api_key = api_key;
            cfg.model = model;
            cfg.provider = match provider {
                ProviderArg::DeepseekOfficial => ProviderKind::DeepSeekOfficial,
                ProviderArg::OpenaiCompatible => ProviderKind::OpenAiCompatible,
            };
            cfg.base_url = base_url;
            cfg.timeout_secs = timeout;
            cfg.max_retries = max_retries;
            cfg.glossary_file = glossary;
            cfg.concurrent_workers = workers;
            let task_id = cfg.task_id.clone();
            engine.run_task(cfg).await?;
            println!("done task_id={}", task_id);
        }
        Commands::Download { url, output } => {
            let body = reqwest::get(url).await?.text().await?;
            if let Some(parent) = output.parent() {
                std::fs::create_dir_all(parent)?;
            }
            std::fs::write(output, body)?;
            println!("downloaded");
        }
    }
    Ok(())
}
