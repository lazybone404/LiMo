/**
 * 模型能力位 → 中文徽标。
 *
 * **新能力位必须在此登记**，否则用户会看到裸英文（如 `reasoning`）。
 * 与后端 config.py 预设里的 `capabilities` 取值一一对应，取值来源见：
 *   - `image`   ← routers/model.py 的 `_detect_capabilities`（名字自动检测）+ 预设
 *   - `reasoning` / `document` / `video` ← **仅由预设标注**（按模型，不按 provider）
 *
 * ⚠️ 与后端 `routers/capabilities.py` 的引擎能力（asr/ocr/doc/pdf/office）**是两套
 * 互不相干的词汇**，不要混用同一个映射表 —— 那边管的是「哪个引擎解析哪种文件」，
 * 这边管的是「哪个模型收得下什么模态」。
 *
 * 单独成文件（而非留在 GenerateOptions 内）是因为设置页也要展示同一组能力位：
 * 两处各写一份必然漂移（改了一处忘了另一处，用户在两个界面看到同一模型的不同标签）。
 */
export const CAP_LABELS: Record<string, string> = {
  text: '文本',
  image: '图片',
  video: '视频',
  reasoning: '思考档位',
  document: 'PDF 直传',
}

/** 取中文徽标，未登记的能力位回退原文（宁可显示英文也不要显示 undefined） */
export function capLabel(cap: string): string {
  return CAP_LABELS[cap] || cap
}
