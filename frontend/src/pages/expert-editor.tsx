import { useEffect, useMemo, useState } from 'react'
import { ArrowLeft, CircleCheck, CircleX, Play, Rocket, Save } from 'lucide-react'
import { Button } from '../components/ui/button'
import { Input } from '../components/ui/input'
import { Textarea } from '../components/ui/textarea'
import { Checkbox } from '../components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/ui/select'
import { Badge } from '../components/common'
import { CopyValue, OsStatusBadge } from '../components/expert-os'
import { ExpertChatDrawer } from '../components/expert-chat-drawer'
import { useApp, toast } from '../store/app-store'
import { isValidSlug, providerUsable, suggestSlug, useExpertOs, validateDraft, type ExpertCheckSection, type ExpertDraft } from '../store/expert-os-store'

type EditorFieldPick = Pick<ExpertDraft, 'name' | 'slug' | 'description' | 'systemPrompt' | 'providerId' | 'model' | 'skills' | 'knowledgeBaseIds'>

const emptyDraft = (): ExpertDraft => ({ name: '', slug: '', description: '', systemPrompt: '', providerId: '', model: '', skills: [], knowledgeBaseIds: [] })

const fieldsOf = (draft: ExpertDraft): EditorFieldPick => ({ name: draft.name, slug: draft.slug, description: draft.description, systemPrompt: draft.systemPrompt, providerId: draft.providerId, model: draft.model, skills: draft.skills, knowledgeBaseIds: draft.knowledgeBaseIds })

export function ExpertEditorPage() {
  const { expertEditorTarget, navigate } = useApp()
  const { state, createOrSaveExpert, createTestChatSession, markConfigTested, publishExpert } = useExpertOs()

  const [draft, setDraft] = useState<ExpertDraft>(emptyDraft)
  const [baseline, setBaseline] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [openingTest, setOpeningTest] = useState(false)
  const [testProviderModelId, setTestProviderModelId] = useState('')
  const [chatSession, setChatSession] = useState<{ id: string; title: string; providerModelId: string } | null>(null)

  // 由目标 id 一次性初始化草稿；之后完全以本地草稿为准，实时校验不再依赖已保存数据。
  // 编辑模式必须等 serverSynced：否则会拿 localStorage 镜像里的旧配置（模型绑定丢失）落库覆盖服务端
  useEffect(() => {
    if (expertEditorTarget && !state.serverSynced) return
    if (!expertEditorTarget) {
      setDraft(emptyDraft())
      setBaseline(JSON.stringify(fieldsOf(emptyDraft())))
      setSlugTouched(false)
      setChatSession(null)
      return
    }
    const expert = state.experts.find((item) => item.id === expertEditorTarget)
    const config = state.configs[expertEditorTarget]
    const loaded: ExpertDraft = {
      id: expertEditorTarget,
      name: expert?.name ?? '',
      slug: expert?.slug ?? '',
      description: expert?.description ?? '',
      systemPrompt: config?.systemPrompt ?? '',
      providerId: config?.providerId ?? '',
      model: config?.model ?? '',
      skills: expert?.skills ?? [],
      knowledgeBaseIds: config?.knowledgeBaseIds ?? [],
    }
    setDraft(loaded)
    setBaseline(JSON.stringify(fieldsOf(loaded)))
    setSlugTouched(true)
    setChatSession(null)
    const matchingProvider = state.providers.find((provider) => provider.id === loaded.providerId)
    const matchingEntry = matchingProvider?.modelEntries?.find((entry) => entry.model === loaded.model)
    setTestProviderModelId(matchingEntry?.id ?? '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expertEditorTarget, state.serverSynced])

  const dirty = JSON.stringify(fieldsOf(draft)) !== baseline
  const savedExpert = draft.id ? state.experts.find((item) => item.id === draft.id) : undefined
  const readOnly = savedExpert?.kind === 'builtin'
  const config = draft.id ? state.configs[draft.id] : undefined
  /** 已有专家的详细配置尚未同步完成时禁止写操作，避免用空配置覆盖服务端模型绑定 */
  const hydrating = !!draft.id && !config
  const testedMatches = !!config && config.testedRevision !== null && config.testedRevision === config.revision

  const checks = useMemo(
    () => validateDraft(draft, { ...state, testedRevisionMatches: testedMatches }, { publishing: true }),
    // 校验只依赖草稿与资源快照，普通输入不需要重算全部资源引用
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [draft, state.providers, state.skills, state.knowledgeBases, testedMatches],
  )
  const failedChecks = checks.filter((item) => !item.ok)
  const publishReady = !readOnly && !dirty && !failedChecks.length

  // 有未保存修改时拦截误关页面（应用内返回走 goBack 二次确认）
  useEffect(() => {
    if (!dirty) return
    const guard = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [dirty])

  const update = (patch: Partial<ExpertDraft>) => setDraft((current) => ({ ...current, ...patch }))
  const updateName = (value: string) => {
    setDraft((current) => ({ ...current, name: value, slug: !slugTouched && !current.id ? suggestSlug(value) : current.slug }))
  }

  const goBack = () => {
    if (dirty && !window.confirm('当前修改尚未保存，确定离开编辑器？')) return
    navigate('expert-center')
  }

  const scrollToSection = (section: ExpertCheckSection) => document.getElementById(`sec-${section}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  /** 落库（新建 POST / 编辑 PATCH），成功后同步基线与本地 id，并返回版本 id 供测试会话绑定 */
  const persist = async (): Promise<{ id: string; versionId: string } | null> => {
    if (!draft.name.trim()) { toast.error('请先填写 Expert 名称'); return null }
    if (!draft.id && !isValidSlug(draft.slug.trim())) { toast.error('请先补全合法的 Slug（小写字母开头，仅小写字母、数字、连字符）'); return null }
    setSaving(true)
    try {
      const normalized = { ...draft, name: draft.name.trim(), slug: draft.slug.trim().toLowerCase(), description: draft.description.trim() }
      const saved = await createOrSaveExpert(normalized)
      const next = { ...normalized, id: saved.id }
      setDraft(next)
      setBaseline(JSON.stringify(fieldsOf(next)))
      toast.success(draft.id ? '草稿已保存' : '已创建 Expert 草稿')
      return saved
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '保存失败')
      return null
    } finally {
      setSaving(false)
    }
  }

  const save = async () => { await persist() }

  /** 在 AiChat 中测试：保存草稿 → 创建绑定当前版本的聊天会话 → 打开对话抽屉 */
  const openChatTest = async () => {
    if (!testProviderModelId) { toast.error('请先选择健康且已配置凭据的测试模型'); return }
    setOpeningTest(true)
    try {
      const saved = await persist()
      if (!saved?.versionId) return
      const session = await createTestChatSession(draft.name, saved.versionId, testProviderModelId)
      setChatSession(session)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '创建测试会话失败')
    } finally {
      setOpeningTest(false)
    }
  }

  const publish = async () => {
    if (dirty) {
      // 先保存会让测试状态失效，直接引导用户重测而不是静默发布旧测试结果
      toast.error('配置有未保存的修改：保存后需重新运行测试再发布')
      return
    }
    if (!draft.id || failedChecks.length) return
    setPublishing(true)
    try {
      await publishExpert(draft.id)
      toast.success('Expert 版本已发布，可回到 Expert 中心创建 Deployment')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '发布失败')
    } finally {
      setPublishing(false)
    }
  }

  const provider = state.providers.find((item) => item.id === draft.providerId)
  const testModelOptions = state.providers
    .filter((item) => providerUsable(item))
    .flatMap((item) => (item.modelEntries ?? []).map((entry) => ({ id: entry.id, label: `${item.name} · ${entry.model}` })))
  const selectedTestModel = testModelOptions.find((item) => item.id === testProviderModelId)
  // 新建模式与资源异步到达后都默认选中 Expert 当前模型；当前模型不可用则选第一个健康模型。
  useEffect(() => {
    if (testProviderModelId || !testModelOptions.length) return
    const configured = state.providers.find((item) => item.id === draft.providerId)?.modelEntries?.find((entry) => entry.model === draft.model)
    setTestProviderModelId(configured?.id ?? testModelOptions[0].id)
  }, [draft.model, draft.providerId, state.providers, testModelOptions, testProviderModelId])
  const slugInvalid = !isValidSlug(draft.slug.trim())
  const hasProviderChoices = state.providers.length > 0
  const boundSkillEntries = [
    ...state.skills.filter((skill) => ['published', 'testing'].includes(skill.status)).map((skill) => ({ key: skill.id, skill, stale: false })),
    ...draft.skills
      .filter((skillId) => !state.skills.some((skill) => skill.id === skillId && ['published', 'testing'].includes(skill.status)))
      .map((skillId) => ({ key: skillId, skill: state.skills.find((skill) => skill.id === skillId), stale: true })),
  ]
  const riskyTools = state.mcpTools.filter((tool) => ['write_commit', 'critical'].includes(tool.risk))
  const flowSteps = [
    { label: '保存草稿', done: !!draft.id },
    { label: '运行测试', done: testedMatches },
    { label: '发布版本', done: savedExpert?.status === 'published' },
    { label: '创建 Deployment', done: false, note: '在 Expert 中心详情中操作' },
  ]

  return (
    <div className="page-container">
      {/* ===== 顶栏：返回 / 标识 / 主操作 ===== */}
      <div className="mb-5 flex flex-wrap items-start gap-3">
        <button className="mt-1 flex h-8 w-8 flex-none items-center justify-center rounded-lg border border-slate-200 bg-white text-slate-500 hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300" onClick={goBack} aria-label="返回 Expert 中心"><ArrowLeft className="h-4 w-4" /></button>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            {savedExpert ? <OsStatusBadge status={savedExpert.status} /> : <Badge tone="pur">未保存草稿</Badge>}
            {readOnly && <Badge tone="info">内置 · 只读</Badge>}
            <h1 className="truncate text-lg font-semibold text-slate-900 dark:text-slate-100">{draft.name.trim() || '新建 Expert'}</h1>
            {savedExpert && <span className="font-mono text-[11px] text-slate-400">{savedExpert.version}</span>}
          </div>
          <p className="mt-0.5 text-[11.5px] text-slate-400">{hydrating ? '正在同步专家最新配置…' : '从上到下完成三个分区即可发布；右侧检查清单会实时提示缺失项。'}</p>
        </div>
        <div className="ml-auto flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={() => document.getElementById('sec-test')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}><Play className="mr-1 h-3.5 w-3.5" />打开测试</Button>
          <Button size="sm" disabled={saving || readOnly || hydrating} title={hydrating ? '正在同步配置，请稍候' : undefined} onClick={() => void save()}>
            <Save className="mr-1 h-3.5 w-3.5" />{saving ? '保存中…' : dirty || !draft.id ? '保存草稿' : '已保存'}
          </Button>
          <Button size="sm" disabled={!publishReady || publishing} title={publishReady ? '发布当前已测试版本' : dirty ? '先保存并重新测试后才能发布' : `尚未满足发布条件：${failedChecks.map((item) => item.label).join('；')}`} onClick={() => void publish()}>
            <Rocket className="mr-1 h-3.5 w-3.5" />{publishing ? '发布中…' : '发布版本'}
          </Button>
        </div>
      </div>

      {!hasProviderChoices && (
        <button className="mb-4 w-full rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-left text-[12.5px] text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300" onClick={() => navigate('provider-center')}>
          尚无可用 Provider：模型指令分区需要至少一个健康且已配置凭据的 Provider。前往「AI 构建 / Provider」添加并检测连接 →
        </button>
      )}

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* ===== 左栏：分区表单 ===== */}
        <div className="min-w-0 space-y-5">
          <SectionCard id="sec-basic" index={1} title="基本信息" desc="名称与描述会出现在列表、AiChat 与审计记录中">
            <div className="grid gap-4">
              <Field label="名称" required error={!draft.name.trim() ? '请填写名称' : undefined}>
                <Input value={draft.name} disabled={readOnly} placeholder="例如 Release Notes Writer" onChange={(event) => updateName(event.target.value)} />
              </Field>
              {draft.id ? (
                <Field label="Slug" hint="创建后不可修改，用于 API 与 Deployment 引用">
                  <div className="flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 text-sm text-slate-500 dark:border-slate-700 dark:bg-slate-800/60">
                    <span className="font-mono">{draft.slug || '—'}</span>
                    {draft.slug && <CopyValue value={draft.slug} />}
                  </div>
                </Field>
              ) : (
                <Field label="Slug" required error={slugInvalid ? '仅允许小写字母开头的小写字母、数字和连字符' : undefined} hint={suggestSlug(draft.name) ? '' : '中文名称无法自动生成 Slug 时需手动填写'}>
                  <Input value={draft.slug} placeholder="例如 release-notes" className="font-mono" onBlur={() => setDraft((current) => ({ ...current, slug: current.slug.trim().toLowerCase() }))} onChange={(event) => { setSlugTouched(true); update({ slug: event.target.value }) }} />
                </Field>
              )}
              <Field label="描述" required error={!draft.description.trim() ? '请填写描述' : undefined}>
                <Textarea value={draft.description} disabled={readOnly} rows={3} placeholder="一句话说明该 Expert 的职责、适用场景与边界。" onChange={(event) => update({ description: event.target.value })} />
              </Field>
            </div>
          </SectionCard>

          <SectionCard id="sec-model" index={2} title="模型与指令" desc="选择推理模型并编写 System Prompt，这里是行为的核心">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Provider" required hint={providerUsable(provider) ? `${provider!.status === 'healthy' ? '健康' : provider!.status} · 凭据已配置` : '仅列出健康且凭据可用的 Provider'}>
                <Select value={draft.providerId} disabled={readOnly} onValueChange={(value) => update({ providerId: value, model: '' })}>
                  <SelectTrigger className="w-full"><SelectValue placeholder={hasProviderChoices ? '选择 Provider' : '暂无可用 Provider'} /></SelectTrigger>
                  <SelectContent>
                    {state.providers.map((item) => {
                      const usable = providerUsable(item)
                      return <SelectItem key={item.id} value={item.id} disabled={!usable && item.id !== draft.providerId}>{item.name}{usable ? '' : '（不可用）'}</SelectItem>
                    })}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="模型" required hint={draft.providerId && !draft.model ? '请选择该 Provider 下的模型' : undefined}>
                <Select value={draft.model} disabled={readOnly || !draft.providerId} onValueChange={(value) => update({ model: value })}>
                  <SelectTrigger className="w-full"><SelectValue placeholder={draft.providerId ? '选择模型' : '先选择 Provider'} /></SelectTrigger>
                  <SelectContent>
                    {(provider?.models ?? []).map((model) => <SelectItem key={model} value={model}>{model}</SelectItem>)}
                  </SelectContent>
                </Select>
              </Field>
            </div>
            <div className="mt-4 grid gap-2">
              <Field label="System Prompt" required error={!draft.systemPrompt.trim() ? 'System Prompt 不能为空' : undefined}>
                <Textarea value={draft.systemPrompt} disabled={readOnly} rows={14} className="font-mono text-[12.5px] leading-relaxed" placeholder={'你是……\n\n## 职责\n- …\n\n## 工作方式\n- …\n\n## 输出格式\n- …'} onChange={(event) => update({ systemPrompt: event.target.value })} />
                <span className="text-right text-[11px] text-slate-400">{draft.systemPrompt.length} 字符</span>
              </Field>
            </div>
          </SectionCard>

          <SectionCard id="sec-binding" index={3} title="能力与绑定" desc="Skills 与知识库为可选增强，状态异常时不能用于发布">
            <SubTitle>绑定 Skills</SubTitle>
            {boundSkillEntries.length ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {boundSkillEntries.map(({ key, skill, stale }) => (
                  <label key={key} className={`flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2.5 ${stale ? 'border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10' : 'border-slate-200 hover:border-blue-300 dark:border-slate-700 dark:hover:border-blue-500/50'}`}>
                    <Checkbox className="mt-0.5" checked={draft.skills.includes(key)} onCheckedChange={() => toggle(setDraft, 'skills', key)} />
                    <span className="min-w-0 flex-1">
                      <span className="flex items-center gap-1.5"><b className="truncate text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{skill?.name ?? key}</b><span className="font-mono text-[10px] text-slate-400">{skill?.version}</span></span>
                      <span className="mt-0.5 flex items-center gap-1.5 text-[10.5px] text-slate-400">{stale ? '状态已失效，建议解除绑定' : skill?.description}</span>
                    </span>
                    {skill && <OsStatusBadge status={skill.status} />}
                  </label>
                ))}
              </div>
            ) : (
              <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-[12px] text-slate-400 dark:border-slate-700">暂无可绑定的 Skill。可到「AI 构建 / Expert Skill」上传后再回来绑定。</p>
            )}
            <SubTitle className="mt-5">知识库</SubTitle>
            {state.knowledgeBases.length ? (
              <div className="grid gap-2 sm:grid-cols-2">
                {state.knowledgeBases.map((base) => (
                  <label key={base.id} className="flex cursor-pointer items-center gap-2.5 rounded-lg border border-slate-200 px-3 py-2.5 hover:border-blue-300 dark:border-slate-700 dark:hover:border-blue-500/50">
                    <Checkbox checked={draft.knowledgeBaseIds.includes(base.id)} onCheckedChange={() => toggle(setDraft, 'knowledgeBaseIds', base.id)} />
                    <span className="min-w-0 flex-1 truncate text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{base.name}</span>
                    <Badge tone={base.status === 'indexed' ? 'suc' : 'warn'}>{base.status === 'indexed' ? '已索引' : '索引中'}</Badge>
                  </label>
                ))}
              </div>
            ) : (
              <p className="rounded-lg border border-dashed border-slate-200 px-3 py-4 text-center text-[12px] text-slate-400 dark:border-slate-700">知识库索引能力接入后将在此开放绑定，当前可跳过。</p>
            )}
          </SectionCard>

          <section className="rounded-xl border border-slate-200 bg-white p-5 text-[12px] leading-relaxed text-slate-500 dark:border-slate-700/60 dark:bg-slate-900 dark:text-slate-400">
            <b className="text-[13px] text-slate-700 dark:text-slate-200">工具审批策略</b>
            <p className="mt-1">写入类工具（write_commit 及以上风险）在 LangGraph 运行时会中断并进入审批队列，由有权限的成员批准后继续。</p>
            {riskyTools.length > 0 && (
              <ul className="mt-2 space-y-1.5">
                {riskyTools.map((tool) => <li key={tool.id} className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-amber-800 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-300">{tool.name} · {tool.risk} · 需要人工审批</li>)}
              </ul>
            )}
          </section>
        </div>

        {/* ===== 右栏：实时检查 + 流程指引 + 测试 ===== */}
        <aside className="hidden space-y-4 lg:sticky lg:top-20 lg:block">
          <div className={`rounded-xl border p-4 ${failedChecks.length ? 'border-amber-200 bg-amber-50/70 dark:border-amber-500/30 dark:bg-amber-500/10' : 'border-emerald-200 bg-emerald-50/70 dark:border-emerald-500/30 dark:bg-emerald-500/10'}`}>
            <b className="text-[13px] text-slate-700 dark:text-slate-200">发布检查</b>
            <p className="mt-0.5 text-[11px] text-slate-400">{failedChecks.length ? `${failedChecks.length} 项待处理，实时校验当前草稿` : '已满足全部发布条件'}</p>
            <ul className="mt-3 space-y-2.5">
              {checks.map((item) => (
                <li key={item.key} className="flex items-start gap-2 text-[12px]">
                  {item.ok ? <CircleCheck className="mt-0.5 h-3.5 w-3.5 flex-none text-emerald-600 dark:text-emerald-400" /> : <CircleX className="mt-0.5 h-3.5 w-3.5 flex-none text-amber-600 dark:text-amber-400" />}
                  <span className="min-w-0 flex-1">
                    <span className={item.ok ? 'text-slate-600 dark:text-slate-300' : 'font-medium text-amber-800 dark:text-amber-300'}>{item.label}</span>
                    {!item.ok && item.hint && <span className="mt-0.5 block text-[11px] text-slate-400">{item.hint}</span>}
                    {!item.ok && item.section !== 'publish' && (
                      <button className="mt-0.5 text-[11px] font-medium text-blue-600 hover:underline dark:text-blue-400" onClick={() => scrollToSection(item.section)}>去修复 →</button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700/60 dark:bg-slate-900">
            <b className="text-[13px] text-slate-700 dark:text-slate-200">上线流程</b>
            <ol className="mt-3 space-y-2.5">
              {flowSteps.map((step, index) => (
                <li key={step.label} className="flex items-center gap-2.5 text-[12px]">
                  <span className={`flex h-5 w-5 flex-none items-center justify-center rounded-full text-[10px] font-semibold ${step.done ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-300' : index === flowSteps.findIndex((item) => !item.done) ? 'bg-blue-600 text-white' : 'bg-slate-100 text-slate-400 dark:bg-slate-800'}`}>{step.done ? '✓' : index + 1}</span>
                  <span className={step.done ? 'text-slate-500 line-through decoration-slate-300 dark:text-slate-400' : 'font-medium text-slate-700 dark:text-slate-200'}>{step.label}</span>
                  {'note' in step && step.note && <span className="ml-auto text-[10.5px] text-slate-400">{step.note}</span>}
                </li>
              ))}
            </ol>
            <p className="mt-3 border-t border-slate-100 pt-2.5 text-[11px] leading-relaxed text-slate-400 dark:border-slate-800">编辑已发布版本的配置会自动生成新的草稿小版本；Deployment 始终固定在创建时的版本，不受后续编辑影响。</p>
          </div>

          <div id="sec-test" className="scroll-mt-20 rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-700/60 dark:bg-slate-900">
            <b className="text-[13px] text-slate-700 dark:text-slate-200">测试</b>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-400">选择本轮测试使用的模型后，AiChat 会固定绑定「当前草稿版本 + 该模型」进行多轮对话；不会修改 Expert 的正式模型配置。</p>
            <label className="mt-3 grid gap-1.5 text-[11.5px] font-medium text-slate-600 dark:text-slate-300">测试模型
              <Select value={testProviderModelId} onValueChange={setTestProviderModelId} disabled={readOnly || hydrating || !testModelOptions.length}>
                <SelectTrigger className="w-full"><SelectValue placeholder={testModelOptions.length ? '选择测试模型' : '暂无健康可用模型'} /></SelectTrigger>
                <SelectContent>{testModelOptions.map((item) => <SelectItem key={item.id} value={item.id}>{item.label}</SelectItem>)}</SelectContent>
              </Select>
            </label>
            <Button size="sm" className="mt-3 w-full" disabled={saving || openingTest || readOnly || hydrating || !testProviderModelId} title={hydrating ? '正在同步配置，请稍候' : !testModelOptions.length ? '请先在 Provider 中配置并检测模型连接' : undefined} onClick={() => void openChatTest()}><Play className="mr-1 h-3.5 w-3.5" />{openingTest ? '正在打开…' : '在 AiChat 中测试'}</Button>
            {selectedTestModel && <p className="mt-2 text-[11px] text-slate-400">本会话固定使用：{selectedTestModel.label}</p>}
            <p className={`mt-2.5 text-[11px] ${testedMatches ? 'font-medium text-emerald-600 dark:text-emerald-400' : 'text-slate-400'}`}>{testedMatches ? '✓ 当前版本已通过测试' : draft.id ? '○ 尚未通过测试：修改保存后需重新对话一轮' : '○ 尚未保存草稿，点击上方按钮自动保存并开始测试'}</p>
          </div>
        </aside>
      </div>

      <ExpertChatDrawer sessionId={chatSession?.id ?? null} sessionTitle={chatSession?.title ?? 'Expert 测试'} subtitle={draft.name.trim() ? `与「${draft.name.trim()}」当前草稿配置多轮对话` : undefined} modelLabel={selectedTestModel?.label} onClose={() => setChatSession(null)} onTested={() => { if (draft.id) markConfigTested(draft.id) }} />
    </div>
  )
}

function toggle(setDraft: React.Dispatch<React.SetStateAction<ExpertDraft>>, key: 'skills' | 'knowledgeBaseIds', value: string) {
  setDraft((current) => ({ ...current, [key]: current[key].includes(value) ? current[key].filter((item) => item !== value) : [...current[key], value] }))
}

function SectionCard({ id, index, title, desc, children }: { id: string; index: number; title: string; desc: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-20 rounded-xl border border-slate-200 bg-white p-5 dark:border-slate-700/60 dark:bg-slate-900">
      <header className="mb-4 flex items-baseline gap-2.5">
        <span className="flex h-6 w-6 flex-none items-center justify-center self-center rounded-lg bg-blue-50 text-[11px] font-semibold text-blue-600 dark:bg-blue-500/15">{index}</span>
        <div className="min-w-0"><h2 className="text-[14px] font-semibold text-slate-800 dark:text-slate-100">{title}</h2><p className="text-[11.5px] text-slate-400">{desc}</p></div>
      </header>
      {children}
    </section>
  )
}

function Field({ label, required, error, hint, children }: { label: string; required?: boolean; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    // 用 div 而非 label，避免嵌套 Select/Checkbox 等按钮型控件时点击穿透
    <div className="grid gap-1.5">
      <span className="flex items-center gap-1 text-[12.5px] font-medium text-slate-700 dark:text-slate-200">{label}{required && <b className="text-red-500">*</b>}</span>
      {children}
      {error ? <span className="text-[11px] text-red-600 dark:text-red-400">{error}</span> : hint ? <span className="text-[11px] text-slate-400">{hint}</span> : null}
    </div>
  )
}

function SubTitle({ className = '', children }: { className?: string; children: React.ReactNode }) {
  return <h3 className={`mb-2 text-[12.5px] font-semibold text-slate-600 dark:text-slate-300 ${className}`}>{children}</h3>
}
