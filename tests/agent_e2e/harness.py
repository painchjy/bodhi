from __future__ import annotations
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "tools" / "ke-core")):
    if p not in sys.path:
        sys.path.insert(0, p)


def load_cases() -> tuple:
    plan = json.loads((HERE / "cases" / "plan.json").read_text(encoding="utf-8"))
    oracle = json.loads((HERE / "cases" / "oracle.json").read_text(encoding="utf-8"))
    plan_ids = {c["case_id"] for c in plan.get("cases", [])}
    oracle_ids = set((oracle.get("cases") or {}).keys())
    if plan_ids != oracle_ids:
        raise ValueError("plan/oracle case ids mismatch")
    return plan, oracle


def _psql(sql: str) -> list:
    from ke_db import psql_csv
    return psql_csv(sql)

def parse_scope(prompt: str) -> list:
    if "【库范围】" not in prompt:
        raise RuntimeError("agent prompt missing 【库范围】domain_kb/work_kb")
    scope = prompt.split("【库范围】", 1)[1].splitlines()[0]
    parts = [x.strip() for x in scope.split("|")]
    if len(parts) < 2 or "domain_kb:" not in parts[0] or "work_kb:" not in parts[1]:
        raise RuntimeError("bad 【库范围】format: " + scope)
    def _name(value: str) -> str:
        return value.strip().strip("*`").strip()
    return [_name(parts[0].split("domain_kb:", 1)[1]), _name(parts[1].split("work_kb:", 1)[1])]



@dataclass
class WeKnoraClient:
    base_url: str
    token: str
    agent_id: str
    kb_ids: list
    timeout: float = 480.0

    @classmethod
    def from_env(cls):
        token = os.environ.get("BODHI_WEKNORA_TOKEN", "").strip()
        from ke_db import sql_str
        if not token:
            rows = _psql("SELECT token FROM auth_tokens WHERE is_revoked = false AND token_type = " + sql_str("access_token") + " AND expires_at > now() ORDER BY created_at DESC LIMIT 1")
            token = str(rows[0].get("token") or "") if rows else ""

        agent_id = os.environ.get("BODHI_REVIEW_AGENT_ID", "").strip()
        if not agent_id:
            agent_name = os.environ.get("BODHI_REVIEW_AGENT_NAME", "技术方案评审智能体").strip()
            rows = _psql("SELECT id FROM custom_agents WHERE name = " + sql_str(agent_name)
                         + " AND name NOT LIKE " + sql_str("%停用%")
                         + " ORDER BY created_at DESC LIMIT 1")
            agent_id = str(rows[0].get("id") or "") if rows else ""
        if not agent_id:
            # Prompt-scoped fallback: survives agent renames and avoids the old “（测试）” name.
            rows = _psql("SELECT id FROM custom_agents WHERE name NOT LIKE " + sql_str("%停用%")
                         + " AND config->>" + sql_str("system_prompt") + " LIKE " + sql_str("%【库范围】%")
                         + " ORDER BY created_at DESC LIMIT 1")
            agent_id = str(rows[0].get("id") or "") if rows else ""
        rows = _psql("SELECT config->>" + sql_str("system_prompt") + " AS prompt FROM custom_agents WHERE id = " + sql_str(agent_id))
        prompt = str(rows[0].get("prompt") or "") if rows else ""
        names = parse_scope(prompt)

        # Resolve each prompt scope name independently. A stale soft-deleted duplicate
        # (same display name) must never enter the session's kb_ids.
        kb_rows = _psql("SELECT id,name,COALESCE(updated_at::text,'') AS updated_at FROM knowledge_bases "
                        "WHERE deleted_at IS NULL AND name IN (" + ",".join(sql_str(x) for x in names) + ")")
        by_name = {}
        for row in kb_rows:
            by_name.setdefault(str(row.get("name") or ""), []).append(row)
        page_counts = {}
        ids = [str(r.get("id")) for r in kb_rows if r.get("id")]
        if ids:
            for row in _psql("SELECT knowledge_base_id,count(*) AS n FROM wiki_pages WHERE deleted_at IS NULL "
                             "AND knowledge_base_id IN (" + ",".join(sql_str(x) for x in ids) + ") GROUP BY 1"):
                page_counts[str(row.get("knowledge_base_id"))] = int(row.get("n") or 0)
        kbs = []
        for name in names:
            candidates = by_name.get(name) or []
            if not candidates:
                raise RuntimeError("scope kb not found: " + name)
            candidates.sort(key=lambda r: (page_counts.get(str(r.get("id")), 0),
                                           str(r.get("updated_at") or ""), str(r.get("id"))), reverse=True)
            chosen = str(candidates[0].get("id") or "")
            if chosen and chosen not in kbs:
                kbs.append(chosen)
        override = [x.strip() for x in os.environ.get("BODHI_REVIEW_KB_IDS", "").split(",") if x.strip()]
        if override:
            kbs = override
        client = cls(os.environ.get("BODHI_WEKNORA_BASE_URL", "http://localhost:8080").rstrip("/"), token, agent_id, kbs)
        if not (client.token and client.agent_id and client.kb_ids):
            raise RuntimeError("need token/agent/prompt scope")
        return client

    def _call(self, path: str, body=None, accept: str = "application/json"):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base_url + path, method="POST" if body is not None else "GET", data=data, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", "Accept": accept})
        return urllib.request.urlopen(req, timeout=self.timeout)

    def create_session(self, title: str) -> str:
        for key in ("agent_id", "custom_agent_id"):
            try:
                with self._call("/api/v1/sessions", {key: self.agent_id, "title": title}) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                data = body.get("data") or body
                sid = data.get("id") or data.get("session_id") or ""
                if sid:
                    return str(sid)
            except urllib.error.HTTPError:
                continue
        raise RuntimeError("cannot create review session")

    def upload_document(self, file_path: str, review_no: str = "", doc_id: str = "") -> str:
        """Upload a case file to the external document service and bind it to review_no.

        The review agent reads the file through ``ext_doc_parse(doc_id=...)``; the
        upload service is the real storage implementation (not a test replacement).
        """
        path = Path(file_path)
        if not path.is_file():
            raise RuntimeError("case source file not found: " + str(path))
        raw = path.read_bytes()
        base = (os.environ.get("BODHI_IT_MCP_BASE")
                or os.environ.get("IT_MCP_PUBLIC_BASE")
                or "http://127.0.0.1:8770").rstrip("/")
        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", path.stem).strip("-") or "case"
        doc_id = doc_id or ("e2e-" + safe_stem + "-" + hashlib.sha256(raw).hexdigest()[:12])
        query = {"name": path.name}
        if review_no:
            query["review_no"] = review_no
        url = base + "/files/" + urllib.parse.quote(doc_id, safe="") + "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, data=raw, method="POST",
                                     headers={"Content-Type": "text/markdown",
                                              "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("case upload failed HTTP %s: %s" % (exc.code, detail)) from exc
        if not body.get("ok"):
            raise RuntimeError("case upload rejected: " + json.dumps(body, ensure_ascii=False)[:300])
        return str(body.get("doc_id") or doc_id)

    def chat(self, session_id: str, query: str) -> str:
        payload = {"query": query, "agent_id": self.agent_id, "knowledge_base_ids": self.kb_ids, "agent_enabled": True, "channel": "web"}
        collected = []
        with self._call("/api/v1/agent-chat/" + session_id, payload, "text/event-stream") as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip()
                if line.startswith("data:"):
                    collected.append(line[5:].strip())
        return chr(10).join(collected)

    def messages(self, session_id: str) -> list:
        from ke_db import sql_str
        rows = _psql("SELECT id, role, content, COALESCE(agent_steps::text, " + sql_str("[]") + ") AS steps FROM messages WHERE session_id = " + sql_str(session_id) + " AND deleted_at IS NULL ORDER BY created_at")
        out = []
        for row in rows:
            try:
                steps = json.loads(row.get("steps") or "[]")
            except Exception:
                steps = []
            out.append({"id": row.get("id"), "role": row.get("role"), "content": row.get("content") or "", "agent_steps": steps})
        return out

    def run_case(self, case: dict) -> dict:
        raw_doc = str(case.get("input_doc_id") or "")
        doc_id = os.environ.get(raw_doc[4:], "") if raw_doc.startswith("env:") else raw_doc
        source_file = str(case.get("source_file") or "").strip()
        if not doc_id and source_file:
            path = Path(source_file)
            if not path.is_absolute():
                path = REPO / path
            doc_id = self.upload_document(str(path), review_no="e2e-" + str(case.get("case_id") or "").lower())
        if not doc_id:
            raise RuntimeError("missing input doc for " + str(case.get("case_id")))
        query = str(case.get("review_query") or "").replace("{{doc_id}}", doc_id)
        sid = self.create_session("agent-e2e-" + str(case.get("case_id")))
        self.chat(sid, query)
        msgs = self.messages(sid)
        report = next((m["content"] for m in reversed(msgs) if m["role"] == "assistant"), "")
        return {"case_id": case.get("case_id"), "session_id": sid, "report": report, "messages": msgs}


def _forbidden_hit(text: str, claim: str) -> bool:
    """True only for an affirmative forbidden claim, not for a denial/quote of it."""
    if not claim:
        return False
    start = 0
    while True:
        pos = text.find(claim, start)
        if pos < 0:
            return False
        prefix = text[max(0, pos - 18):pos]
        if re.search(r"(没有出现|未出现|不存在|并非|不是|不能|不得|禁止|避免|不构成)[^。；\n]{0,12}$", prefix):
            start = pos + len(claim)
            continue
        return True


def _causal_section(text: str) -> str:
    """Return the report's causal-analysis section, if it has an explicit heading."""
    lines = str(text or "").splitlines()
    out = []
    active = False
    for line in lines:
        if line.startswith("## "):
            active = ("因果" in line) or ("专项" in line)
            continue
        if active:
            out.append(line)
    return "\n".join(out)


def score_case(plan_case: dict, oracle_case: dict, run: dict) -> dict:
    text = str(run.get("report") or "")
    steps = json.dumps([m.get("agent_steps") for m in run.get("messages") or []], ensure_ascii=False)
    expected = str(oracle_case.get("expected_verdict") or "")
    scope = str(oracle_case.get("verdict_scope") or "").strip().lower()
    verdict_text = _causal_section(text) if scope == "causal" else text
    if scope == "causal" and not verdict_text.strip():
        verdict_text = text
    def verdict_hit(value: str) -> bool:
        if value == "符合":
            return re.search(r"(?<!不)(?<!未)(?<!无)(?<!判)符合", verdict_text) is not None
        return bool(value and value in verdict_text)
    patterns = [str(x) for x in oracle_case.get("expected_patterns") or []]
    forbidden = [str(x) for x in oracle_case.get("forbidden_claims") or []]
    markers = [str(x) for x in oracle_case.get("oracle_markers") or []]
    required = [str(x) for x in oracle_case.get("required_terms") or []]
    checks = {"verdict": verdict_hit(expected),"patterns": all(p in text or p in steps for p in patterns),"forbidden": not any(_forbidden_hit(text, x) for x in forbidden),"oracle_leak": not any(x in text for x in markers),"required_terms": all(x in text for x in required),"nonempty": bool(text.strip())}
    errors = [k for k, ok in checks.items() if not ok]
    return {"case_id": plan_case.get("case_id"), "passed": not errors, "checks": checks, "errors": errors}


__all__ = ["WeKnoraClient", "load_cases", "score_case"]
