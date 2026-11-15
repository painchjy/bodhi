"""Slide definitions for Bodhi2 (1600x900 design units)."""

import pathlib

from deck_lib import (
    AMBER, AMBER_SOFT, BLUE, BLUE_SOFT, CYAN, CYAN_SOFT, EMERALD, EMERALD_SOFT,
    FAINT, GRAY_SOFT, INK, LINE, MONO, MUTED, PAPER, PAPER2, ROSE, ROSE_SOFT,
    SANS, VIOLET, VIOLET_SOFT, WHITE,
    bullets, card, dot, image, label, line, pill, rect, tag, text,
)

HERE = pathlib.Path(__file__).resolve().parent


def header(kicker, title_runs, sub):
    return [
        text(80, 42, 1000, 30, kicker, size=20, color=CYAN, bold=True),
        text(80, 72, 1500, 80, runs=title_runs, size=54, bold=True, color=INK),
        text(80, 154, 1460, 40, sub, size=24, color=MUTED, spacing=2),
    ]


def footer(s, page):
    return [
        text(80, 858, 1350, 26, s, size=16, color=FAINT, spacing=0),
        text(1440, 858, 80, 26, page, size=16, color=FAINT, font=MONO, align="right", spacing=0),
    ]


def band_label(x, y, layer, name, note, color):
    return [
        tag(x, y, 120, 34, layer, color, WHITE, size=17),
        text(x, y + 42, 220, 38, name, size=28, color=color, bold=True),
        text(x, y + 80, 220, 26, note, size=18, color=MUTED),
    ]


# ===================== P1 四层架构 =====================

def slide1():
    e = []
    e += header(
        "03 / 分层架构",
        [{"s": "分层架构：", "color": INK}, {"s": "从本体到智能体", "color": VIOLET}],
        "本体 → 工具/技能 → 领域知识库 → 智能体；企业知识建模智能体 + 企业知识管理领域是上两层中的虚拟列。",
    )

    # 智能体层（顶）
    e += [rect(80, 210, 1080, 120, fill=EMERALD_SOFT, line=EMERALD, radius=14)]
    e += [tag(100, 222, 80, 28, "智能体", EMERALD, WHITE, size=14),
          text(100, 254, 200, 26, "决策与编排", size=16, color=MUTED)]
    agents = ["技术方案评审智能体", "重点产品知识问答智能体", "其他领域智能体"]
    for i, name in enumerate(agents):
        x = 320 + i * 250
        e += [card(x, 228, 230, 80, fill=WHITE, line=EMERALD, radius=10)]
        e += [text(x, 228, 230, 80, name, size=15, bold=True,
                   color=EMERALD, align="center", valign="middle")]

    # 领域知识库层
    e += [rect(80, 345, 1080, 105, fill=CYAN_SOFT, line=CYAN, radius=14)]
    e += [tag(100, 355, 150, 28, "领域本体实例知识库", CYAN, WHITE, size=12),
          text(100, 387, 200, 24, "业务技能、流程、数据", size=14, color=MUTED)]
    domains = ["技术方案评审领域库", "重点产品知识库", "其他领域"]
    for i, name in enumerate(domains):
        x = 320 + i * 250
        e += [card(x, 358, 230, 70, fill=WHITE, line=CYAN, radius=10)]
        e += [text(x, 358, 230, 70, name, size=15, bold=True, color=CYAN,
                   align="center", valign="middle")]

    # 工具 + 技能层
    e += [rect(80, 465, 1440, 175, fill=ROSE_SOFT, line=ROSE, radius=14)]
    e += [tag(100, 475, 120, 28, "工具 + 技能", ROSE, WHITE, size=13),
          text(100, 507, 200, 24, "概率性 + 确定性", size=15, color=MUTED)]
    groups = [("执行边界", "8"), ("知识规范", "6"), ("知识完整", "8"),
              ("运行控制", "8+4"), ("跨库迁移", "9"), ("外部连接", "4")]
    for i, (name, n) in enumerate(groups):
        x = 320 + (i % 3) * 250
        y = 500 + (i // 3) * 32
        e += [card(x, y, 240, 28, fill=WHITE, line=ROSE, radius=7)]
        e += [text(x + 8, y + 3, 170, 22, name, size=13, bold=True, color=ROSE),
              text(x + 190, y + 2, 40, 24, n, size=15, bold=True, color=ROSE, font=MONO, align="right")]
    skills = ["知识计划", "知识提取", "规则判定", "报告组装", "会话溯源", "库迁移"]
    for i, s in enumerate(skills):
        x = 320 + i * 140
        e += [rect(x, 580, 130, 30, fill=WHITE, line=ROSE, radius=7)]
        e += [text(x, 580, 130, 30, s, size=13, color=ROSE, align="center", valign="middle")]

    # 本体模型层（底）
    e += [rect(80, 650, 1440, 120, fill=VIOLET_SOFT, line=VIOLET, radius=14)]
    e += [tag(100, 660, 100, 28, "本体模型", VIOLET, WHITE, size=13),
          text(100, 692, 200, 24, "知识的基础规范", size=15, color=MUTED)]
    mods = ["SCHEMA", "BMM", "BPMN", "AGENT"]
    for i, m in enumerate(mods):
        x = 320 + i * 160
        e += [rect(x, 665, 150, 45, fill=WHITE, line=VIOLET, radius=8)]
        e += [text(x, 665, 150, 45, m, size=15, bold=True, color=VIOLET, font=MONO,
                   align="center", valign="middle")]
    e += [text(1300, 665, 160, 24, "54 类 / 71 属性", size=14, bold=True, color=VIOLET),
          text(1300, 695, 160, 24, "55 数据属性", size=13, color=MUTED)]

    # 虚拟列：只在上两层（智能体 + 领域知识库）
    e += [rect(1180, 210, 340, 240, fill=AMBER_SOFT, line=AMBER, radius=16, dash=True)]
    e += [text(1192, 216, 300, 20, "虚拟列（仅上两层）", size=10, color=AMBER, bold=True)]
    e += [card(1192, 238, 316, 76, fill=WHITE, line=EMERALD, radius=10)]
    e += [text(1204, 244, 292, 24, "企业知识建模智能体", size=16, bold=True, color=EMERALD),
          text(1204, 270, 292, 38, "基于本体，构建企业领域知识（本体实例）", size=11, color=MUTED)]
    e += [line(1350, 314, 1350, 334, color=AMBER, width=2, arrow=True)]
    e += [text(1360, 318, 50, 20, "使用", size=10, color=AMBER)]
    e += [card(1192, 340, 316, 98, fill=WHITE, line=AMBER, radius=10)]
    e += [text(1204, 346, 292, 24, "企业知识管理领域", size=14, bold=True, color=AMBER),
          text(1204, 374, 292, 54, "意图工程、规则工程、流程建模…\nT1–T7 任务页 / 规则 / I-O 契约 / 模板", size=11, color=INK, spacing=2)]

    # 全宽层说明
    e += [text(1200, 500, 280, 24, "工具 + 技能：全宽层", size=12, bold=True, color=ROSE),
          text(1200, 528, 280, 44, "不含虚拟列\n43 工具 / 6 技能", size=11, color=MUTED, spacing=2)]

    e += [text(80, 790, 1440, 26,
               "企业知识建模智能体 + 企业知识管理领域是智能体/领域知识两层中的虚拟列，区别于其他领域智能体/领域知识；不贯穿工具/本体层。",
               size=12.5, color=INK)]
    e += footer("企业知识建模智能体 + 企业知识管理领域是上两层中的虚拟列；建模智能体用它创建其他领域知识。", "03 / 08")
    return {"bg": PAPER, "elements": e}
# ===================== P2 四层协作 =====================

def slide2():
    e = []
    e += header(
        "04 / 两个闭环",
        [{"s": "两个闭环：", "color": INK}, {"s": "领域知识与能力建设", "color": EMERALD}],
        "建模智能体在中间串接两个闭环：读取企业知识管理领域、优化领域知识；自身不更新。",
    )

    # E2E 测试与定位（上层）
    e += [card(440, 200, 720, 120, fill=CYAN_SOFT, line=CYAN, radius=16)]
    e += [text(460, 204, 680, 30, "E2E 测试与定位（接受领域 BAD CASE）", size=17, bold=True, color=CYAN),
          text(460, 232, 680, 22, "输入：现象 / 期望 / 实际 / 证据", size=12, color=INK)]
    causes = ["领域知识", "工具返回", "技能", "大模型推理"]
    for i, c in enumerate(causes):
        x = 460 + i * 170
        e += [rect(x, 258, 155, 28, fill=WHITE, line=CYAN, radius=7)]
        e += [text(x, 258, 155, 28, c, size=12, color=CYAN, align="center", valign="middle")]
    e += [text(460, 292, 680, 22, "输出：根因 + 同一 case 回归验证", size=12, color=INK)]
    e += [line(800, 320, 800, 396, color=CYAN, width=3, arrow=True)]

    # 中间通道：建模智能体
    e += [card(580, 330, 440, 340, fill=EMERALD_SOFT, line=EMERALD, radius=16)]
    e += [text(600, 340, 300, 30, "建模智能体", size=20, bold=True, color=EMERALD),
          text(600, 372, 300, 22, "中间通道", size=13.5, color=EMERALD)]
    e += [rect(660, 400, 240, 70, fill=WHITE, line=EMERALD, radius=12)]
    e += [text(660, 400, 240, 70, "建模智能体", size=18, bold=True, color=EMERALD,
               align="center", valign="middle")]
    e += [text(600, 500, 400, 24, "读取：企业知识管理领域", size=14, color=AMBER),
          text(600, 535, 400, 24, "优化：领域知识", size=14, color=CYAN),
          text(600, 570, 400, 24, "自身不更新", size=13, bold=True, color=ROSE)]

    # 左：领域知识闭环（全自动）
    e += [card(80, 330, 480, 340, fill=WHITE, line=EMERALD, radius=16)]
    e += [text(100, 340, 420, 28, "领域知识闭环（全自动）", size=18, bold=True, color=EMERALD),
          text(100, 370, 420, 22, "只改领域知识", size=12, color=MUTED)]
    e += [rect(110, 410, 150, 50, fill=PAPER2, line=AMBER, radius=9)]
    e += [text(110, 410, 150, 50, "领域智能体", size=15, bold=True, color=AMBER, align="center", valign="middle")]
    e += [rect(300, 410, 120, 50, fill=PAPER2, line=ROSE, radius=9)]
    e += [text(300, 410, 120, 50, "BAD CASE", size=14, bold=True, color=ROSE, align="center", valign="middle")]
    e += [rect(110, 540, 150, 50, fill=PAPER2, line=CYAN, radius=9)]
    e += [text(110, 540, 150, 50, "更新领域知识", size=14, bold=True, color=CYAN, align="center", valign="middle")]
    e += [line(260, 435, 296, 435, color=EMERALD, width=3, arrow=True),
          line(420, 435, 656, 435, color=EMERALD, width=3, arrow=True),
          line(660, 470, 260, 565, color=EMERALD, width=3, arrow=True),
          line(185, 540, 185, 464, color=EMERALD, width=3, arrow=True)]
    e += [text(262, 416, 60, 18, "发现", size=12, color=EMERALD),
          text(430, 416, 80, 18, "输入", size=12, color=EMERALD),
          text(430, 500, 80, 18, "优化", size=12, color=EMERALD),
          text(195, 500, 60, 18, "回归", size=12, color=EMERALD)]
    e += [text(100, 610, 420, 22, "领域智能体不变；harness 入口", size=11, color=MUTED)]

    # 右：能力建设闭环
    e += [card(1040, 330, 480, 340, fill=WHITE, line=ROSE, radius=16)]
    e += [text(1060, 340, 420, 28, "能力建设闭环", size=18, bold=True, color=ROSE),
          text(1060, 370, 440, 22, "开发智能体驱动；改工具/技能/本体/EM 领域", size=11.5, color=MUTED)]
    e += [rect(1080, 410, 160, 50, fill=PAPER2, line=ROSE, radius=9)]
    e += [text(1080, 410, 160, 50, "开发智能体", size=15, bold=True, color=ROSE, align="center", valign="middle")]
    e += [rect(1260, 410, 180, 50, fill=PAPER2, line=VIOLET, radius=9)]
    e += [text(1260, 410, 180, 50, "更新工具/技能/本体", size=13, bold=True, color=VIOLET, align="center", valign="middle")]
    e += [rect(1080, 540, 200, 50, fill=PAPER2, line=AMBER, radius=9)]
    e += [text(1080, 540, 200, 50, "企业知识管理领域", size=14, bold=True, color=AMBER, align="center", valign="middle")]
    e += [line(920, 435, 1076, 435, color=ROSE, width=3, arrow=True),
          line(1240, 435, 1256, 435, color=ROSE, width=3, arrow=True),
          line(1160, 460, 1160, 536, color=ROSE, width=3, arrow=True),
          line(1280, 565, 1510, 565, color=ROSE, width=3, dash=True),
          line(1510, 565, 1510, 310, color=ROSE, width=3, dash=True),
          line(1510, 310, 1164, 310, color=ROSE, width=3, dash=True, arrow=True),
          line(1080, 565, 1024, 565, color=AMBER, width=3),
          line(1024, 565, 1024, 455, color=AMBER, width=3),
          line(1024, 455, 904, 455, color=AMBER, width=3, arrow=True)]
    e += [text(930, 416, 70, 18, "分派", size=12, color=ROSE),
          text(1170, 490, 90, 18, "更新", size=12, color=ROSE),
          text(1370, 286, 120, 18, "回归 E2E", size=11, color=ROSE)]
    e += [text(1060, 610, 440, 22, "智能体不变；开发智能体驱动能力变更", size=11, color=MUTED)]

    e += [rect(80, 700, 1440, 50, fill=INK, line=INK, radius=12)]
    e += [text(100, 700, 1420, 50,
               "建模智能体是中间通道；领域知识闭环只改领域知识，能力建设闭环由开发智能体驱动。",
               size=14, bold=True, color=WHITE, valign="middle")]

    e += footer("领域知识闭环全自动；能力建设闭环由开发智能体驱动；建模智能体自身不更新。", "04 / 08")
    return {"bg": PAPER, "elements": e}

# ===================== P3 本体 × 领域知识 =====================

def slide3():
    e = []
    e += header(
        "05 / 本体与知识",
        [{"s": "本体定规范，", "color": INK}, {"s": "领域知识装业务", "color": CYAN}],
        "本体（TBox）只说类型与约束；领域知识（ABox）只说实例、关系与来源。",
    )

    stats = [("4", "本体模块"), ("54", "类"), ("71", "对象属性"), ("55", "数据属性")]
    for i, (n, name) in enumerate(stats):
        x = 80 + i * 360
        e += [card(x, 200, 340, 54, fill=WHITE, line=LINE, radius=10)]
        e += [text(x + 16, 204, 80, 44, n, size=30, bold=True, color=VIOLET, font=MONO),
              text(x + 80, 214, 240, 28, name, size=18, color=MUTED)]

    # TBox
    e += [rect(80, 275, 610, 460, fill=PAPER2, line=LINE, radius=14)]
    e += [tag(100, 290, 110, 32, "TBox 本体", VIOLET, VIOLET_SOFT, size=16),
          text(220, 294, 300, 26, "只描述类型 · 不写实例", size=17, color=MUTED)]
    mods = [
        ("SCHEMA", "创作 / 模板 / 正文", "CreativeWork · DocumentTemplate · articleBody"),
        ("BMM", "意图 / 规则 / 报告", "Goal · BusinessPolicy · BusinessRule · Report"),
        ("BPMN", "流程 / 活动 / 任务", "BusinessProcess · Activity · Task · DataObject"),
        ("AGENT", "智能体 / 技能 / 工具", "Agent · Skill · Tool · WorkKnowledgeBase"),
    ]
    for i, (m, note, ex) in enumerate(mods):
        y = 335 + i * 88
        e += [card(100, y, 570, 78, fill=WHITE, line=VIOLET, radius=10)]
        e += [text(114, y + 8, 130, 28, m, size=18, bold=True, color=VIOLET, font=MONO),
              text(250, y + 10, 400, 26, note, size=16, color=MUTED, align="right"),
              text(114, y + 42, 540, 24, ex, size=14, color=INK, font=MONO)]

    # Gate
    e += [tag(710, 290, 96, 34, "校验闸门", AMBER, AMBER_SOFT, size=16)]
    gates = ["类型在册", "属性已声明", "domain / range", "溯源可回访"]
    for i, g in enumerate(gates):
        y = 350 + i * 70
        e += [card(710, y, 135, 50, fill=WHITE, line=AMBER, radius=9)]
        e += [text(710, y, 135, 50, g, size=13, bold=True, color=AMBER,
                   align="center", valign="middle")]

    # ABox
    e += [rect(850, 275, 670, 460, fill=PAPER2, line=LINE, radius=14)]
    e += [tag(870, 290, 110, 32, "ABox 知识", CYAN, CYAN_SOFT, size=16),
          text(990, 294, 300, 26, "一页 = 一个实例", size=17, color=MUTED)]
    e += [rect(870, 335, 630, 42, fill=INK, line=INK, radius=8)]
    e += [text(884, 335, 400, 42, "slug = 模块 / 类 / 名称", size=16, color=WHITE, font=MONO,
               valign="middle"),
          text(1250, 339, 236, 34, "bmm:OperativeBusinessRule", size=13, color="9FE7DF",
               font=MONO, align="right")]
    e += [card(870, 388, 305, 130, fill=WHITE, line=LINE, radius=8)]
    e += [text(884, 396, 280, 26, "数据属性", size=16, bold=True, color=CYAN),
          text(884, 426, 280, 84, "ruleHash = sha1:…\nknowledgeNeed = kn/text-logic-v1\nruleImplementation = LLM 软规则", size=14, color=INK, font=MONO, spacing=2)]
    e += [card(1195, 388, 305, 130, fill=WHITE, line=LINE, radius=8)]
    e += [text(1209, 396, 280, 26, "对象关系（图边）", size=16, bold=True, color=CYAN),
          text(1209, 426, 290, 84, "isDerivedFrom → bmm:BusinessPolicy\nguides → bmm:BusinessProcess\nsourceSession → bmm:KnowledgeSession", size=12.5, color=INK, font=MONO, spacing=2)]
    e += [card(870, 530, 630, 82, fill=WHITE, line=LINE, radius=8)]
    e += [text(884, 538, 280, 26, "正文", size=16, bold=True, color=CYAN),
          text(884, 566, 280, 36, "## 规则契约\n## 原文依据（逐字 + 定位）", size=14, color=INK, font=MONO, spacing=2)]
    e += [text(1195, 538, 290, 26, "溯源", size=16, bold=True, color=CYAN),
          text(1195, 566, 290, 36, "session / kb:…/doc:…#section\n定位可回访", size=14, color=INK, font=MONO, spacing=2)]
    e += [card(870, 622, 630, 98, fill=WHITE, line=CYAN, radius=8)]
    e += [text(884, 630, 280, 26, "同名跨库先判", size=16, bold=True, color=CYAN),
          text(884, 660, 590, 24, "equivalent / distinct / unknown", size=15, color=INK, font=MONO),
          text(884, 690, 590, 22, "未裁决前不当同义词用；权威可写、副本只读。", size=14, color=MUTED)]

    # KB roles
    roles = [
        ("企业本体模型", "只读 · 类型与约束来源", VIOLET),
        ("企业共享概念模型", "跨库同名治理", CYAN),
        ("领域知识库", "定义业务智能体 / 技能 / 工具", EMERALD),
        ("工作知识库", "业务运行时读写", AMBER),
    ]
    for i, (name, note, color) in enumerate(roles):
        x = 80 + i * 360
        e += [card(x, 755, 340, 62, fill=WHITE, line=color, radius=10)]
        e += [text(x + 12, 762, 316, 26, name, size=16, bold=True, color=color),
              text(x + 12, 788, 316, 22, note, size=13, color=MUTED)]

    e += footer("本体不写实例；领域库不写类型。页面可读、图边可推理、溯源可回访。", "05 / 08")
    return {"bg": PAPER, "elements": e}


# ===================== P4 本体的作用 =====================

def slide4():
    e = []
    e += header(
        "06 / 业务问题",
        [{"s": "本体能解决", "color": INK}, {"s": "哪些业务问题", "color": VIOLET}],
        "以资金可管可控为例：消歧、规范性约束、隐性知识与完整性判定。",
    )

    cols = [("领域", 150), ("管控对象", 220), ("核心目标", 220), ("驱动来源", 230), ("典型机制", 620)]
    x0, y0, head_h = 80, 190, 32
    e += [rect(x0, y0, 1440, head_h, fill=INK, line=INK, radius=0)]
    x = x0
    for name, w in cols:
        e += [text(x + 10, y0, w - 20, head_h, name, size=13, bold=True, color=WHITE, valign="middle")]
        x += w

    rows = [
        ("集团司库", "成员单位账户与资金", "效率与风险平衡", "集团治理权威",
         "分层现金池、主动划拨、实时归集、预算限额"),
        ("跨境出海", "境内外本外币资金", "合规前提下全球可视可控", "外汇监管政策 + 集团管控",
         "跨境资金池、SWIFT 共享、逐级审批"),
        ("供应链金融", "产业链交易资金", "防挪用、闭环穿透", "交易合约 + 智能合约",
         "三流交叉验证、数币定向支付、跨机构监管"),
    ]
    colors = [VIOLET, CYAN, AMBER]
    y = y0 + head_h
    for i, row in enumerate(rows):
        h = 36
        e += [rect(x0, y, 1440, h, fill=PAPER2 if i % 2 == 0 else WHITE, line=LINE, radius=0)]
        x = x0
        for j, (val, (_, w)) in enumerate(zip(row, cols)):
            e += [text(x + 10, y + 4, w - 20, h - 8, val, size=12.5 if j else 13,
                       bold=(j == 0), color=colors[i] if j == 0 else INK, spacing=2)]
            if j < 4:
                e += [line(x + w, y, x + w, y + h, color=LINE, width=1)]
            x += w
        y += h

    # ① 消歧：企业概念层 → 三个领域，各自 3 个同名概念
    e += [card(80, 335, 460, 365, fill=WHITE, line=VIOLET, radius=14)]
    e += [text(96, 343, 300, 32, "① 消歧", size=24, bold=True, color=VIOLET)]
    ent = ["司库资金可控", "跨境资金合规", "供应链资金闭环"]
    for i, name in enumerate(ent):
        x = 96 + i * 145
        e += [rect(x, 382, 135, 36, fill=VIOLET_SOFT, line=VIOLET, radius=8)]
        e += [text(x, 382, 135, 36, name, size=12, bold=True, color=VIOLET,
                   align="center", valign="middle")]
        e += [line(x + 67, 418, x + 67, 438, color=VIOLET, width=2, arrow=True)]
    doms = [
        ("集团司库", ["账户", "资金", "预算"]),
        ("跨境出海", ["本外币", "跨境池", "审批"]),
        ("供应链金融", ["交易资金", "三流", "定向支付"]),
    ]
    for i, (dname, objs) in enumerate(doms):
        x = 96 + i * 145
        color = [VIOLET, CYAN, AMBER][i]
        e += [rect(x, 440, 135, 198, fill=PAPER2, line=LINE, radius=8)]
        e += [text(x, 444, 135, 24, dname, size=12.5, bold=True, color=color,
                   align="center", valign="middle")]
        for j, obj in enumerate(objs):
            y = 472 + j * 54
            e += [rect(x + 6, y, 123, 46, fill=WHITE, line=color, radius=6)]
            e += [text(x + 6, y + 3, 123, 20, "资金可管可控", size=11, color=INK, align="center")]
            e += [text(x + 6, y + 22, 123, 20, obj, size=11, color=MUTED, align="center")]
    e += [text(96, 646, 430, 36, "同名 3×3；上层企业概念给标准名", size=12, color=MUTED)]

    # ② 规范性约束：管控对象必填，某领域缺失
    e += [card(570, 335, 460, 365, fill=WHITE, line=AMBER, radius=14)]
    e += [text(586, 343, 300, 32, "② 规范性约束", size=24, bold=True, color=AMBER)]
    e += [rect(586, 385, 430, 44, fill=AMBER_SOFT, line=AMBER, radius=8)]
    e += [text(586, 385, 430, 44, "规则：每个领域必须提供管控对象", size=13.5, bold=True,
               color=AMBER, align="center", valign="middle")]
    checks = [
        ("集团司库", "成员单位账户与资金", True),
        ("跨境出海", "— 缺失 —", False),
        ("供应链金融", "产业链交易资金", True),
    ]
    for i, (name, obj, ok) in enumerate(checks):
        y = 445 + i * 55
        e += [rect(586, y, 430, 46, fill=WHITE if ok else ROSE_SOFT,
                   line=AMBER if ok else ROSE, radius=8)]
        e += [text(598, y + 4, 110, 20, name, size=12.5, bold=True, color=AMBER if ok else ROSE)]
        e += [text(710, y + 4, 220, 20, obj, size=11.5, color=INK if ok else ROSE)]
        e += [text(950, y + 4, 50, 20, "√" if ok else "×", size=14, bold=True,
                   color=EMERALD if ok else ROSE, align="center")]
    e += [line(1000, 500, 1000, 596, color=ROSE, width=2, dash=True, arrow=True)]
    e += [text(740, 608, 280, 24, "缺失 → 阻断", size=14, bold=True, color=ROSE)]
    e += [text(586, 650, 430, 26, "必填校验，不是提示词", size=12, color=MUTED)]

    # ③ 隐性知识和完整性判定
    e += [card(1060, 335, 460, 365, fill=WHITE, line=CYAN, radius=14)]
    e += [text(1076, 343, 430, 32, "③ 隐性知识和完整性判定", size=19, bold=True, color=CYAN)]
    e += [rect(1076, 385, 430, 118, fill=CYAN_SOFT, line=CYAN, radius=8)]
    e += [text(1088, 391, 400, 22, "A 核心目标缺量化", size=13, bold=True, color=CYAN),
          text(1088, 417, 400, 22, "“效率与风险平衡”", size=12, color=MUTED),
          text(1088, 443, 400, 26, "→ 补：资金归集率 ≥ 95%", size=12, color=INK),
          text(1088, 469, 400, 26, "→ 补：审批时长 ≤ 2 天", size=12, color=INK)]
    e += [rect(1076, 515, 430, 118, fill=ROSE_SOFT, line=ROSE, radius=8)]
    e += [text(1088, 521, 400, 22, "B 跨域机制重叠", size=13, bold=True, color=ROSE),
          text(1088, 547, 400, 22, "司库：资金池 / 逐级审批", size=12, color=INK),
          text(1088, 573, 400, 22, "跨境：跨境池 / 逐级审批", size=12, color=INK),
          text(1088, 599, 400, 22, "→ 标记重叠，拆分或说明差异", size=12, color=ROSE, bold=True)]
    e += [text(1076, 650, 430, 26, "隐性知识：没写出来但影响判定", size=12, color=MUTED)]

    e += [rect(80, 745, 1440, 50, fill=INK, line=INK, radius=12)]
    e += [text(100, 745, 1420, 50,
               "本体的产出：统一的词　·　可校验的边　·　稳定的口径　·　可追溯的结论",
               size=18, bold=True, color=WHITE, valign="middle")]

    e += footer("案例：资金可管可控（集团司库 / 跨境出海 / 供应链金融）", "06 / 08")
    return {"bg": PAPER, "elements": e}
# ===================== P5 智能体 × 技能 =====================

def slide5():
    e = []
    e += header(
        "07 / 智能体与技能",
        [{"s": "智能体做决策，", "color": INK}, {"s": "技能给方法", "color": ROSE}],
        "技能提供概率性功能；工具提供确定性执行。",
    )

    agents = [
        ("企业知识管理建模", "按企业知识管理领域 T1–T7 任务页执行", "产出：意图 / 规则 / 流程 / 对象 / 验收"),
        ("技术方案评审", "规则驱动，逐条判定", "链路：知识计划 → 提取 → 判定 → 报告"),
        ("知识运维", "巡检、清理、迁移", "链路：扫描 → 计划 → 执行 → 回执"),
    ]
    for i, (name, note, out) in enumerate(agents):
        y = 200 + i * 170
        e += [card(80, y, 600, 150, fill=WHITE, line=EMERALD, radius=14)]
        e += [tag(100, y + 14, 80, 30, "智能体", EMERALD, EMERALD_SOFT, size=15),
              text(100, y + 50, 550, 32, name, size=24, bold=True),
              text(100, y + 88, 550, 24, note, size=16, color=MUTED),
              text(100, y + 114, 550, 24, out, size=15, color=INK)]

    skills = [
        ("knowledge_plan", "知识计划", "多条规则的知识需要 → 一份计划"),
        ("knowledge_extract", "知识提取", "按计划取逐字事实并落库"),
        ("rule_judge", "规则判定", "符合 / 不符合 / 无法判定 / 不适用"),
        ("report_compose", "报告组装", "按模板章节渲染并校回执"),
        ("session_provenance", "会话溯源", "把会话记成可溯源知识页"),
        ("kb_transfer", "库迁移", "导出 → 导入 → 一致性比对"),
    ]
    for i, (sid, name, note) in enumerate(skills):
        x = 720 + (i % 2) * 400
        y = 200 + (i // 2) * 170
        e += [card(x, y, 380, 150, fill=ROSE_SOFT, line=ROSE, radius=14)]
        e += [text(x + 16, y + 12, 348, 22, sid, size=15, color=ROSE, font=MONO),
              text(x + 16, y + 40, 348, 32, name, size=22, bold=True, color=ROSE),
              text(x + 16, y + 82, 348, 52, note, size=16, color=INK, spacing=2)]

    # 两条链路
    e += [card(80, 720, 700, 100, fill=WHITE, line=LINE, radius=12)]
    e += [text(96, 728, 300, 26, "L1 建模 · 4 活动 / 7 任务", size=17, bold=True, color=EMERALD),
          text(96, 760, 660, 24, "T1 意图 → T3 规则 → T5 流程 → T6 对象 → T7 绑定验收 → T2 评估设计 → T4 验收",
               size=14, color=INK)]
    e += [card(800, 720, 720, 100, fill=WHITE, line=LINE, radius=12)]
    e += [text(816, 728, 300, 26, "评审 · 规则驱动链", size=17, bold=True, color=ROSE),
          text(816, 760, 680, 24, "规则解析 → 知识计划 → 知识提取 → 规则判定 → 报告组装",
               size=14, color=INK)]

    e += footer("技能 = 概率性；工具 = 确定性。设计开发 / domain_modeling / 需求分析已废弃。", "07 / 08")
    return {"bg": PAPER, "elements": e}


# ===================== P6 工具 =====================

def slide6():
    e = []
    e += header(
        "08 / 工具",
        [{"s": "工具提供", "color": INK}, {"s": "确定性执行", "color": BLUE}],
        "43 个现役 MCP 工具 + 4 个待建运行控制工具；执行边界（领域建模）是核心，运行控制仍缺权限/审批。",
    )

    groups = [
        ("执行边界", "8", True,
         ["save_knowledge", "doc_outline", "extract_state", "extract_candidates",
          "image_extract", "import_probe", "import_plan", "import_apply"],
         "核心：领域建模 / 落页 / 写属性 / 结构化导入", None),
        ("知识规范", "6", False,
         ["ontology_types", "skills", "page_attributes_set", "migrate_article_body",
          "clean_alias_noise", "page_copy"],
         "类型 / 属性 / 别名 / 正文迁移 / 复制", None),
        ("知识完整", "8", False,
         ["knowledge_lookup", "lookup_turn_begin", "evidence_score", "reference_lookup",
          "rules_resolve", "ke_rule_check", "ke_rule_publish", "rule_publish"],
         "查知识 / 取规则 / 判证据 / 发布", None),
        ("运行控制", "8+4", False,
         ["audit_scan", "audit_plan", "audit_purge", "retag_preview",
          "retag_apply", "retag_rollback", "metric_compute", "job_status",
          "agent_auth", "tool_authorize", "ifc_guard", "budget_breaker"],
         "现役 8 + 待建 4：权限 / 审批 / IFC / 熔断", 8),
        ("跨库迁移", "9", False,
         ["context_scan", "context_lookup", "context_page", "context_concept_apply",
          "context_concept_rollback", "context_authority", "context_authority_apply",
          "kb_export", "kb_import"],
         "同名治理 / 权威副本 / 库迁移", None),
        ("外部工具连接", "4", False,
         ["ext_attach_probe", "ext_doc_push", "ext_doc_parse", "ext_table"],
         "外部系统 / 文档 / 表单，按契约取数", None),
    ]
    colors = [AMBER, VIOLET, CYAN, ROSE, BLUE, EMERALD]
    for i, (name, n, core, tools, note, gap_from) in enumerate(groups):
        x = 80 + (i % 3) * 490
        y = 200 + (i // 3) * 270
        color = colors[i]
        e += [card(x, y, 460, 250, fill=WHITE, line=color, radius=14)]
        e += [text(x + 18, y + 14, 300, 34, name, size=22, bold=True, color=color),
              text(x + 378, y + 6, 64, 50, n, size=24 if n == "8+4" else 36,
                   bold=True, color=ROSE if n == "8+4" else color, font=MONO, align="right")]
        if core:
            e += tag(x + 120, y + 16, 58, 22, "核心", AMBER, AMBER_SOFT, size=12)
        for j, t in enumerate(tools):
            col, row = j % 2, j // 2
            w = 220 if col == 0 else 192
            e += [text(x + 18 + col * 232, y + 58 + row * 25, w, 24, t,
                       size=12.5 if len(t) > 20 else 13.5,
                       color=ROSE if gap_from is not None and j >= gap_from else INK,
                       font=MONO)]
        e += [text(x + 18, y + 214, 424, 26, note, size=13.5, color=MUTED)]

    e += [rect(80, 740, 1440, 60, fill=INK, line=INK, radius=12)]
    e += [text(100, 740, 220, 60, "执行系统", size=18, bold=True, color="7DE3D8", valign="middle"),
          text(220, 740, 1280, 60,
               "企业 API　·　领域 MCP（it_platform）　·　DB　｜　绑定边 bpmn:dataObjectBindingTool　｜　先预览、后执行",
               size=16, color=WHITE, valign="middle")]

    e += footer("43 个现役 + 4 个待建运行控制工具；执行边界（领域建模）是核心。", "08 / 08")
    return {"bg": PAPER, "elements": e}

# ===================== P7 三类问题 =====================

def slide7():
    e = []
    e += header(
        "01 / 三类问题",
        [{"s": "企业智能体的", "color": INK}, {"s": "三类问题", "color": ROSE}],
        "内容安全有成熟护栏；知识规范“结构成熟、语义缺口”；执行控制仍在演进。",
    )

    # ① 内容安全
    e += [card(80, 200, 460, 430, fill=WHITE, line=CYAN, radius=16)]
    e += [tag(100, 218, 130, 32, "① 内容安全", CYAN, CYAN_SOFT, size=15),
          text(100, 262, 420, 34, "生成了什么", size=24, bold=True, color=CYAN)]
    e += [text(100, 310, 420, 30, "风险：违规内容 / PII / 提示注入 / 话题越界", size=16, color=INK),
          text(100, 352, 420, 30, "方案：NeMo / Azure / Bedrock 已覆盖", size=16, color=INK),
          text(100, 394, 420, 30, "边界：管“生成层”，不管动作执行", size=16, color=ROSE, bold=True)]
    e += [rect(100, 512, 420, 52, fill=CYAN_SOFT, line=CYAN, radius=10)]
    e += [text(100, 512, 420, 52, "成熟 · 可直接采用", size=18, bold=True, color=CYAN,
               align="center", valign="middle")]

    # ② 知识规范性与完整性
    e += [card(570, 200, 460, 430, fill=WHITE, line=ROSE, radius=16)]
    e += [tag(590, 218, 190, 32, "② 知识规范性与完整性", ROSE, ROSE_SOFT, size=14),
          text(590, 262, 420, 34, "知识是什么、是否完整", size=22, bold=True, color=ROSE)]
    e += [text(590, 308, 420, 30, "结构 lint：frontmatter / 死链 / 孤立页 / 索引同步", size=15, color=INK),
          text(590, 348, 420, 30, "引文：无引文标记、不可解析 → 阻断", size=15, color=INK),
          text(590, 388, 420, 30, "语义：歧义 / 口径 / 溯源仍缺", size=15, color=INK),
          text(590, 428, 420, 30, "结论：结构成熟，语义缺口", size=16, color=ROSE, bold=True)]
    e += [rect(590, 512, 420, 52, fill=AMBER_SOFT, line=AMBER, radius=10)]
    e += [text(590, 512, 420, 52, "结构成熟 · 语义缺口", size=18, bold=True, color=AMBER,
               align="center", valign="middle")]

    # ③ 执行失控
    e += [card(1060, 200, 460, 430, fill=WHITE, line=AMBER, radius=16)]
    e += [tag(1080, 218, 130, 32, "③ 执行失控", AMBER, AMBER_SOFT, size=15),
          text(1080, 262, 420, 34, "动作是否真的被执行", size=22, bold=True, color=AMBER)]
    e += [text(1080, 310, 420, 30, "风险：越权调用 / 误写 / 数据外流", size=16, color=INK),
          text(1080, 352, 420, 30, "规范：MCP / A2A 只管“能连上”", size=16, color=INK),
          text(1080, 394, 420, 30, "边界：需运行时网关 / 任务授权 / IFC", size=16, color=ROSE, bold=True)]
    e += [rect(1080, 512, 420, 52, fill=AMBER_SOFT, line=AMBER, radius=10)]
    e += [text(1080, 512, 420, 52, "快速演进 · 需自建", size=18, bold=True, color=AMBER,
               align="center", valign="middle")]

    e += [text(80, 668, 1440, 50, "内容安全 ≠ 知识规范；结构 lint 不等于语义完整。", size=28, bold=True, color=INK)]
    e += [text(80, 728, 1440, 30, "规范负责“能连上”，平台负责“该不该放行”。", size=17, color=MUTED)]

    e += footer("三类问题：内容安全 / 知识规范性与完整性 / 执行控制", "01 / 08")
    return {"bg": PAPER, "elements": e}


# ===================== P8 Bodhi 补位与缺口 =====================

def slide8():
    e = []
    e += header(
        "02 / 对策",
        [{"s": "本体库 MCP 工具包（Bodhi）", "color": INK}],
        "结构完整性用本体 + SHACL；语义完整性用业务规则 + 建模智能体；运行时按规范控制流程/工具/数据。",
    )

    # 左：知识规范性与完整性
    e += [card(80, 200, 700, 520, fill=WHITE, line=VIOLET, radius=16)]
    e += [tag(100, 216, 380, 32, "本体库 MCP 工具包（Bodhi）核心补位", VIOLET, VIOLET_SOFT, size=13),
          text(100, 258, 660, 34, "知识规范性与完整性", size=24, bold=True, color=VIOLET)]

    e += [rect(100, 310, 660, 120, fill=VIOLET_SOFT, line=VIOLET, radius=12)]
    e += [text(116, 318, 300, 28, "结构完整性：本体 + SHACL", size=18, bold=True, color=VIOLET),
          text(116, 350, 628, 28, "类型 / 属性 / 关系 / 基数 / 值域", size=15, color=INK),
          text(116, 382, 628, 28, "结构 lint：frontmatter / 死链 / 孤立页 / 索引同步", size=15, color=INK)]

    e += [rect(100, 446, 660, 120, fill=ROSE_SOFT, line=ROSE, radius=12)]
    e += [text(116, 454, 340, 28, "语义完整性：业务规则 + 建模智能体", size=18, bold=True, color=ROSE),
          text(116, 486, 628, 28, "歧义 / 口径 / 证据", size=15, color=INK),
          text(116, 518, 628, 28, "无引文标记、引文不可解析 → 阻断", size=15, color=INK)]

    e += [text(100, 590, 660, 34, "结果：规范、完整、可溯源", size=18, bold=True, color=VIOLET)]

    # 翅膀图片：规范性 / 完整性的“提升”
    e += [image(HERE / "assets" / "wing.png", 590, 210, 160, 112)]
    e += [text(650, 326, 70, 20, "提升", size=13, color=VIOLET, bold=True)]

    # 右侧：企业智能体 = 领域智能体 + 运行时控制
    e += [rect(820, 200, 700, 520, fill=PAPER2, line=EMERALD, radius=18)]
    e += [text(840, 210, 300, 32, "企业智能体", size=20, bold=True, color=EMERALD),
          text(1040, 214, 460, 26, "领域智能体 + 运行时控制", size=14, color=MUTED)]

    # 执行边界
    e += [card(840, 250, 660, 215, fill=WHITE, line=AMBER, radius=14)]
    e += [tag(860, 260, 150, 30, "执行边界", AMBER, AMBER_SOFT, size=14),
          text(860, 298, 520, 32, "领域智能体", size=21, bold=True, color=AMBER)]
    e += [text(860, 334, 520, 24, "业务流程建模 → 精准意图识别", size=14.5, color=INK),
          text(860, 360, 520, 24, "任务页 / 规则 / I-O 契约", size=14.5, color=INK),
          text(860, 386, 520, 24, "工具 dry_run / ticket / 幂等", size=14.5, color=INK),
          text(860, 412, 520, 24, "domain / work KB 隔离", size=14.5, color=INK),
          text(860, 438, 520, 24, "边界：应用层约束 ≠ 运行时强制", size=14.5, color=ROSE, bold=True)]

    # 运行时控制
    e += [card(840, 475, 660, 225, fill=WHITE, line=ROSE, radius=14)]
    e += [tag(860, 485, 150, 30, "还需补齐", ROSE, ROSE_SOFT, size=14),
          text(860, 523, 520, 32, "运行时控制", size=21, bold=True, color=ROSE)]
    e += [text(860, 563, 520, 24, "智能体角色认证 + 任务级权限", size=15, color=INK),
          text(860, 595, 520, 24, "运行时网关：allow / deny / 审批", size=15, color=INK),
          text(860, 627, 520, 24, "信息流控制 + 执行隔离", size=15, color=INK),
          text(860, 659, 520, 24, "预算熔断 + 结构化审计", size=15, color=INK)]

    # 紧箍咒图片：运行时约束
    e += [image(HERE / "assets" / "headband.png", 1380, 480, 110, 77)]
    e += [text(1390, 560, 90, 20, "紧箍咒", size=12, color=AMBER, bold=True, align="center")]

    e += [rect(80, 720, 1440, 60, fill=INK, line=INK, radius=12)]
    e += [text(100, 720, 1440, 60,
               "结构用本体 + SHACL　｜　语义用规则 + 建模智能体　｜　执行用运行时闸门",
               size=18, bold=True, color=WHITE, valign="middle")]

    e += footer("先卡执行，再补内容；知识规范用本体，执行控制用运行时。", "02 / 08")
    return {"bg": PAPER, "elements": e}


SLIDES = [slide7(), slide8(), slide1(), slide2(), slide3(), slide4(), slide5(), slide6()]

