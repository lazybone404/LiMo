/**
 * 插件管理页 — 设置页「插件」tab（EnginePlugin v1）
 * 按 category 分组渲染（类别无关），卡片含展示名/能力描述/license 徽章/启用开关/卸载/config_schema 表单。
 */
import { useState, useEffect, useCallback } from 'react'
import {
  listPlugins, setPluginEnabled, deletePlugin, savePluginConfig,
  type PluginInfo,
} from '../../api/plugins'
import { getErrorMessage } from '../../api/core'
import { useStore } from '../../store'
import { Puzzle, Trash2, Loader2 } from 'lucide-react'

const CAT_LABELS: Record<string, string> = {
  doc: '文档引擎', ocr: '图片识别', asr: '语音转写',
}

function defaultForType(t: string): unknown {
  if (t === 'bool') return false
  if (t === 'int' || t === 'number') return 0
  return ''
}

/** config_schema 简版表单（string→text / int|number→number / bool→checkbox / select→下拉 / textarea→多行） */
function ConfigForm({ plugin }: { plugin: PluginInfo }) {
  const addToast = useStore(s => s.addToast)
  const [values, setValues] = useState<Record<string, unknown>>(() => {
    const v: Record<string, unknown> = {}
    for (const f of plugin.configSchema || []) {
      v[f.name] = plugin.config?.[f.name] ?? f.default ?? defaultForType(f.type)
    }
    return v
  })
  const [saving, setSaving] = useState(false)

  const save = async () => {
    setSaving(true)
    try {
      await savePluginConfig(plugin.name, values)
      addToast(`已保存 ${plugin.displayName || plugin.name} 配置`, 'success')
    } catch (e) {
      addToast(`保存失败: ${getErrorMessage(e)}`, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!plugin.configSchema || plugin.configSchema.length === 0) return null

  return (
    <div className="pt-3 mt-3 border-t border-border-light space-y-3">
      <span className="text-[11px] text-disabled uppercase tracking-wider">配置</span>
      {plugin.configSchema.map(field => (
        <div key={field.name}>
          <label className="text-[12px] font-medium text-dimmed block mb-1">{field.label || field.name}</label>
          {field.type === 'bool' ? (
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox" checked={!!values[field.name]}
                onChange={e => setValues(v => ({ ...v, [field.name]: e.target.checked }))}
                className="w-4 h-4 accent-[#007AFF]"
              />
              {field.hint && <span className="text-[12px] text-muted">{field.hint}</span>}
            </label>
          ) : field.type === 'select' ? (
            <select
              value={String(values[field.name] ?? '')}
              onChange={e => setValues(v => ({ ...v, [field.name]: e.target.value }))}
              className="w-full px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] outline-none"
            >
              {(field.options || []).map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : field.type === 'textarea' ? (
            <textarea
              rows={3} value={String(values[field.name] ?? '')}
              onChange={e => setValues(v => ({ ...v, [field.name]: e.target.value }))}
              className="w-full px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] outline-none resize-none"
            />
          ) : (
            <input
              type={field.type === 'int' || field.type === 'number' ? 'number' : 'text'}
              value={String(values[field.name] ?? '')}
              onChange={e => setValues(v => ({
                ...v,
                [field.name]: field.type === 'int' || field.type === 'number' ? Number(e.target.value) : e.target.value,
              }))}
              className="w-full px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[13px] outline-none"
            />
          )}
          {field.hint && field.type !== 'bool' && (
            <p className="text-[11px] text-disabled mt-1">{field.hint}</p>
          )}
        </div>
      ))}
      <button
        onClick={save} disabled={saving}
        className="px-4 py-1.5 text-[12px] font-medium bg-accent text-white rounded-lg hover:bg-accent-hover disabled:opacity-40"
      >
        {saving ? '保存中...' : '保存配置'}
      </button>
    </div>
  )
}

function PluginCard({ plugin, onChanged }: { plugin: PluginInfo; onChanged: () => void }) {
  const addToast = useStore(s => s.addToast)
  const [busy, setBusy] = useState(false)

  const toggle = async (enabled: boolean) => {
    setBusy(true)
    try {
      await setPluginEnabled(plugin.name, enabled)
      addToast(`${enabled ? '已启用' : '已禁用'} ${plugin.displayName || plugin.name}，重启后端后生效`, 'info')
      onChanged()
    } catch (e) {
      addToast(`操作失败: ${getErrorMessage(e)}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  const uninstall = async () => {
    const ok = window.confirm(
      `将删除插件目录（含独立 venv 与模型，不可恢复）。确定卸载「${plugin.displayName || plugin.name}」？`,
    )
    if (!ok) return
    setBusy(true)
    try {
      await deletePlugin(plugin.name)
      addToast(`已卸载 ${plugin.displayName || plugin.name}`, 'success')
      onChanged()
    } catch (e) {
      addToast(`卸载失败: ${getErrorMessage(e)}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-elevated rounded-xl border border-border-light p-5 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-[15px] font-medium">{plugin.displayName || plugin.name}</span>
            {plugin.license && (
              <span className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-surface-hover text-dimmed">{plugin.license}</span>
            )}
            <span className="text-[10px] text-disabled font-mono">{plugin.name}</span>
            {plugin.execMode === 'inproc' && (
              <span className="px-1.5 py-0.5 rounded text-[9px] font-medium bg-accent/10 text-accent">进程内</span>
            )}
          </div>
          {plugin.userDesc && <p className="text-[12px] text-muted mt-1">{plugin.userDesc}</p>}
          {plugin.reason && (
            <p className="text-[11px] text-warning mt-1">
              未加载：{plugin.reason === 'no-command-no-trusted' ? '无 command 且未声明 trusted=true' : plugin.reason}
            </p>
          )}
          <p className="text-[10px] text-disabled mt-1.5 font-mono truncate" title={plugin.path}>{plugin.path}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={() => toggle(!plugin.enabled)} disabled={busy || !!plugin.reason}
            className={`px-3 py-1.5 text-[12px] font-medium rounded-lg border transition-colors ${
              plugin.enabled
                ? 'bg-accent/10 text-accent border-accent/20 hover:bg-accent/15'
                : 'bg-surface text-dimmed border-border-default hover:bg-surface-hover'
            } disabled:opacity-40`}
          >
            {plugin.enabled ? '禁用' : '启用'}
          </button>
          <button
            onClick={uninstall} disabled={busy}
            className="flex items-center gap-1 px-3 py-1.5 text-[12px] font-medium text-muted rounded-lg border border-border-default hover:text-red-500 hover:border-red-300 disabled:opacity-40"
          >
            <Trash2 size={12} /> 卸载
          </button>
        </div>
      </div>
      {plugin.enabled && <ConfigForm plugin={plugin} />}
    </div>
  )
}

export function PluginSettings() {
  const [plugins, setPlugins] = useState<PluginInfo[]>([])
  const [loading, setLoading] = useState(true)
  const addToast = useStore(s => s.addToast)

  const load = useCallback(async () => {
    try {
      setPlugins(await listPlugins())
    } catch (e) {
      addToast(`加载插件列表失败: ${getErrorMessage(e)}`, 'error')
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => { load() }, [load])

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-disabled">
        <Loader2 size={24} className="animate-spin" />
      </div>
    )
  }

  if (plugins.length === 0) {
    return (
      <div className="bg-elevated rounded-xl border border-border-light p-8 text-center space-y-3">
        <Puzzle size={32} className="mx-auto text-disabled" />
        <p className="text-[14px] text-muted">暂无插件</p>
        <p className="text-[12px] text-disabled max-w-md mx-auto leading-relaxed">
          可从插件发布仓库 clone/拷贝到 <span className="font-mono">~/.limo/plugins/</span>
          （进程外插件需按 README 建独立 venv），重启后端生效。
        </p>
      </div>
    )
  }

  // 按 category 分组（类别无关：未知类别直接显示原始值）
  const groups = plugins.reduce<Record<string, PluginInfo[]>>((acc, p) => {
    if (!acc[p.category]) acc[p.category] = []
    acc[p.category].push(p)
    return acc
  }, {})

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-2 px-3 py-2 rounded-[10px] bg-accent/5 text-[12px] text-dimmed">
        <Puzzle size={14} className="shrink-0 mt-0.5 text-accent" />
        <span>插件由三方发布，主程序只维护契约与加载。启用/禁用重启后生效；卸载删除插件目录（含 venv/模型），不可恢复。</span>
      </div>
      {Object.entries(groups).map(([cat, items]) => (
        <div key={cat}>
          <h4 className="text-[11px] text-disabled font-medium mb-3 uppercase tracking-wide">
            {CAT_LABELS[cat] || cat}
          </h4>
          <div className="space-y-3">
            {items.map(p => <PluginCard key={p.name} plugin={p} onChanged={load} />)}
          </div>
        </div>
      ))}
    </div>
  )
}
