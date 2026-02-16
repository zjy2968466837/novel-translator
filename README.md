# 📖 Novel Translator — 日文轻小说 AI 翻译器

将日文轻小说（EPUB 格式）翻译为流畅地道的中文，支持 **命令行** 与 **图形界面** 双模式。

基于 OpenAI 兼容 API（DeepSeek / Qwen / GPT 等），内置针对轻小说翻译深度调优的提示词系统。

---

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 📗 EPUB 解析 | 自动提取章节、排序、清洗 HTML |
| 🔄 双模型支持 | Chat 模型 + Completion 模型自动检测 |
| 📋 整章翻译 | 可选将整章作为一个整体发送，消除跨分块不一致 |
| 🔗 上下文注入 | 将前文翻译结果注入后续请求，保持人名术语一致 |
| ⚡ 并发翻译 | 多线程并发加速，支持 1~128 线程 |
| 💾 断点续传 | 实时保存进度，中断后自动跳过已完成章节 |
| 📖 术语表 | JSON 格式术语表，强制统一专有名词译名 |
| 📦 双格式输出 | 支持 TXT 纯文本 和 EPUB 电子书输出 |
| 🎨 GUI 界面 | 基于 Flet 的现代图形界面，深色/浅色主题自适应 |
| 🔧 翻译修复 | 质量扫描 + 选择性重翻，修复不一致的译名 |


---

## 📦 安装

### 环境要求

- Python ≥ 3.10
- 一个 OpenAI 兼容的 API Key（推荐 [硅基流动 SiliconFlow](https://siliconflow.cn/)）

### 安装步骤

```bash
# 克隆项目
git clone https://github.com/your-username/novel-translator.git
cd novel-translator

# 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 或以包模式安装（支持 novel-translator 命令）
pip install -e .
```

---

## 🚀 使用方式

### GUI 模式（推荐）

```bash
python -m novel_translator gui
```

启动后即可在图形界面中：
1. 填入 API Key 和选择模型
2. 选择输入 EPUB 文件
3. 配置翻译风格和参数
4. 点击「开始翻译」

### CLI 模式

```bash
python -m novel_translator translate input.epub \
  --api-key sk-your-api-key \
  --model deepseek-ai/DeepSeek-V3.2 \
  --format epub \
  --glossary glossary.json \
  --chunk-size 0 \
  --context-lines 5
```

#### 常用参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--api-key` | API Key（必填） | — |
| `--base-url` | API 地址 | `https://api.siliconflow.cn/v1` |
| `--model` | 模型名称 | `deepseek-ai/DeepSeek-V3.2` |
| `--format` | 输出格式 `txt`/`epub` | `txt` |
| `--glossary` | 术语表 JSON 路径 | — |
| `--chunk-size` | 分块字符数（0=整章） | `1500` |
| `--context-lines` | 上下文注入行数 | `5` |
| `--workers` | 并发线程数 | `1` |
| `--start` / `--end` | 章节范围 | 全部 |
| `--no-checkpoint` | 禁用断点续传 | — |

---

## 📖 术语表格式

术语表是一个简单的 JSON 文件，键为日文原文，值为中文翻译：

```json
{
  "先輩": "学姐",
  "ダンジョン": "迷宫",
  "マナ": "魔力素",
  "スキル": "技能"
}
```

翻译时会将术语表注入 System Prompt，确保模型严格遵守译名。

参见 [examples/glossary_example.json](examples/glossary_example.json)。

---

## 🏗️ 项目结构

```
novel-translator/
├── src/
│   └── novel_translator/
│       ├── __init__.py      # 包版本
│       ├── __main__.py      # python -m 入口
│       ├── cli.py           # 命令行接口
│       ├── engine.py        # 翻译引擎核心
│       └── gui.py           # Flet 图形界面
├── examples/
│   └── glossary_example.json
├── pyproject.toml
├── requirements.txt
├── LICENSE
├── .gitignore
└── README.md
```

---

## ⚙️ 翻译引擎特性

### 模型类型自动检测

引擎支持 **Chat** 和 **Completion** 两种 API 后端：

- **Chat 模型**（DeepSeek / GPT / Qwen / Claude）：使用 `chat.completions` API + System Prompt
- **Completion 模型**（base 模型）：使用 `completions` API + Few-shot Prompt

检测优先级：用户指定 → 模型名关键词匹配 → API 探测

### 上下文注入

将上一段翻译结果的最后 N 行注入到下一段的请求中，帮助模型保持人名、称谓的前后一致。

- 串行模式：每个 chunk 带上前一个 chunk 的翻译尾部
- 并发模式：每个批次的首个 chunk 带上上批末尾的翻译尾部

### 翻译修复

1. **质量扫描**：在断点文件中搜索指定关键词（如 "前辈" "米娅"），定位问题章节
2. **选择性重翻**：仅重新翻译有问题的章节，使用最新的术语表和提示词

---





## 📄 许可证

[MIT License](LICENSE)
