import { useEffect, useMemo, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
} from "@assistant-ui/react";
import type { FileMessagePartProps, TextMessagePartProps } from "@assistant-ui/react";
import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import {
  ArrowDown, Bot, Check, ChevronRight, Copy, Cpu, Eraser, FolderGit2, LoaderCircle,
  Pencil, Plus, RotateCcw, Search, Send, Square, Trash2, Wrench,
} from "lucide-react";
import { api, getToken } from "../lib/api";
import { Badge } from "../components/common";
import { DocumentViewerDrawer } from "../components/document-viewer-drawer";
import { createContext, useContext } from "react";
import { toast } from "../store/app-store";
import { useExpertOs } from "../store/expert-os-store";
import { cn } from "../lib/utils";

/** 消息附件 → 统一预览抽屉（OutputFile 由 assistant-ui 无 props 渲染，经 Context 唤起） */
type PreviewRequest = { files: { id: string; name: string; srcUrl?: string }[]; initialId?: string };
const DocPreviewContext = createContext<{ openPreview: (req: PreviewRequest) => void }>({ openPreview: () => {} });

/** 消息元数据：Text 部件渲染时回传全文，供消息级「复制」使用 */
const TextCaptureContext = createContext<{ capture: (text: string) => void }>({ capture: () => {} });

interface PersistedSession {
  id: string;
  expertId: string | null;
  expertVersionId: string | null;
  deploymentId: string | null;
  providerModelId: string | null;
  projectName: string | null;
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

const WELCOME_SUGGESTIONS = ["查询我的待办任务", "项目进度概览", "最近的问题单有哪些？"];

export function AiChatPage() {
  const { state } = useExpertOs();
  const [preview, setPreview] = useState<PreviewRequest | null>(null);
  const openPreview = (req: PreviewRequest) => setPreview(req);
  const [sessions, setSessions] = useState<PersistedSession[]>([]);
  const [sessionQuery, setSessionQuery] = useState("");
  const [sessionId, setSessionId] = useState("");
  const [messages, setMessages] = useState<PersistedMessage[]>([]);
  const [expertId, setExpertId] = useState("");
  const [providerModelId, setProviderModelId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [messagesVersion, setMessagesVersion] = useState(0);
  const [lastCompacted, setLastCompacted] = useState(false);
  const [stopped, setStopped] = useState(false);
  const [executingTrace, setExecutingTrace] = useState<TraceItem[]>([]);
  const stopRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  const runtimeRef = useRef<ReturnType<typeof useLocalRuntime> | null>(null);
  /** 流式回调的会话守卫：切换会话后，旧会话在途 Run 的事件不再写入当前界面 */
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;
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
        setExecutingTrace([]);
        const sid = sessionId;
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
            // 兜底：服务端返回整段 ok 包络结果（如旧后端），按最终消息消费
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
            if (sid !== sessionIdRef.current) return;
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
          // Run 已结束：清空执行中 trace，过程面板回落到最终消息的 toolTrace（状态均已定稿），
          // 避免开头占位的 running 事件永远挂着导致 spinner 不消失
          setExecutingTrace([]);
          abortRef.current = null;
        }
      },
    },
    { initialMessages },
  );
  runtimeRef.current = runtime;

  /* 会话内全部产出文件（assistant 消息 files，按出现顺序去重）→ 右侧产出文件栏 */
  const sessionFiles = useMemo(() => {
    const seen = new Set<string>();
    const out: OutputFile[] = [];
    for (const m of messages) {
      for (const f of m.files ?? []) {
        if (f.id && !seen.has(f.id)) {
          seen.add(f.id);
          out.push(f);
        }
      }
    }
    return out;
  }, [messages]);

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
          setProjectName(first.projectName ?? "");
        }
      })
      .catch(() => {});
    api
      .get<{ items: { id: string; name: string }[] }>("/api/v1/projects")
      .then((data) =>
        // 下拉用固定顺序：按名称中文拼音排序（后端按 updated 展示串排序，对选择器无意义）
        setProjects(
          [...data.items].sort((a, b) =>
            a.name.localeCompare(b.name, "zh-CN"),
          ),
        ),
      )
      .catch(() => {});
  }, []);

  useEffect(() => {
    // 切换/清空会话：线程状态全部复位，避免上一会话的执行过程/提示串显到当前会话
    setExecutingTrace([]);
    setStopped(false);
    setLastCompacted(false);
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
          project_name: projectName,
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
      setSessionQuery("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建会话失败");
    }
  };

  const selectSession = (session: PersistedSession) => {
    setSessionId(session.id);
    setExpertId(session.expertId ?? "");
    setProviderModelId(session.providerModelId ?? "");
    setProjectName(session.projectName ?? "");
  };
  /** 更新当前会话的 Expert/模型/项目绑定（保留会话选中，不新建/不清空）。 */
  const patchSessionBinding = async (body: {
    expert_id?: string;
    provider_model_id?: string;
    project_name?: string;
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
      setProjectName(data.session.projectName ?? "");
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
            project_name: projectName,
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
          { provider_model_id: value, project_name: projectName, title: "FlowHub 默认对话" },
        );
        setSessions((current) => [data.session, ...current]);
        setSessionId(data.session.id);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "创建会话失败");
      }
    }
  };
  /** 切换项目：有会话 → 更新绑定；无会话 → 带项目新建默认会话并选中。
   *  绑定后，后端会为该项目注入绑定仓库的"地图层"上下文（目录/README/依赖清单）。 */
  const changeProject = async (value: string) => {
    setProjectName(value);
    if (sessionId) {
      await patchSessionBinding({ project_name: value });
    } else if (value) {
      try {
        const data = await api.post<{ session: PersistedSession }>(
          "/api/v1/expert-chat/sessions",
          { provider_model_id: providerModelId, project_name: value, title: "FlowHub 默认对话" },
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
        setProjectName(next?.projectName ?? "");
        setMessages([]);
        setMessagesVersion((v) => v + 1);
      }
      toast.success("会话已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    }
  };
  /** 重新生成：重发当前会话最后一条用户消息 */
  const regenerate = () => {
    if (isRunning || !sessionId) return;
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    if (!lastUser) { toast.error("没有可重新生成的消息"); return; }
    runtimeRef.current?.thread.append({
      role: "user",
      content: [{ type: "text", text: lastUser.content }],
    });
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

  /** 会话搜索 + 时间分组：updated 含「:」视为今天（HH:MM / 刚刚），否则归入更早 */
  const filteredSessions = sessions.filter(
    (s) => !sessionQuery || s.title.toLowerCase().includes(sessionQuery.toLowerCase()),
  );
  const todaySessions = filteredSessions.filter((s) => s.updated.includes(":"));
  const earlierSessions = filteredSessions.filter((s) => !s.updated.includes(":"));
  const renderSessionItem = (session: PersistedSession) => (
    <div
      key={session.id}
      className={cn(
        "group relative mb-0.5 w-full rounded-lg px-3 py-2.5 text-left transition-colors",
        session.id === sessionId
          ? "bg-white shadow-sm dark:bg-slate-800"
          : "hover:bg-white/70 dark:hover:bg-slate-800/60",
      )}
    >
      <button className="block w-full text-left" onClick={() => selectSession(session)}>
        <b className={cn("block truncate text-xs", session.id === sessionId ? "text-slate-800 dark:text-slate-100" : "text-slate-600 dark:text-slate-300")}>
          {session.title}
        </b>
        <span className="text-[10px] text-slate-400">{session.updated}</span>
      </button>
      <div className="absolute right-2 top-2 hidden gap-1 group-hover:flex">
        <button
          title="清空消息（保留会话）"
          className="rounded p-1 text-slate-400 hover:bg-slate-200/70 hover:text-blue-600 dark:hover:bg-slate-700"
          onClick={(event) => {
            event.stopPropagation();
            void clearSession(session.id);
          }}
        >
          <Eraser className="h-3 w-3" />
        </button>
        <button
          title="删除会话"
          className="rounded p-1 text-slate-400 hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-500/10"
          onClick={(event) => {
            event.stopPropagation();
            void deleteSession(session.id);
          }}
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>
    </div>
  );
  const sessionGroup = (label: string, items: PersistedSession[]) =>
    items.length > 0 && (
      <div key={label} className="mb-2">
        <div className="px-3 pb-1 pt-1 text-[10px] font-medium uppercase tracking-wide text-slate-400">{label}</div>
        {items.map(renderSessionItem)}
      </div>
    );

  const expertName =
    activeSession?.expertVersionId && !activeSession.deploymentId
      ? (availableExperts.find((item) => item.id === activeSession.expertId)?.name ?? "草稿版本")
      : (availableExperts.find((item) => item.id === expertId)?.name ?? "");
  const subtitle = activeSession?.expertVersionId && !activeSession.deploymentId
    ? `${expertName} · 版本测试会话（发布前对话测试）`
    : (expertName ||
      (providerModelId ? "FlowHub 默认能力 + 已选模型" : "FlowHub 默认能力：任务、工作项、流程状态查询"));

  return (
    <DocPreviewContext.Provider value={{ openPreview }}>
    <div className="flex h-[calc(100vh-60px)] min-h-[620px] overflow-hidden bg-slate-50 dark:bg-slate-950">
      <aside className="hidden w-[270px] flex-none flex-col border-r border-slate-200 bg-slate-50 lg:flex dark:border-slate-800 dark:bg-slate-900/60">
        <div className="flex items-center justify-between px-4 py-3">
          <b className="text-[13px]">AiChat</b>
          <button
            className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600 text-white transition-colors hover:bg-blue-700"
            onClick={() => void newSession()}
            aria-label="新建会话"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>
        <div className="px-3 pb-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
            <input
              className="h-8 w-full rounded-lg border border-slate-200 bg-white pl-8 pr-2 text-xs outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
              placeholder="搜索会话…"
              value={sessionQuery}
              onChange={(event) => setSessionQuery(event.target.value)}
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto px-2 pb-3">
          {sessionGroup("今天", todaySessions)}
          {sessionGroup("更早", earlierSessions)}
          {!filteredSessions.length && (
            <p className="p-4 text-xs text-slate-400">
              {sessionQuery ? "无匹配会话" : "可直接创建 FlowHub 默认对话，或先选择 Expert。"}
            </p>
          )}
        </div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col bg-white dark:bg-slate-950">
        <header className="flex min-h-[60px] items-center gap-3 border-b border-slate-200 px-5 dark:border-slate-800">
          <span className="flex h-9 w-9 flex-none items-center justify-center rounded-xl bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-sm">
            <Bot className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-1.5">
              <b className="block truncate text-[13px] text-slate-900 dark:text-slate-100">
                {activeSession?.title ?? "新会话"}
              </b>
              {activeSession && (
                <button
                  className="rounded p-0.5 text-slate-300 hover:bg-slate-100 hover:text-blue-600 dark:hover:bg-slate-800"
                  title="重命名"
                  onClick={() => {
                    setRenameValue(activeSession.title);
                    setRenameOpen(true);
                  }}
                >
                  <Pencil className="h-3 w-3" />
                </button>
              )}
              {activeSession && (
                <button
                  className="rounded p-0.5 text-slate-300 hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-500/10"
                  title="删除会话"
                  onClick={() => void deleteSession(activeSession.id)}
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </div>
            <span className="block truncate text-[10.5px] text-slate-400">{subtitle}</span>
          </div>
          <div className="flex flex-none items-center gap-2">
            <label className="relative inline-flex items-center">
              <FolderGit2 className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-emerald-500" />
              <select
                value={projectName}
                onChange={(event) => {
                  void changeProject(event.target.value);
                }}
                className="h-8 max-w-[190px] rounded-lg border border-slate-200 bg-white pl-7 pr-2 text-xs text-slate-600 outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                aria-label="选择项目"
              >
                <option value="">不关联项目</option>
                {projects.map((item) => (
                  <option key={item.id} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="relative inline-flex items-center">
              <Bot className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-violet-500" />
              <select
                value={expertId}
                onChange={(event) => {
                  void changeExpert(event.target.value);
                }}
                className="h-8 max-w-[190px] rounded-lg border border-slate-200 bg-white pl-7 pr-2 text-xs text-slate-600 outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                aria-label="选择 Expert"
              >
                <option value="">不使用 Expert</option>
                {availableExperts.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="relative inline-flex items-center">
              <Cpu className="pointer-events-none absolute left-2 h-3.5 w-3.5 text-blue-500" />
              <select
                value={providerModelId}
                onChange={(event) => {
                  void changeModel(event.target.value);
                }}
                className="h-8 max-w-[190px] rounded-lg border border-slate-200 bg-white pl-7 pr-2 text-xs text-slate-600 outline-none focus:border-blue-400 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                aria-label="选择模型"
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
        <div className="flex min-h-0 flex-1">
        <AssistantRuntimeProvider
          key={`${sessionId || "empty"}-${messagesVersion}`}
          runtime={runtime}
        >
          <ThreadPrimitive.Root className="relative flex min-h-0 flex-1 flex-col">
            <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-5 py-6">
              <div className="mx-auto max-w-[820px]">
                <ThreadPrimitive.Messages
                  components={{
                    UserMessage: UserMessage,
                    AssistantMessage: AssistantMessage,
                  }}
                />
                {/* 过程面板：最近一次运行的工具/Skill 调用（实时更新），默认折叠 */}
                {latestTrace.length > 0 && (
                  <TracePanel trace={latestTrace} onRegenerate={regenerate} />
                )}
                {/* 流式状态内联条：随消息流滚动，不遮挡输入区 */}
                {isRunning && (
                  <div className="mb-4 flex items-center gap-2 rounded-lg px-1 py-1 text-[12px] text-slate-400">
                    <LoaderCircle className="h-3.5 w-3.5 animate-spin text-blue-500" />
                    <span>
                      {executingTrace.length
                        ? `正在执行：${executingTrace[executingTrace.length - 1].tool}…`
                        : "正在思考…"}
                    </span>
                  </div>
                )}
                {lastCompacted && (
                  <div className="mb-4 rounded-lg bg-violet-50 px-3 py-2 text-[11.5px] text-violet-600 dark:bg-violet-500/10 dark:text-violet-300">
                    🧠 会话较长，早期对话已自动压缩（保留最近 6 轮全文），模型仍可基于摘要理解前文。
                  </div>
                )}
                {stopped && (
                  <div className="mb-4 rounded-lg bg-slate-50 px-3 py-2 text-[11.5px] text-slate-500 dark:bg-slate-900 dark:text-slate-400">
                    已停止生成（完整内容已保存，刷新或切换会话可查看）。
                  </div>
                )}
                <ThreadPrimitive.Empty>
                  <div className="mt-14 flex flex-col items-center text-center">
                    <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-violet-500 to-blue-500 text-white shadow-md">
                      <Bot className="h-6 w-6" />
                    </span>
                    <h2 className="mt-4 text-[16px] font-semibold text-slate-800 dark:text-slate-100">
                      {activeDeployment ? "向 Expert 描述你的工作" : "有什么可以帮你？"}
                    </h2>
                    <p className="mt-1.5 max-w-md text-[12.5px] leading-relaxed text-slate-400">
                      {activeDeployment
                        ? providerModelId
                          ? "消息将创建真实 Expert LangGraph Run，并使用所选模型。"
                          : "消息将创建真实 Expert LangGraph Run。"
                        : providerModelId
                          ? "默认能力会携带只读业务上下文交给所选模型推理。"
                          : "可查询项目、任务、问题和流程状态；也可在右上角选择模型进行推理。"}
                    </p>
                    {sessionId && (
                      <div className="mt-5 flex flex-wrap justify-center gap-2">
                        {WELCOME_SUGGESTIONS.map((text) => (
                          <button
                            key={text}
                            className="rounded-full border border-slate-200 px-3.5 py-1.5 text-[12px] text-slate-600 transition-colors hover:border-blue-400 hover:text-blue-600 dark:border-slate-700 dark:text-slate-300 dark:hover:border-blue-500"
                            onClick={() =>
                              runtimeRef.current?.thread.append({
                                role: "user",
                                content: [{ type: "text", text }],
                              })
                            }
                          >
                            {text}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </ThreadPrimitive.Empty>
              </div>
            </ThreadPrimitive.Viewport>
            <ThreadPrimitive.ScrollToBottom asChild>
              <button
                className="absolute bottom-[150px] right-6 z-10 flex h-8 w-8 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 shadow-md transition-colors hover:text-blue-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"
                aria-label="回到底部"
              >
                <ArrowDown className="h-4 w-4" />
              </button>
            </ThreadPrimitive.ScrollToBottom>
            <div className="border-t border-slate-100 p-4 dark:border-slate-800">
              <div className="mx-auto max-w-[820px]">
                <ComposerPrimitive.Root className="flex w-full flex-col rounded-2xl border border-slate-300 bg-white p-2 shadow-sm transition-colors focus-within:border-blue-500 focus-within:ring-[3px] focus-within:ring-blue-500/10 dark:border-slate-700 dark:bg-slate-900">
                  <ComposerPrimitive.Input
                    placeholder={
                      sessionId
                        ? "询问 FlowHub 或向 Expert 描述工作…"
                        : "点击左侧 + 创建会话"
                    }
                    disabled={!sessionId || isRunning}
                    className="max-h-32 min-h-10 w-full flex-1 resize-none bg-transparent px-2 py-2 text-sm outline-none dark:text-slate-200"
                  />
                  <div className="flex items-center justify-between px-2 pt-1">
                    <span className="text-[10.5px] text-slate-400">
                      Enter 发送 · Shift+Enter 换行
                      {expertName ? ` · ${expertName}` : ""}
                      {projectName ? ` · 项目 ${projectName}` : ""}
                      {providerModelId
                        ? ` · ${providerModels.find((m) => m.id === providerModelId)?.model ?? ""}`
                        : ""}
                    </span>
                    {isRunning ? (
                      <button
                        onClick={stopGeneration}
                        className="flex h-8 items-center gap-1.5 rounded-lg bg-red-500 px-3 text-xs font-medium text-white transition-colors hover:bg-red-600"
                        title="停止生成"
                      >
                        <Square className="h-3 w-3 fill-current" />
                        停止
                      </button>
                    ) : (
                      <ComposerPrimitive.Send
                        disabled={!sessionId}
                        className="flex h-8 items-center gap-1.5 rounded-lg bg-blue-600 px-3.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
                      >
                        <Send className="h-3.5 w-3.5" />
                        发送
                      </ComposerPrimitive.Send>
                    )}
                  </div>
                </ComposerPrimitive.Root>
              </div>
            </div>
          </ThreadPrimitive.Root>
        </AssistantRuntimeProvider>
        {/* 右侧产出文件栏：本次会话内 AI 生成的文档，可下载/预览 */}
        {sessionFiles.length > 0 && (
          <aside className="hidden w-[260px] flex-none flex-col overflow-y-auto border-l border-slate-200 bg-slate-50/70 p-3 lg:flex dark:border-slate-800 dark:bg-slate-900/60">
            <div className="mb-2 flex items-center justify-between px-1">
              <b className="text-[12.5px] font-semibold text-slate-600 dark:text-slate-300">产出文件</b>
              <Badge tone="info">{sessionFiles.length}</Badge>
            </div>
            <div className="space-y-2">
              {sessionFiles.map((f) => (
                <div key={f.id} className="rounded-lg border border-slate-200 bg-white p-2.5 dark:border-slate-700 dark:bg-slate-900">
                  <div className="truncate text-[12px] font-medium text-slate-700 dark:text-slate-200" title={f.filename}>{f.filename}</div>
                  <div className="mt-0.5 text-[10.5px] text-slate-400">{f.mimeType}</div>
                  <div className="mt-2 flex gap-1.5">
                    <button
                      className="flex-1 rounded-md bg-blue-600 px-2 py-1 text-[11px] font-medium text-white transition-colors hover:bg-blue-700"
                      onClick={() => {
                        const token = getToken();
                        void fetch(f.downloadUrl, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
                          .then((r) => { if (!r.ok) throw new Error("文件下载失败"); return r.blob(); })
                          .then((blob) => {
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = f.filename;
                            a.click();
                            URL.revokeObjectURL(url);
                          })
                          .catch(() => toast.error("文件下载失败"));
                      }}
                    >
                      下载
                    </button>
                    <button
                      className="flex-1 rounded-md border border-blue-200 px-2 py-1 text-[11px] font-medium text-blue-600 transition-colors hover:bg-blue-50 dark:border-blue-500/30 dark:text-blue-300 dark:hover:bg-blue-500/10"
                      onClick={() => openPreview({ files: sessionFiles.map((x) => ({ id: x.id, name: x.filename, srcUrl: x.downloadUrl })), initialId: f.id })}
                    >
                      预览
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </aside>
        )}
        </div>
        {renameOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/30 p-4">
            <div className="w-full max-w-sm rounded-xl border border-slate-200 bg-white p-5 shadow-xl dark:border-slate-700 dark:bg-slate-900">
              <b className="text-sm text-slate-800 dark:text-slate-100">重命名会话</b>
              <input
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                className="mt-3 h-9 w-full rounded-lg border border-slate-300 px-3 text-sm outline-none focus:border-blue-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
                autoFocus
                onKeyDown={(event) => {
                  if (event.key === "Enter") void renameSession();
                }}
              />
              <div className="mt-4 flex justify-end gap-2">
                <button
                  className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
                  onClick={() => setRenameOpen(false)}
                >
                  取消
                </button>
                <button
                  className="rounded-lg bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-700"
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
    <MessagePrimitive.Root className="mb-6 flex justify-end">
      <div className="max-w-[80%] rounded-2xl rounded-br-md bg-blue-50 px-4 py-2.5 text-[14px] leading-relaxed text-slate-800 dark:bg-blue-500/15 dark:text-blue-50">
        <MessagePrimitive.Content />
      </div>
    </MessagePrimitive.Root>
  );
}

/** AI 回复：无气泡全宽 Markdown + 头像行 + 复制操作 */
function AssistantMessage() {
  const [copied, setCopied] = useState(false);
  const textRef = useRef("");
  const capture = (text: string) => { textRef.current = text; };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(textRef.current);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("复制失败");
    }
  };
  return (
    <MessagePrimitive.Root className="mb-6 flex gap-3">
      <span className="mt-0.5 flex h-7 w-7 flex-none items-center justify-center rounded-lg bg-gradient-to-br from-violet-500 to-blue-500 text-white">
        <Bot className="h-3.5 w-3.5" />
      </span>
      <div className="min-w-0 flex-1 text-[14px] text-slate-700 dark:text-slate-200 group/msg">
        <TextCaptureContext.Provider value={{ capture }}>
          <MessagePrimitive.Content
            components={{ Text: AssistantText, File: OutputFile }}
          />
        </TextCaptureContext.Provider>
        <div className="mt-1 flex items-center gap-1 opacity-0 transition-opacity group-hover/msg:opacity-100">
          <button
            className="rounded p-1 text-slate-300 hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800"
            title="复制"
            onClick={() => void copy()}
          >
            {copied ? <Check className="h-3.5 w-3.5 text-emerald-500" /> : <Copy className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

/** 最近一次运行的工具/Skill 过程：默认折叠一行，可展开明细；附「重新生成」 */
function TracePanel({ trace, onRegenerate }: { trace: TraceItem[]; onRegenerate: () => void }) {
  const [open, setOpen] = useState(false);
  const running = trace.some((item) => item.status === "running");
  return (
    <div className="mb-6 flex gap-3">
      <span className="mt-0.5 flex h-7 w-7 flex-none items-center justify-center">
        <Wrench className="h-3.5 w-3.5 text-slate-300 dark:text-slate-600" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <button
            className="flex items-center gap-1.5 text-[11.5px] text-slate-400 transition-colors hover:text-blue-600"
            onClick={() => setOpen((v) => !v)}
          >
            {running
              ? <><LoaderCircle className="h-3 w-3 animate-spin text-amber-500" />正在执行工具/Skill…</>
              : <>已执行 {trace.length} 次工具/Skill</>}
            <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} />
          </button>
          <button
            className="rounded p-1 text-slate-300 hover:bg-slate-100 hover:text-blue-600 dark:hover:bg-slate-800"
            title="重新生成（重发上一条消息）"
            onClick={onRegenerate}
          >
            <RotateCcw className="h-3.5 w-3.5" />
          </button>
        </div>
        {open && (
          <div className="mt-1.5 space-y-1 rounded-lg bg-slate-50 px-3 py-2 dark:bg-slate-900">
            {trace.map((item, index) => (
              <div key={`${item.tool}-${index}`} className="flex items-start gap-1.5 text-[11px] leading-5">
                <span
                  className={cn(
                    "mt-1.5 h-1.5 w-1.5 flex-none rounded-full",
                    item.status === "failed" ? "bg-red-400" : item.status === "running" ? "bg-amber-400" : "bg-emerald-400",
                  )}
                />
                <span className="font-medium text-slate-600 dark:text-slate-300">{item.tool}</span>
                <span className="truncate text-slate-400">
                  {typeof item.summary === "string" ? item.summary : JSON.stringify(item.summary)}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function AssistantText(props: TextMessagePartProps) {
  const { capture } = useContext(TextCaptureContext);
  useEffect(() => { capture(props.text); }, [props.text, capture]);
  return <AssistantMarkdown />;
}
