import { useState, useEffect } from 'react'
import { getConfig, updateConfig, getErrorMessage } from '../../api/client'
import { useStore } from '../../store'
import { ACCENT_GROUPS, ACCENT_SWATCH } from '../../lib/accent'
import { ArrowLeft, CheckCircle, Circle, Info, Cpu, Key, Globe, Settings, Wrench, Loader2, Palette } from 'lucide-react'
import { ProviderSettings } from './ProviderSettings'

/** BiliNote 式设置布局 — 左侧菜单导航 + 右侧内容区 */

const MENU = [
  { key: 'model', label: '模型 & 引擎', icon: Cpu },
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
  const [verifyMode, setVerifyMode] = useState('llm')
  const [targetLanguage, setTargetLanguage] = useState('zh-CN')
  const [loading, setLoading] = useState(true)

  const accent = useStore(s => s.accent)
  const setAccent = useStore(s => s.setAccent)

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
        if (cfg.agent_tools.verify_mode) {
          setVerifyMode(cfg.agent_tools.verify_mode)
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
          verify_mode: verifyMode,
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
            <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center text-white font-bold text-xs">D</div>
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
                {tab === 'auth' && '配置各平台的认证 Cookie'}
                {tab === 'proxy' && '网络代理与访问控制'}
                {tab === 'agent' && '配置 AI 对话中网络搜索和网页抓取的方案'}
                {tab === 'appearance' && '主题模式与主色调配色方案'}
              </p>
            </div>
            <button onClick={handleSave}
              className="flex items-center gap-1.5 px-5 py-2.5 bg-accent text-white text-[13px] font-medium rounded-full hover:bg-accent-hover transition-all">
              {saved ? <><CheckCircle size={14} /> 已保存</> : '保存设置'}
            </button>
          </div>

          {/* 模型 & 引擎 */}
          {tab === 'model' && <ProviderSettings />}

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
                <input type="checkbox" checked={proxyEnabled} onChange={e => setProxyEnabled(e.target.checked)} className="w-4 h-4 accent-[#007AFF]" />
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
                          const sw = ACCENT_SWATCH[a.key]
                          const selected = accent === a.key
                          return (
                            <button
                              key={a.key}
                              onClick={() => setAccent(a.key)}
                              title={a.label}
                              className="flex flex-col items-center gap-1.5 group"
                            >
                              <div
                                className={`w-10 h-10 rounded-full transition-all ${
                                  selected
                                    ? 'ring-2 ring-offset-2 ring-accent ring-offset-surface scale-110'
                                    : 'hover:scale-105'
                                }`}
                                style={{
                                  background: `conic-gradient(${sw.primary} 0deg 180deg, ${sw.secondary} 180deg 360deg)`,
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
              </div>
            </div>
          )}

          {tab === 'general' && (
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
          )}

          {/* Agent 工具 */}

          {tab === 'agent' && (
            <div className="space-y-4">
              <div className="bg-elevated rounded-xl border border-border-light p-6 space-y-5">
                <p className="text-[13px] text-muted">
                  AI 对话和报告审核时使用以下方案搜索网络和抓取网页。DuckDuckGo 免费零配置，SearXNG 需本地部署，Tavily 需 API Key 但质量最高。
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

                {/* AI 审核验证方式 */}
                <div>
                  <label className="text-[15px] font-medium mb-2 block">AI 审核验证方式</label>
                  <select value={verifyMode} onChange={e => setVerifyMode(e.target.value)}
                    className="w-full px-3 py-2.5 bg-surface border border-border-default rounded-[10px] text-[14px] outline-none">
                    <option value="llm">基于模型知识（默认，快速，无需搜索引擎）</option>
                    <option value="web">联网搜索验证（更可靠但较慢，需配置搜索方案）</option>
                  </select>
                  <p className="text-[12px] text-disabled mt-1.5 leading-relaxed">
                    LLM 模式基于模型训练知识批量验证，几秒内完成。联网模式会搜索网页交叉验证，结果更可靠但耗时较长。
                    可在上述「网络搜索」中切换搜索引擎。
                  </p>
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
