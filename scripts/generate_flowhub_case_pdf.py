"""Generate a standalone two-page FlowHub enterprise service casebook."""
from pathlib import Path
import os
import fitz


OUTPUT = Path('/Users/sli/Documents/self/FlowHub企业服务案例介绍.pdf')
FONT = '/Users/sli/Library/Fonts/NotoSansSC.ttf'
W, H = 612, 792

INK = (15 / 255, 23 / 255, 42 / 255)
MUTED = (71 / 255, 85 / 255, 105 / 255)
LINE = (226 / 255, 232 / 255, 240 / 255)
BLUE = (37 / 255, 99 / 255, 235 / 255)
BLUE_LIGHT = (239 / 255, 246 / 255, 255 / 255)
PURPLE = (124 / 255, 58 / 255, 237 / 255)
PURPLE_LIGHT = (245 / 255, 243 / 255, 255 / 255)
CYAN = (8 / 255, 145 / 255, 178 / 255)
CYAN_LIGHT = (236 / 255, 254 / 255, 255 / 255)
ORANGE = (234 / 255, 88 / 255, 12 / 255)
ORANGE_LIGHT = (255 / 255, 247 / 255, 237 / 255)
SLATE = (51 / 255, 65 / 255, 85 / 255)


def rect(page, x0, y0, x1, y1, *, fill=None, stroke=None, radius=0, width=0.7):
    shape = page.new_shape()
    box = fitz.Rect(x0, y0, x1, y1)
    # PyMuPDF 1.24 does not provide a stable rounded-rectangle primitive across
    # all macOS builds; the restrained square cards also match the source deck.
    shape.draw_rect(box)
    shape.finish(color=stroke, fill=fill, width=width)
    shape.commit()


def text(page, box, value, size=10, color=INK, bold=False, align=0, leading=None):
    return page.insert_textbox(
        fitz.Rect(box), value, fontname='noto', fontfile=FONT,
        fontsize=size, color=color, align=align,
        lineheight=leading or 1.35,
        render_mode=0,
    )


def line(page, x0, y0, x1, y1, color=LINE, width=0.7):
    page.draw_line((x0, y0), (x1, y1), color=color, width=width)


def header(page, page_number, eyebrow, title, subtitle):
    rect(page, 0, 0, W, 8, fill=BLUE)
    text(page, (44, 35, 568, 54), eyebrow, 9, BLUE)
    text(page, (44, 54, 568, 87), title, 18.5, INK)
    text(page, (44, 97, 568, 122), subtitle, 9.2, MUTED)
    line(page, 44, 136, 568, 136, BLUE, 1.2)
    text(page, (44, 754, 490, 774), 'FlowHub 企业服务案例 · 流程协同引擎 × AI 专家平台', 8, MUTED)
    text(page, (520, 754, 568, 774), str(page_number), 8, MUTED, align=2)


def pill(page, x, y, label, fill, color):
    width = max(50, len(label) * 8 + 18)
    rect(page, x, y, x + width, y + 20, fill=fill, radius=10)
    text(page, (x + 7, y + 4, x + width - 7, y + 18), label, 7.8, color, align=1)
    return width


def compare_card(page, x, y, width, title, focus, limitation, accent, light):
    rect(page, x, y, x + width, y + 118, fill=(1, 1, 1), stroke=LINE, radius=8)
    rect(page, x, y, x + 5, y + 118, fill=accent, radius=3)
    text(page, (x + 16, y + 13, x + width - 12, y + 32), title, 10, INK)
    text(page, (x + 16, y + 40, x + width - 12, y + 64), '强项  ' + focus, 8.3, MUTED)
    rect(page, x + 14, y + 72, x + width - 12, y + 108, fill=light, radius=4)
    text(page, (x + 20, y + 77, x + width - 18, y + 103), limitation, 6.5, accent, leading=1.15)


def layer(page, y, title, detail, fill, accent, icon):
    rect(page, 61, y, 551, y + 48, fill=fill, stroke=LINE, radius=8)
    rect(page, 74, y + 11, 100, y + 37, fill=accent, radius=5)
    text(page, (77, y + 15, 97, y + 34), icon, 10, (1, 1, 1), align=1)
    text(page, (113, y + 10, 245, y + 28), title, 9.4, INK)
    text(page, (113, y + 27, 535, y + 43), detail, 8, MUTED)


def draw_architecture(page):
    text(page, (44, 455, 568, 478), '一体化架构：把流程、AI 与企业治理放进同一条责任链', 12, INK)
    layer(page, 486, '业务流程层', '画布 · 任务 · SLA · 表单 · 并行 / 回退', BLUE_LIGHT, BLUE, '流')
    layer(page, 541, 'AI 执行层', '节点 Expert · 上游上下文 · 知识库 · MCP / 工具', PURPLE_LIGHT, PURPLE, '智')
    layer(page, 596, '开放接入层', 'n8n 自动化 · Dify / 自建 Agent · 既有企业系统', CYAN_LIGHT, CYAN, '接')
    layer(page, 651, '风险与审计层', '权限 · 动作分级 · 人工审批 · Run / Trace · 审计导出', ORANGE_LIGHT, ORANGE, '管')
    for y in (534, 589, 644):
        page.draw_line((306, y), (306, y + 7), color=SLATE, width=1)
        page.draw_line((302, y + 3), (306, y + 7), color=SLATE, width=1)
        page.draw_line((310, y + 3), (306, y + 7), color=SLATE, width=1)
    text(page, (64, 710, 548, 735), '接入而非替换：复用已有自动化与 Agent 能力，但任务状态、授权范围、风险审批与审计口径统一由 FlowHub 承接。', 8.6, SLATE)


def page_one(doc, number):
    page = doc.new_page(width=W, height=H)
    header(page, number, '企业 AI 服务案例 · FlowHub', 'FlowHub：让 AI 在正式流程中“能干活，也能管得住”', '流程协同引擎 × AI 专家平台 · 将内部 Expert、外部 Agent 与组织治理放入同一生产闭环')
    text(page, (44, 154, 568, 228), '企业推进 AI 时，往往面临两种割裂：传统审批 / BPM 系统擅长流程、权限与留痕，却只能把 AI 当作填单或摘要插件；通用 Agent 与自动化平台擅长生成、调用工具和连接系统，却通常不掌握一笔业务任务的责任人、SLA、审批链路与审计口径。FlowHub 的定位不是替换它们，而是成为企业 AI 的流程与治理层。', 10.1, INK)
    text(page, (44, 246, 568, 268), '产品定位比照', 12, INK)
    compare_card(page, 44, 280, 162, 'n8n / 自动化编排', '系统触发、API 串联、数据搬运', '结果进入正式任务：处理人、SLA、验收与回退。', CYAN, CYAN_LIGHT)
    compare_card(page, 225, 280, 162, 'Dify / Agent 构建', '知识库、对话、提示词与工具调用', '节点 AI：审批、审计、版本可追溯。', PURPLE, PURPLE_LIGHT)
    compare_card(page, 406, 280, 162, '审批 / OA / BPM', '表单、权限、审批与流程流转', '节点 AI：分析、草案、受控工具。', ORANGE, ORANGE_LIGHT)
    rect(page, 44, 412, 568, 441, fill=BLUE, radius=7)
    text(page, (58, 419, 554, 436), 'FlowHub 的关键优势：把“事怎么走、AI 怎么干、谁来批准、过程如何追溯”统一为同一套运行语义。', 9.3, (1, 1, 1), align=1)
    draw_architecture(page)
    return page


def case_card(page, x, y, title, flow, ai, value, accent, light):
    rect(page, x, y, x + 250, y + 140, fill=(1, 1, 1), stroke=LINE, radius=8)
    rect(page, x, y, x + 250, y + 6, fill=accent, radius=4)
    text(page, (x + 14, y + 18, x + 236, y + 38), title, 10.5, INK)
    text(page, (x + 14, y + 48, x + 236, y + 74), '流程  ' + flow, 8.1, MUTED)
    rect(page, x + 12, y + 82, x + 238, y + 108, fill=light, radius=4)
    text(page, (x + 18, y + 87, x + 232, y + 103), 'AI  ' + ai, 7.7, accent)
    text(page, (x + 14, y + 116, x + 236, y + 133), '价值  ' + value, 7.6, SLATE)


def step(page, x, y, index, title, detail, accent, light):
    rect(page, x, y, x + 96, y + 85, fill=(1, 1, 1), stroke=LINE, radius=7)
    rect(page, x + 10, y + 10, x + 31, y + 31, fill=light, radius=11)
    text(page, (x + 10, y + 14, x + 31, y + 29), str(index), 8, accent, align=1)
    text(page, (x + 10, y + 40, x + 86, y + 55), title, 8.2, INK, align=1)
    text(page, (x + 10, y + 57, x + 86, y + 78), detail, 5.9, MUTED, align=1, leading=1.15)


def page_two(doc, number):
    page = doc.new_page(width=W, height=H)
    header(page, number, '企业 AI 服务案例 · FlowHub', '三个业务场景 + 一条外部 Agent 接入链路', '从内部协同到既有智能体接入：AI 进入节点执行，人保留决策权，过程可回放、可审计')
    text(page, (44, 154, 568, 178), '代表性可交付场景', 12, INK)
    case_card(page, 44, 190, '研发需求评审与交付闭环', '需求受理 → 分析 → 开发 → 测试 → 验收', '汇总上游信息、辅助分析与修复草案', '测试回退与问题验证形成独立闭环', BLUE, BLUE_LIGHT)
    case_card(page, 318, 190, '合同多级审批与 AI 法务预审', '发起 → AI 预审 → 法务 / 财务 / 负责人审批', '合同摘要、风险条款识别、预审意见', 'AI 提效，人做最终决策，文件留痕', ORANGE, ORANGE_LIGHT)
    case_card(page, 44, 348, '新员工入职跨部门协同', 'HR 审核 → IT / 行政 / 导师并行 → 汇合确认', '材料检查、任务说明、入职问答辅助', '从群聊催办变为有时限的服务流程', PURPLE, PURPLE_LIGHT)
    rect(page, 318, 348, 568, 488, fill=CYAN_LIGHT, stroke=(153 / 255, 246 / 255, 228 / 255), radius=8)
    pill(page, 332, 362, '生态接入能力', CYAN, (1, 1, 1))
    text(page, (332, 395, 553, 418), '不重建已有 Agent，给它们流程与治理。', 10.3, INK)
    text(page, (332, 426, 553, 474), '外部 Agent、供应商 Agent 或自研系统可接管被授权节点；它拿到的是任务上下文与操作边界，而非脱离业务的泛化聊天入口。', 8.2, SLATE)
    text(page, (44, 518, 568, 541), '外部 Agent 接入操作链路', 12, INK)
    steps = [
        ('授权身份', 'Access Key\n限定权限'),
        ('获取任务', 'MCP / 标准接口\n领取节点'),
        ('取得上下文', '任务、上游结果\n表单与边界'),
        ('回传结果', '分析、填报或\n执行结果提交'),
        ('流转与审计', '审批闸控、继续\n流转、全程留痕'),
    ]
    for i, (title, detail) in enumerate(steps):
        x = 44 + i * 105
        step(page, x, 555, i + 1, title, detail, CYAN, CYAN_LIGHT)
        if i < len(steps) - 1:
            page.draw_line((x + 96, 595), (x + 103, 595), color=CYAN, width=1.2)
            page.draw_line((x + 99, 591), (x + 103, 595), color=CYAN, width=1.2)
            page.draw_line((x + 99, 599), (x + 103, 595), color=CYAN, width=1.2)
    rect(page, 44, 658, 568, 711, fill=(248 / 255, 250 / 255, 252 / 255), stroke=LINE, radius=7)
    text(page, (60, 671, 552, 698), '核心结论：FlowHub 不与 n8n、Dify 或既有审批系统争夺单点能力；它让这些能力在同一个任务状态、组织权限、风险审批与审计边界内协同运行。', 9, INK, align=1)
    text(page, (44, 724, 568, 741), '注：合同审批与入职协同为可交付流程模板；其余功能描述均基于 FlowHub 现有案例材料。', 7.4, MUTED)
    return page


def main():
    output = fitz.open()
    page_one(output, 1)
    page_two(output, 2)
    output.set_metadata({
        'title': 'FlowHub企业服务案例介绍',
        'author': 'FlowHub',
        'subject': 'FlowHub 企业流程协同与 AI 专家平台案例',
    })
    temporary = OUTPUT.with_name(OUTPUT.stem + '.tmp.pdf')
    if temporary.exists():
        temporary.unlink()
    output.save(temporary, garbage=4, deflate=True)
    output.close()
    os.replace(temporary, OUTPUT)
    print(OUTPUT)


if __name__ == '__main__':
    main()
