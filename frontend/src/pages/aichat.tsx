import { useEffect, useMemo, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
} from "@assistant-ui/react";
import type { FileMessagePartProps } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, LoaderCircle, Plus, Send, Square } from "lucide-react";
import { api, getToken } from "../lib/api";
import { DocumentViewerDrawer } from "../components/document-viewer-drawer";
import { createContext, useContext } from "react";
import { toast } from "../store/app-store";
import { useExpertOs } from "../store/expert-os-store";

/** 消息附件 → 统一预览抽屉（OutputFile 由 assistant-ui 无 props 渲染，经 Context 唤起） */
type PreviewRequest = { files: { id: string; name: string; srcUrl?: string }[]; initialId?: string };
const DocPreviewContext = createContext<{ openPreview: (req: PreviewRequest) => void }>({ openPreview: () => {} });

interface PersistedSession {
  id: string;
  expertId: string | null;
  expertVersionId: string | null;
  deploymentId: string | null;
  providerModelId: string | null;
  title: string;
  updated: string;
}
interface OutputFile {
  id: string;
  filename: string;
  mimeType: string;
  downloadUrl: string;
}
interface TraceItem {
  kind?: "skill" | "mcp" | "tool" | "approval" | "model";
  tool: string;
  status: string;
  summary: string | Record<string, unknown>;
}
interface PersistedMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  time: string;
  status: string;
  runId: string | null;
  toolTrace: TraceItem[];
  files: OutputFile[];
  compacted?: boolean;
}

export function AiChatPage() {
  const { state } = useExpertOs();
  const [preview, setPreview] = useState<PreviewRequest | null>(null);
  const openPreview = (req: PreviewRequest) => setPreview(req);
  const [sessions, setSessions] = useState<PersistedSession[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<PersistedMessage[]>([]);
  const [expertId, setExpertId] = useState("");
  const [providerModelId, setProviderModelId] = useState("");
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [messagesVersion, setMessagesVersion] = useState(0);
  const [lastCompacted, setLastCompacted] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [executingTrace, setExecutingTrace] = useState<TraceItem[]>([]);
  const stopRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  /** 停止正在进行的生成：中断打字机 + 取消挂起的请求。 */
  const stopGeneration = () => {
    stopRef.current = true;
    abortRef.current?.abort();
    setStopped(true);
    setIsRunning(false);
  };

  const activeDeployment = state.deployments.find(
    (item) => item.expertId === expertId && item.status === "active",
  );
  const providerModels = (
    state.providers as ((typeof state.providers)[number] & {
      modelEntries?: { id: string; model: string }[];
    })[]
  )
    .filter(
      (item) => item.status === "healthy" && item.credential === "configured",
    )
    .flatMap((item) =>
      (item.modelEntries ?? []).map((model) => ({
        ...model,
        providerName: item.name,
      })),
    );
  // initialMessages 只在会话/历史版本变化时重建（稳定引用），避免每次重渲染重置 assistant-ui 线程
  // eslint-disable-next-line react-hooks/exhaustive-deps -- messages 变化不应重建（重建会清空正在进行的对话）
  const initialMessages = useMemo(
    () =>
      messages.map((message) => ({
        id: message.id,
        role: message.role,
        content: [
          { type: "text" as const, text: message.content },
          ...(message.files ?? []).map((file) => ({
            type: "file" as const,
            data: file.downloadUrl,
            mimeType: file.mimeType,
            filename: file.filename,
            sourceType: "url" as const,
          })),
        ],
        createdAt: new Date(message.time),
      })),
    [sessionId, messagesVersion],
  );
  const runtime = useLocalRuntime(
    {
      async *run({ messages: runtimeMessages }) {
        if (!sessionId) throw new Error("请先创建会话");
        const latest = runtimeMessages.at(-1);
        const content =
          latest?.content
            .filter((part) => part.type === "text")
            .map((part) => part.text)
            .join("") ?? "";
        stopRef.current = false;
        setStopped(false);
        const abort = new AbortController();
        abortRef.current = abort;
        setIsRunning(true);
        try {
          const response = await fetch(
            `/api/v1/expert-chat/sessions/${sessionId}/messages/stream`,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                ...(getToken()
                  ? { Authorization: `Bearer ${getToken()}` }
                  : {}),
              },
              body: JSON.stringify({
                content,
                write_intent: /提交|创建|写入|删除|发布/.test(content),
              }),
              signal: abort.signal,
            },
          );
          if (!response.ok || !response.body)
            throw new Error(`流式请求失败（HTTP ${response.status}）`);
          if (
            !response.headers
              .get("content-type")
              ?.startsWith("text/event-stream")
          ) {
            // Expert / 版本绑定会话：服务端不做逐 token 流式，返回整段 ok 包络结果。
            // 此前这里误按错误抛出，导致 Expert 会话永远显示「发送失败」——正确语义是消费 assistantMessage。
            const payload = (await response.json()) as {
              code?: number;
              message?: string;
              data?: { assistantMessage?: PersistedMessage };
            };
            const completed = payload.data?.assistantMessage;
            if (payload.code !== 0 || !completed)
              throw new Error(payload.message || "消息处理失败");
            setMessages((current) => [...current, completed]);
            if (completed.compacted) setLastCompacted(true);
            const files = completed.files ?? [];
            yield {
              content: [
                { type: "text" as const, text: completed.content },
                ...files.map((file) => ({
                  type: "file" as const,
                  data: file.downloadUrl,
                  mimeType: file.mimeType,
                  filename: file.filename,
                  sourceType: "url" as const,
                })),
              ],
            };
            return;
          }
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let text = "";
          let finalMessage: PersistedMessage | null = null;
          const consume = (block: string) => {
            const lines = block.split("\n");
            const event = lines
              .find((line) => line.startsWith("event: "))
              ?.slice(7);
            const raw = lines
              .find((line) => line.startsWith("data: "))
              ?.slice(6);
            if (!event || !raw) return;
            const data = JSON.parse(raw) as TraceItem & {
              text?: string;
              message?: PersistedMessage;
            };
            if (event === "trace")
              setExecutingTrace((current) => {
                const index = current.findIndex(
                  (item) => item.tool === data.tool,
                );
                if (index < 0) return [...current, data];
                const next = [...current];
                next[index] = data;
                return next;
              });
            if (event === "token" && data.text) text += data.text;
            if (event === "done" && data.message) finalMessage = data.message;
          };
          try {
            while (true) {
              const chunk = await reader.read();
              buffer += decoder.decode(chunk.value ?? new Uint8Array(), {
                stream: !chunk.done,
              });
              const blocks = buffer.split("\n\n");
              buffer = blocks.pop() ?? "";
              blocks.forEach(consume);
              if (text) yield { content: [{ type: "text" as const, text }] };
              if (chunk.done) break;
            }
          } finally {
            await reader.cancel().catch(() => {});
          }
          const completedMessage = finalMessage as PersistedMessage | null;
          if (completedMessage) {
            setMessages((current) => [...current, completedMessage]);
            if (completedMessage.compacted) setLastCompacted(true);
            const files = completedMessage.files ?? [];
            if (files.length)
              yield {
                content: [
                  { type: "text" as const, text },
                  ...files.map((file) => ({
                    type: "file" as const,
                    data: file.downloadUrl,
                    mimeType: file.mimeType,
                    filename: file.filename,
                    sourceType: "url" as const,
                  })),
                ],
              };
          }
        } catch (error) {
          // 用户主动停止（请求被取消）→ 不报错，提示已停止；完整内容已存库，刷新可恢复
          if (abort.signal.aborted) {
            setStopped(true);
            return;
          }
          toast.error(error instanceof Error ? error.message : "消息发送失败");
          yield {
            content: [
              {
                type: "text" as const,
                text: `（发送失败：${error instanceof Error ? error.message : "未知错误"}）`,
              },
            ],
          };
        } finally {
          setIsRunning(false);
          abortRef.current = null;
        }
      },
    },
    { initialMessages },
  );

  useEffect(() => {
    api
      .get<{ items: PersistedSession[] }>("/api/v1/expert-chat/sessions")
      .then((data) => {
        setSessions(data.items);
        const first = data.items[0];
        if (first) {
          setSessionId(first.id);
          setExpertId(first.expertId ?? "");
          setProviderModelId(first.providerModelId ?? "");
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setMessages([]);
      return;
    }
    api
      .get<{ items: PersistedMessage[] }>(
        `/api/v1/expert-chat/sessions/${sessionId}/messages`,
      )
      .then((data) => {
        setMessages(data.items);
        setMessagesVersion((version) => version + 1);
      })
      .catch(() => {});
  }, [sessionId]);

  const newSession = async () => {
    try {
      const data = await api.post<{ session: PersistedSession }>(
        "/api/v1/expert-chat/sessions",
        {
          deployment_id: activeDeployment?.id ?? "",
          provider_model_id: providerModelId,
          title: activeDeployment ? "新会话" : "FlowHub 默认对话",
        },
      );
      setSessions((current) => [data.session, ...current]);
      setSessionId(data.session.id);
      setMessages([]);
      setMessagesVersion((version) => version + 1);
      setExecutingTrace([]);
      setLastCompacted(false);
      setStopped(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建会话失败");
    }
  };

  const selectSession = (session: PersistedSession) => {
    setSessionId(session.id);
    setExpertId(session.expertId ?? "");
    setProviderModelId(session.providerModelId ?? "");
  };
  /** 更新当前会话的 Expert/模型绑定（保留会话选中，不新建/不清空）。 */
  const patchSessionBinding = async (body: {
    expert_id?: string;
    provider_model_id?: string;
  }) => {
    if (!sessionId) return;
    try {
      const data = await api.patch<{ session: PersistedSession }>(
        `/api/v1/expert-chat/sessions/${sessionId}`,
        body,
      );
      setSessions((current) =>
        current.map((session) =>
          session.id === sessionId ? data.session : session,
        ),
      );
      setProviderModelId(data.session.providerModelId ?? "");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "更新会话绑定失败");
    }
  };
  /** 切换 Expert：有会话 → 更新绑定；无会话 → 用该 Expert 的活跃部署新建并选中。 */
  const changeExpert = async (value: string) => {
    setExpertId(value);
    if (sessionId) {
      await patchSessionBinding({ expert_id: value });
    } else if (value) {
      const deployment = state.deployments.find(
        (item) => item.expertId === value && item.status === "active",
      );
      try {
        const data = await api.post<{ session: PersistedSession }>(
          "/api/v1/expert-chat/sessions",
          {
            deployment_id: deployment?.id ?? "",
            provider_model_id: providerModelId,
            title: "新会话",
          },
        );
        setSessions((current) => [data.session, ...current]);
        setSessionId(data.session.id);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "创建会话失败");
      }
    }
  };
  /** 切换模型：有会话 → 更新绑定；无会话 → 带模型新建默认会话并选中。 */
  const changeModel = async (value: string) => {
    setProviderModelId(value);
    if (sessionId) {
      await patchSessionBinding({ provider_model_id: value });
    } else if (value) {
      try {
        const data = await api.post<{ session: PersistedSession }>(
          "/api/v1/expert-chat/sessions",
          { provider_model_id: value, title: "FlowHub 默认对话" },
        );
        setSessions((current) => [data.session, ...current]);
        setSessionId(data.session.id);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "创建会话失败");
      }
    }
  };
  const renameSession = async () => {
    if (!sessionId || !renameValue.trim()) return;
    try {
      const data = await api.patch<{ session: PersistedSession }>(
        `/api/v1/expert-chat/sessions/${sessionId}`,
        { title: renameValue.trim() },
      );
      setSessions((current) =>
        current.map((session) =>
          session.id === sessionId ? data.session : session,
        ),
      );
      setRenameOpen(false);
      toast.success("会话已重命名");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "会话重命名失败");
    }
  };
  /** 清空当前会话消息（保留会话与绑定）。 */
  const clearSession = async (id: string) => {
    const target = sessions.find((s) => s.id === id);
    if (
      !window.confirm(
        `清空会话「${target?.title ?? id.slice(0, 8)}」的全部消息？历史将不可恢复。`,
      )
    )
      return;
    try {
      await api.del(`/api/v1/expert-chat/sessions/${id}/messages`);
      if (id === sessionId) {
        setMessages([]);
        setMessagesVersion((v) => v + 1);
      }
      toast.success("会话消息已清空");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "清空失败");
    }
  };
  /** 删除会话（消息级联删除）。 */
  const deleteSession = async (id: string) => {
    const target = sessions.find((s) => s.id === id);
    if (
      !window.confirm(
        `删除会话「${target?.title ?? id.slice(0, 8)}」？消息将一并删除，不可恢复。`,
      )
    )
      return;
    try {
      await api.del(`/api/v1/expert-chat/sessions/${id}`);
      const rest = sessions.filter((s) => s.id !== id);
      setSessions(rest);
      if (id === sessionId) {
        const next = rest[0];
        setSessionId(next?.id ?? "");
        setExpertId(next?.expertId ?? "");
        setProviderModelId(next?.providerModelId ?? "");
        setMessages([]);
        setMessagesVersion((v) => v + 1);
      }
      toast.success("会话已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    }
  };
  const availableExperts = state.experts.filter(
    (item) =>
      item.status === "published" &&
      state.deployments.some(
        (deployment) =>
          deployment.expertId === item.id && deployment.status === "active",
      ),
  );

  const activeSession = sessions.find((item) => item.id === sessionId);
  const latestTrace = executingTrace.length
    ? executingTrace
    : ([...messages]
        .reverse()
        .find(
          (message) =>
            message.role === "assistant" && message.toolTrace?.length,
        )?.toolTrace ?? []);
  return (
    <DocPreviewContext.Provider value={{ openPreview }}>
    <div className="flex h-[calc(100vh-60px)] min-h-[620px] overflow-hidden bg-slate-50 dark:bg-slate-950">
      <aside className="hidden w-[270px] flex-none flex-col border-r border-slate-200 bg-slate-50 lg:flex dark:border-slate-800 dark:bg-slate-900/60">
        <div className="flex items-center justify-between px-4 py-4">
          <b className="text-[13px]">AiChat</b>
          <button
            className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600 text-white"
            onClick={() => void newSession()}
            aria-label="新建会话"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-2">
          {sessions.map((session) => (
            <div
              key={session.id}
              className={`group relative mb-1 w-full rounded-lg px-3 py-2.5 text-left ${session.id === sessionId ? "bg-white shadow-sm dark:bg-slate-800" : "hover:bg-white/70 dark:hover:bg-slate-800/60"}`}
            >
              <button
                className="block w-full text-left"
                onClick={() => selectSession(session)}
              >
                <b className="block truncate text-xs">{session.title}</b>
                <span className="text-[10px] text-slate-400">
                  {session.updated}
                </span>
              </button>
              <div className="absolute right-2 top-2 hidden gap-1 group-hover:flex">
                <button
                  title="清空消息（保留会话）"
                  className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500 hover:text-blue-600 dark:bg-slate-700 dark:text-slate-300"
                  onClick={(event) => {
                    event.stopPropagation();
                    void clearSession(session.id);
                  }}
                >
                  清空
                </button>
                <button
                  title="删除会话"
                  className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500 hover:text-red-600 dark:bg-slate-700 dark:text-slate-300"
                  onClick={(event) => {
                    event.stopPropagation();
                    void deleteSession(session.id);
                  }}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
          {!sessions.length && (
            <p className="p-4 text-xs text-slate-400">
              可直接创建 FlowHub 默认对话，或先选择 Expert。
            </p>
          )}
        </div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col bg-white dark:bg-slate-950">
        <header className="flex min-h-[64px] items-center gap-3 border-b border-slate-200 px-5 dark:border-slate-800">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-violet-50 text-violet-600">
            <Bot className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <b className="block truncate text-[13px]">
                {activeSession?.title ?? "新会话"}
              </b>
              {activeSession && (
                <button
                  className="text-[10px] text-blue-600 hover:underline"
                  onClick={() => {
                    setRenameValue(activeSession.title);
                    setRenameOpen(true);
                  }}
                >
                  重命名
                </button>
              )}
              {activeSession && (
                <button
                  className="text-[10px] text-slate-400 hover:text-red-500 hover:underline"
                  title="删除会话"
                  onClick={() => void deleteSession(activeSession.id)}
                >
                  删除
                </button>
              )}
            </div>
            <span className="text-[10.5px] text-slate-400">
              {activeSession?.expertVersionId && !activeSession.deploymentId
                ? `${availableExperts.find((item) => item.id === activeSession.expertId)?.name ?? "草稿版本"} · 版本测试会话（发布前对话测试）`
                : (availableExperts.find((item) => item.id === expertId)
                    ?.name ??
                  (providerModelId
                    ? "FlowHub 默认能力 + 已选模型"
                    : "FlowHub 默认能力：任务、工作项、流程状态查询"))}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-[11px] text-slate-400">
              Expert{" "}
              <select
                value={expertId}
                onChange={(event) => {
                  void changeExpert(event.target.value);
                }}
                className="ml-2 h-8 rounded-lg border border-slate-300 bg-white px-2 text-xs dark:border-slate-700 dark:bg-slate-900"
              >
                <option value="">不使用 Expert</option>
                {availableExperts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-[11px] text-slate-400">
              模型{" "}
              <select
                value={providerModelId}
                onChange={(event) => {
                  void changeModel(event.target.value);
                }}
                className="ml-2 h-8 rounded-lg border border-slate-300 bg-white px-2 text-xs dark:border-slate-700 dark:bg-slate-900"
              >
                <option value="">选择模型（可选）</option>
                {providerModels.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.providerName} · {model.model}
                  </option>
                ))}
              </select>
            </label>
          </div>
        </header>
        <AssistantRuntimeProvider
          key={`${sessionId || "empty"}-${messagesVersion}`}
          runtime={runtime}
        >
          <ThreadPrimitive.Root className="flex min-h-0 flex-1 flex-col">
            <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-5 py-6">
              <ThreadPrimitive.Messages
                components={{
                  UserMessage: UserMessage,
                  AssistantMessage: AssistantMessage,
                }}
              />
              <ThreadPrimitive.Empty>
                <div className="mx-auto mt-16 max-w-md rounded-xl border border-dashed border-slate-200 p-6 text-center text-sm text-slate-400 dark:border-slate-700">
                  {activeDeployment
                    ? providerModelId
                      ? "输入消息即可创建真实 Expert LangGraph Run，并使用所选模型。"
                      : "输入消息即可创建真实 Expert LangGraph Run。"
                    : providerModelId
                      ? "默认 FlowHub 能力会携带只读业务上下文交给所选模型推理。"
                      : "FlowHub 默认对话可查询项目、任务、问题和流程状态；也可在右上角选择模型进行推理。"}
                </div>
              </ThreadPrimitive.Empty>
            </ThreadPrimitive.Viewport>
            <div className="border-t border-slate-200 p-4 dark:border-slate-800">
              {isRunning && (
                <div className="mx-auto mb-2 flex max-w-[780px] items-center gap-2 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-700 dark:border-blue-500/20 dark:bg-blue-500/10 dark:text-blue-300">
                  <LoaderCircle className="h-4 w-4 animate-spin" />
                  <span>
                    <b>正在执行 LangGraph</b> · 读取上下文、调用工具并生成回答…
                  </span>
                </div>
              )}
              {lastCompacted && (
                <div className="mx-auto mb-2 flex max-w-[780px] items-center gap-2 rounded-lg border border-violet-100 bg-violet-50 px-3 py-2 text-xs text-violet-700 dark:border-violet-500/20 dark:bg-violet-500/10 dark:text-violet-300">
                  <span className="flex-none">🧠 上下文已自动压缩</span>
                  <span className="text-[11px] opacity-80">
                    会话较长，早期对话已摘要（保留最近 6
                    轮全文），模型仍可基于摘要理解前文。
                  </span>
                </div>
              )}
              {latestTrace.length > 0 && (
                <div className="mx-auto mb-2 max-w-[780px] rounded-lg bg-slate-50 px-3 py-2 text-[11px] text-slate-500 dark:bg-slate-900">
                  {latestTrace.map((trace, index) => (
                    <div key={`${trace.tool}-${index}`}>
                      <b>{trace.tool}</b> · {trace.status} ·{" "}
                      {typeof trace.summary === "string"
                        ? trace.summary
                        : JSON.stringify(trace.summary)}
                    </div>
                  ))}
                </div>
              )}
              {stopped && (
                <div className="mx-auto mb-2 flex max-w-[780px] items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:border-slate-700 dark:bg-slate-900">
                  <span>
                    已停止生成（完整内容已保存，刷新或切换会话可查看）。
                  </span>
                </div>
              )}
              <ComposerPrimitive.Root className="mx-auto flex max-w-[780px] items-end gap-2 rounded-xl border border-slate-300 p-2 focus-within:border-blue-500 dark:border-slate-700">
                <ComposerPrimitive.Input
                  placeholder={
                    sessionId
                      ? "询问 FlowHub 或向 Expert 描述工作…"
                      : "点击左侧 + 创建会话"
                  }
                  disabled={!sessionId || isRunning}
                  className="max-h-32 min-h-10 flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none"
                />
                {isRunning && (
                  <button
                    onClick={stopGeneration}
                    className="flex h-9 items-center gap-1 rounded-lg border border-red-200 px-3 text-xs font-medium text-red-600 hover:bg-red-50 dark:border-red-500/30 dark:text-red-400"
                    title="停止生成"
                  >
                    <Square className="h-3 w-3 fill-current" />
                    停止
                  </button>
                )}
                <ComposerPrimitive.Send
                  disabled={!sessionId || isRunning}
                  className="flex h-9 items-center gap-1 rounded-lg bg-blue-600 px-3 text-xs font-medium text-white disabled:opacity-40"
                >
                  <Send className="h-3.5 w-3.5" />
                  {isRunning ? "执行中" : "发送"}
                </ComposerPrimitive.Send>
              </ComposerPrimitive.Root>
            </div>
          </ThreadPrimitive.Root>
        </AssistantRuntimeProvider>
        {renameOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30">
            <div className="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl dark:bg-slate-900">
              <b className="text-sm">重命名会话</b>
              <input
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                className="mt-3 h-9 w-full rounded-lg border border-slate-300 px-3 text-sm dark:border-slate-700 dark:bg-slate-800"
                autoFocus
              />
              <div className="mt-4 flex justify-end gap-2">
                <button
                  className="rounded-lg border px-3 py-2 text-xs"
                  onClick={() => setRenameOpen(false)}
                >
                  取消
                </button>
                <button
                  className="rounded-lg bg-blue-600 px-3 py-2 text-xs text-white"
                  onClick={() => void renameSession()}
                >
                  保存
                </button>
              </div>
            </div>
          </div>
        )}
      </main>
      {preview && (
        <DocumentViewerDrawer
          open
          docs={preview.files}
          initialDocId={preview.initialId}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
    </DocPreviewContext.Provider>
  );
}

function AssistantMarkdown() {
  return (
    <MarkdownTextPrimitive
      className="aui-markdown"
      smooth
      defer
      remarkPlugins={[remarkGfm]}
    />
  );
}

function OutputFile({ data, filename, mimeType }: FileMessagePartProps) {
  const { openPreview } = useContext(DocPreviewContext);
  const download = async () => {
    const token = getToken();
    const response = await fetch(data, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!response.ok) throw new Error("文件下载失败");
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename ?? "flowhub-output";
    anchor.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="mt-2 flex max-w-full items-center gap-1.5">
      <button
        type="button"
        className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-left text-xs text-blue-700 hover:border-blue-400 dark:border-slate-700 dark:bg-slate-950 dark:text-blue-300"
        onClick={() =>
          void download().catch((error: unknown) =>
            toast.error(error instanceof Error ? error.message : "文件下载失败"),
          )
        }
        title={`下载 ${filename ?? "产出文件"}`}
      >
        <span className="truncate">{filename ?? "产出文件"}</span>
        <span className="flex-none text-[10px] text-slate-400">{mimeType}</span>
      </button>
      <button
        type="button"
        className="flex-none rounded-lg border border-blue-200 px-2.5 py-2 text-xs font-medium text-blue-600 hover:bg-blue-50 dark:border-blue-500/30 dark:text-blue-300 dark:hover:bg-blue-500/10"
        title="预览"
        onClick={() =>
          openPreview({
            files: [{ id: data, name: filename ?? "产出文件", srcUrl: data }],
            initialId: data,
          })
        }
      >
        预览
      </button>
    </div>
  );
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mx-auto mb-4 flex max-w-[780px] justify-end">
      <div className="max-w-[80%] rounded-xl bg-blue-600 px-4 py-3 text-sm text-white">
        <MessagePrimitive.Content />
      </div>
    </MessagePrimitive.Root>
  );
}
function AssistantMessage() {
  return (
    <MessagePrimitive.Root className="mx-auto mb-4 flex max-w-[780px]">
      <div className="max-w-[80%] rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-700 dark:border-slate-800 dark:bg-slate-900 dark:text-slate-200">
        <MessagePrimitive.Content
          components={{ Text: AssistantMarkdown, File: OutputFile }}
        />
      </div>
    </MessagePrimitive.Root>
  );
}
