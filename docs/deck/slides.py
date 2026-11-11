"""Slide definitions for Bodhi2 (1600x900 design units)."""

from deck_lib import (
    AMBER, AMBER_SOFT, BLUE, BLUE_SOFT, CYAN, CYAN_SOFT, EMERALD, EMERALD_SOFT,
    FAINT, GRAY_SOFT, INK, LINE, MONO, MUTED, PAPER, PAPER2, ROSE, ROSE_SOFT,
    SANS, VIOLET, VIOLET_SOFT, WHITE,
    bullets, card, label, line, pill, rect, tag, text,
)


def header(kicker, title_runs, sub):
    return [
        text(80, 42, 1000, 30, kicker, size=20, color=CYAN, bold=True),
        text(80, 78, 1500, 74, runs=title_runs, size=54, bold=True, color=INK),
        text(80, 156, 1460, 40, sub, size=24, color=MUTED, spacing=2),
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
        "03 / 四层架构",
        [{"s": "四层架构：", "color": INK}, {"s": "从智能体到本体", "color": VIOLET}],
        "智能体调用技能/工具；技能/工具使用领域知识；领域知识受本体约束。",
    )

    # 第 1 层 智能体
    e += [rect(80, 215, 1440, 135, fill=EMERALD_SOFT, line=EMERALD, radius=16)]
    e += band_label(100, 232, "第 1 层", "智能体", "决策与编排", EMERALD)
    agents = ["技术方案评审", "知识运维", "企业知识管理建模"]
    for i, name in enumerate(agents):
        x = 380 + i * 360
        core = name == "企业知识管理建模"
        e += [card(x, 250, 330, 64, fill=AMBER_SOFT if core else WHITE,
                   line=AMBER if core else EMERALD, radius=12)]
        e += [text(x, 250, 330, 64, name, size=22, bold=True, color=AMBER if core else EMERALD,
                   align="center", valign="middle")]

    # 第 2 层 技能 + 工具
    e += [rect(80, 365, 780, 150, fill=ROSE_SOFT, line=ROSE, radius=16)]
    e += band_label(100, 382, "第 2 层", "技能", "概率性功能 · 6 个", ROSE)
    skills = ["知识计划", "知识提取", "规则判定", "报告组装", "会话溯源", "库迁移"]
    for i, s in enumerate(skills):
        x = 300 + (i % 3) * 180
        y = 390 + (i // 3) * 56
        e += pill(x, y, 165, 44, s, size=18, fg=ROSE, bg=WHITE, border=ROSE)

    e += [rect(880, 365, 640, 150, fill=BLUE_SOFT, line=BLUE, radius=16)]
    e += band_label(900, 382, "第 2 层", "工具", "确定性功能 · 43 个", BLUE)
    groups = [("执行边界", "8", True), ("知识规范", "6", False), ("知识完整", "8", False),
              ("运行控制", "8", False), ("跨库迁移", "9", False), ("外部连接", "4", False)]
    for i, (name, n, core) in enumerate(groups):
        x = 1080 + (i % 2) * 220
        y = 384 + (i // 2) * 44
        e += [card(x, y, 205, 38, fill=AMBER_SOFT if core else WHITE,
                   line=AMBER if core else BLUE, radius=9)]
        e += [text(x + 12, y + 5, 140, 28, name, size=16, bold=True, color=AMBER if core else BLUE),
              text(x + 155, y + 4, 40, 28, n, size=19, bold=True, color=AMBER if core else BLUE,
                   font=MONO, align="right")]

    # 第 3 层 领域知识
    e += [rect(80, 530, 1440, 135, fill=CYAN_SOFT, line=CYAN, radius=16)]
    e += band_label(100, 547, "第 3 层", "领域知识", "作为业务技能使用", CYAN)
    domains = ["技术方案评审", "重点产品智能问数", "企业知识管理领域"]
    for i, name in enumerate(domains):
        x = 380 + i * 360
        core = name == "企业知识管理领域"
        e += [card(x, 560, 330, 72, fill=AMBER_SOFT if core else WHITE,
                   line=AMBER if core else CYAN, radius=12)]
        e += [text(x, 560, 330, 72, name, size=21, bold=True, color=AMBER if core else CYAN,
                   align="center", valign="middle")]

    # 第 4 层 本体模型
    e += [rect(80, 680, 1440, 135, fill=VIOLET_SOFT, line=VIOLET, radius=16)]
    e += band_label(100, 697, "第 4 层", "本体模型", "知识的基础规范", VIOLET)
    mods = ["SCHEMA", "BMM", "BPMN", "AGENT"]
    for i, m in enumerate(mods):
        x = 380 + i * 190
        e += [card(x, 712, 170, 64, fill=WHITE, line=VIOLET, radius=10)]
        e += [text(x, 712, 170, 64, m, size=22, bold=True, color=VIOLET, font=MONO,
                   align="center", valign="middle")]
    e += [text(1160, 706, 340, 34, "54 类 · 71 对象属性", size=20, bold=True, color=VIOLET),
          text(1160, 744, 340, 30, "55 数据属性 · 4 条约束", size=18, color=MUTED)]

    # 核心连接：建模智能体 ⇄ 企业知识管理领域
    e += [line(1430, 282, 1530, 282, color=AMBER, width=2),
          line(1430, 596, 1530, 596, color=AMBER, width=2),
          line(1530, 282, 1530, 405, color=AMBER, width=3, arrow=True),
          line(1530, 596, 1530, 473, color=AMBER, width=3, arrow=True)]
    e += [text(1540, 390, 26, 120, "核\n心\n连\n接", size=14, color=AMBER, bold=True,
               align="center", spacing=2)]

    e += [text(80, 826, 1460, 28,
               "核心连接：企业知识管理领域 + 建模智能体 —— 把本体规范落成领域知识　｜　设计开发 / domain_modeling / 需求分析已并入",
               size=16, color=INK, bold=True)]
    e += footer("范围：生产链路（测试知识库与已退役能力不计）", "03 / 08")
    return {"bg": PAPER, "elements": e}
# ===================== P2 四层协作 =====================

def slide2():
    e = []
    e += header(
        "04 / 总体架构",
        [{"s": "四层如何协作：", "color": INK}, {"s": "技能/工具是总线", "color": BLUE}],
        "核心连接：企业知识管理领域 + 建模智能体；技能做概率性判断，工具做确定性执行。",
    )

    # 1 智能体
    e += [rect(80, 205, 1440, 100, fill=EMERALD_SOFT, line=EMERALD, radius=14)]
    e += [tag(100, 220, 86, 32, "1 智能体", EMERALD, WHITE, size=16),
          text(100, 258, 190, 28, "决策与编排", size=18, color=MUTED)]
    agents = [("技术方案评审", "规则驱动逐条判定"), ("知识运维", "巡检 · 清理 · 迁移"),
              ("企业知识管理建模", "按 T1–T7 任务页执行")]
    for i, (name, note) in enumerate(agents):
        x = 310 + i * 340
        core = name == "企业知识管理建模"
        e += [card(x, 220, 310, 70, fill=AMBER_SOFT if core else WHITE,
                   line=AMBER if core else EMERALD, radius=10)]
        e += [text(x + 14, 228, 280, 28, name, size=21, bold=True, color=AMBER if core else INK),
              text(x + 14, 258, 280, 24, note, size=17, color=AMBER if core else MUTED)]

    # 2 技能 + 工具
    e += [rect(80, 315, 640, 165, fill=ROSE_SOFT, line=ROSE, radius=14)]
    e += [tag(100, 330, 70, 30, "2 技能", ROSE, WHITE, size=16),
          text(100, 366, 300, 26, "概率性功能", size=18, color=MUTED)]
    skills = ["知识计划", "知识提取", "规则判定", "报告组装", "会话溯源", "库迁移"]
    for i, s in enumerate(skills):
        x = 100 + (i % 3) * 200
        y = 400 + (i // 3) * 40
        e += pill(x, y, 185, 34, s, size=17, fg=ROSE, bg=WHITE, border=ROSE)

    e += [rect(740, 315, 780, 165, fill=BLUE_SOFT, line=BLUE, radius=14)]
    e += [tag(760, 330, 70, 30, "2 工具", BLUE, WHITE, size=16),
          text(760, 366, 320, 26, "确定性功能 · 43 个", size=18, color=MUTED)]
    groups = [("执行边界", "8", True), ("知识规范", "6", False), ("知识完整", "8", False),
              ("运行控制", "8", False), ("跨库迁移", "9", False), ("外部连接", "4", False)]
    for i, (name, n, core) in enumerate(groups):
        x = 760 + (i % 3) * 245
        y = 400 + (i // 3) * 40
        e += [card(x, y, 230, 34, fill=AMBER_SOFT if core else WHITE,
                   line=AMBER if core else BLUE, radius=9)]
        e += [text(x + 12, y + 4, 160, 26, name, size=15, bold=True, color=AMBER if core else BLUE),
              text(x + 178, y + 3, 42, 26, n, size=18, bold=True, color=AMBER if core else BLUE,
                   font=MONO, align="right")]

    # 3 领域知识
    e += [rect(80, 490, 1440, 150, fill=CYAN_SOFT, line=CYAN, radius=14)]
    e += [tag(100, 505, 90, 32, "3 领域知识", CYAN, WHITE, size=16),
          text(100, 545, 200, 26, "作为业务技能使用", size=18, color=MUTED)]
    domains = [
        ("技术方案评审", "任务 · 规则 · 模板 · 流程", "业务技能：评审/判定"),
        ("重点产品智能问数", "任务 · 对象 · 指标 · 问答", "业务技能：问数/解读"),
        ("企业知识管理领域", "T1–T7 · 任务页 · I/O 契约", "业务技能：建模方法"),
    ]
    for i, (name, obj, skill) in enumerate(domains):
        x = 310 + i * 340
        core = name == "企业知识管理领域"
        e += [card(x, 505, 310, 118, fill=AMBER_SOFT if core else WHITE,
                   line=AMBER if core else CYAN, radius=10)]
        e += [text(x + 14, 514, 280, 28, name, size=21, bold=True, color=AMBER if core else CYAN),
              text(x + 14, 548, 280, 24, obj, size=16, color=AMBER if core else MUTED),
              text(x + 14, 578, 280, 24, skill, size=16, color=INK, bold=True)]

    # 4 本体
    e += [rect(80, 650, 1440, 135, fill=VIOLET_SOFT, line=VIOLET, radius=14)]
    e += [tag(100, 665, 90, 32, "4 本体模型", VIOLET, WHITE, size=16),
          text(100, 705, 190, 26, "知识的基础规范", size=18, color=MUTED)]
    mods = ["SCHEMA", "BMM", "BPMN", "AGENT"]
    for i, m in enumerate(mods):
        x = 310 + i * 150
        e += [card(x, 665, 135, 42, fill=WHITE, line=VIOLET, radius=8)]
        e += [text(x, 665, 135, 42, m, size=18, bold=True, color=VIOLET, font=MONO,
                   align="center", valign="middle")]
    artifacts = ["Neo4j", "JSON Schema", "SHACL", "index", "prompts", "label_map"]
    for i, a in enumerate(artifacts):
        x = 310 + i * 150
        e += [card(x, 718, 135, 42, fill=WHITE, line=VIOLET, radius=8)]
        e += [text(x, 718, 135, 42, a, size=15, color=VIOLET, font=MONO,
                   align="center", valign="middle")]
    e += [text(1250, 665, 250, 28, "54 类 / 71 对象属性", size=18, bold=True, color=VIOLET),
          text(1250, 700, 250, 26, "55 数据属性 / 4 条约束", size=17, color=MUTED)]

    # 核心连接：建模智能体 ⇄ 企业知识管理领域
    e += [line(1300, 255, 1530, 255, color=AMBER, width=2),
          line(1300, 564, 1530, 564, color=AMBER, width=2),
          line(1530, 255, 1530, 380, color=AMBER, width=3, arrow=True),
          line(1530, 564, 1530, 440, color=AMBER, width=3, arrow=True)]
    e += [text(1540, 370, 26, 120, "核\n心\n连\n接", size=14, color=AMBER, bold=True,
               align="center", spacing=2)]

    # 执行系统
    e += [rect(80, 800, 1440, 40, fill=WHITE, line=LINE, radius=10)]
    e += [tag(96, 806, 86, 28, "执行系统", AMBER, AMBER_SOFT, size=15),
          text(200, 808, 1280, 26, "企业 API　·　领域 MCP（it_platform）　·　DB　｜　工具按契约取数/回写",
               size=17, color=INK)]

    e += footer("设计开发 / domain_modeling / 需求分析已废弃 → 由建模智能体 + 企业知识管理领域承接", "04 / 08")
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
    e += [tag(100, 290, 90, 32, "TBox 本体", VIOLET, VIOLET_SOFT, size=16),
          text(200, 294, 300, 26, "只描述类型 · 不写实例", size=17, color=MUTED)]
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
        e += [card(710, y, 120, 50, fill=WHITE, line=AMBER, radius=9)]
        e += [text(710, y, 120, 50, g, size=14, bold=True, color=AMBER,
                   align="center", valign="middle")]

    # ABox
    e += [rect(850, 275, 670, 460, fill=PAPER2, line=LINE, radius=14)]
    e += [tag(870, 290, 96, 32, "ABox 知识", CYAN, CYAN_SOFT, size=16),
          text(976, 294, 300, 26, "一页 = 一个实例", size=17, color=MUTED)]
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
          text(1209, 426, 290, 84, "isDerivedFrom → bmm:BusinessPolicy\nguides → bmm:BusinessProcess\nsourceSession → bmm:KnowledgeSession", size=13.5, color=INK, font=MONO, spacing=2)]
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
        "06 / 为什么需要本体",
        [{"s": "同一句“资金可管可控”，", "color": INK}, {"s": "三个领域三种含义", "color": VIOLET}],
        "先定领域与管控对象，再定约束与判定；不是靠关键词套规则。",
    )

    cols = [("领域", 150), ("管控对象", 220), ("核心目标", 220), ("驱动来源", 230), ("典型机制", 620)]
    x0, y0, head_h = 80, 180, 32
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
            e += [text(x + 6, y + 3, 123, 20, "资金可管可控", size=10.5, color=INK, align="center")]
            e += [text(x + 6, y + 22, 123, 20, obj, size=10.5, color=MUTED, align="center")]
    e += [text(96, 646, 430, 36, "同名 3×3；上层企业概念给标准名", size=12, color=MUTED)]

    # ② 约束：驱动来源 → 约束机制 → 管控对象
    e += [card(570, 335, 460, 365, fill=WHITE, line=AMBER, radius=14)]
    e += [text(586, 343, 300, 32, "② 约束", size=24, bold=True, color=AMBER)]
    lanes = [
        ("集团司库", ["治理授权", "预算限额", "划拨/归集"]),
        ("跨境出海", ["外汇监管", "逐级审批", "跨境池"]),
        ("供应链", ["交易合约", "三流验证", "定向支付"]),
    ]
    for i, (label, steps) in enumerate(lanes):
        y = 385 + i * 82
        e += [text(586, y + 4, 60, 24, label, size=12, bold=True, color=AMBER)]
        for j, step in enumerate(steps):
            x = 650 + j * 125
            e += [rect(x, y, 105, 34, fill=AMBER_SOFT if j == 0 else WHITE,
                       line=AMBER, radius=7)]
            e += [text(x, y, 105, 34, step, size=11.5, color=INK, align="center", valign="middle")]
            if j < 2:
                e += [line(x + 105, y + 17, x + 120, y + 17, color=AMBER, width=2, arrow=True)]
    e += [text(586, 620, 430, 26, "方向：驱动来源 → 约束机制 → 管控对象", size=12, color=MUTED)]
    e += [text(586, 650, 430, 26, "× 不能把司库预算限额套到跨境/供应链", size=12, color=ROSE, bold=True)]

    # ③ 判定：同一句话 → 判定领域 → 三套路线
    e += [card(1060, 335, 460, 365, fill=WHITE, line=CYAN, radius=14)]
    e += [text(1076, 343, 300, 32, "③ 判定", size=24, bold=True, color=CYAN)]
    e += [rect(1150, 382, 180, 42, fill=CYAN_SOFT, line=CYAN, radius=8)]
    e += [text(1150, 382, 180, 42, "同一句\n资金可管可控", size=12, color=INK, align="center", valign="middle", spacing=1)]
    e += [line(1240, 424, 1240, 438, color=CYAN, width=2, arrow=True)]
    e += [rect(1170, 440, 140, 30, fill=WHITE, line=CYAN, radius=7)]
    e += [text(1170, 440, 140, 30, "判定领域", size=12, bold=True, color=CYAN, align="center", valign="middle")]
    branches = [
        ("司库", "预算 / 归集", 1076, CYAN),
        ("跨境", "合规 / 审批", 1228, CYAN),
        ("供应链", "三流 / 闭环", 1380, CYAN),
    ]
    for name, rule, x, color in branches:
        e += [line(1240, 470, x + 67, 486, color=CYAN, width=2, arrow=True)]
        e += [rect(x, 488, 135, 96, fill=PAPER2, line=color, radius=8)]
        e += [text(x, 492, 135, 24, name, size=12.5, bold=True, color=color, align="center")]
        e += [text(x, 518, 135, 24, rule, size=11.5, color=INK, align="center")]
        e += [text(x, 552, 135, 22, "→ 结论", size=11.5, color=color, align="center")]
    e += [text(1076, 620, 430, 26, "同一句话，三套判定路线", size=12, color=MUTED)]

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
            e += [text(x + 18 + col * 212, y + 58 + row * 25, 206, 24, t,
                       size=13.5, color=ROSE if gap_from is not None and j >= gap_from else INK,
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
        [{"s": "Bodhi 的", "color": INK}, {"s": "补位与缺口", "color": VIOLET}],
        "结构完整性用本体 + SHACL；语义完整性用业务规则 + 建模智能体；执行控制仍需运行时。",
    )

    # 左：知识规范性与完整性
    e += [card(80, 200, 720, 500, fill=WHITE, line=VIOLET, radius=16)]
    e += [tag(100, 216, 160, 32, "Bodhi 核心补位", VIOLET, VIOLET_SOFT, size=15),
          text(100, 258, 680, 34, "知识规范性与完整性", size=24, bold=True, color=VIOLET)]

    e += [rect(100, 310, 680, 120, fill=VIOLET_SOFT, line=VIOLET, radius=12)]
    e += [text(116, 318, 300, 28, "结构完整性：本体 + SHACL", size=18, bold=True, color=VIOLET),
          text(116, 350, 648, 28, "类型 / 属性 / 关系 / 基数 / 值域", size=15, color=INK),
          text(116, 382, 648, 28, "结构 lint：frontmatter / 死链 / 孤立页 / 索引同步", size=15, color=INK)]

    e += [rect(100, 446, 680, 120, fill=ROSE_SOFT, line=ROSE, radius=12)]
    e += [text(116, 454, 340, 28, "语义完整性：业务规则 + 建模智能体", size=18, bold=True, color=ROSE),
          text(116, 486, 648, 28, "歧义 / 口径 / 证据", size=15, color=INK),
          text(116, 518, 648, 28, "无引文标记、引文不可解析 → 阻断", size=15, color=INK)]

    e += [text(100, 590, 680, 34, "结果：规范、完整、可溯源", size=18, bold=True, color=VIOLET)]

    # 右上：执行边界
    e += [card(820, 200, 700, 240, fill=WHITE, line=AMBER, radius=16)]
    e += [tag(840, 214, 150, 30, "执行边界", AMBER, AMBER_SOFT, size=14),
          text(840, 252, 660, 32, "领域智能体：已有", size=21, bold=True, color=AMBER)]
    e += [text(840, 296, 660, 26, "任务页 / 规则 / I-O 契约", size=15, color=INK),
          text(840, 328, 660, 26, "工具 dry_run / ticket / 幂等", size=15, color=INK),
          text(840, 360, 660, 26, "domain / work KB 隔离", size=15, color=INK),
          text(840, 392, 660, 26, "边界：应用层约束 ≠ 运行时强制", size=15, color=ROSE, bold=True)]

    # 右下：运行时控制
    e += [card(820, 460, 700, 240, fill=WHITE, line=ROSE, radius=16)]
    e += [tag(840, 474, 150, 30, "还需补齐", ROSE, ROSE_SOFT, size=14),
          text(840, 512, 660, 32, "运行时控制：缺口", size=21, bold=True, color=ROSE)]
    e += [text(840, 556, 660, 26, "智能体角色认证 + 任务级权限", size=15, color=INK),
          text(840, 588, 660, 26, "运行时网关：allow / deny / 审批", size=15, color=INK),
          text(840, 620, 660, 26, "信息流控制 + 执行隔离", size=15, color=INK),
          text(840, 652, 660, 26, "预算熔断 + 结构化审计", size=15, color=INK)]

    e += [rect(80, 720, 1440, 60, fill=INK, line=INK, radius=12)]
    e += [text(100, 720, 1440, 60,
               "结构用本体 + SHACL　｜　语义用规则 + 建模智能体　｜　执行用运行时闸门",
               size=18, bold=True, color=WHITE, valign="middle")]

    e += footer("先卡执行，再补内容；知识规范用本体，执行控制用运行时。", "02 / 08")
    return {"bg": PAPER, "elements": e}


SLIDES = [slide7(), slide8(), slide1(), slide2(), slide3(), slide4(), slide5(), slide6()]

