#!/usr/bin/env python3
"""生成《FlowHub 企业服务案例介绍》单文件 HTML（A4 分页，可浏览器打印为 PDF）。

素材：doc-assets/raw/*.png（客户部署实例真实界面截图，已做列级/行内脱敏）
输出：FlowHub企业服务案例介绍.html
"""
import base64
import io
import pathlib
import re

from PIL import Image

ROOT = pathlib.Path("/Users/sli/projects/ai/flowhub")
RAW = ROOT / "doc-assets" / "raw"
OUT = ROOT / "FlowHub企业服务案例介绍.html"

# ---------------------------------------------------------------- 图片内嵌


# 所有截图统一按同一宽度出图：文档里不存在"大小不一"的图，
# 细节靠点击放大（lightbox）解决，而不是靠把某几张图单独放大。
#
# 截图源为 2 倍屏采集（3200×1800），嵌到 A4 上按 100% 全宽渲染（≈180mm），
# 2600px 宽 → 有效约 367 dpi，满足印刷 300 dpi 要求。改小会直接掉清晰度。
IMG_W = 2600
IMG_Q = 82


def embed(name: str, width: int = IMG_W, quality: int = IMG_Q) -> str:
    p = RAW / f"{name}.png"
    im = Image.open(p).convert("RGB")
    if im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    data = base64.b64encode(buf.getvalue()).decode()
    print(f"  {name:22s} {len(data)//1024:5d} KB(b64)")
    return f"data:image/jpeg;base64,{data}"


def fig(name, caption):
    """单图块，固定全宽（唯一尺寸）。点击可放大。"""
    return (f'<figure><img src="{embed(name)}" alt="">'
            f"<figcaption>{caption}</figcaption></figure>")


# ---------------------------------------------------------------- 流程图 SVG

NODE_COLORS = {
    "start": ("#dcfce7", "#16a34a", "#14532d"),
    "task": ("#dbeafe", "#2563eb", "#1e3a8a"),
    "decision": ("#fef3c7", "#d97706", "#78350f"),
    "parallel": ("#ede9fe", "#7c3aed", "#4c1d95"),
    "acceptance": ("#ccfbf1", "#0d9488", "#134e4a"),
    "closure": ("#e2e8f0", "#475569", "#1e293b"),
    "end": ("#ffe4e6", "#e11d48", "#881337"),
    "ext": ("#fce7f3", "#db2777", "#831843"),
}


ACTOR_COLORS = {
    "ai": "#db2777",
    "human": "#2563eb",
    "sys": "#94a3b8",
    "ext": "#7c3aed",
}
ACTOR_LEGEND = [("ai", "AI 介入"), ("human", "人执行 / 人担责"),
                ("sys", "系统自动"), ("ext", "外部智能体")]


def _actor(item):
    """第 4 项为 (kind, text) 时才返回，否则 None。"""
    return item[3] if len(item) > 3 and item[3] else None


def flow_svg(nodes, returns=(), width=1010, legend=True):
    """节点流程条 —— 每个环节下方标注「谁在做这件事」。

    nodes: [(label, sub, type)] 或 [(label, sub, type, (kind, text))]
           kind ∈ {ai, human, sys, ext}；AI 环节用粗描边 + 粉色标签突出。
    returns: [(from_idx, to_idx, label)] 虚线回退路径。
    高度按内容自适应（不再需要外部传 height）。
    """
    n = len(nodes)
    gap = 14
    bw = (width - 40 - gap * (n - 1)) / n
    bh = 78
    top = 38 if legend else 16
    bottom = top + bh + (30 if returns else 6)
    height = bottom + 8

    s = [f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="PingFang SC,Microsoft YaHei,sans-serif">']
    s.append('<defs><marker id="ar" markerWidth="9" markerHeight="9" refX="7" refY="3" '
             'orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#94a3b8"/></marker>'
             '<marker id="arr" markerWidth="9" markerHeight="9" refX="7" refY="3" '
             'orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#e11d48"/></marker></defs>')

    # ---- 图例（右上角，只列出本图实际出现的角色）----
    if legend:
        seen = []
        for it in nodes:
            a = _actor(it)
            if a and a[0] not in seen:
                seen.append(a[0])
        items = [(k, t) for k, t in ACTOR_LEGEND if k in seen]
        ws = [len(t) * 9.6 + 22 for _, t in items]
        x0 = width - 20 - sum(ws)
        for (k, t), w in zip(items, ws):
            s.append(f'<circle cx="{x0+4:.1f}" cy="{top-24}" r="3.5" fill="{ACTOR_COLORS[k]}"/>')
            s.append(f'<text x="{x0+12:.1f}" y="{top-20}" font-size="9.5" fill="#94a3b8">{t}</text>')
            x0 += w
        s.append(f'<text x="20" y="{top-20}" font-size="9.5" fill="#94a3b8">'
                 f'环节中的 AI 介入点</text>')

    centers = []
    fs = 9.2 if bw > 128 else 8.6
    for i, item in enumerate(nodes):
        label, sub, ntype = item[0], item[1], item[2]
        a = _actor(item)
        is_ai = bool(a) and a[0] in ("ai", "ext")
        x = 20 + i * (bw + gap)
        centers.append(x + bw / 2)
        fill, stroke, text = NODE_COLORS.get(ntype, NODE_COLORS["task"])
        s.append(f'<rect x="{x:.1f}" y="{top}" width="{bw:.1f}" height="{bh}" rx="9" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{2.1 if is_ai else 1.4}"/>')
        s.append(f'<text x="{x + bw/2:.1f}" y="{top+26}" text-anchor="middle" font-size="13.5" '
                 f'font-weight="600" fill="{text}">{label}</text>')
        if sub:
            s.append(f'<text x="{x + bw/2:.1f}" y="{top+45}" text-anchor="middle" font-size="10.5" '
                     f'fill="{text}" opacity="0.72">{sub}</text>')
        if a:
            k, t = a
            col = ACTOR_COLORS.get(k, "#64748b")
            tw = len(t) * fs * 0.92
            s.append(f'<circle cx="{x + bw/2 - tw/2 - 7:.1f}" cy="{top+62.6:.1f}" r="3.1" fill="{col}"/>')
            s.append(f'<text x="{x + bw/2 - tw/2:.1f}" y="{top+66}" font-size="{fs}" '
                     f'font-weight="600" fill="{col}">{t}</text>')
        if i < n - 1:
            x2 = 20 + (i + 1) * (bw + gap)
            s.append(f'<line x1="{x + bw:.1f}" y1="{top + bh/2}" x2="{x2 - 3:.1f}" y2="{top + bh/2}" '
                     f'stroke="#94a3b8" stroke-width="1.5" marker-end="url(#ar)"/>')
    if returns:
        yb = top + bh + 24
        for a, b, lab in returns:
            xa, xb = centers[a], centers[b]
            s.append(f'<path d="M {xa:.1f} {top+bh} L {xa:.1f} {yb} L {xb:.1f} {yb} L {xb:.1f} {top+bh+4}" '
                     f'fill="none" stroke="#e11d48" stroke-width="1.4" stroke-dasharray="5 4" '
                     f'marker-end="url(#arr)"/>')
            s.append(f'<text x="{(xa+xb)/2:.1f}" y="{yb - 6}" text-anchor="middle" font-size="10.5" '
                     f'fill="#e11d48">{lab}</text>')
    s.append("</svg>")
    return "\n".join(s)


def build_arch_svg():
    """能力分层架构图：六个横向切面，每层标注它回答什么问题。"""
    layers = [
        ("接入层", "#2563eb", "#eff6ff", "#dbeafe",
         [("Web 工作台", "流程 · 任务 · 看板 · 审计"),
          ("AiChat 对话", "按 Expert 找专家"),
          ("外部智能体接入", "MCP + Access Key"),
          ("通知渠道", "站内 · 钉钉 · 企微 · 邮件")],
         "这一层回答：人与外部系统从哪里进来，进来的是不是同一个身份"),
        ("知识与记忆层", "#0d9488", "#f0fdfa", "#99f6e4",
         [("知识库", "集合 · 文档 · 分块 · 溯源"),
          ("文档中心", "校验链 · 版本 · 归档"),
          ("Memory", "按 Expert / 任务 / 运行分域"),
          ("代码仓库镜像", "只读检索 · 附定位引用")],
         "这一层回答：AI 凭什么做判断、记得住什么、产出沉淀到哪里"),
        ("智能能力层", "#7c3aed", "#f5f3ff", "#ddd6fe",
         [("Expert / Skill 注册", "版本化发布，发布后不可改"),
          ("Deployment 挂载", "挂到流程节点上执行"),
          ("Provider 多模型", "按 vendor 接入，含国产模型"),
          ("MCP 工具中心", "注册 · 健康检查 · 风险分级")],
         "这一层回答：AI 具备什么能力、用的是哪个版本、接的是哪些模型与工具"),
        ("流程运行层", "#0891b2", "#ecfeff", "#a5f3fc",
         [("画布建模", "开始 / 任务 / 决策 / 并行 …"),
          ("版本化模板", "draft → published"),
          ("工作项与 SLA", "状态 · 计时 · 超时预警"),
          ("退回 / 转办 / 认领", "任务级操作与留痕")],
         "这一层回答：一件事怎么走、现在走到哪、卡在谁手上"),
        ("治理与安全层", "#e11d48", "#fff1f2", "#fecdd3",
         [("组织与权限矩阵", "角色 × 权限点，支持继承"),
          ("风险闸控", "只读放行，写操作转人工"),
          ("全量审计留痕", "不可删改，导出再审计"),
          ("密钥与访问控制", "Access Key 分发与吊销")],
         "这一层回答：谁有权做什么、什么必须人批、做过的事能不能查"),
        ("基础设施层", "#0f172a", "#f8fafc", "#e2e8f0",
         [("PostgreSQL 主库", "流程、任务、审计数据"),
          ("对象存储", "文档与 AI 产出物"),
          ("Redis 缓存", "队列、锁与实时状态"),
          ("私有化 Docker 部署", "内网自持，数据不出域")],
         "这一层回答：数据放在哪、能不能私有化、能不能容灾"),
    ]
    H = 34 + len(layers) * 78 + 6
    s = [f'<svg viewBox="0 0 1010 {H}" xmlns="http://www.w3.org/2000/svg" '
         'font-family="PingFang SC,Microsoft YaHei,sans-serif">']
    s.append('<rect x="0" y="0" width="1010" height="%d" rx="12" fill="#ffffff"/>' % H)
    s.append('<text x="18" y="22" font-size="12.5" font-weight="700" fill="#0f172a" '
             'letter-spacing="0.4">能力分层架构 · 全景</text>')
    s.append('<text x="992" y="22" text-anchor="end" font-size="10" fill="#7c8798">'
             '自上而下六层，每层回答一个具体问题</text>')
    y = 34
    for name, accent, fill, stroke, cards, note in layers:
        s.append(f'<rect x="18" y="{y}" width="76" height="54" rx="9" fill="{accent}"/>')
        s.append(f'<text x="56" y="{y+31}" text-anchor="middle" font-size="11" font-weight="700" '
                 f'fill="#ffffff">{name}</text>')
        cw = (894 - 8 * (len(cards) - 1)) / len(cards)
        for i, (t, sub) in enumerate(cards):
            cx = 100 + i * (cw + 8)
            s.append(f'<rect x="{cx:.1f}" y="{y}" width="{cw:.1f}" height="54" rx="9" '
                     f'fill="{fill}" stroke="{stroke}"/>')
            s.append(f'<text x="{cx+13:.1f}" y="{y+24}" font-size="10.8" font-weight="700" '
                     f'fill="#0f172a">{t}</text>')
            s.append(f'<text x="{cx+13:.1f}" y="{y+42}" font-size="9" fill="#5b6b7f">{sub}</text>')
        s.append(f'<rect x="18" y="{y+58}" width="976" height="18" rx="5" fill="#f1f5f9"/>')
        s.append(f'<text x="30" y="{y+71}" font-size="9.4" fill="#64748b">{note}</text>')
        y += 78
    s.append("</svg>")
    return "\n".join(s)


def build_journey_svg():
    """一次任务的完整链路（七个环节，含外部智能体接力）+ 第八步：反哺沉淀。"""
    rows = [
        ("业务发起", "人", "#2563eb",
         "在系统里提交需求 / 问题 / 申请，附上背景材料与附件",
         "一条业务申请", "工作项进入台账"),
        ("节点派发", "系统", "#0891b2",
         "按角色或技能匹配候选人，推送待办并开始 SLA 计时",
         "待办 + 通知 + 计时器", "任务队列与处理人"),
        ("AI 起草", "内部 Expert", "#db2777",
         "Expert 汇总节点上下文与知识库，产出草案并附引用来源",
         "草案 + 知识库引用", "一次 Run 与完整 Trace"),
        ("外部接力", "外部智能体", "#7c3aed",
         "企业已有的 Dify / 自研智能体凭 Access Key 接管本节点处理",
         "外部产出 + 来源引用", "回到本节点，同套留痕"),
        ("风险闸控", "系统", "#e11d48",
         "按工具风险分级判定：只读直接执行，写操作强制中断",
         "放行 / 中断待批", "审批队列（含运行上下文）"),
        ("人签署", "人", "#2563eb",
         "在同一屏看到 AI 产出与引用，确认、修改或退回重做",
         "批准（恢复执行一次）", "审批记录，进入审计"),
        ("闭环留痕", "系统", "#0f172a",
         "记录人与 AI 的每一个动作，不可删改，导出再审计",
         "可回溯的完整链路", "审计中心"),
    ]
    feed = [
        ("① 产出归档", "→ 文档中心", "草案、修订与附件成为可复用文档资产"),
        ("② 知识沉淀", "→ 知识库", "本次的判断依据与结论进入集合，可检索引用"),
        ("③ 记忆更新", "→ Memory", "上下文与处理偏好按命名空间留存，不必从头讲"),
        ("④ 策略调优", "→ 模板 / Skill", "卡点与返工沉淀为新版本，而非热改线上"),
    ]
    H = 490
    s = ['<svg viewBox="0 0 1010 %d" xmlns="http://www.w3.org/2000/svg" '
         'font-family="PingFang SC,Microsoft YaHei,sans-serif">' % H]
    s.append('<rect x="0" y="0" width="1010" height="%d" rx="12" fill="#ffffff"/>' % H)
    s.append('<text x="18" y="22" font-size="12.5" font-weight="700" fill="#0f172a" '
             'letter-spacing="0.4">一次任务的完整旅程 · 从发起到反哺</text>')
    s.append('<text x="992" y="22" text-anchor="end" font-size="10" fill="#7c8798">'
             '前七步把事办完，第八步把它变成下一次的起点</text>')
    for x, lab in [(18, "阶段"), (158, "谁在动"), (268, "这一步发生什么"),
                   (556, "产出"), (758, "落到哪")]:
        s.append(f'<text x="{x}" y="46" font-size="9.5" font-weight="700" fill="#94a3b8" '
                 f'letter-spacing="0.6">{lab}</text>')
    for i, (name, actor, accent, what, out, land) in enumerate(rows):
        y = 56 + i * 46
        if i < len(rows) - 1:
            s.append(f'<line x1="32" y1="{y+30}" x2="32" y2="{y+46}" stroke="#cbd5e1" '
                     f'stroke-width="1.4"/>')
        s.append(f'<rect x="18" y="{y}" width="976" height="40" rx="8" fill="#fbfcfe" '
                 f'stroke="#e9eef5"/>')
        s.append(f'<circle cx="32" cy="{y+20}" r="10" fill="{accent}"/>')
        s.append(f'<text x="32" y="{y+24}" text-anchor="middle" font-size="10.5" '
                 f'font-weight="700" fill="#ffffff">{i+1}</text>')
        s.append(f'<text x="50" y="{y+24}" font-size="10.5" font-weight="700" '
                 f'fill="#0f172a">{name}</text>')
        s.append(f'<circle cx="163" cy="{y+17}" r="3" fill="{accent}"/>')
        s.append(f'<text x="172" y="{y+21}" font-size="10" fill="#334155">{actor}</text>')
        s.append(f'<text x="268" y="{y+24}" font-size="10" fill="#334155">{what}</text>')
        s.append(f'<text x="556" y="{y+24}" font-size="10" fill="#334155">{out}</text>')
        s.append(f'<text x="758" y="{y+24}" font-size="10" fill="#334155">{land}</text>')
    # ---- 反哺区 ----
    s.append('<rect x="18" y="388" width="976" height="92" rx="10" fill="#0f172a"/>')
    s.append('<text x="36" y="412" font-size="11.4" font-weight="700" fill="#ffffff">'
             '⑧ 反哺沉淀 —— 任务关闭不是终点，这一次的产出是下一次的起点</text>')
    s.append('<text x="976" y="412" text-anchor="end" font-size="9.6" fill="#94a3b8">'
             '↺ 回流到环节 ①，下一轮同类任务起点已不同</text>')
    fw = (976 - 2 * 18 - 3 * 8) / 4
    for i, (t, arrow, desc) in enumerate(feed):
        fx = 36 + i * (fw + 8)
        s.append(f'<rect x="{fx:.1f}" y="422" width="{fw:.1f}" height="46" rx="7" '
                 f'fill="#ffffff" fill-opacity="0.07" stroke="#334155"/>')
        s.append(f'<text x="{fx+12:.1f}" y="440" font-size="9.8" font-weight="700" '
                 f'fill="#ffffff">{t}<tspan font-weight="400" fill="#94a3b8">　{arrow}</tspan></text>')
        s.append(f'<text x="{fx+12:.1f}" y="457" font-size="8.6" fill="#94a3b8">{desc}</text>')
    s.append("</svg>")
    return "\n".join(s)


def build_matrix_svg():
    """能力侧重矩阵：4 个方案 × 6 个维度。"""
    FULL, HALF, NONE = "full", "half", "none"
    cols = [
        ("传统流程流转", "OA · BPM 审批", 290.0, 179.0),
        ("Dify", "LLM 应用平台", 469.0, 179.0),
        ("n8n", "自动化集成平台", 648.0, 179.0),
        ("FlowHub", "流程协同 × AI 专家", 827.0, 179.0),
    ]
    rows = [
        ("流程实例与业务台账", [
            (FULL, "工作项 · 超时提醒"), (NONE, ""), (NONE, ""), (FULL, "工作项 + 节点 SLA"),
        ]),
        ("组织与权限矩阵", [
            (HALF, "角色审批人"), (HALF, "工作空间成员"), (HALF, "RBAC · 项目"), (FULL, "节点级业务权限"),
        ]),
        ("AI 资产的版本化", [
            (NONE, ""), (FULL, "应用 / 工作流版本"), (HALF, "工作流版本"), (FULL, "Expert / Skill 版本"),
        ]),
        ("人机闸控与责任签署", [
            (HALF, "人工审批节点"), (HALF, "Human Input 节点"), (HALF, "Agent 工具审批"), (FULL, "风险分级 + 中断恢复"),
        ]),
        ("审计与合规留痕", [
            (HALF, "流程日志"), (HALF, "AI 调用日志"), (HALF, "执行历史"), (FULL, "不可删改 + 导出审计"),
        ]),
        ("开放与集成生态", [
            (HALF, "定制集成"), (FULL, "插件 + MCP"), (FULL, "数千连接器"), (HALF, "MCP 双向 · 多模型"),
        ]),
    ]

    s = ['<svg viewBox="0 0 1010 310" xmlns="http://www.w3.org/2000/svg" '
         'font-family="PingFang SC,Microsoft YaHei,sans-serif">']
    # FlowHub 列高亮
    s.append('<rect x="827" y="0" width="179" height="302" rx="8" fill="#eff6ff"/>')
    s.append('<text x="16" y="24" font-size="11" font-weight="700" fill="#0f172a" '
             'letter-spacing="0.5">对比维度</text>')
    for name, sub, x, w in cols:
        cx = x + w / 2
        strong = name == "FlowHub"
        s.append(f'<text x="{cx:.1f}" y="19" text-anchor="middle" font-size="12" '
                 f'font-weight="700" fill="{"#1e3a8a" if strong else "#0f172a"}">{name}</text>')
        s.append(f'<text x="{cx:.1f}" y="33" text-anchor="middle" font-size="8.5" '
                 f'fill="#94a3b8">{sub}</text>')
    s.append('<line x1="4" y1="41" x2="1006" y2="41" stroke="#cbd5e1"/>')

    for i, (dim, cells) in enumerate(rows):
        cy = 60 + i * 44
        if i:
            s.append(f'<line x1="4" y1="{cy-22}" x2="1006" y2="{cy-22}" stroke="#eef2f7"/>')
        s.append(f'<text x="16" y="{cy+5}" font-size="11" fill="#334155">{dim}</text>')
        for (mark, label), (_, _, x, w) in zip(cells, cols):
            cx, sx = x + w / 2, x + w / 2 - 52
            if mark == FULL:
                s.append(f'<circle cx="{sx:.1f}" cy="{cy}" r="8" fill="#2563eb"/>')
            elif mark == HALF:
                s.append(f'<circle cx="{sx:.1f}" cy="{cy}" r="8" fill="#e2e8f0"/>')
                s.append(f'<path d="M {sx:.1f} {cy-8} A 8,8 0 0 0 {sx:.1f} {cy+8} Z" fill="#2563eb"/>')
            else:
                s.append(f'<circle cx="{sx:.1f}" cy="{cy}" r="7.5" fill="none" '
                         f'stroke="#cbd5e1" stroke-width="1.6"/>')
            if label:
                s.append(f'<text x="{sx+24:.1f}" y="{cy+4}" font-size="9.5" fill="#475569">{label}</text>')
    s.append("</svg>")
    return "\n".join(s)


def build_ai_locus_svg():
    """AI 站在流程的哪一侧：四个方案里，AI 与「业务台账 / 处理人 / SLA / 审计」
    这个责任边界的相对位置。左边虚线框 = 业务流程与责任边界，右边实线框 = AI 待的地方；
    两者之间的虚线箭头 = AI 的产出要跨过边界所付的代价。"""
    W, HEAD, RH = 1010, 46, 72
    NC = {
        "human": ("#dbeafe", "#2563eb", "#1e3a8a"),
        "ai": ("#fce7f3", "#db2777", "#831843"),
        "sys": ("#e2e8f0", "#475569", "#1e293b"),
    }
    rows = [
        dict(name="传统流程流转", sub="OA / BPM",
             nodes=[("提报", "human"), ("审批", "human"), ("归档", "sys")],
             ai=("流程之外的工具", "人自己开对话框，再复制粘贴进表单"),
             arrow="复制粘贴", wide=False),
        dict(name="Dify", sub="LLM 应用平台",
             nodes=[("提报", "human"), ("审批", "human"), ("归档", "sys")],
             ai=("Dify 运行空间", "一次 run，跑完即结束"),
             arrow="接口 / 人工搬", wide=False),
        dict(name="n8n", sub="自动化集成",
             nodes=[("提报", "human"), ("审批", "human"), ("归档", "sys")],
             ai=("n8n 执行引擎", "一条 execution 记录"),
             arrow="自动回写", wide=False),
        dict(name="FlowHub", sub="流程协同 × AI",
             nodes=[("提报", "human"), ("AI 起草", "ai"), ("风险闸控", "sys"),
                    ("人签署", "human"), ("归档留痕", "sys")],
             ai=None, arrow=None, wide=True),
    ]
    H = HEAD + RH * len(rows) + 6
    s = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'font-family="PingFang SC,Microsoft YaHei,sans-serif">']
    s.append('<defs><marker id="ail-ar" markerWidth="9" markerHeight="9" refX="7" refY="3" '
             'orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#94a3b8"/></marker>'
             '<marker id="ail-aia" markerWidth="9" markerHeight="9" refX="7" refY="3" '
             'orient="auto"><path d="M0,0 L7,3 L0,6 z" fill="#d97706"/></marker></defs>')

    s.append(f'<text x="333" y="26" text-anchor="middle" font-size="10.2" font-weight="700" '
             f'fill="#334155">企业的业务流程 —— 工作项 · 处理人 · SLA · 审计</text>')
    s.append(f'<text x="856" y="26" text-anchor="middle" font-size="10.2" font-weight="700" '
             f'fill="#334155">AI 实际待在哪</text>')

    for i, r in enumerate(rows):
        y0 = HEAD + i * RH
        by, bh = y0 + 6, 56
        if r["wide"]:
            s.append(f'<rect x="14" y="{by}" width="982" height="{bh}" rx="10" fill="#eff6ff" '
                     f'stroke="#2563eb" stroke-width="1.6"/>')
            xs, xe = 118, 884
        else:
            s.append(f'<rect x="14" y="{by}" width="638" height="{bh}" rx="10" fill="#fbfcfe" '
                     f'stroke="#cbd5e1" stroke-width="1.3" stroke-dasharray="6 4"/>')
            xs, xe = 118, 640
            ax, aw = 716, W - 20 - 716
            s.append(f'<rect x="{ax}" y="{by}" width="{aw}" height="{bh}" rx="10" fill="#fdf2f8" '
                     f'stroke="#f9a8d4" stroke-width="1.5"/>')
            t1, t2 = r["ai"]
            s.append(f'<text x="{ax+14}" y="{by+25}" font-size="10.6" font-weight="700" '
                     f'fill="#831843">{t1}</text>')
            s.append(f'<text x="{ax+14}" y="{by+42}" font-size="8.8" fill="#7c8798">{t2}</text>')
            ym = by + bh / 2
            s.append(f'<line x1="{ax-4}" y1="{ym}" x2="656" y2="{ym}" stroke="#d97706" '
                     f'stroke-width="1.4" stroke-dasharray="5 3" marker-end="url(#ail-aia)"/>')
            s.append(f'<text x="{(ax-4+656)/2:.1f}" y="{ym-8}" text-anchor="middle" '
                     f'font-size="8.6" fill="#d97706">{r["arrow"]}</text>')
        s.append(f'<text x="28" y="{by+26}" font-size="11.4" font-weight="700" '
                 f'fill="#0f172a">{r["name"]}</text>')
        s.append(f'<text x="28" y="{by+42}" font-size="8.4" fill="#94a3b8">{r["sub"]}</text>')

        nn, g = len(r["nodes"]), 8
        nw = (xe - xs - g * (nn - 1)) / nn
        ny = by + (bh - 34) / 2
        for j, (lab, kind) in enumerate(r["nodes"]):
            nx = xs + j * (nw + g)
            f_, st, tx = NC[kind]
            s.append(f'<rect x="{nx:.1f}" y="{ny:.1f}" width="{nw:.1f}" height="34" rx="8" '
                     f'fill="{f_}" stroke="{st}" stroke-width="{2.0 if kind == "ai" else 1.2}"/>')
            s.append(f'<text x="{nx+nw/2:.1f}" y="{ny+22:.1f}" text-anchor="middle" '
                     f'font-size="{11.4 if nw > 130 else 10.6}" font-weight="600" '
                     f'fill="{tx}">{lab}</text>')
            if j < nn - 1:
                s.append(f'<line x1="{nx+nw:.1f}" y1="{ny+17:.1f}" x2="{nx+nw+g-2:.1f}" '
                         f'y2="{ny+17:.1f}" stroke="#94a3b8" stroke-width="1.3" '
                         f'marker-end="url(#ail-ar)"/>')
        if r["wide"]:
            s.append(f'<rect x="896" y="{by+16}" width="88" height="24" rx="12" fill="#dcfce7" '
                     f'stroke="#16a34a" stroke-width="1.1"/>')
            s.append(f'<text x="940" y="{by+32}" text-anchor="middle" font-size="9.6" '
                     f'font-weight="700" fill="#15803d">AI 在框内</text>')
    s.append("</svg>")
    return "\n".join(s)


def build_bridge_svg():
    """组合关系图：FlowHub 作为治理层拦在已有 AI 应用与自动化前面。"""
    s = ['<svg viewBox="0 0 1010 188" xmlns="http://www.w3.org/2000/svg" '
         'font-family="PingFang SC,Microsoft YaHei,sans-serif">']
    # 上排：企业已有的东西
    for x, title, sub in [
        (80.0, "企业已有的 AI 应用", "Dify · Coze · 自研 Agent · AiChat"),
        (510.0, "企业已有的自动化 / 系统", "n8n · RPA 脚本 · GitLab · Confluence"),
    ]:
        s.append(f'<rect x="{x}" y="14" width="420" height="54" rx="9" fill="#ffffff" stroke="#e2e8f0"/>')
        s.append(f'<text x="{x+20}" y="38" font-size="12.5" font-weight="700" fill="#0f172a">{title}</text>')
        s.append(f'<text x="{x+20}" y="56" font-size="10" fill="#64748b">{sub}</text>')
    # 汇聚箭头
    for x in (290.0, 720.0):
        s.append(f'<path d="M {x} 68 L {x} 82 L 505 82 L 505 90" fill="none" '
                 f'stroke="#94a3b8" stroke-width="1.4"/>')
    s.append('<path d="M497,86 L505,96 L513,86" fill="none" stroke="#94a3b8" stroke-width="1.4"/>')
    # FlowHub 治理层
    s.append('<rect x="80" y="100" width="850" height="50" rx="10" fill="#0f172a"/>')
    s.append('<text x="100" y="122" font-size="14" font-weight="700" fill="#ffffff">'
             'FlowHub：流程节点 + 风险闸控（五级）+ 审计留痕</text>')
    s.append('<text x="100" y="140" font-size="10" fill="#94a3b8">'
             'Access Key 按用户分发 · 数据可见性 = 当前任务 + 已执行的上游链路 · 产出回传后流程自动往下走</text>')
    s.append('<path d="M505,150 L505,164" fill="none" stroke="#94a3b8" stroke-width="1.4"/>')
    s.append('<path d="M497,160 L505,170 L513,160" fill="none" stroke="#94a3b8" stroke-width="1.4"/>')
    s.append('<text x="505" y="186" text-anchor="middle" font-size="11" fill="#334155">'
             '业务流程继续往下走 —— 人与 AI 的每一步都在同一套权限与审计里</text>')
    s.append("</svg>")
    return "\n".join(s)

# ---------------------------------------------------------------- 样式

CSS = """
:root{--brand:#2563eb;--brand-dark:#1e40af;--ink:#0f172a;--ink-3:#64748b;--line:#e2e8f0}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:#e9e9e6;color:var(--ink);
  font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei","Source Han Sans SC",sans-serif;
  -webkit-font-smoothing:antialiased}
.page{width:210mm;min-height:297mm;padding:15mm 15mm 12mm;background:#fff;margin:0 auto 7mm;
  position:relative;box-shadow:0 1px 3px rgba(0,0,0,.13),0 10px 34px rgba(0,0,0,.07);
  page-break-after:always;break-after:page;display:flow-root}
.page:last-child{page-break-after:auto;break-after:auto}
.pno{position:absolute;right:16mm;bottom:9mm;font-size:9pt;color:#94a3b8}

h1.doc-title{font-size:28pt;font-weight:800;letter-spacing:-.6px;margin:0;line-height:1.22}
.title-rule{height:3px;width:62px;background:var(--brand);margin:9px 0 6px;border-radius:2px}
.doc-sub{font-size:11.5pt;color:var(--ink-3);margin:0 0 30px;letter-spacing:.2px}

h2.section{font-size:17.5pt;font-weight:700;margin:0 0 13px;letter-spacing:-.3px}
h3.case{font-size:14.5pt;font-weight:700;margin:17px 0 9px;letter-spacing:-.2px}
h4.sub{font-size:11.5pt;font-weight:700;margin:12px 0 5px}

p.body{font-size:10.2pt;line-height:1.9;color:#1f2937;margin:0 0 8px;text-align:justify}
p.body.tight{margin-bottom:5px}
.lead{font-size:10.3pt;line-height:1.92;color:#1f2937;text-align:justify;margin:0 0 11px}
b{color:#0f172a}

figure{margin:10px 0 4px;background:#f5f6f8;border:1px solid #eceef1;border-radius:9px;
  padding:8px 8px 7px;position:relative}
figure img{width:100%;display:block;border-radius:6px;border:1px solid #e2e8f0;cursor:zoom-in}
figure img:hover{outline:2px solid #93c5fd;outline-offset:1px}
figure::after{content:"⌕ 点击放大";position:absolute;top:15px;right:15px;font-size:7.6pt;
  color:#fff;background:rgba(15,23,42,.62);padding:2px 9px;border-radius:20px;letter-spacing:.2px;
  opacity:0;transition:opacity .15s;pointer-events:none}
figure:hover::after{opacity:1}
figcaption{font-size:8.3pt;color:#64748b;padding:7px 2px 1px;line-height:1.58}

.lb{position:fixed;inset:0;z-index:999;background:rgba(9,14,24,.95);display:none;
  align-items:center;justify-content:center;flex-direction:column;padding:2.2vh 1.6vw;cursor:zoom-out}
.lb.on{display:flex}
.lb img{max-width:96vw;max-height:86vh;border-radius:6px;background:#fff;
  box-shadow:0 20px 70px rgba(0,0,0,.6)}
.lb-bar{color:#cbd5e1;font-size:11.5pt;margin-top:13px;letter-spacing:.2px;text-align:center;
  line-height:1.7;max-width:80vw}
.lb-hint{color:#64748b;font-size:9pt;margin-top:6px}

.panel{background:#f8fafc;border:1px solid var(--line);border-radius:9px;padding:7px 10px;margin:7px 0}
.panel svg{width:100%;display:block}

table{width:100%;border-collapse:collapse;margin:10px 0 3px;font-size:9.4pt}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--line);vertical-align:top;line-height:1.55}
th{background:#f8fafc;font-weight:600;color:#334155;font-size:9.1pt}
td b{color:var(--brand-dark)}

.chip{display:inline-block;font-size:8.4pt;font-weight:600;padding:2px 8px;border-radius:20px;
  background:#dcfce7;color:#15803d;vertical-align:3px;margin-left:8px;letter-spacing:.2px}
.chip.plan{background:#e0e7ff;color:#4338ca}
.note{font-size:8.9pt;color:#7c8798;line-height:1.75;margin:9px 0 0;text-align:justify}

.diff{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0 4px}
.diff>div{background:#fff;border:1px solid #e8ecf1;border-left:3px solid #e11d48;
  border-radius:7px;padding:8px 11px 9px}
.diff b{display:block;font-size:10.2pt;color:#0f172a;margin-bottom:3px}
.diff span{font-size:8.9pt;color:#475569;line-height:1.62}
td.neg{color:#64748b}

.quick{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:9px 0 2px}
.quick>div{background:#f8fafc;border:1px solid var(--line);border-radius:7px;padding:8px 10px 9px}
.quick b{display:block;font-size:9.4pt;color:#0f172a;margin-bottom:3px}
.quick em{display:block;font-style:normal;font-size:8.2pt;color:var(--brand-dark);
  font-weight:600;margin-bottom:3px}
.quick span{font-size:8.2pt;color:#64748b;line-height:1.52;display:block}

@media print{
  body{background:#fff}
  /* 打印时锁定 A4 实体尺寸：必须显式给宽高，Chrome 才会按 1:1 输出，
     否则会套用默认纸边距并把整页缩小（实测缩到 94.1%） */
  .page{margin:0;box-shadow:none;border-radius:0;
        width:210mm;min-height:296mm;padding:13mm 15mm 10mm}
  figure,.panel,table{break-inside:avoid}
  h2.section,h3.case,h4.sub{break-after:avoid}
  .lb{display:none!important}
  figure::after{display:none}
  figure img{cursor:auto}
}
@page{size:A4;margin:0}
"""


LIGHTBOX = r"""
<div class="lb" id="lb">
  <img id="lbimg" alt="">
  <div class="lb-bar" id="lbcap"></div>
  <div class="lb-hint">点击任意处或按 Esc 关闭　·　可右键另存原图</div>
</div>
<script>
(function(){
  var lb=document.getElementById('lb'), im=document.getElementById('lbimg'), cp=document.getElementById('lbcap');
  function close(){ lb.classList.remove('on'); document.body.style.overflow=''; im.removeAttribute('src'); cp.textContent=''; }
  function open(src,cap){ im.src=src; cp.textContent=cap||''; lb.classList.add('on'); document.body.style.overflow='hidden'; }
  document.querySelectorAll('figure img').forEach(function(el){
    el.addEventListener('click', function(){
      var f=el.closest('figure'), fc=f?f.querySelector('figcaption'):null;
      var t=fc?fc.textContent.replace(/\s+/g,' ').trim():'';
      open(el.getAttribute('src'), t.length>110 ? t.slice(0,110)+'…' : t);
    });
  });
  lb.addEventListener('click', close);
  document.addEventListener('keydown', function(e){ if(e.key==='Escape') close(); });
})();
</script>
"""


def page(inner, pno=None):
    tail = f'<div class="pno">{pno}</div>' if pno else ""
    return f'<section class="page">{inner}{tail}</section>'


PAGES = []

# ---- P1  整体说明（图 1-1 能力分层全景） ----------------------------------
PAGES.append(f"""
<h1 class="doc-title">FlowHub 企业服务案例介绍</h1>
<div class="title-rule"></div>
<p class="doc-sub">流程协同引擎 × AI 专家平台（Expert OS）· 让每个流程节点可运行、可追溯、可审计</p>

<h2 class="section">一、整体说明</h2>
<p class="lead">FlowHub 是一套面向中大型企业的「流程协同引擎 × AI 专家平台」，它解决的是一个很具体的错位：<b>企业的 AI 试点一个接一个，却始终进不了受控的生产流程</b>——缺组织权限矩阵、缺 SLA 管理、缺多级审批、缺版本化审计；而企业已有的流程平台又大多停在「线上化」，AI 只做到表单摘要、智能填单这一层。结果是 AI 的效果说不太清、责任落不到人、审计过不了关，POC 做完就停在原地。</p>
<p class="lead">FlowHub 站在两条曲线的交点上：<b>用可视化流程引擎管住「事怎么走」，用 Expert OS 让 AI 专家干得了「活怎么干」，再用风险闸控与人机协同决定「谁说了算」</b>。AI 的每一份产出都带知识库引用、都落在某个流程节点上、都绑定一个不可篡改的 Expert 版本；人的每一次确认与退回同样留痕可查——让 AI 专家成为企业的「数字员工」，而不是又一个说不清效果的工具。</p>
<p class="lead">下面这套系统的能力已在客户环境持续运行：Expert 覆盖从需求评审到排查修复的研发全链路，流程实例、AI 运行与审计留痕落在同一套数据模型内。下面先跟着一次真实任务走完整条链路，再逐个展开各项能力。</p>

<div class="panel">{build_arch_svg()}</div>
<p class="note"><b>图 1-1　FlowHub 能力分层全景</b>　读图方式：自上而下六层，每层回答一个具体问题——谁在用、凭什么做判断、有什么能力、事怎么走、按什么规矩、数据放在哪，每层下方的灰条即该层职责。<b>治理与安全层不是某一层的附属，而是横贯全流程的约束</b>：AI 的每一次工具调用都要穿过「用户 RBAC ∩ Expert 策略 ∩ 工具风险等级 ∩ 当前审批状态」，任何一层不过就不执行。各层在真实业务里的协同方式见第二章「一次任务的完整旅程」。</p>
""")
# ---- P2  一次任务的完整旅程 + 反哺（图 2-1） ------------------------------
PAGES.append(f"""
<h2 class="section">二、一次任务的完整旅程</h2>
<p class="lead">能力全景讲的是这套系统由什么构成，客户真正会问的却是那一句：<b>一件事在这套系统里，到底是怎么走完的？</b>下面跟着一条链路走一遍——从业务发起到任务关闭之后的<b>反哺</b>。</p>

<div class="panel">{build_journey_svg()}</div>
<p class="note"><b>图 2-1　一次任务的完整旅程</b>　前七个环节逐行标注「谁在动 / 发生什么 / 产出什么 / 落到哪」，共用同一套权限与审计；其中<b>外部接力</b>是 FlowHub 不替换已有智能体的方式——Dify / 自研智能体凭 Access Key 接管本节点，可见范围限于本任务与上游链路。第八步反哺是与「一次性自动化」的分界：产出归档与策略调优已在客户环境运行，知识库与 Memory 按交付批次开放。</p>

<h4 class="sub">反哺：让第 N 次任务比第 1 次更准</h4>
<p class="body">不做反哺，AI 在流程里干的活就是「一次性劳动」：下一个人遇到同类问题，还得从零讲一遍背景、再等一遍生成。FlowHub 在任务闭环那一刻让四路产出同时回流：AI 草案、修订记录与附件按工作项归属归档到<b>文档中心</b>，成为可复用资产；判断依据与结论沉淀进<b>知识库</b>集合，附来源、可分块检索；上下文、角色设定与处理偏好写回 Expert 的 <b>Memory</b>；节点卡点、返工次数与人工修改幅度则反馈到<b>流程模板与 Expert 提示词</b>上。</p>
<p class="body">有一条刻意的设计：策略调优不做热改，而是生成新的 draft 版本、走发布校验后再生效——线上 Expert 版本永远不可变，改了什么、谁改的都能回溯。四路回流同样受权限与审计约束，全量留痕。于是第二十次同类任务启动时，它拿到的知识、记忆与流程版本都比第一次更准——<b>每一次执行都在给下一次降成本</b>。</p>

<table>
  <tr><th style="width:20%">维度</th><th style="width:40%">不做反哺（一次性自动化）</th><th>做了反哺（FlowHub）</th></tr>
  <tr><td><b>知识</b></td><td>判断依据散在个人经验与聊天记录里，人一走就带走了</td><td>结论与来源沉淀为可检索的知识资产，越用越厚</td></tr>
  <tr><td><b>记忆</b></td><td>每次都要从零讲一遍背景、喂一遍上下文</td><td>上下文与偏好按命名空间留存，开箱即用</td></tr>
  <tr><td><b>流程</b></td><td>卡点靠人吐槽，改流程要走需求排期</td><td>卡点与返工数据反馈到模板，走版本发布即时生效</td></tr>
  <tr><td><b>责任</b></td><td>事后说不清这次依据是什么、谁改过什么</td><td>每次回流都有 actor、版本与审计记录，可归因</td></tr>
</table>
""")
# ---- P3  功能模块 1：流程协同引擎 -----------------------------------------
PAGES.append(f"""
<h2 class="section">三、功能介绍</h2>
<p class="lead">上一章那条链路里的每一环，落到产品上就是下面这七个能力模块。每个模块先说它解决什么问题，再给真实界面。本章全部截图取自客户部署实例，<b>任意图片点击即可放大查看细节</b>。</p>

<h3 class="case">1. 流程协同引擎</h3>
<h4 class="sub">1.1. 背景介绍</h4>
<p class="body">流程这件事，过去一直散在文档、群聊和邮件里——谁做到哪一步、卡在谁手上，全靠问；而传统 BPM 与 OA 的定制周期普遍在半年以上，改一次流程就要走一次开发。FlowHub 的流程协同引擎把流程从「文档」变成「资产」：画布提供开始、任务、决策、并行分叉与汇合、验收、闭环确认、结束等节点，业务人员拖拽即可建模；模板走 draft→published 的版本化流程，发布前自动跑完整性校验（起止节点、孤立节点、处理人绑定等），历史版本一经发布即冻结、可回溯比对；节点级 SLA 超时预警、任务认领/退回/转办在同一张画布上配置。流程跑起来后，谁在做、做到哪、超时没有，全在系统里，而不是在某个人脑子里。</p>

<h4 class="sub">1.2. 产品示意</h4>
{fig('02-templates', '<b>图 3-1　流程模板与版本化</b>　模板列表展示每条流程的当前版本与节点结构，每个历史版本都可预览；已发布版本不可直接修改，需另存草稿后发布新版本。')}
""")
# ---- P4 ------------------------------------------------------------------
PAGES.append(f"""
{fig('21-canvas-req', '<b>图 3-2　需求流程画布</b>　左侧为主线流转与虚线回退路径，右侧属性面板配置处理主体、节点 SLA 与表单 Schema；节点可绑定已发布的 Expert Deployment。')}

<h3 class="case">2. Expert OS —— 企业 AI 专家平台</h3>
<h4 class="sub">2.1. 背景介绍</h4>
<p class="body">今天 AI 已经不是「要不要做」，而是「怎么做得快、做得起」。但多数企业想自建 AI 能力，却卡在三件事上：专家经验没法沉淀成可复用的资产、AI 产出没有版本可追溯、模型上线后无人运维——于是大量项目止步于 POC，迟迟变不成生产力。Expert OS 把「建专家、管版本、接工具、用模型」做成一套平台能力：Expert 走 draft 到 published 的不可变发布，每次发布生成一个版本，绑定 Deployment 后即可挂到流程节点上；Expert Skill 把指令、输入输出契约与策略打包成能力包，与 Expert 解耦、可被多个专家复用；MCP 中心统一管理工具的注册、健康检查、风险分级与审批要求；Provider 中心按 vendor 接入多模型（含国产模型），支持私有化部署。落到一线，就是「需求评审专家」「技术方案专家」「后端开发专家」「开发排查修复专家」这样一个个具体角色——企业不必重金组一支算法团队，也能把专家经验变成能持续运行的 AI 资产。</p>
""")
# ---- P5 ------------------------------------------------------------------
PAGES.append(f"""
<h4 class="sub">2.2. 产品示意</h4>
{fig('04-expert-center', '<b>图 3-3　Expert 中心</b>　列表中的 Expert 均处于「已发布」状态，各自带版本号、Skill 绑定数与 Deployment 数，可见覆盖研发全链路的专家分工。')}
{fig('01-os-overview', '<b>图 3-4　Expert OS 总览</b>　左侧按日展示运行趋势与异常中断，右侧为 Expert 调用排行与运行层健康度（Expert / Skill / MCP / Provider 各类资产的就绪状态）。')}
""")
# ---- P6 ------------------------------------------------------------------
PAGES.append(f"""
{fig('06-mcp-center', '<b>图 3-5　MCP 中心</b>　已接入 Confluence MCP Server（连接正常），注册的工具按风险等级分级标注，写入与关键动作类工具被标记为「需审批」。')}

<h3 class="case">3. 人机协同与风险闸控</h3>
<h4 class="sub">3.1. 背景介绍</h4>
<p class="body">企业把 AI 放进流程，最怕两件事：一是 AI 直接写生产、失控了收不回来；二是 AI 只敢给建议，落不了地。FlowHub 的做法是把 AI 的动作按风险分五级——read（只读）、generate（生成草稿）、write_draft（写草稿）、write_commit（提交写入）、critical（关键动作）：只读与生成类直接执行，写提交与关键动作强制中断、转人工审批，批准后从断点精确恢复执行一次。落到页面上，就是 AI 先在节点里把活干出来（汇总上下文、产出草案、填充表单），人在同一屏上看到 AI 产出与知识库引用，确认或修改后签字采纳——AI 干的量上去了，责任边界还在人手里。外部工具在注册时就标注风险等级，写入与关键动作类强制转为人工审批；审计记录里能看到 task:ai_fill（AI 填充）→ task:correction_propose（AI 修正提案）→ task:correction_review（人工复核）→ task:correction_rework_complete（返工闭环）这样一条完整的「AI 干、人把关」链路。</p>
""")
# ---- P7 ------------------------------------------------------------------
PAGES.append(f"""
<h4 class="sub">3.2. 产品示意</h4>
{fig('23-node-process', '<b>图 3-6　节点处理页：AI 起草 + 人工确认</b>　工作项头部的「Expert 待审批」入口、节点候选人绑定（按角色或技能匹配）、左侧流程进度逐节点推进、右侧历史处理留痕；表单区提供「Expert 辅助填充」，Schema 由画布节点驱动，与建模配置保持一致。（图中处理人姓名已脱敏）')}
{fig('09-approvals', '<b>图 3-7　审批队列</b>　AI 产出按风险进入待审批列表，逐条展示运行上下文与操作人；批准后从断点精确恢复执行一次，拒绝则中断本次运行。')}
""")
# ---- P8 ------------------------------------------------------------------
PAGES.append(f"""
<h3 class="case">4. 运行可复现与全量审计</h3>
<h4 class="sub">4.1. 背景介绍</h4>
<p class="body">AI 出了结果，最常被问的一句话是：「当时用的是哪个版本的提示词、哪个技能、哪个工具、喂了哪些知识？」如果答不上来，金融、政企这类强合规客户的审计部门就不会让这套东西进生产。FlowHub 把这件事做成硬约束：Expert、Skill、Tool 三层版本发布后不可修改、带校验值，每次运行都对应一个 Run 与一条 Trace，可回放完整执行路径；审计中心记录人与 AI 的每一个动作，审计记录不可删除，导出行为本身也要二次审计，actor（操作人）、authorized_user（授权用户）、request_id（请求 ID）可全链路追溯。运行中心里每一次 Expert 运行都能点开看到耗时、绑定的 Deployment 与失败原因；审计中心支持按动作、结果、主体维度下钻，人与 AI 的每一步都在同一条时间线上。</p>

<h4 class="sub">4.2. 产品示意</h4>
{fig('08-runtime-center', '<b>图 3-8　运行中心</b>　每行是一次 Run 及其 Trace，标注绑定的 Deployment、耗时与开始时间；顶部分流运行中、已中断、成功、失败四种状态。')}
""")
# ---- P9 ------------------------------------------------------------------
PAGES.append(f"""
{fig('12-audit', '<b>图 3-9　审计中心（按业务动作过滤）</b>　审计字段覆盖 actor / authorized_user / action / target / before / after / result / IP / request_id，导出需再次审计；图中可见 task:submit、task:ai_fill、task:correction_propose、task:correction_review 等一串人机协同动作。（主体列已脱敏）')}

<h3 class="case">5. AiChat —— 企业 AI 对话工作台</h3>
<h4 class="sub">5.1. 背景介绍</h4>
<p class="body">员工的 AI 使用往往是失控的：每个人各自开一个公网账号，聊了什么、用了什么模型、有没有把内部资料贴进去，企业一概不知。AiChat 把这件事收回到治理边界内：它不是一个通用聊天框，而是「按 Expert 找专家」的对话入口——用户先选一个已发布的 Expert Deployment，再带着具体问题开聊；模型由 Provider 中心按 vendor 统一配置（含国产模型），会话与 Expert、Deployment、项目绑定，可追溯；长对话自动做摘要压缩，并按模型能力设置上下文与输出预算，超出时明确提示而不是静默截断；对话中产出的文件可以保存到工作项或文档中心。对员工来说，这是日常办公里最好用的那个 AI 入口；对企业来说，AI 的每一次对话都落在同一套组织、权限与留痕体系里。</p>
""")
# ---- P10 -----------------------------------------------------------------
PAGES.append(f"""
<h4 class="sub">5.2. 产品示意</h4>
{fig('10-aichat', '<b>图 3-10　AiChat 工作台</b>　左侧为会话列表（按 Expert Deployment 归属），右侧为对话区；模型由 Provider 中心统一配置，会话可绑定项目与工作项，产出可留存为文档。')}

<h3 class="case">6. 治理底座：组织权限 · 通知 · 文档</h3>
<h4 class="sub">6.1. 背景介绍</h4>
<p class="body">AI 进了流程，如果权限、通知、文档还是散的，治理就是一句空话。FlowHub 把治理做成一整层底座：组织与权限侧提供细粒度的角色与权限点矩阵，支持技能匹配、角色继承与防自锁（不允许把自己所在的最后一个管理角色摘掉），并可与钉钉、企业微信通讯录同步；通知中心统一站内、钉钉、企业微信、邮件多渠道，逐条记录投递状态与重试，渠道健康检查一眼看出哪个通道没配好；文档中心的所有上传都走校验链（扩展名、MIME、病毒扫描），并按敏感等级分级管控，下载走短时链接，审计可查「谁在什么时候取走了哪份文件」。这样 AI 与人在同一套组织与权限语义里协作——AI 能做什么，不取决于它多聪明，而取决于它在哪个节点、以什么身份、被授了什么权。</p>
""")
# ---- P11 -----------------------------------------------------------------
PAGES.append(f"""
<h4 class="sub">6.2. 产品示意</h4>
{fig('11-matrix', '<b>图 3-11　权限矩阵</b>　角色 × 权限点的可视化配置，按模块分组呈现，支持逐格开关与角色继承，含防自锁校验。')}
{fig('14-channels', '<b>图 3-12　通知渠道配置</b>　站内、钉钉、企业微信、邮件各渠道独立配置与连通性测试，投递失败自动重试并留痕。')}
""")
# ---- P12 -----------------------------------------------------------------
PAGES.append(f"""
{fig('15-documents', '<b>图 3-13　文档中心</b>　上传走扩展名 / MIME / 病毒扫描校验链，按敏感等级分级，支持版本与短时下载链接。')}

<h3 class="case">7. 开放能力：外部智能体接力流程节点</h3>
<h4 class="sub">7.1. 背景介绍</h4>
<p class="body">企业里往往已经买了或自建了别的智能体，换不掉也不想换。FlowHub 的策略不是替换，而是「给它们流程和权限」：FlowHub 自身作为 MCP Server 对外输出，外部智能体拿着按用户分发的 Access Key，在授权范围内接管某个流程节点的处理——它能读到的是当前任务节点加上所有已经执行过的上游链路上下文，产出的结果通过标准的任务提交接口自动回传，流程自动往下走，全程留痕与内部 Expert 完全一致；页面上还可以直接下载 Skill 指令（Markdown），把「怎么接、能做什么、边界在哪」一次性交付给外部开发者。反方向上，内部 Expert 也能出站调用已批准的外部工具——目前已接入 Confluence MCP Server（注册工具按风险分级，写入类需人工审批），并支持 GitLab 代码仓库镜像接入，让代码类回答必须基于仓库证据、附带定位引用。这一进一出，让 FlowHub 成为企业 AI 生态里的「流程与治理层」，而不是又一个孤立的平台。</p>
""")
# ---- P13 -----------------------------------------------------------------
PAGES.append(f"""
<h4 class="sub">7.2. 产品示意</h4>
{fig('17-external-tools', '<b>图 3-14　能力中心 / 外部工具接入</b>　对外提供 FlowHub MCP Server 端点与页面级 Skill 指令下载；下方 Access Key 管理按用户分发密钥、可随时吊销，外部智能体的可见范围由任务可见性与密钥共同约束。')}
{fig('16-repos', '<b>图 3-15　代码仓库镜像</b>　绑定的 GitLab 仓库完成本地镜像，代码类回答基于镜像工作树做只读检索、附定位引用，不访问外网。')}
""")
# ---- P14 与同类方案的对比（一）：章首 + 能力矩阵 + AI 位置四宫格 -----------
PAGES.append(f"""
<h2 class="section">四、与同类方案的对比</h2>
<p class="lead">选型时最常被问到的一句话是：「Dify、n8n、OA 审批我们都有了，为什么还要 FlowHub？」先用一句话回答：<b>AI 能力本身不稀缺</b>——大家接的是同一批模型，能写的提示词、能调的参数也差不多，同一个业务问题交给谁做，草稿质量不会差出一个量级。真正拉开差距的是<b>AI 被放在流程的哪个位置</b>：是把产出交给下一个人手工搬运，还是让 AI 直接坐在流程节点上、由这个节点上的人对结果负责。</p>
<p class="lead">所以本章不比「谁的 AI 更强」，只比一件事：<b>AI 的产出，怎么进入一个有责任人、有 SLA、有审计的业务流程</b>。写法依旧务实——先讲清每个方案真正的优点，再讲清选它要付的代价；最后同样把 FlowHub 自己的代价写在明面上，包括哪些情况下我们建议你<b>不要</b>用 FlowHub。</p>

<div class="panel">{build_matrix_svg()}</div>
<p class="note"><b>图 4-1　能力侧重对比</b>　● 原生内建且为产品设计重心；◐ 部分覆盖，或需二次建设才能达到企业级；○ 不是该产品的设计目标。判分依据各产品公开文档与社区实践，可自行核验：Dify 自 v1.13 起提供 Human Input 人工介入节点、v1.14 起提供 HITL Service API 供外部系统驱动审批；n8n 的原生 HITL 覆盖 AI Agent 的工具调用审批，社区反馈其不保留审批决策历史；自托管开源的 n8n 默认不提供不可篡改审计日志（企业级审计与 SOC 2 访问日志在 Cloud Enterprise 版本）。<b>FlowHub 的「开放与集成生态」一项自己标为 ◐</b>：MCP 双向接入与模型中立已经具备，但连接器数量远不及 n8n 这类集成平台，通用 SaaS 对接要靠 MCP 或定制开发。</p>

<div class="quick">
  <div><b>传统流程流转</b><em>AI 在流程之外</em><span>审批最稳、组织与流程日志成熟；AI 是流程外面另开的对话框，产出要人手工搬进表单。</span></div>
  <div><b>Dify</b><em>AI 在自己的运行空间</em><span>从想法到能跑的 AI 应用最快；但一次 run 跑完即结束，组织权限与不可篡改审计要自己补。</span></div>
  <div><b>n8n</b><em>AI 在事件链路上</em><span>连接器之王，系统之间「最后一公里」非常高效；但一次执行只是一条日志，没有工作项、没有责任链。</span></div>
  <div><b>FlowHub</b><em>AI 就在流程节点上</em><span>节点触发即产出、带知识库引用；写操作强制转人签署，签字后从断点恢复且只执行一次。</span></div>
</div>
<p class="body tight" style="margin-top:9px">下面按这个顺序展开：<b>4.1 逐家的优点与真实代价 → 4.2 AI 站在流程的哪一侧 → 4.3 同一件事四个方案分别怎么走 → 4.4 四个关键差异 → 4.5 我们自己的代价 → 4.6 组合关系 → 4.7 选型建议 → 4.8 迁移与共存</b>。</p>
""")
# ---- P15 与同类方案的对比（二）：4.1 逐家 + 4.2 AI 站在流程哪一侧（新图） ----
PAGES.append(f"""
<h3 class="case">4.1. 逐个看：它的优点，和 AI 用到这一步时你会碰到什么</h3>
<p class="body tight">左栏是这些产品<b>确实做得好、我们也建议你保留</b>的部分；右栏不是抽象的功能对比，而是<b>AI 真的在这套工具里跑起来之后，反复出现的那一幕</b>。</p>
<table>
  <tr><th style="width:14%">方案</th><th style="width:33%">它真正的优点（值得学的部分）</th><th>AI 用到这一步时，你会碰到什么</th></tr>
  <tr><td><b>传统流程流转</b><br><span style="font-size:8.4pt;color:#7c8798">OA / BPM：宜搭、简道云、Activiti</span></td>
      <td>审批链、组织架构、流程日志都很成熟，合规部门熟悉这套语言；实施商多、落地经验足；流程一旦配好就非常稳定。</td>
      <td class="neg"><b>AI 写完一份评审意见，人还得复制粘贴进 OA 的表单字段。</b>半年后领导问「这条意见当时用的是哪版提示词」，翻遍系统查不到——OA 里根本没有「提示词」这个概念。AI 的产出与这条审批记录之间，没有任何引用关系。</td></tr>
  <tr><td><b>Dify</b><br><span style="font-size:8.4pt;color:#7c8798">LLM 应用开发平台</span></td>
      <td>从零搭一个 AI 应用最快：工作流 / Agent / RAG / 插件 / 可观测一条龙，社区活跃、模型与工具生态开放，人工介入节点也已具备。</td>
      <td class="neg"><b>加一个人工确认节点很容易，但结论只留在 Dify 的运行日志里。</b>领导问「这份合同现在卡在谁手上」，答不出来——Dify 里没有「谁」，只有一次 run 和它的节点状态；组织权限与不可篡改审计还得自己补。</td></tr>
  <tr><td><b>n8n</b><br><span style="font-size:8.4pt;color:#7c8798">自动化集成平台</span></td>
      <td>连接器生态庞大、事件驱动极强、自托管友好，做系统之间「最后一公里」的对接非常高效，性价比高。</td>
      <td class="neg"><b>把 AI 的判断结果自动推到 ERP 很方便。</b>三个月后审计要「谁批准了这次写入」：只有一张 execution 列表，没有签字人、没有审批意见、没有业务动作名；自托管开源版默认连不可篡改日志都没有。</td></tr>
  <tr><td><b>自研 Agent / 内部中台</b><br><span style="font-size:8.4pt;color:#7c8798">大模型应用团队自建</span></td>
      <td>完全贴合自身业务，数据与调用链可控，不受外部产品迭代节奏影响，长期看最灵活。</td>
      <td class="neg"><b>三个月做出很亮眼的 demo，六个月后发现要补权限矩阵、审计、版本管理、SLA 才敢上线。</b>最难的部分不是 AI，而是「让 AI 的产出有人担责」——而这没有一项是业务部门能验收的功能，团队很容易在这里耗掉一年。</td></tr>
</table>

<h3 class="case">4.2. AI 站在流程的哪一侧</h3>
<p class="body tight">上面四个方案在「AI 会不会写、写得好不好」上差距不大；真正的分野在下面这件事上——<b>AI 与「有责任人、有 SLA、有审计的业务流程」是什么关系</b>。图中左侧虚线框是企业真正的业务流程边界，工作项台账、处理人、SLA、审计留痕都在框内；右侧实线框是 AI 实际待的地方。</p>
<div class="panel">{build_ai_locus_svg()}</div>
<p class="note"><b>图 4-2　AI 站在流程的哪一侧</b>　读图只需看一点：<b>AI 在框内还是框外</b>。前三行里 AI 都待在业务流程之外，它的产出要跨过那条边界，只能靠人复制粘贴、靠接口回传或靠自动回写——而每一次跨越都会丢掉一样东西：要么丢掉 AI 与结论的关联（OA 里查不到提示词），要么丢掉「谁批的」（Dify 与 n8n 里只有 run / execution）。第四行 FlowHub 里，「AI 起草」本身就是流程中的一个环节，与「风险闸控」「人签署」处在同一个框内，产出不需要搬运，也就不会在路上丢掉责任。<b>这里说的「流程」特指企业有台账、有处理人、有 SLA 的业务流程，不是「AI 平台内部的那个工作流」。</b></p>
""")
PAGES.append(f"""
<p class="body tight">这个差别在试用阶段几乎看不出来——AI 都能起草，也都能加一个人工确认节点。它只在四个时刻暴露：<b>① 出问题时追责</b>（谁批的、依据的是哪版提示词）；<b>② 交接时</b>（这件事现在卡在谁手上、还剩多少时间）；<b>③ 审计时</b>（把 AI 的产出与人的审批动作连起来看）；<b>④ 复盘优化时</b>（上一版 AI 被人工改动了哪些地方，这一版流程模板要不要吸收）。</p>
<h3 class="case">4.3. 同一件事，四个方案分别怎么走</h3>
<p class="body tight">抽象对比不如看一步具体的：<b>「AI 起草一份合同风险意见，必须法务签字后才能对外发出」</b>——这一步几乎每家企业都有。下面看的是同一件事在四个方案里，<b>AI 被放在哪儿、它的产出交给了谁</b>。</p>
<table>
  <tr><th style="width:14%">方案</th><th style="width:32%">AI 在哪一步被触发</th><th style="width:24%">AI 的产出交给谁</th><th>最后留下什么</th></tr>
  <tr><td><b>传统流程流转</b></td><td>流程之外，人自己另开一个对话框问 AI（系统完全无感知）</td><td>人自己判断，再手工粘进 OA 表单</td><td class="neg">一条审批记录；AI 与这条记录毫无关系</td></tr>
  <tr><td><b>Dify</b></td><td>Dify 工作流里的一个节点，产出风险意见与引用来源</td><td>法务在 Dify 界面上点确认</td><td class="neg">一次 run 记录；OA 与合同系统里查不到这次签字</td></tr>
  <tr><td><b>n8n</b></td><td>合同创建事件触发，AI 生成意见后推到飞书 / Slack</td><td>法务在飞书里点 Approve</td><td class="neg">一条 execution 与一条消息；决策历史默认不保留</td></tr>
  <tr><td><b>FlowHub</b></td><td><b>合同节点自己挂了 Expert</b>：节点一触发就产出，带知识库引用与上游上下文</td><td>节点处理人本来就是法务——有角色、有 SLA、有节点上下文</td><td><b>一个业务实例</b>：谁签的字、AI 用的是哪版 Expert、这份意见从哪来，全部可查、可导出</td></tr>
</table>
<h3 class="case">4.4. 四个关键差异</h3>
<p class="body tight">功能表上它们越来越像：都有工作流、都能接模型、都能加人工确认。分水岭在下面四件事上——每一件都对应着 4.1 里那些「你会碰到的事」。</p>
<div class="diff">
  <div><b>① AI 是流程的旁观者，还是流程里的一个节点</b><span>Dify / n8n 里 AI 跑在一个并行世界里，它与业务流程之间靠接口或人工连接，而这个连接处没有责任人。FlowHub 里 AI 是节点的一种「动法」：这个节点有处理人角色、有 SLA、有上下文，AI 产出与人的修改落在同一条时间线上。</span></div>
  <div><b>② 人工确认是「表单决策」还是「责任签署」</b><span>三者在技术上都支持「停下来等人点确认」。区别在后面：FlowHub 的闸控由工具风险等级驱动（五级），审批绑定流程节点与处理人角色并受 SLA 约束，批准后从断点恢复且只执行一次；而表单上的一个「同意」，在追责时说明不了任何事。</span></div>
  <div><b>③ AI 产出能不能归因到版本</b><span>Dify / n8n 的版本停在「工作流」这一层。FlowHub 把版本下沉到 Expert / ExpertVersion / SkillVersion / Deployment / Tool 五个对象，每一次运行都能回答：用的哪个版本提示词、哪套技能、绑了哪些工具、喂了哪些知识。</span></div>
  <div><b>④ AI 的有效权限怎么算</b><span>Dify / n8n 的 RBAC 解决「谁能编辑这个工作流」，与 AI 在流程里能做什么无关。FlowHub 的有效权限是一次交集运算：用户 RBAC ∩ Expert 策略 ∩ 工具策略 ∩ 节点范围 ∩ 数据范围 ∩ 审批状态——缺一项，调用就不执行。</span></div>
</div>
""")
# ---- P16 与同类方案的对比（四）：4.5 自身代价 + 4.6 组合 -------------------
PAGES.append(f"""
<h3 class="case">4.5. FlowHub 自己的强项，与你要接受的代价</h3>
<table>
  <tr><th style="width:44%">我们的强项</th><th>你要接受的代价（及我们的看法）</th></tr>
  <tr><td>流程与 AI 在同一个数据模型里：AI 产出、人的动作、审批与审计同源</td>
      <td class="neg">Expert 上线要走「注册 → 版本 → 发布 → 部署 → 节点绑定」，比 Dify 直接发布一条工作流重，初次接入大约多 2–3 天。<b>这是我们刻意选的路</b>：可追溯是靠这些步骤换来的。</td></tr>
  <tr><td>写操作强制人工签署，批准后从断点恢复且只执行一次</td>
      <td class="neg">高风险节点会真的拦住执行、等人处理；风险等级配错会导致「该放行的被拦」或「该拦的放行了」。<b>需要有人对这套配置负责</b>，不能配完不管。</td></tr>
  <tr><td>审计不可删改，导出行为本身二次审计</td>
      <td class="neg">审计记录只增不减，存储与查询要提前规划；数据写错只能补更正记录、改不了历史。对习惯了「后台改一条数据」的团队，这是行为习惯上的改变。</td></tr>
  <tr><td>节点级业务权限：有效权限 = 用户 RBAC ∩ Expert 策略 ∩ 工具策略 ∩ 节点范围 ∩ 数据范围 ∩ 审批状态</td>
      <td class="neg">权限矩阵需要一次性梳理角色与权限点，初期有配置与沟通成本；组织架构频繁调整的客户要留出维护人力。</td></tr>
  <tr><td>MCP 双向接入、模型中立、私有化部署</td>
      <td class="neg">连接器数量远不及 n8n，通用 SaaS 对接要自建 MCP 或定制开发；模型能力上限受客户自有算力与所接模型限制，升级要自己运维。</td></tr>
  <tr><td>业务人员可自助建模，改流程不等研发排期</td>
      <td class="neg">流程还没定型就搬上来，会反复走版本发布；行业模板需按客户逐步沉淀，不是开箱即全。<b>工具解决「怎么走」，解决不了「该不该走」。</b></td></tr>
</table>

<h3 class="case">4.6. 关系是组合，不是替换</h3>
<div class="panel">{build_bridge_svg()}</div>
<p class="note"><b>图 4-3　与已有 AI 平台 / 自动化工具的组合关系</b>　企业已经买了、已经跑起来的东西不必推倒：AI 应用的产出、自动化的结果，都可以通过 MCP 或任务提交接口回传到流程节点上，由 FlowHub 决定「这一步要不要人批、批了才落库、落库之后谁负责」。<b>AI 平台负责聪明，FlowHub 负责算数</b>——把 AI 放在流程里，不是为了换掉它，而是为了让它干出来的活有人接着。</p>
""")
# ---- P17 与同类方案的对比（五）：4.7 选型 + 4.8 迁移 + 4.9 小结 -------------
PAGES.append(f"""
<h3 class="case">4.7. 选型建议：包括「什么情况下别选我们」</h3>
<table>
  <tr><th style="width:47%">你的真实诉求</th><th>建议</th></tr>
  <tr><td>只想快速验证一个 AI 想法、做个 POC 给领导看</td><td>用 Dify / Coze，两三天能出演示；不必上流程引擎</td></tr>
  <tr><td>只想搬运系统间的数据，或只要一个知识库问答入口</td><td>分别用 n8n / iPaaS 与成熟的 RAG 产品，纯问答与纯搬运场景用不上</td></tr>
  <tr><td>流程本身还没定型，也没人愿意当节点上的责任人</td><td><b>先把流程理清楚再上系统</b>。工具能保证「按规矩走」，保证不了「规矩是对的」</td></tr>
  <tr><td>已有 Dify / n8n / OA 且跑得挺好，AI 产出不需要进业务流程、不需要过审计</td><td>保持现状即可，不必引入新的平台</td></tr>
  <tr><td>已有这些系统，但 AI 产出要进业务流程、要担责、要过审计</td><td><b>保留它们，用 FlowHub 做治理层</b>：流程节点 + 风险闸控 + 全量审计，通过 MCP 与 Access Key 接入，产出回传后流程继续走</td></tr>
  <tr><td>流程要版本化、要 SLA、要 AI 深度参与且全程留痕可审计</td><td>由 FlowHub 的流程引擎 + Expert OS 直接承载（第五章六套场景即为此类）</td></tr>
</table>
<p class="note"><b>一句话总结</b>：Dify 让你最快做出 AI，n8n 让你最快连起系统，OA 让审批最稳妥；FlowHub 只做它们都不做的那件事——让 AI 的产出落进有责任、有 SLA、有审计的流程里。</p>

<h3 class="case">4.8. 迁移与共存：不做一次性切换</h3>
<table>
  <tr><th style="width:20%">阶段</th><th style="width:34%">你要做的</th><th>FlowHub 的位置</th></tr>
  <tr><td><b>第一步</b></td><td>挑一条最痛、责任人最明确的流程搬上来</td><td>只接管这条流程的节点、SLA 与审批；原有系统不动</td></tr>
  <tr><td><b>第二步</b></td><td>把已有 AI 应用的产出接进来</td><td>通过 MCP / 任务提交接口回传，由人确认后落库</td></tr>
  <tr><td><b>第三步</b></td><td>按条扩到更多流程，沉淀行业模板</td><td>模板与 Expert 逐步积累，交付后业务可自助复制</td></tr>
</table>
<p class="note">这三步没有「停机窗口」，也不必说服业务部门换工具——他们仍在原来的工作台上干活，只是这一次，AI 的产出有了签字的人。</p>

<h3 class="case">4.9. 收尾：AI 在这四类方案里，分别「能做什么、做不到什么」</h3>
<table>
  <tr><th style="width:19%">方案</th><th style="width:40%">AI 能自动做到</th><th>AI 做不到（必须靠人或靠流程补上）</th></tr>
  <tr><td><b>传统流程流转</b></td><td>起草、摘要、翻译、比对——但全部在业务系统之外完成</td><td class="neg">进入审批链、被业务台账引用、被追溯到「当时用的是哪版提示词」</td></tr>
  <tr><td><b>Dify</b></td><td>编排复杂的 AI 流程、RAG 检索、多工具调用，人工介入节点也已具备</td><td class="neg">产生业务工作项、绑定组织角色与节点 SLA、形成一条可导出的签字链</td></tr>
  <tr><td><b>n8n</b></td><td>事件驱动地调用 AI、跨系统回写数据、极低成本地连起已有系统</td><td class="neg">记住「谁批准了这次写入」、提供不可删改的审计、界定节点级责任</td></tr>
  <tr><td><b>FlowHub</b></td><td>在流程节点上产出并附引用、按风险分级中断、断点恢复且只执行一次、可归因到五层版本</td><td class="neg"><b>替人签字、替人担责——这一条永远做不到</b>，而这正是整套设计的前提</td></tr>
</table>
<p class="note">整章的落点：选哪套工具，决定了 AI 在你的组织里是<b>「一个更快的助手」还是「一个能被追责的参与者」</b>。</p>
""")
# ---- P18 典型业务场景：开场 + 场景一（软件与 IT 服务，生产运行中） ----------
PAGES.append(f"""
<h2 class="section">五、典型业务场景</h2>
<p class="lead">下面六套流程分别来自六个不同行业，是可直接演示、也可直接交付的落地组合。它们共用同一套引擎能力，差别在于<b>节点怎么切、SLA 定多长、AI 挂在哪一步、谁签字</b>。每张流程图都用颜色标出角色的落点：<b>粉色 = AI 介入的环节，蓝色 = 人执行 / 人担责，灰色 = 系统自动完成</b>；每个场景后面再逐环节说明 AI 到底做了什么、人保留了哪部分决定。场景一已在客户环境生产运行（有真实工作项在跑），其余五套为已完成设计的可交付模板。</p>
{fig('03-workitems', '<b>图 5-1　线上工作项</b>　每条记录带类型、状态、优先级、标签、当前处理人与当前节点，是流程实例的统一台账；不同行业的流程实例落在同一张表里，可筛可查、可按部门统计超时。（当前处理人列已脱敏）')}

<h3 class="case">场景一　需求变更协同<span class="chip">软件与 IT 服务 · 生产运行中</span></h3>
<p class="body tight">软件团队的典型困境是：需求从哪来、方案谁定、前后端怎么并行、验收不过怎么办，全靠会议纪要和群聊记录。这套流程把「需求提交 → 需求评审 → 内容产出与技术方案拆解并行 → 前后端并行开发 → 冒烟与测试 → 验收」的主线固定下来，并带评审打回的回退路径；线上一批需求工作项正分别停留在「后端开发」「测试」「需求评审」等节点。</p>
<div class="panel">{flow_svg([
    ("需求提交", "START · 开始", "start", ("human", "人提交")),
    ("需求评审", "TASK · 24h", "task", ("ai", "AI 起草 · 人签")),
    ("内容产出", "TASK · 并行", "parallel", ("human", "人产出")),
    ("技术方案拆解", "TASK · 并行", "parallel", ("ai", "AI 草案 · 人定")),
    ("前端开发", "TASK · 48h", "task", ("human", "人开发")),
    ("后端开发", "TASK · 48h", "task", ("ai", "AI 草案 · 人写")),
    ("开发冒烟", "TASK", "task", ("sys", "系统执行")),
    ("测试", "TASK · 24h", "task", ("human", "人测试")),
    ("验收", "END · 结束", "end", ("human", "人验收")),
], returns=[(7, 1, "测试不通过，回退需求评审")])}</div>
<p class="note"><b>AI 出现在三个环节，且都不替人做决定</b>：① <b>需求评审</b>——「需求评审专家」汇总需求描述、历史同类评审记录与知识库条款，产出评审意见草案并附引用，<b>评审人确认或改写后才进下游</b>；② <b>技术方案拆解</b>——「架构专家」基于代码仓库镜像与接口规范产出拆解草案，<b>方案由技术负责人定稿</b>；③ <b>后端开发</b>——「后端开发专家」产出接口定义与实现草案，工程师在此基础上编码。其余六个环节 AI 不介入：需求由人提交、交付内容由人产出、冒烟由系统跑、测试与验收由人签字。<b>客户价值</b>：需求从进到出全程有处理人与 SLA，AI 省掉的是起草和汇总的时间，不是评审的责任。</p>
""")
# ---- P19 场景二（制造业）+ 场景三（金融业） ------------------------------
PAGES.append(f"""
<h3 class="case">场景二　设备停机异常处置<span class="chip plan">制造业 · 交付模板</span></h3>
<p class="body tight">设备一停，生产、品质、计划三条线都在等；而维修记录多半散在纸质单和微信群里，同一个故障反复发生。这套流程把「异常提报 → 设备点检 → 维修方案评审 / 备件确认 → 现场维修 → 试机验证 → 恢复生产」固定下来，SLA 按小时计，试机不通过回退方案评审。更麻烦的是决策点没人签：方案要不要动工艺、备件要不要紧急采购，常常一个班组长就定了，事后查不到谁批的——流程把这些判断固定成节点，方案评审由设备主管签、备件确认由供应部门签。</p>
<div class="panel">{flow_svg([
    ("异常提报", "START · 0.5h", "start", ("human", "人提报")),
    ("设备点检", "TASK · 2h", "task", ("human", "人点检")),
    ("方案评审", "TASK · 4h", "task", ("ai", "AI 诊断 · 人签")),
    ("备件确认", "TASK · 并行", "parallel", ("ai", "AI 查库存 · 人签")),
    ("现场维修", "TASK · 8h", "task", ("human", "人维修")),
    ("试机验证", "TASK · 2h", "task", ("human", "人验证")),
    ("恢复生产", "END", "end", ("sys", "系统放行")),
], returns=[(5, 2, "试机未通过，回退方案评审")])}</div>
<p class="note"><b>AI 集中在两个决策环节，供给侧只给数据不给结论</b>：① <b>方案评审</b>——「设备故障诊断专家」读取设备手册、历史维修档案与该设备近三个月的点检记录，给出诊断假设与处置步骤草案，<b>维修方案由设备主管签字确认</b>；② <b>备件确认</b>——通过只读 MCP 工具查库存可用量与到货时间，把数据摆到供应部门面前，<b>买不买、走不走紧急采购仍由供应部门签</b>。提报、点检、维修、试机由人执行，恢复生产由系统放行——AI 不碰现场作业，也不碰设备启停。<b>客户价值</b>：停机时长第一次变成可量化数据（提报、点检、待件、维修各段都计时），维修知识沉淀成设备档案，而不是老师傅的脑子。</p>

<h3 class="case">场景三　对公授信审批<span class="chip plan">银行 · 交付模板</span></h3>
<p class="body tight">授信材料多、口径不一，客户经理反复补件；审查依赖个人经验，风险条款识别不一致；监管要求全链路留痕。这套流程把「授信申请 → 材料预审 → 客户经理尽调 → 风险审查 → 贷审会决议 → 合同签署」固定下来，审查不通过即回退补充材料。授信审批真正的成本在「来回补件」和「口径不一」：材料缺项往往到贷审会才暴露，同一类客户在不同审查人手里结论不同——流程把材料清单固化成起始表单，缺口在预审节点就暴露。</p>
<div class="panel">{flow_svg([
    ("授信申请", "START", "start", ("human", "人提交")),
    ("材料预审", "AI 辅助 · 24h", "ext", ("ai", "AI 校验 · 人复核")),
    ("客户经理尽调", "TASK · 48h", "task", ("human", "人尽调")),
    ("风险审查", "TASK · 48h", "task", ("ai", "AI 清单 · 人签")),
    ("贷审会决议", "DECISION", "decision", ("human", "人决策")),
    ("合同签署", "END", "end", ("sys", "写操作 → 人签")),
], returns=[(3, 1, "审查不通过，补件后重审")])}</div>
<p class="note"><b>AI 只做两件事，且都在人签字之前</b>：① <b>材料预审</b>——「授信材料合规专家」按制度条款做完整性与一致性校验，缺项在 24 小时内暴露，<b>复核结论由客户经理确认</b>；② <b>风险审查</b>——「财务分析专家」输出财务指标与异常项清单，<b>只供审查人参考、不替代审查结论</b>，意见逐条留痕。尽调与贷审会决议由人完成；合同签署属写操作，<b>强制转人工审批，批准后从断点恢复且只执行一次</b>。<b>客户价值</b>：审查人签的是自己的判断而不是「AI 说的」；每一次补件与意见修改都在审计链上，可直接应对监管检查。</p>
""")
# ---- P20 场景四（医疗健康）+ 场景五（能源电力） --------------------------
PAGES.append(f"""
<h3 class="case">场景四　不良事件上报与质控整改<span class="chip plan">医疗健康 · 交付模板</span></h3>
<p class="body tight">不良事件上报靠纸质和邮件，法定时限容易踩线；根因分析依赖质控办个人经验；整改是否真落地，追踪起来全靠催。这套流程把「事件上报 → 科室初评 → 质控办审核 → 根因分析与整改 → 整改验证 → 归档上报」固定下来，整改验证不通过回退重新分析。质控办最常被追问的两个问题是：这件事你们什么时候知道的、为什么整改报告还是上一版模板——流程让上报时间可查、整改方案可追溯。</p>
<div class="panel">{flow_svg([
    ("事件上报", "START · 4h", "start", ("human", "人上报")),
    ("科室初评", "TASK · 24h", "task", ("human", "人初评")),
    ("质控办审核", "TASK · 24h", "task", ("human", "人审核")),
    ("根因与整改", "AI 辅助 · 72h", "ext", ("ai", "AI 假设 · 人定稿")),
    ("整改验证", "TASK · 48h", "task", ("human", "人验证")),
    ("归档上报", "END", "end", ("ai", "AI 拟稿 · 人签发")),
], returns=[(4, 3, "整改验证不通过，回退重新分析")])}</div>
<p class="note"><b>AI 只在首尾两端介入，中间三段全由人把关</b>：① <b>根因分析与整改</b>——「医疗质控专家」按事件分级标准与历史案例给出根因假设与整改建议草案，<b>整改方案由科室与质控办定稿</b>；② <b>归档上报</b>——按上报口径用文档中心模板生成报告初稿，<b>由质控办签发</b>。上报、科室初评、质控办审核、整改验证全部由人执行并签字——医疗场景的合规底线是「不能让 AI 写结论」。<b>客户价值</b>：几个时限节点（上报 4h、初评 24h、整改 72h）全部有计时与超时预警，及时率可考核；整改闭环每一步都有责任人与留痕，质控办不必再追着科室要材料。</p>

<h3 class="case">场景五　客户报障抢修工单<span class="chip plan">能源电力 · 交付模板</span></h3>
<p class="body tight">报障电话进来要人工记、人工派，抢修班组凭经验跑现场；复电时间与客户回访口径常常对不齐。这套流程把「报障受理 → 智能派单 → 现场勘查与安全措施 → 抢修作业 → 复电确认 → 工单归档」固定下来，SLA 细化到半小时级，复电失败即回退抢修作业。抢修最贵的成本是时间，也最容易说不清：客户投诉超时，班组说路上堵、仓库说没备件——流程把每一段耗时切出来，超时点能精确定位到是派单慢还是待件久。</p>
<div class="panel">{flow_svg([
    ("报障受理", "START · 0.5h", "start", ("ai", "AI 转工单")),
    ("智能派单", "TASK · 1h", "task", ("ai", "AI 派单")),
    ("现场勘查", "TASK · 2h", "task", ("human", "人勘查")),
    ("抢修作业", "TASK · 4h", "task", ("ai", "AI 建议 · 人作业")),
    ("复电确认", "TASK · 1h", "task", ("human", "人确认")),
    ("工单归档", "END", "end", ("ai", "AI 拟回访稿")),
], returns=[(4, 3, "复电未成功，回退抢修")])}</div>
<p class="note"><b>这是 AI 介入最靠前的一个场景——从受理那一刻就开始了</b>：① <b>报障受理</b>——「客服受理专家」把通话与文字记录转成工单要素（地址、设备、故障现象、紧急等级），只读加生成、不直接改工单；② <b>智能派单</b>——按故障类型、班组技能标签与当前位置匹配派单对象，派单依据记入工单上下文，可回溯「为什么派给他」；③ <b>抢修作业</b>——「设备故障诊断专家」给出处置建议，<b>现场作业与安全措施由人执行</b>；④ <b>工单归档</b>——自动生成回访要点。现场勘查与复电确认由人判断——<b>复电属安全相关动作，不交给 AI 自动确认</b>。<b>客户价值</b>：从报障到复电每一段耗时都在系统里，超时自动预警到班组与管理层；抢修经验进知识库，新人不靠「跟着老师傅跑三年」。</p>
""")
# ---- P21 场景六（政务）+ 交付与落地方式 ---------------------------------
PAGES.append(f"""
<h3 class="case">场景六　群众诉求工单办理<span class="chip plan">政务与公共服务 · 交付模板</span></h3>
<p class="body tight">热线工单量大，分拨靠人工看内容判断归口部门，容易分错来回退单；答复口径不统一，同一诉求两个部门答得不一样；办结时限是硬考核。这套流程把「诉求受理 → 智能分拨 → 部门承办 → 答复审核 → 回访办结」固定下来，答复不合规即退回承办部门重办。工单的考核压力集中在时限与满意度上：分拨错一次就要退单重来，一天就没了——流程把五个环节各自定时限，哪个环节拖了进度一眼能看到。</p>
<div class="panel">{flow_svg([
    ("诉求受理", "START · 4h", "start", ("ai", "AI 分类提取")),
    ("智能分拨", "TASK · 4h", "task", ("ai", "AI 分拨")),
    ("部门承办", "TASK · 5d", "task", ("human", "人承办")),
    ("答复审核", "TASK · 24h", "decision", ("ai", "AI 校验 · 人签")),
    ("回访办结", "END · 24h", "end", ("human", "人回访")),
], returns=[(3, 2, "答复不合规，退回承办部门")])}</div>
<p class="note"><b>AI 覆盖前段两次判断与末段一次校验，中段由部门承办</b>：① <b>诉求受理</b>——自动做诉求分类与要素提取（类别、属地、紧急程度），为分拨提供依据；② <b>智能分拨</b>——按归口部门职责库匹配承办单位，<b>分拨结果可在承办阶段被退回修正</b>；③ <b>答复审核</b>——「政策口径专家」校验答复是否引用了现行有效政策条款、是否存在越界承诺，<b>不合规即退回，是否通过由审核人签</b>。部门承办与回访由人执行。<b>客户价值</b>：分拨准确率与退单率可以量化，答复口径一致性从「事后抽查」变成「事前卡点」，每个环节的时限与责任人都可追溯。</p>

<h3 class="case">交付与落地方式</h3>
<table>
  <tr><th style="width:26%">阶段</th><th style="width:22%">周期</th><th>交付内容</th></tr>
  <tr><td><b>环境部署</b></td><td>1 天</td><td>Docker 化私有部署，数据库 / 对象存储 / 缓存自持，数据不出企业内网</td></tr>
  <tr><td><b>组织与权限接入</b></td><td>2–3 天</td><td>钉钉 / 企业微信通讯录同步，角色与权限点按客户组织架构落位</td></tr>
  <tr><td><b>流程建模</b></td><td>1–2 周</td><td>2–3 条核心流程在画布上建模、绑定处理人与 SLA、发布上线</td></tr>
  <tr><td><b>Expert 绑定与试运行</b></td><td>1–2 周</td><td>按节点挂载 Expert、配置风险等级与审批要求，小范围试运行并调优</td></tr>
  <tr><td><b>审计验收</b></td><td>3–5 天</td><td>审计口径与导出规则确认，合规部门验收留痕能力</td></tr>
</table>
<p class="note">说明：上表为标准交付节奏，实际周期随流程条数与集成范围浮动；以上六套流程中，软件研发场景已在生产运行，其余五套为已完成节点规格设计的可交付模板，可直接导入画布调整后使用。业务人员在画布上可自助建模新流程，交付后边际成本趋近于零。</p>
""")
# ---- P22 一页纸总结 -----------------------------------------------------
PAGES.append(f"""
<h2 class="section">六、一页纸总结</h2>
<table>
  <tr><th style="width:21%">能力模块</th><th style="width:41%">能力落地形态</th><th>客户价值</th></tr>
  <tr><td><b>流程协同引擎</b></td><td>画布建模 + 版本化模板 + 工作项台账；需求、问题两类流程已在实际运行</td><td>流程成为可版本化的资产，业务自助建模，改流程不再等开发</td></tr>
  <tr><td><b>Expert OS</b></td><td>Expert / Skill 版本化注册与发布，Deployment 挂载到流程节点</td><td>专家经验沉淀为不可变版本的 AI 资产，可复用、可回溯</td></tr>
  <tr><td><b>人机协同与闸控</b></td><td>工具按风险分级，写操作强制转人工，批准后从断点恢复执行一次</td><td>AI 敢干重活，责任边界仍在人手里</td></tr>
  <tr><td><b>运行与审计</b></td><td>每次运行带 Trace 可回放；审计不可删改，导出行为再审计</td><td>每一次 AI 产出可复现、可归因，审计与合规部门可直接采纳</td></tr>
  <tr><td><b>AiChat</b></td><td>按 Expert 发起的对话工作台，会话与项目、工作项绑定</td><td>员工的 AI 入口收回治理边界内，数据不出内网</td></tr>
  <tr><td><b>治理底座</b></td><td>角色 × 权限点矩阵；多渠道通知；文档走校验链与敏感分级</td><td>AI 与人在同一套组织、权限、通知语义里协作</td></tr>
  <tr><td><b>知识与记忆</b></td><td>文档中心的上传校验链、版本与归档已在运行；知识库与 Memory 的界面与版本模型已就位，检索索引与留存服务按交付批次开放</td><td>AI 的判断有出处、经验可积累，任务产出沉淀为企业资产</td></tr>
  <tr><td><b>开放接入</b></td><td>FlowHub as MCP Server + Access Key 分发；外部工具经 MCP 接入；代码仓库镜像</td><td>不替换企业已有智能体，给它们流程与权限</td></tr>
</table>

<p class="body" style="margin-top:18px">一句话概括：<b>FlowHub 让企业的每一条流程都成为可版本化、可审计的资产，并让 AI 专家在治理边界内成为流程的「数字员工」。</b>对已经在用 Dify / n8n / OA 审批的企业，FlowHub 不是替换，而是补上它们都不负责的那一层——业务流程的责任边界。</p>

<p class="note" style="margin-top:22px">说明：① 本文档中的界面截图采集自 FlowHub 客户部署实例（采集日期 2026-09-16），涉及个人姓名的列与字段已做脱敏处理；HTML 版本中<b>所有图片点击即可放大查看</b>，PDF 版本可直接放大页面。② 第五章六套流程中，软件与 IT 服务（需求变更协同）已在上述实例生产运行，制造业、银行、医疗健康、能源电力、政务与公共服务五套为已完成节点规格设计的可交付模板，可直接导入画布调整后使用，文中已逐条标注。③ 本文正文不引用该实例的运营数据统计，截图中的数字为界面本身的实时显示，仅用于说明产品形态；知识库检索索引与 Memory 留存服务尚未在该实例接入，涉及处已单独标注。④ 第四章竞品信息依据各产品 2026-09 公开文档与社区实践，具体版本能力可能随对方迭代变化，选型前建议再核对一次。</p>
""")


def main():
    print("嵌入截图：")
    body = "\n".join(page(p, i + 1) for i, p in enumerate(PAGES))
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FlowHub 企业服务案例介绍</title>
<style>{CSS}</style>
</head>
<body>
{body}
{LIGHTBOX}
</body>
</html>
"""
    html = re.sub(r"\n{3,}", "\n\n", html)
    OUT.write_text(html, encoding="utf-8")
    print(f"\n共 {len(PAGES)} 页 → {OUT.name}  ({len(html)/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
