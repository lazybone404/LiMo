import { useState, useEffect } from 'react'
import { getConfig, updateConfig, getErrorMessage } from '../../api/client'
import { useStore } from '../../store'
import { ACCENT_GROUPS } from '../../lib/accent'
import { TEXTURE_OPTIONS, FONT_OPTIONS, getSavedIntensity, applyIntensity, saveIntensity } from '../../lib/texture'
import { BASE_OPTIONS } from '../../lib/theme-base'
import { PARA_STYLE_OPTIONS, LINE_HEIGHT_RANGE, MEASURE_RANGE, MEASURE_DEFAULT_ON, FONT_SCALE_RANGE, READING_DEFAULTS } from '../../lib/reading-prefs'
import { SKELETON_OPTIONS, CARD_STYLE_OPTIONS, CARD_BG_OPTIONS, CARD_BORDER_OPTIONS, isLiftDisabled } from '../../lib/card-prefs'
import { LAYOUT_OPTIONS } from '../../lib/layout-prefs'
import { THEME_PRESETS } from '../../lib/theme-presets'
import { ArrowLeft, CheckCircle, Circle, Info, Cpu, Key, Globe, Settings, Wrench, Loader2, Palette, Puzzle } from 'lucide-react'
import { ProviderSettings } from './ProviderSettings'
import { PluginSettings } from './PluginSettings'

/**
 * 卡片「逐维度微调」的档位按钮 —— 与方案按钮同一视觉语言但更小（它是次级控件）。
 * 抽成组件只是因为同一形态要出现十次（底 5 + 边 7），不是新机制。
 */
function TuneChip({ selected, onClick, children }: {
  selected: boolean; onClick: () => void; children: string
}) {
  return (
    <button
      onClick={onClick}
      className={`text-[11.5px] px-2.5 py-1 rounded-md border transition-colors ${
        selected
          ? 'bg-accent/10 border-accent/30 text-accent font-medium'
          : 'border-border-light text-dimmed hover:bg-surface-hover'
      }`}
    >
      {children}
    </button>
  )
}

/** BiliNote 式设置布局 — 左侧菜单导航 + 右侧内容区 */

const MENU = [
  { key: 'model', label: '模型 & 引擎', icon: Cpu },
  { key: 'plugins', label: '插件', icon: Puzzle },
  { key: 'auth', label: '平台认证', icon: Key },
  { key: 'proxy', label: '代理', icon: Globe },
  { key: 'agent', label: 'Agent 工具', icon: Wrench },
  { key: 'general', label: '通用', icon: Settings },
  { key: 'appearance', label: '外观', icon: Palette },
] as const

const LANG_OPTIONS = [
  { value: 'zh-CN', label: '简体中文' },
  { value: 'zh-TW', label: '繁體中文' },
  { value: 'en', label: 'English' },
  { value: 'ja', label: '日本語' },
  { value: 'ko', label: '한국어' },
  { value: 'fr', label: 'Français' },
  { value: 'de', label: 'Deutsch' },
  { value: 'es', label: 'Español' },
] as const

export function SettingsPage({ onBack }: { onBack: () => void }) {
  const [tab, setTab] = useState('model')

  const [bilibiliCookie, setBilibiliCookie] = useState('')
  const [youtubeCookie, setYoutubeCookie] = useState('')
  const [douyinCookie, setDouyinCookie] = useState('')
  const [proxyEnabled, setProxyEnabled] = useState(false)
  const [proxyUrl, setProxyUrl] = useState('http://127.0.0.1:7890')
  const [saved, setSaved] = useState(false)
  const [cookieConfigured, setCookieConfigured] = useState<Record<string, boolean>>({})
  const [searchProvider, setSearchProvider] = useState('duckduckgo')
  const [searxngUrl, setSearxngUrl] = useState('http://127.0.0.1:18080')
  const [tavilyApiKey, setTavilyApiKey] = useState('')
  const [fetchProvider, setFetchProvider] = useState('trafilatura')
  const [targetLanguage, setTargetLanguage] = useState('zh-CN')
  const [loading, setLoading] = useState(true)

  const accent = useStore(s => s.accent)
  const setAccent = useStore(s => s.setAccent)
  const texture = useStore(s => s.texture)
  const setTexture = useStore(s => s.setTexture)
  const font = useStore(s => s.font)
  const setFont = useStore(s => s.setFont)
  const base = useStore(s => s.base)
  const setBase = useStore(s => s.setBase)
  const reading = useStore(s => s.reading)
  const setReading = useStore(s => s.setReading)
  const skeleton = useStore(s => s.skeleton)
  const cardStyle = useStore(s => s.cardStyle)
  const cardBg = useStore(s => s.cardBg)
  const cardLift = useStore(s => s.cardLift)
  const cardBorder = useStore(s => s.cardBorder)
  const setCard = useStore(s => s.setCard)
  // 卡片微调的两个派生量 —— 都不是 hook，放在这里只是紧挨着相关取值。
  // `liftDisabled` 读的是**运行时** `--card-bg-base`（`outline` 方案本身就是透明底，
  // 「跟随方案」时前端并不知道），而 `setCard` 已同步 `applyCard` 写过 DOM，
  // 故本次渲染读到的就是最新状态。
  const cardTuned = cardBg !== null || cardLift !== null || cardBorder !== null
  const liftDisabled = isLiftDisabled({ bg: cardBg })
  const markerDecor = useStore(s => s.markerDecor)
  const setMarker = useStore(s => s.setMarker)
  const layout = useStore(s => s.layout)
  const setLayout = useStore(s => s.setLayout)
  const exportPrefs = useStore(s => s.exportPrefs)
  const setExportPrefs = useStore(s => s.setExportPrefs)
  const applyPreset = useStore(s => s.applyPreset)
  const [intensity, setIntensity] = useState(getSavedIntensity())

  useEffect(() => {
    getConfig().then(cfg => {
      if (cfg.fetch) {
        setProxyUrl(cfg.fetch.proxy || 'http://127.0.0.1:7890')
        setProxyEnabled(!!cfg.fetch.proxy)
      }
      if (cfg.auth) {
        setCookieConfigured({
          bilibili: !!(cfg.auth.bilibili && cfg.auth.bilibili !== '***'),
          youtube: !!(cfg.auth.youtube && cfg.auth.youtube !== '***'),
          douyin: !!(cfg.auth.douyin && cfg.auth.douyin !== '***'),
        })
        if (cfg.auth.bilibili) setBilibiliCookie(cfg.auth.bilibili)
        if (cfg.auth.youtube) setYoutubeCookie(cfg.auth.youtube)
        if (cfg.auth.douyin) setDouyinCookie(cfg.auth.douyin)
      }
      if (cfg.target_language) setTargetLanguage(cfg.target_language)
      if (cfg.agent_tools) {
        if (cfg.agent_tools.search) {
          setSearchProvider(cfg.agent_tools.search.provider || 'duckduckgo')
          setSearxngUrl(cfg.agent_tools.search.searxng_url || 'http://127.0.0.1:18080')
          setTavilyApiKey(cfg.agent_tools.search.tavily_api_key || '')
        }
        if (cfg.agent_tools.fetch) {
          setFetchProvider(cfg.agent_tools.fetch.provider || 'trafilatura')
        }
      }
    }).catch(err => console.error('加载配置失败:', err))
    .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    try {
      await updateConfig({
        target_language: targetLanguage,
        fetch: { proxy: proxyEnabled ? proxyUrl : '' },
        auth: { bilibili: bilibiliCookie, youtube: youtubeCookie, douyin: douyinCookie },
        agent_tools: {
          search: { provider: searchProvider, searxng_url: searxngUrl, tavily_api_key: tavilyApiKey },
          fetch: { provider: fetchProvider },
        },
      })
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
      setCookieConfigured({ bilibili: !!bilibiliCookie, youtube: !!youtubeCookie, douyin: !!douyinCookie })
    } catch (e: unknown) {
      console.error('Save failed:', getErrorMessage(e))
      useStore.getState().addToast(`保存失败: ${getErrorMessage(e)}`, 'error')
    }
  }

  const COOKIE_PLATFORMS = [
    {
      key: 'bilibili', label: 'B站', configured: cookieConfigured.bilibili,
      value: bilibiliCookie, setValue: setBilibiliCookie,
      hint: '浏览器 F12 → Application → Cookies → bilibili.com → 全选复制',
      placeholder: 'buvid3=xxx; bili_jct=xxx; DedeUserID=xxx; SESSDATA=xxx',
    },
    {
      key: 'youtube', label: 'YouTube', configured: cookieConfigured.youtube,
      value: youtubeCookie, setValue: setYoutubeCookie,
      hint: '公开视频不需要。仅年龄限制/会员视频需要登录。',
      placeholder: 'SAPISID=xxx; HSID=xxx; LOGIN_INFO=xxx',
    },
  ]
  // 抖音 API 调通后取消注释:
  // ,{ key: 'douyin', label: '抖音', configured: cookieConfigured.douyin, value: douyinCookie, setValue: setDouyinCookie, hint: '浏览器 F12 → Application → Cookies → douyin.com → 全选复制', placeholder: 's_v_web_id=xxx; fpk1=xxx; fpk2=xxx; UIFID_TEMP=xxx' }


  return (
    <div className="flex-1 flex overflow-hidden bg-surface">
      {/* ── 左侧菜单栏 — 对标 BiliNote SettingLayout sidebar ── */}
      <aside className="flex w-[260px] flex-col border-r border-border-light bg-elevated shrink-0">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-border-light">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center text-on-accent font-bold text-xs">D</div>
            <span className="text-lg font-bold text-foreground">设置</span>
          </div>
          <button
            onClick={onBack}
            className="p-1.5 rounded-md hover:bg-surface-hover text-muted hover:text-dimmed transition-colors"
            title="返回首页"
          >
            <ArrowLeft size={16} />
          </button>
        </div>

        {/* 菜单项 — 对标 BiliNote Menu + MenuBar */}
        <nav className="flex-1 overflow-y-auto p-3 space-y-1">
          {MENU.map(item => {
            const isActive = tab === item.key
            return (
              <button
                key={item.key}
                onClick={() => setTab(item.key)}
                className={`flex w-full items-center gap-3 h-11 px-3 rounded-lg text-[15px] transition-colors
                  ${isActive
                    ? 'bg-surface-hover font-semibold text-accent'
                    : 'text-dimmed hover:bg-surface-hover'
                  }`}
              >
                <item.icon size={18} className={isActive ? 'text-accent' : 'text-muted'} />
                <span>{item.label}</span>
              </button>
            )
          })}
        </nav>
      </aside>

      {/* ── 右侧内容区 — 对标 BiliNote SettingLayout main + Outlet ── */}
      <main className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="flex items-center justify-center h-full py-20">
            <div className="flex flex-col items-center gap-3 text-disabled">
              <Loader2 size={28} className="animate-spin" />
              <span className="text-[13px]">加载设置...</span>
            </div>
          </div>
        ) : (
        <div className="max-w-3xl mx-auto px-8 py-10">
          {/* 标题 + 保存按钮 */}
          <div className="flex items-center justify-between mb-8">
            <div>
              <h1 className="text-[28px] font-semibold">
                {MENU.find(m => m.key === tab)?.label || '设置'}
              </h1>
              <p className="text-[13px] text-muted mt-1">
                {tab === 'model' && '管理 LLM 厂商、引擎厂商及模型用途分配'}
                {tab === 'plugins' && '管理第三方插件：启停 / 卸载 / 配置'}
                {tab === 'auth' && '配置各平台的认证 Cookie'}
                {tab === 'proxy' && '网络代理与访问控制'}
                {tab === 'agent' && '配置 AI 对话中网络搜索和网页抓取的方案'}
                {tab === 'appearance' && '主题模式与主色调配色方案'}
              </p>
            </div>
            <button onClick={handleSave}
              className="flex items-center gap-1.5 px-5 py-2.5 bg-accent text-on-accent text-[13px] font-medium rounded-full hover:bg-accent-hover transition-all">
              {saved ? <><CheckCircle size={14} /> 已保存</> : '保存设置'}
            </button>
          </div>

          {/* 模型 & 引擎 */}
          {tab === 'model' && <ProviderSettings />}

          {/* 插件 */}
          {tab === 'plugins' && <PluginSettings />}

          {/* 平台认证 */}
          {tab === 'auth' && (
            <div className="space-y-4">
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4">
                <div className="flex items-start gap-2 px-3 py-2 rounded-[10px] bg-accent/5 text-[12px] text-dimmed">
                  <Info size={14} className="shrink-0 mt-0.5 text-accent" />
                  <span>公开内容无需配置。需要登录时粘贴任意格式的 Cookie，系统自动识别并提取关键字段。每个平台独立保存，互不覆盖。</span>
                </div>
                {COOKIE_PLATFORMS.map(plat => (
                    <div key={plat.key}>
                      <div className="flex items-center gap-2 mb-1.5">
                        <label className="text-[15px] font-medium">{plat.label}</label>
                        {plat.configured ? (
                          <span className="flex items-center gap-1 text-[11px] text-success"><CheckCircle size={11} />已配置</span>
                        ) : (
                          <span className="flex items-center gap-1 text-[11px] text-dimmed"><Circle size={11} />未配置</span>
                        )}
                      </div>
                      <p className="text-[12px] text-muted mb-2">{plat.hint}</p>
                      <textarea rows={2} value={plat.value} onChange={e => plat.setValue(e.target.value)}
                        placeholder={plat.placeholder}
                        className={`w-full px-3 py-2 bg-surface border rounded-[10px] text-[13px] font-mono outline-none resize-none ${
                          plat.configured ? 'border-success/30' : 'border-border-default'
                        }`} />
                      {plat.value.includes('***') && (
                        <p className="mt-1 text-[11px] text-dimmed">已脱敏显示，重新粘贴后保存即可更新</p>
                      )}
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* 代理 */}
          {tab === 'proxy' && (
            <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4">
              <p className="text-[13px] text-muted">访问 YouTube / GitHub 等需要代理。作用于 LLM API + 视频下载。</p>
              <label className="flex items-center gap-3 cursor-pointer">
                <input type="checkbox" checked={proxyEnabled} onChange={e => setProxyEnabled(e.target.checked)} className="w-4 h-4 accent-accent" />
                <span className="text-[15px]">启用代理</span>
              </label>
              {proxyEnabled && (
                <input type="text" value={proxyUrl} onChange={e => setProxyUrl(e.target.value)}
                  placeholder="http://127.0.0.1:7890" className="w-full px-3 py-2.5 bg-surface border border-border-default rounded-[10px] text-[15px] outline-none" />
              )}
            </div>
          )}

          {/* 通用 */}
          {/* 外观 */}
          {tab === 'appearance' && (
            <div className="space-y-6">
              {/* 界面基调 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-5">
                <div>
                  <label className="text-[15px] font-medium mb-1 block">界面基调</label>
                  <p className="text-[12px] text-muted mb-4">
                    控制全局底色和边框颜色，影响整个应用界面
                  </p>
                  <div className="grid grid-cols-3 gap-3">
                    {BASE_OPTIONS.map(b => {
                      const selected = base === b.key
                      return (
                        <button
                          key={b.key}
                          onClick={() => setBase(b.key)}
                          className={`flex flex-col items-center gap-1.5 p-2 rounded-xl transition-all ${
                            selected
                              ? 'ring-2 ring-accent ring-offset-2 ring-offset-surface'
                              : 'hover:bg-surface-hover'
                          }`}
                        >
                          {/* 色卡 = `data-base` 自驱动 + 消费 `var()`（2026-10-16 档 A）。
                              ⚠️ 内联只写 `backgroundColor`（长名）—— 写 shorthand `background`
                              会把 CSS 规则层可能给的 `background-image` 一并重置掉（内联层赢）。
                              颜色只有一份真相源：`styles/theme-layer.css` 的 `[data-base]` 规则。 */}
                          <div
                            data-base={b.key}
                            className="w-full h-10 rounded-lg border border-border-light"
                            style={{ backgroundColor: 'var(--tk-surface)' }}
                          />
                          <span className={`text-[12px] ${
                            selected ? 'text-accent font-medium' : 'text-muted'
                          }`}>{b.label}</span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>

              {/* 主色调 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-5">
                <div>
                  <label className="text-[15px] font-medium mb-1 block">主色调</label>
                  <p className="text-[12px] text-muted mb-5">
                    更改应用的整体配色，影响按钮、链接和 Markdown 内容的颜色
                  </p>
                  {ACCENT_GROUPS.map(group => (
                    <div key={group.label} className="mb-5 last:mb-0">
                      <h4 className="text-[11px] text-dimmed font-medium mb-3 uppercase tracking-wide">
                        {group.label}
                      </h4>
                      <div className="grid grid-cols-6 gap-3">
                        {group.accents.map(a => {
                          const selected = accent === a.key
                          return (
                            <button
                              key={a.key}
                              onClick={() => setAccent(a.key)}
                              title={a.label}
                              className="flex flex-col items-center gap-1.5 group"
                            >
                              {/* ⚠️ `data-accent` 挂在这个 div 上，不是装饰 —— 它是
                                  **色卡能显示自己那套配色的唯一原因**：`--tk-accent`
                                  是 `var()` 派生出来的，而**自定义属性继承的是计算值**，
                                  子元素重设原语不能回溯改变 `<html>` 上算好的那个
                                  （详见 theme-layer.css 的 Layer 0b）。去掉它，
                                  26 个色块会全部显示成应用当前配色。 */}
                              <div
                                data-accent={a.key}
                                className={`w-10 h-10 rounded-full transition-all ${
                                  selected
                                    ? 'ring-2 ring-offset-2 ring-accent ring-offset-surface scale-110'
                                    : 'hover:scale-105'
                                }`}
                                style={{
                                  backgroundImage: 'conic-gradient(var(--tk-accent) 0deg 180deg, var(--tk-secondary) 180deg 360deg)',
                                }}
                              />
                              <span className={`text-[11px] ${
                                selected ? 'text-accent font-medium' : 'text-muted group-hover:text-dimmed'
                              }`}>{a.label}</span>
                            </button>
                          )
                        })}
                      </div>
                    </div>
                  ))}
                </div>

                {/* 背景纹理 */}
                <div>
                  <label className="text-[15px] font-medium mb-1 block">背景纹理</label>
                  <p className="text-[12px] text-muted mb-5">
                    为 Markdown 预览区添加纸张质感纹理，不影响 UI 组件
                  </p>
                  <div className="grid grid-cols-3 gap-3">
                    {TEXTURE_OPTIONS.map(t => {
                      const selected = texture === t.key
                      return (
                        <button
                          key={t.key}
                          onClick={() => setTexture(t.key)}
                          className={`flex flex-col items-center gap-1.5 p-2 rounded-xl transition-all ${
                            selected
                              ? 'ring-2 ring-accent ring-offset-2 ring-offset-surface'
                              : 'hover:bg-surface-hover'
                          }`}
                        >
                          {/* 显示**真实纹理**（用户 2026-10-16 定）——底色 + 图案层，
                              不再是一块纯色。`tex-card` 类是必须的：图案与 blend-mode
                              由 `lib/texture.css` 的 `.tex-card[data-texture]:not(plain)`
                              规则提供，没有它只剩底色。
                              ⚠️ `plain` 档**刻意不在 theme-layer 里声明变量**，故这里给
                              `var(--tk-surface)` 兜底 ⇒ 显示当前基调底色、无图案
                              （不给兜底就是一片透明，看像是坏了）。 */}
                          <div
                            data-texture={t.key}
                            className="tex-card w-full h-12 rounded-lg border border-border-light"
                            style={{ backgroundColor: 'var(--texture-bg-color, var(--tk-surface))' }}
                          />
                          <span className={`text-[12px] ${
                            selected ? 'text-accent font-medium' : 'text-muted'
                          }`}>{t.label}</span>
                        </button>
                      )
                    })}
                  </div>
                  {/* 纹理强度 */}
                  {texture !== 'plain' && (
                    <div className="mt-5 pt-4 border-t border-border-light">
                      <div className="flex items-center justify-between mb-3">
                        <span className="text-[13px] font-medium">纹理强度</span>
                        <span className="text-[12px] text-accent font-medium bg-accent/10 px-2 py-0.5 rounded-full">{intensity}%</span>
                      </div>
                      <div className="flex items-stretch gap-3">
                        <div className="flex-1 flex flex-col justify-center">
                          <div className="flex items-center gap-2">
                            <span className="text-[11px] text-disabled shrink-0">淡</span>
                            <input
                              type="range" min="10" max="100" value={intensity}
                              onChange={e => {
                                const v = parseInt(e.target.value)
                                setIntensity(v)
                                saveIntensity(v)
                                applyIntensity(v)
                              }}
                              className="w-full h-2 rounded-full appearance-none cursor-pointer
                                [&::-webkit-slider-thumb]:w-5 [&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:rounded-full
                                [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:shadow-md
                                [&::-webkit-slider-thumb]:appearance-none
                                bg-surface-hover"
                            />
                            <span className="text-[11px] text-disabled shrink-0">浓</span>
                          </div>
                        </div>
                        {/* 预览卡片 */}
                        <div className="preview-area shrink-0 w-20 h-14 rounded-lg border border-border-light flex items-center justify-center overflow-hidden">
                          <span className="text-[11px] text-muted select-none">Aa</span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* 阅读字体 */}
                <div>
                  <label className="text-[15px] font-medium mb-1 block">阅读字体</label>
                  <p className="text-[12px] text-muted mb-4">
                    选择 Markdown 预览区的正文字体。web 字体会在首次使用时加载
                  </p>
                  <div className="space-y-1">
                    {FONT_OPTIONS.map(f => {
                      const selected = font === f.key
                      return (
                        <button
                          key={f.key}
                          onClick={() => setFont(f.key)}
                          className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-[14px] transition-colors ${
                            selected
                              ? 'bg-accent/10 text-accent font-medium'
                              : 'text-dimmed hover:bg-surface-hover'
                          }`}
                        >
                          <span>{f.label}</span>
                          <span className="text-[11px] text-disabled">
                            {f.category === 'web' ? '需加载' : '系统内置'}
                          </span>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>

              {/* 侧栏布局 —— 左侧面板是互斥切换还是允许并排。
                  ⚠️ 与「阅读版式」的「默认即现状」**相反**：这里默认互斥是**有意的默认
                  变更**（2026-10-10 用户拍板），所以必须给一条随时改回去的路，而不是
                  让不习惯的人只能去改 localStorage。 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6">
                <label className="text-[15px] font-medium mb-1 block">侧栏布局</label>
                <p className="text-[12px] text-muted mb-4">
                  左侧面板（生成 / 文档库）的展开方式。入口是最左边的活动栏图标
                </p>
                <div className="space-y-1">
                  {LAYOUT_OPTIONS.map(o => {
                    const selected = layout.leftPanelsExclusive === o.key
                    return (
                      <button
                        key={String(o.key)}
                        onClick={() => setLayout({ leftPanelsExclusive: o.key })}
                        className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors ${selected ? 'bg-accent/10' : 'hover:bg-surface-hover'}`}
                      >
                        <div className={`text-[14px] ${selected ? 'text-accent font-medium' : 'text-dimmed'}`}>{o.label}</div>
                        <div className="text-[11px] text-disabled mt-0.5">{o.hint}</div>
                      </button>
                    )
                  })}
                </div>
              </div>

              {/* 阅读版式 — 沉浸阅读的「纸感」实质（行高 / 每行字数 / 段落区隔）。
                  ⚠️ 刻意做成独立卡片、且**不进「推荐搭配」主题包**：版式关乎阅读生理，
                  不该被「换一个主题包」连带重置。默认值全部等于改造前的现状，
                  所以「不动它」= 零变化（见 lib/reading-prefs.ts 的注释）。 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-5">
                <div>
                  <label className="text-[15px] font-medium mb-1 block">阅读版式</label>
                  <p className="text-[12px] text-muted mb-4">
                    调整 Markdown 预览区的正文排版。默认与改造前一致，按需微调即可
                  </p>

                  {/* 段落区隔 —— clreq：首行缩进与加大段距是两套并行传统，不建议混用，
                      故是二选一的单选，而不是两个可自由组合的旋钮 */}
                  <div className="mb-5">
                    <div className="text-[13px] font-medium mb-2">段落区隔</div>
                    <div className="space-y-1">
                      {PARA_STYLE_OPTIONS.map(o => {
                        const selected = reading.paraStyle === o.key
                        return (
                          <button
                            key={o.key}
                            onClick={() => setReading({ paraStyle: o.key })}
                            className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors ${selected ? 'bg-accent/10' : 'hover:bg-surface-hover'}`}
                          >
                            <div className={`text-[14px] ${selected ? 'text-accent font-medium' : 'text-dimmed'}`}>{o.label}</div>
                            <div className="text-[11px] text-disabled mt-0.5">{o.hint}</div>
                          </button>
                        )
                      })}
                    </div>
                  </div>

                  {/* 行高 */}
                  <div className="mb-5">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[13px] font-medium">行高</span>
                      <span className="text-[12px] text-accent font-medium bg-accent/10 px-2 py-0.5 rounded-full">{reading.lineHeight.toFixed(2)}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-disabled shrink-0">紧凑</span>
                      <input
                        type="range"
                        min={LINE_HEIGHT_RANGE.min} max={LINE_HEIGHT_RANGE.max} step={LINE_HEIGHT_RANGE.step}
                        value={reading.lineHeight}
                        onChange={e => setReading({ lineHeight: parseFloat(e.target.value) })}
                        className="w-full h-2 rounded-full appearance-none cursor-pointer
                          [&::-webkit-slider-thumb]:w-5 [&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:rounded-full
                          [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:shadow-md
                          [&::-webkit-slider-thumb]:appearance-none
                          bg-surface-hover"
                      />
                      <span className="text-[11px] text-disabled shrink-0">宽松</span>
                    </div>
                  </div>

                  {/* 每行字数 —— 勾选框 + 条件滑条（不用魔法值表达「不限」，
                      免得 0/负数在两处来回转换） */}
                  <div className="mb-5">
                    <label className="flex items-center gap-2 cursor-pointer select-none">
                      <input
                        type="checkbox"
                        checked={reading.measure !== null}
                        onChange={e => setReading({ measure: e.target.checked ? MEASURE_DEFAULT_ON : null })}
                        className="accent-accent w-4 h-4 cursor-pointer"
                      />
                      <span className="text-[13px] font-medium">限制每行字数</span>
                      <span className="text-[12px] text-muted">
                        {reading.measure === null ? '不限，跟随面板宽度' : `约 ${reading.measure} 字／行`}
                      </span>
                    </label>
                    {reading.measure !== null && (
                      <div className="flex items-center gap-2 mt-3">
                        <span className="text-[11px] text-disabled shrink-0">{MEASURE_RANGE.min}</span>
                        <input
                          type="range"
                          min={MEASURE_RANGE.min} max={MEASURE_RANGE.max} step={MEASURE_RANGE.step}
                          value={reading.measure}
                          onChange={e => setReading({ measure: parseInt(e.target.value) })}
                          className="w-full h-2 rounded-full appearance-none cursor-pointer
                            [&::-webkit-slider-thumb]:w-5 [&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:rounded-full
                            [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:shadow-md
                            [&::-webkit-slider-thumb]:appearance-none
                            bg-surface-hover"
                        />
                        <span className="text-[11px] text-disabled shrink-0">{MEASURE_RANGE.max}</span>
                      </div>
                    )}
                    <p className="text-[11px] text-muted mt-2">
                      中文排版建议每行 30-40 字（约 550-640px）。行宽过宽时视线容易跳行
                    </p>
                  </div>

                  {/* 正文字号 —— ⚠️ 这一项补的是一个**真实缺口**而不是多给一个旋钮：
                      `.markdown-body` 的字号被 vendor（github-markdown-light.css）硬写成
                      绝对 16px，而本区此前的规则只覆盖了行高与段距，所以正文大小一直调不动。
                      标题 / 代码 / 表格 / 引用都是相对 em，跟着一起等比缩放。 */}
                  <div>
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-[13px] font-medium">正文字号</span>
                      <span className="text-[12px] text-accent font-medium bg-accent/10 px-2 py-0.5 rounded-full">
                        {Math.round(reading.fontScale * 100)}%
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] text-disabled shrink-0">小</span>
                      <input
                        type="range"
                        min={FONT_SCALE_RANGE.min} max={FONT_SCALE_RANGE.max} step={FONT_SCALE_RANGE.step}
                        value={reading.fontScale}
                        // 归一到两位小数：range 的 value 是字符串，浮点步进可能吐出
                        // 1.2000000000000002 这类值，与 nextFontScale 的输出对不上
                        onChange={e => setReading({ fontScale: Math.round(parseFloat(e.target.value) * 100) / 100 })}
                        className="w-full h-2 rounded-full appearance-none cursor-pointer
                          [&::-webkit-slider-thumb]:w-5 [&::-webkit-slider-thumb]:h-5 [&::-webkit-slider-thumb]:rounded-full
                          [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:shadow-md
                          [&::-webkit-slider-thumb]:appearance-none
                          bg-surface-hover"
                      />
                      <span className="text-[11px] text-disabled shrink-0">大</span>
                    </div>
                    <div className="flex items-start justify-between gap-3 mt-2">
                      <p className="text-[11px] text-muted">
                        只作用于预览区正文。快捷键 Ctrl + 滚轮 / Ctrl + + / Ctrl + −
                      </p>
                      <button
                        onClick={() => setReading({ fontScale: READING_DEFAULTS.fontScale })}
                        disabled={reading.fontScale === READING_DEFAULTS.fontScale}
                        className="text-[11px] text-accent hover:underline shrink-0 disabled:text-disabled disabled:no-underline disabled:cursor-default"
                      >
                        重置为 100%
                      </button>
                    </div>
                  </div>
                </div>
              </div>

              {/* 产物骨架 + 卡片视觉方案 —— 与「阅读版式」同族的独立卡片，
                  **刻意不进「推荐搭配」主题包**（换主题包不该把产物骨架连带重置）。
                  默认 skeleton='paper' = 改造前逐像素一致（见 lib/card-prefs.ts）。 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-5">
                <div>
                  <label className="text-[15px] font-medium mb-1 block">产物骨架</label>
                  <p className="text-[12px] text-muted mb-4">
                    笔记、整理版原文的排版方式。默认「整篇」与改造前完全一致
                  </p>
                  <div className="space-y-1">
                    {SKELETON_OPTIONS.map(o => {
                      const selected = skeleton === o.key
                      return (
                        <button
                          key={o.key}
                          onClick={() => setCard({ skeleton: o.key })}
                          className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors ${selected ? 'bg-accent/10' : 'hover:bg-surface-hover'}`}
                        >
                          <div className={`text-[14px] ${selected ? 'text-accent font-medium' : 'text-dimmed'}`}>{o.label}</div>
                          <div className="text-[11px] text-disabled mt-0.5">{o.hint}</div>
                        </button>
                      )
                    })}
                  </div>
                </div>

                {/* ⚠️ 骨架 = 整篇时**整块隐藏**（而不是灰置）—— 没有卡片可画，
                    留着一个「调了没反应」的旋钮比少一个控件更让人困惑（模式 #53）。 */}
                {skeleton === 'card' && (
                  <div>
                    <div className="text-[13px] font-medium mb-2">卡片视觉方案</div>
                    <div className="grid grid-cols-2 gap-1.5">
                      {CARD_STYLE_OPTIONS.map(o => {
                        const selected = cardStyle === o.key
                        return (
                          <button
                            key={o.key}
                            // ⚠️ **切方案时一并清空微调**（原型样板 `setVars` 的第一步同款）。
                            // 不清的表现是「调完圆角再切预设，微调看着还在、实际被新方案压住」——
                            // 用户以为改了方案、其实还带着上一次的覆盖，零报错。语义上微调是
                            // 「在方案之上的一层覆盖」，换地基就该重新决定要不要覆盖。
                            onClick={() => setCard({ style: o.key, bg: null, lift: null, border: null })}
                            title={o.hint}
                            className={`text-left px-3 py-2 rounded-lg transition-colors border ${selected ? 'bg-accent/10 border-accent/30' : 'border-transparent hover:bg-surface-hover'}`}
                          >
                            {/* 迷你小样 —— 让 8 套方案的差异**看得见**，而不是只有名字。
                                ⚠️ `data-card-style` 写在小样元素自己身上：`cards.css` 的
                                方案块（`[data-card-style="x"] { --card-*: … }`）**不带骨架前缀**，
                                故这里直接就能取到那批变量；小样元素用它们画一个缩微卡片
                                （`.card-preview`，与 `.md-card` 消费同一批变量）。 */}
                            <span className="flex items-center gap-2.5">
                              <span aria-hidden data-card-style={o.key} className="card-preview shrink-0" />
                              <span className={`text-[13px] ${selected ? 'text-accent font-medium' : 'text-dimmed'}`}>{o.label}</span>
                            </span>
                            <span className="text-[10.5px] text-disabled mt-1.5 block leading-snug">{o.hint}</span>
                          </button>
                        )
                      })}
                    </div>
                    <p className="text-[11px] text-muted mt-3">
                      只改变卡片的观感与分节方式。Markdown 本身不变，HTML / Word / PDF
                      导出会跟着一起变；切回「整篇」即恢复原样
                    </p>

                    {/* ── 逐维度微调 ────────────────────────────────────────────
                        8 套方案**覆盖不到全部组合**：最典型的是「抬升」——8 套里只有
                        0% 与 100% 两个取值，中间一整段一格都没有。微调是同一个 CSS
                        变量的第二个入口（`lib/card-prefs.ts`），不是第二套机制。
                        ⚠️ 「跟随方案」是每组的**第一个选项**（默认选中）：`null` 表示
                        不写 inline 变量、原样落回方案取值 —— 于是新增旋钮**不改变任何
                        既有用户的默认观感**（零回归）。 */}
                    <div className="mt-5 pt-5 border-t border-border-light space-y-3">
                      <div className="flex items-center justify-between">
                        <div className="text-[13px] font-medium">逐维度微调</div>
                        <button
                          onClick={() => setCard({ bg: null, lift: null, border: null })}
                          disabled={!cardTuned}
                          className="text-[11px] text-accent hover:underline shrink-0 disabled:text-disabled disabled:no-underline disabled:cursor-default"
                        >
                          全部跟随方案
                        </button>
                      </div>
                      <p className="text-[11px] text-muted">
                        不定制就跟随上面的方案；调过的项覆盖方案取值，切换方案时自动清空
                      </p>

                      <div className="flex items-start gap-3" data-card-tune="bg">
                        <div className="text-[12px] text-dimmed w-8 shrink-0 pt-1">底</div>
                        <div className="flex flex-wrap gap-1.5">
                          <TuneChip selected={cardBg === null} onClick={() => setCard({ bg: null })}>跟随</TuneChip>
                          {CARD_BG_OPTIONS.map(o => (
                            <TuneChip key={o.key} selected={cardBg === o.key} onClick={() => setCard({ bg: o.key })}>
                              {o.label}
                            </TuneChip>
                          ))}
                        </div>
                      </div>

                      <div className="flex items-center gap-3" data-card-tune="lift">
                        <div className="text-[12px] text-dimmed w-8 shrink-0">抬升</div>
                        <input
                          type="range" min={0} max={100} step={5}
                          value={cardLift ?? 0}
                          disabled={liftDisabled}
                          onChange={e => setCard({ lift: Number(e.target.value) })}
                          style={{ accentColor: 'var(--tk-accent)' }}
                          className="flex-1 disabled:opacity-35"
                        />
                        <span className="text-[11px] text-muted w-20 shrink-0 text-right">
                          {liftDisabled ? '透明底不适用' : cardLift === null ? '跟随方案' : `${cardLift}%`}
                        </span>
                        {cardLift !== null && !liftDisabled && (
                          <button
                            onClick={() => setCard({ lift: null })}
                            className="text-[11px] text-accent hover:underline shrink-0"
                          >
                            跟随
                          </button>
                        )}
                      </div>

                      <div className="flex items-start gap-3" data-card-tune="border">
                        <div className="text-[12px] text-dimmed w-8 shrink-0 pt-1">边</div>
                        <div className="flex flex-wrap gap-1.5">
                          <TuneChip selected={cardBorder === null} onClick={() => setCard({ border: null })}>跟随</TuneChip>
                          {CARD_BORDER_OPTIONS.map(o => (
                            <TuneChip key={o.key} selected={cardBorder === o.key} onClick={() => setCard({ border: o.key })}>
                              {o.label}
                            </TuneChip>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* 产物语义标记的装饰开关 —— 与「产物骨架」并列而**不塞进去**：
                  标记块与骨架轴正交（「整篇」下它也在正文流里），塞进骨架卡片会
                  暗示它只对卡片生效。默认开启 = 与改造前一致（那边根本没有块）。
                  ⚠️ 2026-10-11 起射程含**内容卡片族**（表格 / 代码 / 图片）—— 关掉后
                  它们还原成「没包过围栏」的排版，这正是用户要的「保留原来的形式」。 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4">
                <div>
                  <label className="text-[15px] font-medium mb-1 block">内容块装饰</label>
                  <p className="text-[12px] text-muted mb-4">
                    模型在笔记里标出的重点、提醒、指标等段落，以及对照表、代码、图片，
                    显示成带装饰的块
                  </p>
                  <label className="flex items-center gap-2.5 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={markerDecor}
                      onChange={e => setMarker({ decor: e.target.checked })}
                      className="w-4 h-4 accent-accent"
                    />
                    <span className="text-[13px] text-dimmed">显示内容块装饰</span>
                  </label>
                  <p className="text-[11px] text-muted mt-3">
                    关闭只是隐藏装饰 —— <span className="text-dimmed">内容一字不少</span>，
                    表格 / 代码 / 图片会还原成原本的排版。预览区工具栏也有同一个开关；
                    HTML / Word / PDF 导出会跟随此设置
                  </p>
                </div>
              </div>

              {/* 推荐搭配 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-5">
                <div>
                  <label className="text-[15px] font-medium mb-1 block">推荐搭配</label>
                  <p className="text-[12px] text-muted mb-4">
                    一键设定全局基调、配色、纹理和字体，也可单独微调
                  </p>
                  {(() => {
                    const renderCard = (p: typeof THEME_PRESETS[number]) => {
                      const fontLabel = FONT_OPTIONS.find(f => f.key === p.font)
                      const textureLabel = TEXTURE_OPTIONS.find(t => t.key === p.texture)
                      // 三轴属性挂在卡片自己身上（与设置页各色卡同款），色值一律 `var()` ——
                      // 这个文件里**一个色值都不许再写**（2026-10-16 档 A）。
                      return (
                        <button
                          key={p.name}
                          onClick={() => applyPreset(p.name)}
                          data-texture={p.texture}
                          data-base={p.base}
                          data-accent={p.accent}
                          data-font={p.font}
                          className="tex-card flex items-center gap-4 p-4 rounded-xl transition-all border border-border-light text-left overflow-hidden relative hover:brightness-[0.97]"
                        >
                          <div className="shrink-0 relative z-10">
                            <div
                              className="w-12 h-12 rounded-full border-2 border-border-light"
                              style={{
                                backgroundImage: 'conic-gradient(var(--tk-accent) 0deg 180deg, var(--tk-surface) 180deg 360deg)',
                              }}
                            />
                          </div>
                          <div className="min-w-0 relative z-10">
                            <span className="text-[15px] font-medium block" style={{ fontFamily: 'var(--font-reading, inherit)' }}>{p.label}</span>
                            <span className="text-[11px] text-dimmed block leading-relaxed">
                              {fontLabel?.label || p.font} · {textureLabel?.label || p.texture}
                            </span>
                          </div>
                        </button>
                      )
                    }
                    const groups = [
                      { label: '护眼', presets: ['roulv', 'nuanyang', 'zhenzhu', 'bohe'] },
                      { label: '经典', presets: ['shuxiang', 'jianjing', 'fugu', 'qingzhu'] },
                      { label: '粉色', presets: ['yingchui', 'taoyao', 'dingxiang', 'mudan'] },
                      { label: '雅致', presets: ['songyan', 'tanxiang', 'yunxiu', 'shazhou'] },
                      { label: '国风', presets: ['zhusha', 'dianlan', 'mailang', 'meigui'] },
                    ]
                    return groups.map(g => (
                      <div key={g.label}>
                        <span className="text-[11px] font-medium text-disabled uppercase tracking-wide block mb-2">{g.label}</span>
                        <div className="grid grid-cols-2 gap-3">
                          {g.presets.map(name => renderCard(THEME_PRESETS.find(p => p.name === name)!))}
                        </div>
                      </div>
                    ))
                  })()}
                </div>
              </div>
            </div>
          )}

          {tab === 'general' && (
            <div className="space-y-4">
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4">
                <p className="text-[13px] text-muted">翻译目标语言及界面显示语言。</p>
                <div>
                  <label className="text-[15px] font-medium mb-2 block">翻译目标语言</label>
                  <p className="text-[12px] text-muted mb-2">生成笔记时，「翻译」选项将内容翻译为此语言。</p>
                  <select value={targetLanguage} onChange={e => setTargetLanguage(e.target.value)}
                    className="w-full px-3 py-2.5 bg-surface border border-border-default rounded-[10px] text-[14px] outline-none">
                    {LANG_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
                  </select>
                </div>
              </div>

              {/* 导出行为 —— 即时生效（与上面需要点「保存」的配置项不同：这一项存的是本机偏好，
                  不走 config.yaml）。见 lib/export-prefs.ts 的文件头。 */}
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-4">
                <p className="text-[13px] text-muted">导出行为（改动立即生效）。</p>
                <label className="flex items-start gap-3 cursor-pointer">
                  <input type="checkbox" checked={exportPrefs.embedImages}
                    onChange={e => setExportPrefs({ embedImages: e.target.checked })}
                    className="mt-1 w-4 h-4 accent-accent shrink-0" />
                  <span>
                    <span className="text-[15px] font-medium block">Markdown 导出时把图片一起带走</span>
                    <span className="text-[12px] text-muted block mt-1 leading-relaxed">
                      开启后，预览区的「下载 MD」会把图片以 base64 直接写进文件，单个 .md 就能离线查看、转发给别人也正常显示
                      —— 代价是这一步要依赖本地后端（正常使用中它一直开着，感知不到）。
                      关闭则只保留图片链接：文件更小、不依赖后端，但离开本应用后图片会显示不出来。
                      本项只影响「下载 MD」，不影响「复制 Markdown」和右侧详细导出面板（那里逐次自选图片处理方式）。
                    </span>
                  </span>
                </label>
              </div>
            </div>
          )}

          {/* Agent 工具 */}

          {tab === 'agent' && (
            <div className="space-y-4">
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-5">
                <p className="text-[13px] text-muted">
                  搜索网络用于 AI 对话和生成笔记时的联网检索，网页抓取仅用于 AI 对话。DuckDuckGo 免费零配置，SearXNG 需本地部署，Tavily 需 API Key 但质量最高。
                </p>

                {/* 搜索方案 */}
                <div>
                  <label className="text-[15px] font-medium mb-2 block">网络搜索</label>
                  <select value={searchProvider} onChange={e => setSearchProvider(e.target.value)}
                    className="w-full px-3 py-2.5 bg-surface border border-border-default rounded-[10px] text-[14px] outline-none">
                    <option value="tavily">Tavily（AI 优化搜索，需 API Key）</option>
                    <option value="searxng">SearXNG（本地免费，自动启动便携版）</option>
                    <option value="duckduckgo">DuckDuckGo（免费零配置，真实网页搜索）</option>
                  </select>
                  {searchProvider === 'tavily' && (
                    <input type="password" value={tavilyApiKey} onChange={e => setTavilyApiKey(e.target.value)}
                      placeholder="tvly-xxxxxxxxxxxxxxxxxxxxxxxx"
                      className="w-full mt-2 px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[14px] outline-none font-mono" />
                  )}
                  {searchProvider === 'searxng' && (
                    <input type="text" value={searxngUrl} onChange={e => setSearxngUrl(e.target.value)}
                      placeholder="http://127.0.0.1:18080"
                      className="w-full mt-2 px-3 py-2 bg-surface border border-border-default rounded-[10px] text-[14px] outline-none font-mono" />
                  )}
                </div>

                {/* 抓取方案 */}
                <div>
                  <label className="text-[15px] font-medium mb-2 block">网页抓取</label>
                  <select value={fetchProvider} onChange={e => setFetchProvider(e.target.value)}
                    className="w-full px-3 py-2.5 bg-surface border border-border-default rounded-[10px] text-[14px] outline-none">
                    <option value="trafilatura">trafilatura（本地提取，需代理）</option>
                    <option value="jina">Jina Reader（免费在线服务）</option>
                  </select>
                </div>
              </div>
            </div>
          )}
        </div>
        )}
      </main>
    </div>
  )
}
