/**
 * API 客户端 barrel — 全部公共符号在此聚合，15 个消费方零改动。
 * 按域拆分见 api/ 子文件（core/capabilities/transcripts/ingest/generate/chat/settings/export）。
 */
export { apiUrl, appUrl, ApiErrorException, getErrorMessage, apiFetch } from './core'
export type {
  ApiError, EngineOption, EngineProviderInfo, Capabilities,
  DocEntry, TranscriptFull, GenerateResult, MarkdownView, ChatResult,
} from './core'
export * from './capabilities'
export * from './transcripts'
export * from './ingest'
export * from './generate'
export * from './chat'
export * from './settings'
export * from './export'
