import { useState, useEffect, useRef } from 'react'
import {
  listProviders, updateProvider, addProvider, deleteProvider, testProviderConnection,
  listEnabledModels, refreshModels, addModel, deleteModel, updateModel,
  getSceneModels, setSceneModels,
  getConfig, updateConfig,
  listEngineProviders, addEngineProvider, deleteEngineProvider,
  listEngineModels, deleteEngineModel,
  getEngineBindings, saveEngineBindings,
  getEngineRegistry,
  type Provider, type EnabledModel, type SceneModels,
  type EngineProviderConfig as EngineProv, type EngineModel,
  apiUrl, apiFetch,
} from '../../api/client'
import { useStore } from '../../store'
import { EngineSelect, type EngineOption } from '../gen-panel/EngineSelect'
import { notifyConfigChanged } from '../../lib/configEvents'
import { Plus, X, Trash2, RefreshCw, Wifi, CheckCircle, ChevronDown, ChevronRight, Pencil } from 'lucide-react'

const CAT_LABELS: Record<string, string> = { asr: '语音转文字', ocr: '图片文字识别', doc: '文档解析' }
const CATEGORIES = ['asr', 'ocr', 'doc'] as const

// PDF + Office 平级子场景，每种类型可独立绑定引擎
export function ProviderSettings() {
  const caps = useStore(s => s.capabilities)
  const addToast = useStore(s => s.addToast)

  // LLM
  const [llmProviders, setLlmProviders] = useState<Provider[]>([])
  const [enabledModels, setEnabledModels] = useState<EnabledModel[]>([])
  const [sceneModels, setScene] = useState<SceneModels>({ generate: null, chat: null, image_process: null })
  const [selectedLlmId, setSelectedLlmId] = useState('deepseek')
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [refreshing, setRefreshing] = useState(false)
  const [refreshResult, setRefreshResult] = useState<{ id: string; owned_by: string }[]>([])

  // Engine
  const [engineProviders, setEngineProviders] = useState<EngineProv[]>([])
  const [engineModels, setEngineModels] = useState<EngineModel[]>([])
  const [engineBindings, setEngineBindings] = useState<Record<string, number | null>>({})
  const [selectedEngineId, setSelectedEngineId] = useState('dashscope')
  const [engineApiKeys, setEngineApiKeys] = useState<Record<string, string>>({})
  const [engineRefreshing, setEngineRefreshing] = useState(false)
  const [engineRefreshResult, setEngineRefreshResult] = useState<{ id: string; owned_by: string; desc?: string }[]>([])

  // Add provider
  const [showAddLlm, setShowAddLlm] = useState(false)
  const [newLlmName, setNewLlmName] = useState('')
  const [newLlmBaseUrl, setNewLlmBaseUrl] = useState('')
  const [newLlmApiKey, setNewLlmApiKey] = useState('')
  const [showAddEngine, setShowAddEngine] = useState(false)
  const [newName, setNewName] = useState('')
  const [newBaseUrl, setNewBaseUrl] = useState('')
  const [newApiKey, setNewApiKey] = useState('')
  const [manualModelName, setManualModelName] = useState('')
  const [manualModelCat, setManualModelCat] = useState('asr')

  // Collapse
  const [llmOpen, setLlmOpen] = useState(true)
  const [engineOpen, setEngineOpen] = useState(true)

  const loadAll = async () => {
    try {
      const [p, m, s, ep, em, eb] = await Promise.all([
        listProviders(), listEnabledModels(), getSceneModels(),
        listEngineProviders(), listEngineModels(), getEngineBindings(),
      ])
      setLlmProviders(p as Provider[]); setEnabledModels(m as EnabledModel[]); setScene(s as SceneModels)
      setEngineProviders(ep as EngineProv[]); setEngineModels(em as EngineModel[]); setEngineBindings(eb as Record<string, number | null>)
      // 配置已变更 → 广播，让 GenPanel 模型/引擎下拉、store capabilities 实时刷新
      // （挂在 loadAll 末尾：全部 14 个保存/增删改 handler 都 await loadAll()）
      notifyConfigChanged()
    } catch (err) {
      console.error('加载设置失败:', err)
    }
  }
  useEffect(() => { loadAll() }, [])

  const provName = (pid: string) =>
    llmProviders.find(p => p.id === pid)?.name || engineProviders.find(p => p.id === pid)?.name || pid

  // LLM handlers
  const handleLlmRefresh = async () => {
    setRefreshing(true)
    const r = await refreshModels(selectedLlmId)
    if (r.models) setRefreshResult(r.models)
    setRefreshing(false)
  }
  const handleEnableModel = async (name: string) => { await addModel(selectedLlmId, name); await loadAll(); addToast(`已启用 ${name}`) }
  const handleDeleteLlmModel = async (mid: number) => {
    const model = enabledModels.find(m => m.id === mid)
    await deleteModel(mid)
    if (sceneModels.generate === mid) setScene(s => ({ ...s, generate: null }))
    if (sceneModels.chat === mid) setScene(s => ({ ...s, chat: null }))
    if (sceneModels.image_process === mid) setScene(s => ({ ...s, image_process: null }))
    await loadAll()
    addToast(`已移除 ${model?.model_name || mid}`)
  }
  const handleToggleImageCap = async (m: EnabledModel) => {
    const hasImage = m.capabilities?.includes('image')
    const newCaps = hasImage
      ? (m.capabilities || ['text']).filter(c => c !== 'image')
      : [...(m.capabilities || ['text']), 'image']
    await updateModel(m.id, { capabilities: newCaps })
    await loadAll()
    addToast(hasImage ? '已标记为纯文本' : '已标记为多模态')
  }

  // 模型元数据编辑（上下文窗口 / 价格 —— 中转/自定义模型修正，未填回退厂商默认）
  const [editingModel, setEditingModel] = useState<EnabledModel | null>(null)
  const [editCtx, setEditCtx] = useState('')
  const [editPriceIn, setEditPriceIn] = useState('')
  const [editPriceOut, setEditPriceOut] = useState('')
  const openModelEdit = (m: EnabledModel) => {
    setEditingModel(m)
    setEditCtx(m.context_window ? String(m.context_window) : '')
    setEditPriceIn(m.price_in ? String(m.price_in) : '')
    setEditPriceOut(m.price_out ? String(m.price_out) : '')
  }
  const saveModelEdit = async () => {
    if (!editingModel) return
    const data: { context_window?: number; price_in?: number; price_out?: number } = {}
    if (editCtx.trim()) data.context_window = parseInt(editCtx, 10)
    if (editPriceIn.trim()) data.price_in = parseFloat(editPriceIn)
    if (editPriceOut.trim()) data.price_out = parseFloat(editPriceOut)
    await updateModel(editingModel.id, data)
    setEditingModel(null)
    await loadAll()
    addToast(`已更新 ${editingModel.model_name} 元数据`)
  }

  // 对话上下文设置（C4）：预算档位 / 历史上限 / 文档引用模式（存 cfg.chat_context）
  const [chatCtx, setChatCtx] = useState<{ budget_ratio: string; history_limit: string; doc_mode: string }>({ budget_ratio: 'auto', history_limit: '0', doc_mode: 'auto' })
  useEffect(() => {
    getConfig().then((c: any) => {
      const cc = c?.chat_context || {}
      setChatCtx({
        budget_ratio: cc.budget_ratio ?? 'auto',
        history_limit: String(cc.history_limit ?? 0),
        doc_mode: cc.doc_mode ?? 'auto',
      })
    }).catch(() => {})
  }, [])
  const saveChatCtx = async () => {
    await updateConfig({
      chat_context: {
        budget_ratio: chatCtx.budget_ratio,
        history_limit: parseInt(chatCtx.history_limit, 10) || 0,
        doc_mode: chatCtx.doc_mode,
      },
    })
    notifyConfigChanged()
    addToast('对话上下文设置已保存')
  }
  const handleSceneBind = async (scene: 'generate' | 'chat' | 'image_process', mid: number | null) => {
    const u = { ...sceneModels, [scene]: mid }; setScene(u); await setSceneModels(u)
    notifyConfigChanged()  // 场景绑定不经过 loadAll，单独广播
  }
  const handleSaveLlmKey = async () => {
    const current = llmProviders.find(p => p.id === selectedLlmId)?.api_key || ''
    const k = (apiKeys[selectedLlmId] || undefined) ?? current
    if (k === current) return  // 未变更，跳过
    await updateProvider(selectedLlmId, { api_key: k }); await loadAll()
    addToast('API Key 已保存')
  }
  const handleAddLlmProv = async () => {
    if (!newLlmName.trim()) return
    await addProvider({ name: newLlmName.trim(), base_url: newLlmBaseUrl, api_key: newLlmApiKey, type: 'custom' })
    setNewLlmName(''); setNewLlmBaseUrl(''); setNewLlmApiKey(''); setShowAddLlm(false); await loadAll()
    addToast(`LLM 厂商「${newLlmName.trim()}」已添加`)
  }
  const handleDeleteLlmProv = async (pid: string) => {
    if (!window.confirm('确定删除该 LLM 厂商？关联的启用模型也会被移除。')) return
    await deleteProvider(pid); await loadAll()
    if (selectedLlmId === pid) setSelectedLlmId(llmProviders[0]?.id || 'deepseek')
    addToast('LLM 厂商已删除')
  }

  // Engine handlers
  const handleEngineRefresh = async () => {
    setEngineRefreshing(true)
    const r = await refreshModels(selectedEngineId)
    if (r.models) setEngineRefreshResult(r.models)
    setEngineRefreshing(false)
  }
  const handleSaveEngineKey = async () => {
    const current = engineProviders.find(p => p.id === selectedEngineId)?.api_key || ''
    const k = (engineApiKeys[selectedEngineId] || undefined) ?? current
    if (k === current) return  // 未变更，跳过
    try {
      await apiFetch(apiUrl(`/engine-providers/${selectedEngineId}`), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key: k }),
      }); await loadAll()
      addToast('引擎 Key 已保存')
    } catch (e: any) { addToast(`保存失败: ${e?.message || e}`, 'error') }
  }
  const handleAssignEngine = async (modelName: string, cat: string | null) => {
    if (!cat) {
      const exist = engineModels.find(m => m.provider_id === selectedEngineId && m.model_name === modelName)
      if (exist) { await deleteEngineModel(exist.id); await loadAll() }
    } else {
      try {
        await apiFetch(apiUrl('/engine-models'), {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ provider_id: selectedEngineId, model_name: modelName, category: cat }),
        }); await loadAll()
      } catch (e: any) { addToast(`分配失败: ${e?.message || e}`, 'error') }
    }
  }
  const handleManualAdd = async () => {
    if (!manualModelName.trim()) return
    try {
      await apiFetch(apiUrl('/engine-models'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider_id: selectedEngineId, model_name: manualModelName.trim(), category: manualModelCat }),
      }); setManualModelName(''); await loadAll()
    } catch (e: any) { addToast(`添加失败: ${e?.message || e}`, 'error') }
  }
  const handleAddEngineProv = async () => {
    if (!newName.trim()) return
    await addEngineProvider({ name: newName, base_url: newBaseUrl, api_key: newApiKey, type: 'custom' })
    setNewName(''); setNewBaseUrl(''); setNewApiKey(''); setShowAddEngine(false); await loadAll()
    addToast(`引擎厂商「${newName}」已添加`)
  }
  const handleDeleteEngineProv = async (pid: string) => {
    if (!window.confirm('确定删除？')) return
    await deleteEngineProvider(pid); await loadAll()
    if (selectedEngineId === pid) setSelectedEngineId(engineProviders[0]?.id || 'dashscope')
    addToast('引擎厂商已删除')
  }
  const handleEngineBind = async (cat: string, modelId: number | null) => {
    const u = { ...engineBindings, [cat]: modelId }; setEngineBindings(u); await saveEngineBindings(u)
    addToast('引擎绑定已更新')
  }

  const handleRestoreDefaults = async () => {
    if (!window.confirm('确定恢复所有引擎绑定为系统默认？当前自定义配置将丢失。')) return
    // 收集所有已知 binding key → 全部置空（后端 [] = 清除，下次启动恢复默认）
    const clearAll: Record<string, any[]> = {}
    for (const cat of Object.keys(CAT_LABELS)) {
      clearAll[cat] = []
    }
    const allDocSubs = Object.keys(caps?.scenarioLabels || {})
    for (const key of allDocSubs) {
      clearAll[key] = []
    }
    await saveEngineBindings(clearAll)
    setEngineBindings({})
    await loadAll()
    addToast('引擎绑定已恢复为系统默认')
  }

  // 组件卸载时兜底保存未提交的 Key（用户输入后直接关设置不丢）
  const refSaveLlm = useRef(handleSaveLlmKey); refSaveLlm.current = handleSaveLlmKey
  const refSaveEngine = useRef(handleSaveEngineKey); refSaveEngine.current = handleSaveEngineKey
  useEffect(() => () => { refSaveLlm.current(); refSaveEngine.current() }, [])

  // Derived data
  const selLlm = llmProviders.find(p => p.id === selectedLlmId)
  const selEngine = engineProviders.find(p => p.id === selectedEngineId)
  const allLlmNames = new Set(refreshResult.map(r => r.id))
  enabledModels.filter(m => m.provider_id === selectedLlmId).forEach(m => allLlmNames.add(m.model_name))

  // 从 EngineRegistry 动态获取已注册引擎列表，不再硬编码
  const [engineRegistry, setEngineRegistry] = useState<Record<string, string[]>>({})
  useEffect(() => { getEngineRegistry().then(setEngineRegistry).catch(() => {}) }, [])
  const engineBuiltins = engineRegistry[engineModels.find(m => m.provider_id === selectedEngineId)?.category || ''] || []
  const allEngineNames = new Set(engineBuiltins)
  engineRefreshResult.forEach(r => allEngineNames.add(r.id))
  engineModels.filter(m => m.provider_id === selectedEngineId).forEach(m => allEngineNames.add(m.model_name))

  const engineByCat: Record<string, EngineModel[]> = {}
  for (const m of engineModels) (engineByCat[m.category] ||= []).push(m)

  const CollapseHeader = ({ open, setOpen, title, stats }: { open: boolean; setOpen: (v: boolean) => void; title: string; stats: string }) => (
    <button onClick={() => setOpen(!open)} className="w-full flex items-center gap-2 p-4 hover:bg-surface-hover transition-colors text-left">
      {open ? <ChevronDown size={14} className="text-disabled shrink-0" /> : <ChevronRight size={14} className="text-disabled shrink-0" />}
      <h3 className="text-[15px] font-semibold flex-1">{title}</h3>
      <span className="text-[11px] text-disabled">{stats}</span>
    </button>
  )

  const ProviderTabs = ({ items, selected, onSelect, extra }: {
    items: { id: string; name: string; subtitle?: string }[]; selected: string; onSelect: (id: string) => void; extra?: React.ReactNode
  }) => (
    <div className="flex gap-1.5 flex-wrap">
      {items.map(p => (
        <button key={p.id} onClick={() => onSelect(p.id)}
          className={`px-2.5 py-1 rounded-full text-[12px] transition-colors ${selected === p.id ? 'bg-accent/10 text-accent font-medium' : 'text-dimmed hover:bg-surface-hover'}`}>
          {p.name}
          {p.subtitle && <span className="text-[10px] text-disabled ml-0.5">{p.subtitle}</span>}
        </button>
      ))}
      {extra}
    </div>
  )

  return (
    <div className="space-y-4">
      {/* ======== LLM 厂商 ======== */}
      <div className="bg-elevated rounded-xl border border-border-light overflow-hidden">
        <CollapseHeader open={llmOpen} setOpen={setLlmOpen} title="LLM 厂商"
          stats={`${llmProviders.length} 个厂商 · ${enabledModels.length} 个模型`} />
        {llmOpen && (
          <div className="px-4 pb-4 space-y-3 border-t border-border-light pt-3">
            <ProviderTabs
              items={llmProviders.filter(p => p.enabled !== 0).map(p => ({ id: p.id, name: p.name, subtitle: p.type === 'built-in' ? '内置' : (p.type || '') }))}
              selected={selectedLlmId} onSelect={(id) => { setSelectedLlmId(id); setRefreshResult([]) }}
              extra={<button onClick={() => setShowAddLlm(!showAddLlm)}
                className="px-2.5 py-1 rounded-full text-[11px] text-accent hover:bg-accent/5"><Plus size={12} />添加</button>} />

            {showAddLlm && (
              <div className="p-3 rounded-[10px] bg-surface space-y-2">
                <input value={newLlmName} onChange={e => setNewLlmName(e.target.value)} placeholder="厂商名称（如 vLLM / Ollama）" className="w-full px-3 py-2 bg-elevated border border-border-default rounded-lg text-[13px] outline-none" />
                <input value={newLlmBaseUrl} onChange={e => setNewLlmBaseUrl(e.target.value)} placeholder="Base URL（如 http://localhost:8000/v1）" className="w-full px-3 py-2 bg-elevated border border-border-default rounded-lg text-[13px] outline-none" />
                <input value={newLlmApiKey} onChange={e => setNewLlmApiKey(e.target.value)} placeholder="API Key（可选）" type="password" className="w-full px-3 py-2 bg-elevated border border-border-default rounded-lg text-[13px] outline-none font-mono" />
                <div className="flex gap-2">
                  <button onClick={handleAddLlmProv} disabled={!newLlmName.trim()} className="px-3 py-1.5 text-[12px] font-medium bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-40">添加</button>
                  <button onClick={() => setShowAddLlm(false)} className="px-3 py-1.5 text-[12px] text-dimmed hover:bg-surface-hover rounded-lg">取消</button>
                </div>
              </div>
            )}

            {selLlm && (
              <>
                <div className="flex items-center gap-2">
                  <input type="password" value={apiKeys[selectedLlmId] ?? selLlm.api_key}
                    onChange={e => setApiKeys(k => ({ ...k, [selectedLlmId]: e.target.value }))}
                    onBlur={() => handleSaveLlmKey()}
                    placeholder="API Key（输入后自动保存）" className="flex-1 px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] font-mono outline-none" />
                  <button onClick={() => testProviderConnection(selectedLlmId)}
                    className="px-3 py-2 text-[12px] text-dimmed hover:bg-surface-hover rounded-lg flex items-center gap-1"><Wifi size={12} />测试</button>
                  {selLlm?.type !== 'built-in' && (
                    <button onClick={() => handleDeleteLlmProv(selectedLlmId)}
                      className="flex items-center gap-1 text-[11px] text-error hover:underline"><Trash2 size={11} />删除</button>
                  )}
                  <button onClick={handleLlmRefresh} disabled={refreshing}
                    className="flex items-center gap-1 px-3 py-1.5 text-[12px] text-accent hover:bg-accent/5 rounded-lg">
                    <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />刷新
                  </button>
                </div>
                {allLlmNames.size > 0 && (
                  <div className="max-h-48 overflow-y-auto space-y-1 border border-border-light rounded-[10px] p-2">
                    {[...allLlmNames].map(name => {
                      const enabled = enabledModels.some(em => em.provider_id === selectedLlmId && em.model_name === name)
                      const model = enabledModels.find(em => em.provider_id === selectedLlmId && em.model_name === name)
                      return (
                        <div key={name} className="flex items-center justify-between px-2 py-1.5 rounded-md hover:bg-surface-hover text-[13px]">
                          <span className="flex-1 min-w-0">
                            <span className="truncate">{name}</span>
                            {model?.capabilities && <span className="text-[10px] text-disabled ml-1">{model.capabilities.join(' · ')}</span>}
                            {model?.context_window != null && <span className="text-[10px] text-accent ml-1">{model.context_window >= 1000 ? `${Math.round(model.context_window / 1000)}K` : `${model.context_window}`} ctx</span>}
                          </span>
                          {enabled && model ? (
                            <span className="flex items-center gap-1 shrink-0">
                              <button onClick={() => openModelEdit(model)} className="text-[11px] text-muted hover:text-dimmed" title="编辑上下文窗口/价格"><Pencil size={10} /></button>
                              <button onClick={() => handleDeleteLlmModel(model.id)} className="text-[11px] text-success flex items-center gap-1 hover:text-error transition-colors"><CheckCircle size={10} />已启用</button>
                            </span>
                          ) : <button onClick={() => handleEnableModel(name)} className="text-[11px] text-accent hover:underline shrink-0"><Plus size={10} />启用</button>}
                        </div>
                      )
                    })}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* ======== LLM 用途 ======== */}
      {enabledModels.length > 0 && (
        <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-3">
          <h3 className="text-[17px] font-semibold">LLM 用途</h3>
          <div className="flex flex-wrap gap-2">
            {enabledModels.map(m => {
              const isImage = m.capabilities?.includes('image')
              return (
              <span key={m.id} className="inline-flex items-center gap-1 px-2.5 py-1 bg-accent/10 text-accent rounded-full text-[12px]">
                {provName(m.provider_id)} / {m.model_name}
                <button onClick={() => handleToggleImageCap(m)}
                  className={`px-1 py-0.5 rounded text-[10px] font-medium transition-colors ${isImage ? 'bg-success/20 text-success hover:bg-success/30' : 'bg-surface-hover text-dimmed hover:bg-surface-hover'}`}
                  title={isImage ? '点击移除多模态标记' : '点击标记为多模态模型'}>
                  {isImage ? '多模态' : '文本'}
                </button>
                {/* 视频直传能力标记（capabilities 含 video，方案 §5.2：自动显示，无需配置） */}
                {m.capabilities?.includes('video') && (
                  <span className="px-1 py-0.5 rounded text-[10px] font-medium bg-accent/10 text-accent"
                    title="支持视频直传（视频文件直接传给模型，服务端抽帧）">视频</span>
                )}
                <button onClick={() => handleDeleteLlmModel(m.id)} className="hover:text-error"><X size={11} /></button>
              </span>
              )
            })}
          </div>
          {([
            { key: 'generate' as const, label: '生成笔记', filter: () => true },
            { key: 'chat' as const, label: 'AI 对话', filter: () => true },
            { key: 'image_process' as const, label: '图片处理', filter: (m: EnabledModel) => m.capabilities?.includes('image') },
          ]).map(({ key, label, filter }) => (
            <div key={key} className="flex items-center gap-3">
              <span className="text-[13px] font-medium w-20 shrink-0">{label}</span>
              <select value={sceneModels[key] || ''} onChange={e => handleSceneBind(key, e.target.value ? Number(e.target.value) : null)}
                className="flex-1 px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] outline-none">
                <option value="">使用全局默认</option>
                {enabledModels.filter(filter).map(m => <option key={m.id} value={m.id}>{provName(m.provider_id)} / {m.model_name}</option>)}
              </select>
            </div>
          ))}
        </div>
      )}

      {/* ======== 引擎厂商 ======== */}
      <div className="bg-elevated rounded-xl border border-border-light overflow-hidden">
        <CollapseHeader open={engineOpen} setOpen={setEngineOpen} title="引擎厂商"
          stats={`${engineProviders.length} 个厂商 · ${engineModels.length} 个模型`} />
        {engineOpen && (
          <div className="px-4 pb-4 space-y-3 border-t border-border-light pt-3">
            <ProviderTabs
              items={engineProviders.map(p => ({ id: p.id, name: p.name, subtitle: p.type }))}
              selected={selectedEngineId} onSelect={setSelectedEngineId}
              extra={<button onClick={() => setShowAddEngine(!showAddEngine)}
                className="px-2.5 py-1 rounded-full text-[11px] text-accent hover:bg-accent/5"><Plus size={12} />添加</button>} />

            {showAddEngine && (
              <div className="p-3 rounded-[10px] bg-surface space-y-2">
                <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="厂商名称" className="w-full px-3 py-2 bg-elevated border border-border-default rounded-lg text-[13px] outline-none" />
                <input value={newBaseUrl} onChange={e => setNewBaseUrl(e.target.value)} placeholder="Base URL (可选)" className="w-full px-3 py-2 bg-elevated border border-border-default rounded-lg text-[13px] outline-none" />
                <input value={newApiKey} onChange={e => setNewApiKey(e.target.value)} placeholder="API Key (可选)" type="password" className="w-full px-3 py-2 bg-elevated border border-border-default rounded-lg text-[13px] outline-none font-mono" />
                <div className="flex gap-2">
                  <button onClick={handleAddEngineProv} disabled={!newName.trim()} className="px-3 py-1.5 text-[12px] font-medium bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-40">添加</button>
                  <button onClick={() => setShowAddEngine(false)} className="px-3 py-1.5 text-[12px] text-dimmed hover:bg-surface-hover rounded-lg">取消</button>
                </div>
              </div>
            )}

            {selEngine && (
              <>
                {selEngine.type === 'cloud' && (
                  <div className="flex items-center gap-2">
                    <input type="password" value={engineApiKeys[selectedEngineId] ?? selEngine.api_key}
                      onChange={e => setEngineApiKeys(k => ({ ...k, [selectedEngineId]: e.target.value }))}
                      onBlur={() => handleSaveEngineKey()}
                      placeholder="API Key（输入后自动保存）" className="flex-1 px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] font-mono outline-none" />
                    <button onClick={handleEngineRefresh} disabled={engineRefreshing}
                      className="flex items-center gap-1 px-3 py-1.5 text-[12px] text-accent hover:bg-accent/5 rounded-lg">
                      <RefreshCw size={12} className={engineRefreshing ? 'animate-spin' : ''} />刷新
                    </button>
                  </div>
                )}
                {selectedEngineId !== 'bcut' && selectedEngineId !== 'dashscope' && selectedEngineId !== 'doc-local' && (
                  <button onClick={() => handleDeleteEngineProv(selectedEngineId)}
                    className="flex items-center gap-1 text-[11px] text-error hover:underline"><Trash2 size={11} />删除</button>
                )}

                {allEngineNames.size > 0 && (
                  <div className="space-y-1 max-h-48 overflow-y-auto">
                    {[...allEngineNames].map(name => {
                      const existing = engineModels.find(m => m.provider_id === selectedEngineId && m.model_name === name)
                      const caps = existing?.capabilities || []
                      const desc = existing?.desc || engineRefreshResult.find(r => r.id === name)?.desc || ''
                      return (
                        <div key={name} className="flex items-center justify-between px-2 py-1.5 rounded-md hover:bg-surface-hover text-[13px]">
                          <span className="flex-1 min-w-0">
                            <span className="font-medium">{name}</span>
                            {desc && <span className="text-[10px] text-disabled ml-1.5">{desc}</span>}
                            {caps.length > 0 && (
                              <span className="inline-flex gap-0.5 ml-1.5 align-middle">
                                {caps.map(c => (
                                  <span key={c} className="px-1 py-0.5 rounded text-[9px] font-medium bg-surface-hover text-dimmed">{c}</span>
                                ))}
                              </span>
                            )}
                          </span>
                          <select value={existing?.category || ''}
                            onChange={e => handleAssignEngine(name, e.target.value || null)}
                            className={`shrink-0 ml-2 px-2 py-0.5 rounded-md text-[11px] border outline-none ${existing?.category ? 'border-accent bg-accent/5 text-accent' : 'border-border-light text-disabled'}`}>
                            <option value="">未分配</option>
                            {CATEGORIES.map(c => <option key={c} value={c}>{CAT_LABELS[c]}</option>)}
                          </select>
                        </div>
                      )
                    })}
                  </div>
                )}
                <div className="flex items-center gap-2 pt-2 border-t border-border-light">
                  <input value={manualModelName} onChange={e => setManualModelName(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleManualAdd()}
                    placeholder="输入模型名称..." className="flex-1 px-2.5 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
                  <select value={manualModelCat} onChange={e => setManualModelCat(e.target.value)}
                    className="px-2 py-1.5 bg-surface border border-border-default rounded-lg text-[11px] outline-none">
                    {CATEGORIES.map(c => <option key={c} value={c}>{CAT_LABELS[c]}</option>)}
                  </select>
                  <button onClick={handleManualAdd} disabled={!manualModelName.trim()}
                    className="px-3 py-1.5 text-[11px] font-medium bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-40 whitespace-nowrap">添加</button>
                </div>
              </>
            )}
          </div>
        )}
      </div>

      {/* ======== 引擎用途 ======== */}
      <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-[17px] font-semibold">引擎用途</h3>
            <p className="text-[13px] text-muted">为每种场景选择默认引擎。</p>
          </div>
          <button
            onClick={handleRestoreDefaults}
            className="px-3 py-1.5 text-[12px] text-accent hover:bg-accent/5 rounded-lg border border-accent/20 shrink-0"
          >
            恢复默认
          </button>
        </div>

        {/* asr / ocr — 简单类别，每个一个下拉框 */}
        {Object.entries(CAT_LABELS).map(([cat, label]) => {
          const opts = engineByCat[cat] || []
          if (opts.length === 0) return null
          const options: EngineOption[] = opts.map(m => {
            const co = (caps?.engineOptions?.[cat] || []).find(o => o.name === m.model_name)
            return {
              id: m.id, provider: m.provider_id, name: m.model_name,
              desc: co?.desc || m.desc || '', capabilities: m.capabilities,
              license: co?.license, plugin: co?.plugin,
              displayName: co?.displayName || m.model_name,
            }
          })
          return (
            <EngineSelect key={cat}
              category={cat} label={label} options={options}
              binding={engineBindings[cat] ?? null} onChange={handleEngineBind}
              variant="section"
            />
          )
        })}

        {/* doc 拆分为子场景 */}
        {engineByCat['doc']?.length > 0 && (
          <>
            <div className="pt-2 mt-1 border-t border-border-light">
              <span className="text-[11px] text-disabled uppercase tracking-wider">文档解析方案</span>
            </div>
            {Object.entries(caps?.scenarioLabels || {}).map(([key, label]) => {
              const allDoc = engineByCat['doc'] || []
              const engineNames = caps?.fileTypeMap?.[key.replace('doc:', '')] || []
              const filtered = allDoc.filter(m => engineNames.includes(m.model_name))
              if (filtered.length === 0) return null
              const options: EngineOption[] = filtered.map(m => {
                const co = (caps?.engineOptions?.['doc'] || []).find(o => o.name === m.model_name)
                return {
                  id: m.id, provider: m.provider_id, name: m.model_name,
                  desc: co?.desc || m.desc || '', capabilities: m.capabilities,
                  license: co?.license, plugin: co?.plugin,
                  displayName: co?.displayName || m.model_name,
                }
              })
              return (
                <EngineSelect key={key}
                  category={key} label={label} options={options}
                  binding={engineBindings[key] ?? null} onChange={handleEngineBind}
                  variant="section"
                />
              )
            })}
          </>
        )}
      </div>

      {/* ======== 对话上下文（C4 用户覆盖设置） ======== */}
      <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-3">
        <div>
          <h3 className="text-[17px] font-semibold">对话上下文</h3>
          <p className="text-[13px] text-muted">预算 = 模型窗口 × 比例（留输出空间）。token 敏感可调低。</p>
        </div>
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <label className="w-28 shrink-0 text-[13px] text-dimmed">预算档位</label>
            <select value={chatCtx.budget_ratio} onChange={e => setChatCtx(s => ({ ...s, budget_ratio: e.target.value }))}
              className="flex-1 px-2.5 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none">
              <option value="auto">自动（窗口 × 0.7）</option>
              <option value="0.85">高（× 0.85）</option>
              <option value="0.7">中（× 0.7）</option>
              <option value="0.5">低（× 0.5）</option>
            </select>
          </div>
          <div className="flex items-center gap-3">
            <label className="w-28 shrink-0 text-[13px] text-dimmed">历史轮数上限</label>
            <input value={chatCtx.history_limit} onChange={e => setChatCtx(s => ({ ...s, history_limit: e.target.value }))}
              placeholder="0 = 按预算自动"
              className="flex-1 px-2.5 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none" />
          </div>
          <div className="flex items-center gap-3">
            <label className="w-28 shrink-0 text-[13px] text-dimmed">文档引用</label>
            <select value={chatCtx.doc_mode} onChange={e => setChatCtx(s => ({ ...s, doc_mode: e.target.value }))}
              className="flex-1 px-2.5 py-1.5 bg-surface border border-border-default rounded-lg text-[12px] outline-none">
              <option value="auto">自动（预算富余全文）</option>
              <option value="full">全文（放宽截断）</option>
              <option value="summary">摘要（1000 字符）</option>
            </select>
          </div>
          <button onClick={saveChatCtx} className="px-3 py-1.5 text-[12px] font-medium bg-accent text-white rounded-lg hover:bg-accent-hover">保存</button>
        </div>
      </div>

      {/* 模型元数据编辑弹窗（上下文窗口 / 价格 —— 对话预算与费用显示数据源） */}
      {editingModel && (
        <div className="fixed inset-0 z-[10001] flex items-center justify-center bg-black/20 backdrop-blur-sm" onClick={() => setEditingModel(null)}>
          <div className="bg-elevated rounded-2xl shadow-2xl border border-border-light p-6 max-w-[420px] w-full mx-4" onClick={e => e.stopPropagation()}>
            <h3 className="text-[15px] font-semibold mb-1">模型元数据</h3>
            <p className="text-[11px] text-disabled mb-4">{editingModel.model_name} · 留空则回退厂商默认/保守值（对话预算与费用用）</p>
            <div className="space-y-3">
              <div>
                <label className="text-[11px] text-dimmed">上下文窗口（token）</label>
                <input value={editCtx} onChange={e => setEditCtx(e.target.value)} placeholder="如 128000 或 1000000"
                  className="w-full px-3 py-2 bg-surface border border-border-default rounded-lg text-[12px] outline-none mt-1" />
              </div>
              <div className="flex gap-2">
                <div className="flex-1">
                  <label className="text-[11px] text-dimmed">输入价 ¥/百万 token</label>
                  <input value={editPriceIn} onChange={e => setEditPriceIn(e.target.value)} placeholder="如 2"
                    className="w-full px-3 py-2 bg-surface border border-border-default rounded-lg text-[12px] outline-none mt-1" />
                </div>
                <div className="flex-1">
                  <label className="text-[11px] text-dimmed">输出价 ¥/百万 token</label>
                  <input value={editPriceOut} onChange={e => setEditPriceOut(e.target.value)} placeholder="如 8"
                    className="w-full px-3 py-2 bg-surface border border-border-default rounded-lg text-[12px] outline-none mt-1" />
                </div>
              </div>
            </div>
            <div className="flex gap-2 justify-end mt-5">
              <button onClick={() => setEditingModel(null)} className="px-4 py-2 text-[13px] text-dimmed hover:bg-surface rounded-lg">取消</button>
              <button onClick={saveModelEdit} className="px-4 py-2 bg-accent text-white text-[13px] font-medium rounded-lg">保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
