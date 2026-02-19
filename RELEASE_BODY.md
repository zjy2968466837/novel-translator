# v1.1.1

Release v1.1.1 (Fix)

## 重点修复

- **DeepSeek Beta 上下文连续性修复**
  - 前 1 章到后续章节的翻译上下文可连续注入，不再局限于单章内部分块。
  - 在人名、称谓、语气保持方面更稳定，适合长篇连续翻译。

- **上下文污染控制**
  - 上下文尾部会过滤常见非正文行（如“求评价/求反馈/链接”等），避免模型被元信息干扰。

- **章节筛选修复**
  - 自动排除 `nav/toc/cover/titlepage/copyright` 等非正文章节文件。

- **终端日志兼容性**
  - 在部分系统编码场景下，日志输出会自动降级，避免因字符编码导致中断。

## 使用建议

- DeepSeek Beta 建议使用：
  - `base_url`: `https://api.deepseek.com/beta`
  - `model_name`: `deepseek-chat`
  - `deepseek_beta`: `true`
  - `use_prefix_completion`: `true`

- 长篇翻译建议开启上下文注入：
  - `context_lines`: `5~8`

## 版本信息

- 应用版本：`1.1.1`
- 兼容运行模式：CLI / GUI
