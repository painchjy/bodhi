"""批量服务详细设计（3 批）——把「服务字典」编成通用保存载荷。

用法：python3 detail_bulk.py <batch1|batch2|batch3|all> [dry_run|apply]
设计口径：写方收敛（同一属性只让一个服务写）、跨服务读一律声明 operationDependsOnOperation（经接口）。
"""
import importlib
import json
import sys
sys.path.insert(0, '/mnt/c/Users/PHJY/source/bodhi2/tools/ke-core')
sys.path.insert(0, '/mnt/c/Users/PHJY/source/bodhi2/tools/ontology-mcp')
import ke_db

BIZ = 'dbc2528f-611b-48da-9a71-d7c93975adb4'
REPORT = 'ea/summary/手机银行注册实名验证签约流程it服务概要设计报告'
srv = importlib.reload(importlib.import_module('server'))


def existing_def(slug):
    row = ke_db.psql_csv("SELECT content FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s"
                         % (ke_db.sql_str(BIZ), ke_db.sql_str(slug)))
    if not row:
        return ""
    lines = (row[0]['content'] or '').splitlines()
    start = max((i for i, ln in enumerate(lines) if ln.startswith('> ')), default=-1) + 1
    end = next((i for i, ln in enumerate(lines) if ln.startswith('## ')), len(lines))
    return "\n".join(lines[start:end]).strip()


ENT_SLUG = {
    "客户信息": "ea/businessentity/客户信息",
    "待认证的注册用户": "ea/businessentity/待认证的注册用户",
    "《手机银行服务协议》": "ea/businessentity/《手机银行服务协议》",
    "签约结果": "ea/businessentity/签约结果",
    "行为记录": "ea/businessentity/行为记录",
    "账户": "ea/businessentity/账户",
}


def build(batch):
    """把服务字典编成 nodes/edges（复用既有实体；新属性自动挂到该服务的实体上）。"""
    nodes, edges, seen_attr, seen_ent, seen_op = [], [], set(), set(), set()
    for svc in batch:
        slug = "ea/mcpservice/%s" % svc["name"]
        nodes.append({"name": svc["name"], "type": "ea:MCPService",
                      "definition": existing_def(slug), "purpose": svc["purpose"],
                      "source_text": "（服务详细设计：%s）" % svc.get("note", "操作/属性/CRUD 级")})
        ent = svc["entity"]
        if ent not in seen_ent:
            nodes.append({"name": ent, "type": "ea:BusinessEntity",
                          "definition": existing_def(ENT_SLUG[ent]),
                          "source_text": "（服务详细设计引用的既有业务实体）"})
            seen_ent.add(ent)
        for name, (role, definition) in (svc.get("attrs") or {}).items():
            if name not in seen_attr:
                nodes.append({"name": name, "type": "easvc:BusinessAttribute",
                              "definition": definition,
                              "attributes": {"easvc:keyRole": role}})
                seen_attr.add(name)
                edges.append({"source": ent, "type": "easvc:hasBusinessAttribute", "target": name})
                edges.append({"source": name, "type": "easvc:attributeOf", "target": ent})
        for name, (definition, method, idem, boundary) in (svc.get("ops") or {}).items():
            nodes.append({"name": name, "type": "easvc:ServiceOperation", "definition": definition,
                          "attributes": {"easvc:operationMethod": method,
                                         "easvc:isIdempotent": idem,
                                         "easvc:transactionBoundary": boundary}})
            edges.append({"source": svc["name"], "type": "easvc:serviceHasOperation", "target": name})
        edges.append({"source": svc["name"], "type": "easvc:serviceOwnsEntity", "target": ent})
        for op_name, attr_name, crud in svc.get("crud") or []:
            edges.append({"source": op_name, "type": "easvc:operationOperatesOnAttribute",
                          "target": attr_name, "properties": {"easvc:crudKind": crud}})
        for op_name, dep in svc.get("deps") or []:
            edges.append({"source": op_name, "type": "easvc:operationDependsOnOperation",
                          "target": dep})
    return nodes, edges


def run(batch, mode="dry_run"):
    nodes, edges = build(batch)
    upstream = ["ea/mcpservice/%s" % s["name"] for s in batch] + [REPORT,
                                                                 "ea/mcpservice/身份三要素采集服务",
                                                                 "ea/mcpservice/实名联网核查服务"]
    res = srv.save_knowledge(BIZ, stage='graph', model='ea',
                             report={"upstream": upstream}, nodes=nodes, edges=edges, mode=mode)
    print('=== %s / %s：节点 %d / 边 %d ===' % (
        [s["name"] for s in batch][0][:8] + '…' if len(batch) > 1 else batch[0]["name"],
        mode, len(nodes), len(edges)))
    print('   applied=%s created=%d merged=%d violations=%d'
          % (res.get('applied'), len(res['created']), len(res['merged']), len(res['violations'])))
    for v in res['violations'][:10]:
        print('   [%s] %s' % (v.get('kind'), v.get('reason')))
    if res.get('retract_planned'):
        print('   retract_planned=%s' % json.dumps(res['retract_planned'], ensure_ascii=False))
    if res.get('retract'):
        print('   retract=%s' % json.dumps(res['retract'], ensure_ascii=False)[:300])
    print('   crud_matrix=%s' % json.dumps(res.get('crud_matrix'), ensure_ascii=False))
    return res

BATCH1 = [
    {"name": "客户信息归集与客户编号分配服务", "entity": "客户信息",
     "purpose": "在核查通过的前提下，以三要素为锚归集客户信息至 ECIF 并分配正式客户编号",
     "note": "由本服务**独占**写 客户编号（PK），读三要素与核查结论走接口",
     "ops": {
         "归集客户信息并分配编号": ("收到核查通过结论后归集客户信息并分配/复用正式客户编号",
                                    "MCP tool: collectToECIF", "true", "multi-aggregate"),
         "查询客户编号": ("按三要素/客户标识查询正式客户编号", "MCP tool: getCustomerNo", "true", "none"),
     },
     "crud": [("归集客户信息并分配编号", "客户编号", "C"),
              ("归集客户信息并分配编号", "客户姓名", "R"),
              ("归集客户信息并分配编号", "证件种类", "R"),
              ("归集客户信息并分配编号", "证件号码", "R"),
              ("归集客户信息并分配编号", "渠道补充信息", "R"),
              ("归集客户信息并分配编号", "核查结论", "R"),
              ("归集客户信息并分配编号", "核查流水号", "R"),
              ("查询客户编号", "客户编号", "R"),
              ("查询客户编号", "客户姓名", "R")],
     "deps": [("归集客户信息并分配编号", "查询待核查三要素"),
              ("归集客户信息并分配编号", "查询核查结果")]},

    {"name": "客户账户关系与渠道开通标志同步服务", "entity": "账户",
     "purpose": "把签约结果同步到 ECIF 的「客户-账户」关系与渠道开通标志，保证渠道与主数据一致",
     "note": "由本服务独占写 渠道开通标志/账户号",
     "attrs": {"账户号": ("PK", "客户在本行的账户号（账户实体唯一标识）"),
               "渠道开通标志": ("NONE", "渠道侧是否已开通（签约同步后置位）")},
     "ops": {
         "同步客户账户关系": ("同步 ECIF 的客户-账户关系并置位渠道开通标志",
                              "MCP tool: syncAccountRelation", "true", "multi-aggregate"),
         "查询渠道开通标志": ("查询账户在渠道侧的开通标志", "MCP tool: getChannelFlag", "true", "none"),
     },
     "crud": [("同步客户账户关系", "客户编号", "R"),
              ("同步客户账户关系", "账户号", "R"),
              ("同步客户账户关系", "渠道开通标志", "C,U"),
              ("查询渠道开通标志", "客户编号", "R"),
              ("查询渠道开通标志", "账户号", "R"),
              ("查询渠道开通标志", "渠道开通标志", "R")],
     "deps": [("同步客户账户关系", "查询客户编号"),
              ("查询渠道开通标志", "查询客户编号")]},

    {"name": "手机号码验证服务", "entity": "待认证的注册用户",
     "purpose": "下发并核验一次性短信验证码，防重放与限次，通过后把注册用户置为「号码已核实」",
     "note": "由本服务独占写 号码核实状态；手机号码只读（写方是注册信息录入服务）",
     "attrs": {"号码核实状态": ("NONE", "号码核实状态（未核实/已核实/核实失败）")},
     "ops": {
         "下发验证码": ("下发一次性短信验证码（限次、防重放，旧码作废）",
                        "MCP tool: sendSmsCode", "false", "none"),
         "核实验证码": ("核验客户提交的验证码并把注册用户置为「号码已核实」",
                        "MCP tool: verifySmsCode", "false", "single"),
         "查询号码核实结果": ("查询号码核实状态", "MCP tool: getPhoneVerification", "true", "none"),
     },
     "crud": [("下发验证码", "手机号码", "R"),
              ("下发验证码", "注册状态", "R"),
              ("核实验证码", "手机号码", "R"),
              ("核实验证码", "号码核实状态", "C,U"),
              ("查询号码核实结果", "手机号码", "R"),
              ("查询号码核实结果", "号码核实状态", "R")],
     "deps": [("下发验证码", "查询注册受理状态"),
              ("核实验证码", "查询注册受理状态"),
              ("查询号码核实结果", "查询注册受理状态")]},
]

BATCH2 = [
    {"name": "服务协议签署服务", "entity": "《手机银行服务协议》",
     "purpose": "受理客户签署《手机银行服务协议》，留存协议版本、签署时间与签署渠道",
     "note": "由本服务独占写 协议签署状态/时间/渠道",
     "attrs": {"协议版本": ("NONE", "被签署的协议版本号"),
               "协议签署状态": ("NONE", "协议签署状态（未签署/已签署/已失效）"),
               "协议签署时间": ("NONE", "协议签署时间"),
               "协议签署渠道": ("NONE", "协议签署渠道（App/柜面/网银等）")},
     "ops": {
         "签署服务协议": ("受理客户签署确认，留存协议版本/时间/渠道",
                          "MCP tool: signAgreement", "true", "single"),
         "查询协议签署状态": ("查询协议的签署状态与时间", "MCP tool: getAgreement", "true", "none"),
     },
     "crud": [("签署服务协议", "协议版本", "R"),
              ("签署服务协议", "协议签署状态", "C,U"),
              ("签署服务协议", "协议签署时间", "C,U"),
              ("签署服务协议", "协议签署渠道", "C,U"),
              ("查询协议签署状态", "协议版本", "R"),
              ("查询协议签署状态", "协议签署状态", "R"),
              ("查询协议签署状态", "协议签署时间", "R")]},

    {"name": "渠道权限与限额配置服务", "entity": "账户",
     "purpose": "受理并校验转账限额、风险测评、消息通知、设备绑定等渠道权限与限额配置，形成签约申请参数",
     "note": "由本服务独占写 转账限额/渠道权限/绑定设备",
     "attrs": {"转账限额": ("NONE", "客户设定的转账限额（受渠道与风险等级上限约束）"),
               "渠道权限": ("NONE", "渠道权限与消息通知等配置（含理财/基金风险测评结论）"),
               "绑定设备": ("NONE", "签约绑定的设备标识（换绑需留痕）")},
     "ops": {
         "配置渠道权限与限额": ("校验并保存渠道权限、限额与设备绑定，形成签约申请参数",
                                "MCP tool: configureChannelLimits", "true", "single"),
         "查询渠道权限与限额": ("查询当前渠道权限与限额配置",
                                "MCP tool: getChannelLimits", "true", "none"),
     },
     "crud": [("配置渠道权限与限额", "客户编号", "R"),
              ("配置渠道权限与限额", "账户号", "R"),
              ("配置渠道权限与限额", "渠道开通标志", "R"),
              ("配置渠道权限与限额", "转账限额", "C,U"),
              ("配置渠道权限与限额", "渠道权限", "C,U"),
              ("配置渠道权限与限额", "绑定设备", "C,U"),
              ("查询渠道权限与限额", "转账限额", "R"),
              ("查询渠道权限与限额", "渠道权限", "R"),
              ("查询渠道权限与限额", "绑定设备", "R")],
     "deps": [("配置渠道权限与限额", "查询渠道开通标志"),
              ("配置渠道权限与限额", "查询客户编号")]},

    {"name": "登录凭据设置服务", "entity": "待认证的注册用户",
     "purpose": "受理登录密码/生物特征设置，生成仅存于渠道/设备侧的登录凭据",
     "note": "由本服务独占写 登录凭据状态；凭据本身不进客户主数据",
     "attrs": {"登录凭据状态": ("NONE", "渠道登录凭据设置状态（未设置/已设置）")},
     "ops": {
         "设置登录凭据": ("校验策略并设置登录密码/生物特征，生成渠道凭据",
                          "MCP tool: setLoginCredential", "true", "none"),
         "查询凭据设置结果": ("查询登录凭据设置结果", "MCP tool: getLoginCredential", "true", "none"),
     },
     "crud": [("设置登录凭据", "手机号码", "R"),
              ("设置登录凭据", "号码核实状态", "R"),
              ("设置登录凭据", "登录凭据状态", "C,U"),
              ("查询凭据设置结果", "登录凭据状态", "R")],
     "deps": [("设置登录凭据", "查询号码核实结果"),
              ("设置登录凭据", "查询注册受理状态")]},
]



BATCH3 = [
    {"name": "渠道行为审计留痕服务", "entity": "行为记录",
     "purpose": "横切留痕：追加记录注册、登录、实名认证、签约、解约、限额调整、设备换绑等行为",
     "note": "只追加不改写；客户编号**只读**（写方是归集服务），因此不会与它形成写耦合",
     "attrs": {"行为类型": ("NONE", "行为类型（注册/登录/实名/签约/解约/限额调整/设备换绑…）"),
               "行为时间": ("NONE", "行为发生时间"),
               "操作者": ("NONE", "操作者（客户/柜面/系统）")},
     "ops": {
         "追加行为记录": ("只追加一条行为记录（可审计，不做修改）",
                          "MCP tool: appendAudit", "false", "single"),
         "查询行为记录": ("按客户/时间范围查询行为记录", "MCP tool: queryAudit", "true", "none"),
     },
     "crud": [("追加行为记录", "客户编号", "R"),
              ("追加行为记录", "行为类型", "C"),
              ("追加行为记录", "行为时间", "C"),
              ("追加行为记录", "操作者", "C"),
              ("查询行为记录", "客户编号", "R"),
              ("查询行为记录", "行为类型", "R"),
              ("查询行为记录", "行为时间", "R"),
              ("查询行为记录", "操作者", "R")],
     "deps": [("追加行为记录", "查询客户编号"),
              ("查询行为记录", "查询客户编号")]},

    {"name": "签约结果落地服务", "entity": "签约结果",
     "purpose": "生成签约结果并写入手机银行用户管理服务；重复提交按幂等返回既有签约结果",
     "note": "由本服务独占写 签约状态/编号/时间",
     "attrs": {"签约编号": ("NONE", "签约结果的唯一编号（幂等键之一）"),
               "签约状态": ("NONE", "签约状态（有效/已解约）"),
               "签约时间": ("NONE", "签约/变更时间")},
     "ops": {
         "写入签约结果": ("校验签约申请（账户、协议、渠道参数）后生成并写入签约结果",
                          "MCP tool: saveSignResult", "true", "multi-aggregate"),
         "查询签约结果": ("按签约编号/账户查询签约结果", "MCP tool: getSignResult", "true", "none"),
         "追加签约变更记录": ("限额调整/换绑设备/解约时更新签约结果并留痕",
                              "MCP tool: appendSignChange", "true", "multi-aggregate"),
     },
     "crud": [("写入签约结果", "客户编号", "R"),
              ("写入签约结果", "账户号", "R"),
              ("写入签约结果", "协议签署状态", "R"),
              ("写入签约结果", "渠道开通标志", "R"),
              ("写入签约结果", "签约编号", "C"),
              ("写入签约结果", "签约状态", "C,U"),
              ("写入签约结果", "签约时间", "C,U"),
              ("查询签约结果", "签约编号", "R"),
              ("查询签约结果", "签约状态", "R"),
              ("查询签约结果", "签约时间", "R"),
              ("追加签约变更记录", "签约编号", "R"),
              ("追加签约变更记录", "签约状态", "U"),
              ("追加签约变更记录", "签约时间", "U")],
     "deps": [("写入签约结果", "查询客户编号"),
              ("写入签约结果", "查询渠道开通标志"),
              ("写入签约结果", "查询协议签署状态")]},

    {"name": "签约账户选择服务", "entity": "账户",
     "purpose": "受理签约账户选择：本行账户校验本人名下；他行卡按五要素与所选验证方式完成账户验证",
     "note": "由本服务独占写 签约账户标志/他行卡验证方式",
     "attrs": {"签约账户标志": ("NONE", "该账户是否被选为签约账户"),
               "他行卡号": ("NONE", "他行卡卡号（五要素之一）"),
               "他行卡验证方式": ("NONE", "他行卡验证方式（小额鉴权/短信鉴权/外部清算或卡组织接口）")},
     "ops": {
         "受理本行账户": ("校验所选账户为本人名下并记录为签约账户",
                          "MCP tool: acceptOwnAccount", "true", "single"),
         "验证他行卡并受理": ("按五要素与所选验证方式完成他行卡验证并受理",
                              "MCP tool: acceptOtherBankCard", "false", "multi-aggregate"),
         "查询账户受理结果": ("查询签约账户受理结果", "MCP tool: getAccountAcceptance", "true", "none"),
     },
     "crud": [("受理本行账户", "客户编号", "R"),
              ("受理本行账户", "账户号", "R"),
              ("受理本行账户", "签约账户标志", "C,U"),
              ("验证他行卡并受理", "客户姓名", "R"),
              ("验证他行卡并受理", "证件种类", "R"),
              ("验证他行卡并受理", "证件号码", "R"),
              ("验证他行卡并受理", "他行卡号", "R"),
              ("验证他行卡并受理", "他行卡验证方式", "C"),
              ("验证他行卡并受理", "签约账户标志", "C,U"),
              ("查询账户受理结果", "签约账户标志", "R"),
              ("查询账户受理结果", "他行卡验证方式", "R")],
     "deps": [("受理本行账户", "查询客户编号"),
              ("验证他行卡并受理", "查询待核查三要素")]},
]

# 设计收口（第二轮）：把两条"提醒"消解掉
#  ① 采集服务的查询操作读「客户编号」（归集服务写）→ 声明经接口；
#  ② 4 个非幂等写操作 → 补 `easvc:retryPolicy`（重试/补偿与幂等键来源）。
FIXUPS = {
    "deps": [("查询待核查三要素", "查询客户编号")],
    "ops": [
        # (操作名, 服务名/实体用于定位, 定义复用 slug, 现有属性, 补的 retryPolicy)
        ("查询待核查三要素", "ea-service/serviceoperation/查询待核查三要素",
         {"easvc:operationMethod": "MCP tool: getPendingIdentity",
          "easvc:isIdempotent": "true", "easvc:transactionBoundary": "none"}, None),
        ("核实验证码", "ea-service/serviceoperation/核实验证码",
         {"easvc:operationMethod": "MCP tool: verifySmsCode",
          "easvc:isIdempotent": "false", "easvc:transactionBoundary": "single"},
         "以「手机号码+一次性验证码」为幂等键；验证码校验成功后即作废，失败不重试、需客户重新获取"),
        ("核验三要素一致性", "ea-service/serviceoperation/核验三要素一致性",
         {"easvc:operationMethod": "MCP tool: verifyIdentity",
          "easvc:isIdempotent": "false", "easvc:transactionBoundary": "none"},
         "以「核查流水号」为幂等键：同流水号重复调用返回首次结论，不重复产生新流水"),
        ("追加行为记录", "ea-service/serviceoperation/追加行为记录",
         {"easvc:operationMethod": "MCP tool: appendAudit",
          "easvc:isIdempotent": "false", "easvc:transactionBoundary": "single"},
         "以「行为ID=客户编号+行为类型+行为时间」为幂等键，重复追加按幂等丢弃；失败按 1/2/4s 退避重试 3 次"),
        ("验证他行卡并受理", "ea-service/serviceoperation/验证他行卡并受理",
         {"easvc:operationMethod": "MCP tool: acceptOtherBankCard",
          "easvc:isIdempotent": "false", "easvc:transactionBoundary": "multi-aggregate"},
         "以「他行卡号+客户编号」为幂等键；小额鉴权金额与次数受限额约束，失败不得自动重试，需客户重新发起"),
    ],
}


def run_fixups(mode="dry_run"):
    nodes, edges = [], []
    for op_name, slug, attrs, retry in FIXUPS["ops"]:
        base = dict(attrs)
        if retry:
            base["easvc:operationRetryPolicy"] = retry
        nodes.append({"name": op_name, "type": "easvc:ServiceOperation",
                      "definition": existing_def(slug), "attributes": base})
    for op_name, dep in FIXUPS["deps"]:
        edges.append({"source": op_name, "type": "easvc:operationDependsOnOperation",
                      "target": dep})
    res = srv.save_knowledge(BIZ, stage='graph', model='ea',
                             report={"upstream": ["ea/mcpservice/身份三要素采集服务", REPORT]},
                             nodes=nodes, edges=edges, mode=mode)
    print('=== fixups / %s：节点 %d / 边 %d ===' % (mode, len(nodes), len(edges)))
    print('   applied=%s created=%d merged=%d violations=%d'
          % (res.get('applied'), len(res['created']), len(res['merged']), len(res['violations'])))
    for v in res['violations'][:8]:
        print('   [%s] %s' % (v.get('kind'), v.get('reason')))
    return res


if __name__ == '__main__':
    which = (sys.argv[1] if len(sys.argv) > 1 else 'batch1').lower()
    mode = (sys.argv[2] if len(sys.argv) > 2 else 'dry_run').lower()
    batches = {'batch1': BATCH1, 'batch2': BATCH2, 'batch3': BATCH3}
    if which == 'all':
        for key in ('batch1', 'batch2', 'batch3'):
            run(batches[key], mode)
    elif which == 'fixups':
        run_fixups(mode)
    else:
        run(batches[which], mode)
