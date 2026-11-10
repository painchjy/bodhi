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
        "BODHI2 · 知识工程系统",
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
    groups = [("领域建模", "9", True), ("本体与技能", "3", False), ("检索与规则", "8", False),
              ("治理审计", "8", False), ("跨库与迁移", "11", False), ("外部连接", "4", False)]
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
    e += footer("范围：生产链路（测试知识库与已退役能力不计）", "01 / 06")
    return {"bg": PAPER, "elements": e}
# ===================== P2 四层协作 =====================

def slide2():
    e = []
    e += header(
        "01 / 总体架构",
        [{"s": "四层如何协作：", "color": INK}, {"s": "技能/工具是总线", "color": BLUE}],
        "技能做概率性判断，工具做确定性执行；企业知识管理领域 + 建模智能体把本体落成领域知识。",
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
    groups = [("领域建模", "9", True), ("本体与技能", "3", False), ("检索与规则", "8", False),
              ("治理审计", "8", False), ("跨库与迁移", "11", False), ("外部连接", "4", False)]
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

    e += footer("设计开发 / domain_modeling / 需求分析已废弃 → 由建模智能体 + 企业知识管理领域承接", "02 / 06")
    return {"bg": PAPER, "elements": e}

# ===================== P3 本体 × 领域知识 =====================

def slide3():
    e = []
    e += header(
        "02 / 本体与知识",
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

    e += footer("本体不写实例；领域库不写类型。页面可读、图边可推理、溯源可回访。", "03 / 06")
    return {"bg": PAPER, "elements": e}


# ===================== P4 本体的作用 =====================

def slide4():
    e = []
    e += header(
        "03 / 为什么需要本体",
        [{"s": "本体解决三种歧义：", "color": INK}, {"s": "同名、越级、串模板", "color": VIOLET}],
        "先定类，再定边，再定口径；不是靠关键词猜。",
    )

    # ① 消歧
    e += [card(80, 200, 460, 520, fill=WHITE, line=VIOLET, radius=14)]
    e += [text(100, 216, 300, 38, "① 消歧", size=28, bold=True, color=VIOLET),
          text(100, 258, 420, 26, "同名 ≠ 同义：同一句话，先问“它是什么类”。", size=18, color=MUTED)]
    e += [rect(100, 296, 420, 56, fill=VIOLET_SOFT, line=VIOLET, radius=10)]
    e += [text(100, 296, 420, 56, "技术方案评审", size=30, bold=True, color=VIOLET,
               align="center", valign="middle")]
    classes = [
        ("bpmn:Activity", "活动", "流程里的一段工作"),
        ("bmm:BusinessProcess", "流程", "一组活动的编排"),
        ("bmm:BusinessPolicy", "策略", "规则的依据，不能直接判定"),
        ("schema:DocumentTemplate", "模板", "报告章节结构的载体"),
        ("bmm:OperativeBusinessRule", "规则", "可逐条判定，带 ruleHash"),
    ]
    for i, (cls, name, note) in enumerate(classes):
        y = 366 + i * 56
        e += [card(100, y, 420, 48, fill=PAPER2, line=LINE, radius=8)]
        e += [text(112, y + 4, 190, 22, cls, size=13, color=VIOLET, font=MONO),
              text(112, y + 24, 90, 22, name, size=15, bold=True),
              text(205, y + 24, 305, 22, note, size=13, color=MUTED)]
    e += [text(100, 646, 420, 70,
               "当前库：同名“技术方案评审”横跨\n活动、流程、策略、模板、规则；\n先定类，再看用途——策略是依据，规则才是判定单元。",
               size=14, color=INK, spacing=2)]

    # ② 约束
    e += [card(570, 200, 460, 520, fill=WHITE, line=AMBER, radius=14)]
    e += [text(590, 216, 300, 38, "② 约束", size=28, bold=True, color=AMBER),
          text(590, 258, 420, 26, "概念之间不能越级：产品 ≠ 流程 ≠ 活动 ≠ 任务。", size=18, color=MUTED)]
    e += [card(590, 320, 170, 56, fill=AMBER_SOFT, line=AMBER, radius=9)]
    e += [text(590, 320, 170, 56, "业务流程", size=18, bold=True, color=AMBER, align="center", valign="middle")]
    e += [card(800, 320, 120, 56, fill=WHITE, line=AMBER, radius=9)]
    e += [text(800, 320, 120, 56, "活动", size=18, bold=True, color=AMBER, align="center", valign="middle")]
    e += [card(960, 320, 100, 56, fill=WHITE, line=AMBER, radius=9)]
    e += [text(960, 320, 100, 56, "任务", size=18, bold=True, color=AMBER, align="center", valign="middle")]
    e += [line(760, 348, 796, 348, color=AMBER, width=3, arrow=True),
          line(920, 348, 956, 348, color=AMBER, width=3, arrow=True)]
    e += [text(760, 310, 160, 22, "包含", size=13, color=AMBER, align="center"),
          text(920, 310, 100, 22, "包含", size=13, color=AMBER, align="center")]
    e += [card(590, 450, 170, 56, fill=AMBER_SOFT, line=AMBER, radius=9)]
    e += [text(590, 450, 170, 56, "产品", size=18, bold=True, color=AMBER, align="center", valign="middle")]
    e += [line(675, 376, 675, 446, color=AMBER, width=3, arrow=True)]
    e += [text(685, 392, 130, 24, "交付", size=14, color=AMBER)]
    e += [line(760, 478, 956, 366, color=ROSE, width=3, dash=True, arrow=True)]
    e += [text(780, 500, 240, 24, "越级 / 非法：产品不能直接包含任务", size=13.5, color=ROSE, bold=True)]
    e += [text(590, 550, 420, 110,
               "合法链：业务流程 —交付→ 产品；\n业务流程 → 活动 → 任务。\n非法链：产品 → 活动 / 任务（domain / range 不合法）。",
               size=15, color=INK, spacing=4)]
    e += [text(590, 650, 420, 50, "约束不是提示词，是写入时逐条校验。", size=15, color=AMBER, bold=True)]

    # ③ 判定
    e += [card(1060, 200, 460, 520, fill=WHITE, line=CYAN, radius=14)]
    e += [text(1080, 216, 300, 38, "③ 判定", size=28, bold=True, color=CYAN),
          text(1080, 258, 420, 26, "同是模板，章节与选用规则不同。", size=18, color=MUTED)]
    e += [card(1080, 300, 420, 150, fill=CYAN_SOFT, line=CYAN, radius=10)]
    e += [text(1094, 308, 390, 26, "技术方案评审报告模板", size=18, bold=True, color=CYAN),
          text(1094, 338, 390, 24, "适用类型：bmm:TechnicalReviewReport", size=14, color=INK),
          text(1094, 368, 390, 74, "必填：评审结论 / 规则清单 / 问题清单 / 证据\n选用：按评审类型 + 轮次", size=14, color=INK, spacing=3)]
    e += [text(1094, 410, 390, 24, "输出：评审报告（逐条结论 + 证据）", size=14, color=MUTED)]
    e += [card(1080, 470, 420, 150, fill=CYAN_SOFT, line=CYAN, radius=10)]
    e += [text(1094, 478, 390, 26, "产品手册模板", size=18, bold=True, color=CYAN),
          text(1094, 508, 390, 24, "适用类型：产品手册", size=14, color=INK),
          text(1094, 538, 390, 74, "必填：产品概述 / 功能清单 / 操作步骤 / 常见问题\n选用：按产品 + 版本", size=14, color=INK, spacing=3)]
    e += [text(1094, 580, 390, 24, "输出：产品手册（章节 + 操作步骤）", size=14, color=MUTED)]
    e += [text(1080, 636, 420, 52,
               "先看 applicableReportType，再看 templateSelector；\n类型不匹配不选，缺章节报缺，不静默补。",
               size=15, color=INK, spacing=2)]
    e += [text(1080, 690, 420, 24, "不能按标题里出现“评审/手册”来猜模板。", size=14, color=CYAN, bold=True)]

    e += [rect(80, 740, 1440, 55, fill=INK, line=INK, radius=12)]
    e += [text(100, 740, 300, 55, "本体的产出", size=18, bold=True, color="7DE3D8", valign="middle"),
          text(300, 740, 1200, 55, "统一的词　·　可校验的边　·　稳定的口径　·　可追溯的结论",
               size=20, bold=True, color=WHITE, valign="middle")]

    e += footer("案例：技术方案评审（当前库）＋ 产品/流程/活动/任务（BMM/BPMN 本体）＋ 评审/产品手册模板", "04 / 06")
    return {"bg": PAPER, "elements": e}
# ===================== P5 智能体 × 技能 =====================

def slide5():
    e = []
    e += header(
        "04 / 智能体与技能",
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

    e += footer("技能 = 概率性；工具 = 确定性。设计开发 / domain_modeling / 需求分析已废弃。", "05 / 06")
    return {"bg": PAPER, "elements": e}


# ===================== P6 工具 =====================

def slide6():
    e = []
    e += header(
        "05 / 工具",
        [{"s": "工具提供", "color": INK}, {"s": "确定性执行", "color": BLUE}],
        "43 个现役 MCP 工具，六组能力；领域建模是核心，ext_* 独立为外部工具连接。",
    )

    groups = [
        ("领域建模", "9", True,
         ["save_knowledge", "doc_outline", "extract_state", "extract_candidates",
          "page_attributes_set", "image_extract", "import_probe", "import_plan", "import_apply"],
         "核心：分批建模 / 落页 / 写属性 / 结构化导入"),
        ("本体与技能", "3", False,
         ["ontology_types", "skills", "job_status"],
         "先查类型 / 技能 / 任务状态"),
        ("检索与规则", "8", False,
         ["knowledge_lookup", "lookup_turn_begin", "evidence_score", "reference_lookup",
          "rules_resolve", "ke_rule_check", "ke_rule_publish", "rule_publish"],
         "查知识、取规则、判证据"),
        ("治理审计", "8", False,
         ["audit_scan", "audit_plan", "audit_purge", "retag_preview",
          "retag_apply", "retag_rollback", "metric_compute", "page_copy"],
         "巡检、改类型、指标与复制"),
        ("跨库与迁移", "11", False,
         ["context_scan", "context_lookup", "context_page", "context_concept_apply",
          "context_concept_rollback", "context_authority", "context_authority_apply",
          "kb_export", "kb_import", "migrate_article_body", "clean_alias_noise"],
         "同名治理、权威副本、库迁移"),
        ("外部工具连接", "4", False,
         ["ext_attach_probe", "ext_doc_push", "ext_doc_parse", "ext_table"],
         "连接外部系统 / 文档 / 表单，按契约取数"),
    ]
    colors = [AMBER, VIOLET, ROSE, EMERALD, BLUE, CYAN]
    for i, (name, n, core, tools, note) in enumerate(groups):
        x = 80 + (i % 3) * 490
        y = 200 + (i // 3) * 270
        color = colors[i]
        e += [card(x, y, 460, 250, fill=WHITE, line=color, radius=14)]
        e += [text(x + 18, y + 14, 300, 34, name, size=22, bold=True, color=color),
              text(x + 390, y + 6, 52, 50, n, size=36, bold=True, color=color, font=MONO, align="right")]
        if core:
            e += tag(x + 120, y + 16, 58, 22, "核心", AMBER, AMBER_SOFT, size=12)
        for j, t in enumerate(tools):
            col, row = j % 2, j // 2
            e += [text(x + 18 + col * 212, y + 58 + row * 25, 206, 24, t,
                       size=13.5, color=INK, font=MONO)]
        e += [text(x + 18, y + 214, 424, 26, note, size=13.5, color=MUTED)]

    e += [rect(80, 740, 1440, 60, fill=INK, line=INK, radius=12)]
    e += [text(100, 740, 220, 60, "执行系统", size=18, bold=True, color="7DE3D8", valign="middle"),
          text(220, 740, 1280, 60,
               "企业 API　·　领域 MCP（it_platform）　·　DB　｜　绑定边 bpmn:dataObjectBindingTool　｜　先预览、后执行",
               size=16, color=WHITE, valign="middle")]

    e += footer("43 个工具全部现役；领域建模是核心能力，ext_* 负责外部系统连接。", "06 / 06")
    return {"bg": PAPER, "elements": e}


SLIDES = [slide1(), slide2(), slide3(), slide4(), slide5(), slide6()]

