"""驱动 bodhi-ea-modeler 跑一轮真实对话（观测用，零写入任务）。

- 鉴权：从 DB 取最近未撤销且未过期的 access_token（Bearer）。
- 建会话：POST /api/v1/sessions
- 发消息：POST /api/v1/agent-chat/{session}（SSE）
- 观测：logs/mcp_calls_*.log + messages 表 + 本脚本保存的 SSE 原文
"""
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

BASE = 'http://localhost:8080'
BIZ = 'dbc2528f-611b-48da-9a71-d7c93975adb4'
ONT = '08810cbd-af86-48d1-bd25-3b2c338e3d68'
OUT = pathlib.Path('/mnt/c/Users/PHJY/AppData/Local/Temp/agent_run.log')

PROMPT = (
    "这是一次**只读**验证，不要写任何数据、不要 apply。请按顺序做，并把每步的原样回执摘出来：\n"
    "1) 先调用工具 `skills`（不传参数）看技能目录，告诉我有哪些技能；\n"
    "2) 再调用 `skills(skill=\"service_detailed_design\")` 取该技能完整指令与它允许的类/关系/数据属性；\n"
    "3) 然后用只读方式回答：服务「签约账户选择服务」（slug: ea/mcpservice/签约账户选择服务）\n"
    "   目前写了哪些业务属性、读了哪些（读的是别的哪个服务写的），并据此说明 E1/E2 结论；\n"
    "4) 最后用一句话说明：回执里 `applied=false` 代表什么。\n"
    "回答里请列出你实际调用的工具名与关键参数。"
)


def psql(sql: str) -> str:
    return subprocess.run(
        ['docker', 'exec', 'WeKnora-postgres', 'psql', '-U', 'postgres', '-d', 'WeKnora', '-At', '-c', sql],
        capture_output=True, text=True).stdout.strip()


def call(path: str, body=None, method='POST', accept='application/json', timeout=600):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data, headers={
        'Authorization': 'Bearer ' + TOKEN, 'Content-Type': 'application/json', 'Accept': accept})
    return urllib.request.urlopen(req, timeout=timeout)


log = open(OUT, 'w', encoding='utf-8')


def say(*parts):
    line = ' '.join(str(p) for p in parts)
    print(line)
    log.write(line + '\n')
    log.flush()


TOKEN = psql("SELECT token FROM auth_tokens WHERE is_revoked = false AND token_type = 'access_token' "
             "AND expires_at > now() ORDER BY created_at DESC LIMIT 1")
say('=== token 长度 %d（前 20：%s…）===' % (len(TOKEN), TOKEN[:20]))

try:
    with call('/api/v1/agents', method='GET', timeout=30) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    agents = data.get('data') or []
    say('鉴权 OK：可见智能体 %d 个 → %s' % (len(agents), [a.get('id') for a in agents]))
except urllib.error.HTTPError as exc:
    say('鉴权失败：%s %s' % (exc.code, exc.read().decode('utf-8')[:200]))
    sys.exit(1)

session_id = ''
for payload in ({'agent_id': 'bodhi-ea-modeler', 'title': '技能驱动验证（只读）'},
                {'custom_agent_id': 'bodhi-ea-modeler', 'title': '技能驱动验证（只读）'},
                {'title': '技能驱动验证（只读）'}):
    try:
        with call('/api/v1/sessions', payload, timeout=30) as resp:
            body = json.loads(resp.read().decode('utf-8'))
        session_id = ((body.get('data') or {}).get('id') or (body.get('data') or {}).get('session_id')
                      or body.get('id') or '')
        say('建会话 payload=%s → id=%s' % (json.dumps(payload, ensure_ascii=False), session_id))
        break
    except urllib.error.HTTPError as exc:
        say('建会话失败 payload=%s：%s %s' % (json.dumps(payload, ensure_ascii=False), exc.code,
                                              exc.read().decode('utf-8')[:160]))
if not session_id:
    sys.exit(1)

say('=== 发送消息（SSE，最长 8 分钟）===')
try:
    with call('/api/v1/agent-chat/%s' % session_id,
              {'query': PROMPT, 'agent_id': 'bodhi-ea-modeler',
               'knowledge_base_ids': [BIZ, ONT], 'agent_enabled': True,
               'channel': 'web'}, accept='text/event-stream', timeout=480) as resp:
        for raw in resp:
            line = raw.decode('utf-8', 'replace').rstrip('\n')
            if line.strip():
                say('   | %s' % line[:400])
except urllib.error.HTTPError as exc:
    say('聊天失败：%s %s' % (exc.code, exc.read().decode('utf-8')[:300]))
except Exception as exc:  # noqa: BLE001
    say('聊天异常（可能是超时/流中断）：%s' % str(exc)[:200])

say('=== 会话消息（DB）===')
say(psql("SELECT role || ': ' || left(replace(content, chr(10), ' '), 300) FROM messages "
         "WHERE session_id = '%s' ORDER BY created_at" % session_id)[:2000])
log.close()
print('AGENT_RUN_DONE')
