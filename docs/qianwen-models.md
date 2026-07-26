# 千问平台 — LiMo 适用模型速查

> 来源: [qianwenai.com/models](https://www.qianwenai.com/models)
> API: `https://dashscope.aliyuncs.com/api/v1`（OpenAI 兼容）
> 环境变量: `DASHSCOPE_API_KEY`

## 文本生成

| 模型 | 上下文 | 输入 ¥/M | 输出 ¥/M | 说明 |
|------|--------|---------|---------|------|
| **qwen3.8-max-preview** | — | — | — | 最新旗舰，复杂推理 |
| **qwen3.7-max** | 1M | 6 | 18 | 旗舰，智能体 |
| **qwen3.7-plus** | 1M | 1.6 | 6.4 | 均衡之选，多模态 |
| **qwen3.7-flash** | — | — | — | 快速低成本 |
| **qwen3.6-max** | 262K | 9 | 54 | vibe coding |
| **qwen3.6-plus** | 1M | 2 | 12 | 视觉语言 |
| **qwen-plus** | 1M | 0.8 | 2 | 低成本推理 |
| **qwen-turbo** | 131K | 0.3 | 0.6 | 极低成本 |
| **qwen-flash** | 1M | 0.15 | 1.5 | 最便宜 |

### 第三方文本模型

| 模型 | 输入 ¥/M | 输出 ¥/M | 上下文 | 说明 |
|------|---------|---------|--------|------|
| **deepseek-v4-pro** | 12 | 24 | 1M | 复杂推理，1.6T 参数 |
| **deepseek-v4-flash** | 1 | 2 | 1M | 高性价比，284B/13B MoE |
| **kimi-k3** | 20 | 100 | 1M | 2.8T 参数，支持视觉 |
| **glm-5.2** | 8 | 28 | 1M | 长程任务 |
| **MiniMax-M3** | 4.2 | 16.8 | 1M | Coding + Agent |
| **MiniMax-M2.5** | 2.1 | 8.4 | 204K | 生产力场景 |

## OCR / 视觉

| 模型 | 输入 ¥/M | 输出 ¥/M | 上下文 | 说明 |
|------|---------|---------|--------|------|
| **qwen3-vl-flash** | 0.15 | 1.5 | 262K | 最快最便宜，小尺寸视觉 |
| **qwen3.5-flash** | 0.2 | 2 | 1M | 视觉 Flash，1M 上下文 |
| **vanchin/deepseek-ocr** | 0.216 | 0.216 | 8K | 第三方最低价 OCR |
| **qwen-vl-ocr-latest** | 0.3 | 0.5 | 38K | 纯 OCR，文档/表格/公式 |
| **qwen3.5-ocr** | 0.5 | 2 | 65K | 卡证票据信息抽取增强 |
| **qwen3.7-plus** | 1.6 | 6.4 | 1M | 视觉+文本一体化，推荐 |

## 语音识别 (ASR)

| 模型 | 单价 ¥/秒 | 说明 |
|------|-----------|------|
| **qwen3-asr-flash-realtime** | 0.00033 | 最新 LLM 架构 ASR |
| **qwen3-asr-flash** | 0.00022 | 文件转录 |
| **qwen3-asr-flash-filetrans** | 0.00022 | 大文件转录 |
| **paraformer-realtime-v2** | 0.00024 | 多语种+方言+热词，推荐 |
| **fun-asr** | 0.00022 | 30 语种，古诗词优化 |
| **fun-asr-flash-8k** | 0.00022 | 电话客服场景 |

## LiMo 推荐配置

| 场景 | 推荐模型 | 理由 |
|------|---------|------|
| 生成笔记 | qwen3.7-plus | 1M 上下文，多模态，高性价比 |
| 省钱纯文本 | qwen-flash | ¥0.15/M，最便宜，1M 上下文 |
| 低成本 OCR | qwen3-vl-flash | ¥0.15/M，小尺寸视觉，最快最便宜 |
| 文档 OCR | qwen-vl-ocr-latest | ¥0.3/M，文档/表格/公式 |
| 图片理解 | qwen3.7-plus | 视觉+文本一体化 |
| 语音转写 | paraformer-realtime-v2 | 方言支持最全 |
| 代码生成 | qwen3-coder-plus | 1M 上下文，代码专用 |

> **能力标注说明**：LiMo 预设中已为所有已知模型内置 `capabilities`（text/image），
> 添加模型时优先使用预设标签。API 拉取到的新模型默认纯文本，可在设置页手动调整。
