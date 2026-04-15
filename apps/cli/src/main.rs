use anyhow::Result;
use api_client::ProviderKind;
use clap::{Parser, Subcommand, ValueEnum};
use core_engine::{TranslationEngine, TranslationTaskConfig};
use std::path::PathBuf;
use zip::ZipWriter;
use zip::write::SimpleFileOptions;

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
            let body = reqwest::get(&url).await?.text().await?;
            if let Some(parent) = output.parent() {
                std::fs::create_dir_all(parent)?;
            }
            if output
                .extension()
                .and_then(|x| x.to_str())
                .is_some_and(|x| x.eq_ignore_ascii_case("epub"))
            {
                write_minimal_epub(&output, &url, &body)?;
                println!("downloaded epub");
            } else {
                std::fs::write(output, body)?;
                println!("downloaded raw content");
            }
        }
    }
    Ok(())
}

fn write_minimal_epub(output: &PathBuf, source_url: &str, html_body: &str) -> Result<()> {
    let file = std::fs::File::create(output)?;
    let mut zip = ZipWriter::new(file);
    let uncompressed =
        SimpleFileOptions::default().compression_method(zip::CompressionMethod::Stored);
    let compressed =
        SimpleFileOptions::default().compression_method(zip::CompressionMethod::Deflated);

    zip.start_file("mimetype", uncompressed)?;
    use std::io::Write as _;
    zip.write_all(b"application/epub+zip")?;

    zip.add_directory("META-INF/", compressed)?;
    zip.start_file("META-INF/container.xml", compressed)?;
    zip.write_all(
        br#"<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"#,
    )?;

    zip.add_directory("OEBPS/", compressed)?;
    let cdata_body = html_body.replace("]]>", "]]]]><![CDATA[>");
    zip.start_file("OEBPS/chapter.xhtml", compressed)?;
    zip.write_all(
        format!(
            r#"<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" lang="zh">
  <head><meta charset="utf-8"/><title>Downloaded Chapter</title></head>
  <body>
    <h1>Downloaded Chapter</h1>
    <p>Source: {}</p>
    <pre><![CDATA[{}]]></pre>
  </body>
</html>"#,
            source_url, cdata_body
        )
        .as_bytes(),
    )?;

    zip.start_file("OEBPS/toc.ncx", compressed)?;
    zip.write_all(
        br#"<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:downloaded"/></head>
  <docTitle><text>Downloaded Chapter</text></docTitle>
  <navMap>
    <navPoint id="navPoint-1" playOrder="1">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter.xhtml"/>
    </navPoint>
  </navMap>
</ncx>"#,
    )?;

    zip.start_file("OEBPS/content.opf", compressed)?;
    zip.write_all(
        br#"<?xml version="1.0" encoding="UTF-8"?>
<package version="2.0" xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="BookId">urn:uuid:downloaded</dc:identifier>
    <dc:title>Downloaded Chapter</dc:title>
    <dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chapter1" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chapter1"/>
  </spine>
</package>"#,
    )?;

    zip.finish()?;
    Ok(())
}
