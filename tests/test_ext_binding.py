"""回归：DataObject ↔ Tool **绑定 = 图关系**（2026-10-29 用户口径）。

覆盖主线（全部走真实图/库，缺服务即 skip）：
1. 关系优先生效：有边 ⇒ `resolve_binding` ok 且 `binding_count == 1`（**忽略页面上旧的 3 个工具字符串**）；
2. 删边 ⇒ 不再 ok（`no_binding`），且不会因旧属性回退而"假装有绑定"；
3. 反查 `_objects_by_tool` 走关系；
4. 面板口径：`binding_payload` 的 `bound / unbound_tools`；
5. 绑定 → 映射 → `mappingStatus=confirmed`；
6. 工具页契约同步：建页 + MCP 服务关系 + 幂等；未建模工具刷新被拒。

⚠️ 用例自建自删 `_test_*` 库与图节点，不碰业务库。
"""
import json
import os
import re
import sys
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "ontology-mcp")):
    if p not in sys.path:
        sys.path.insert(0, p)

OBJ = "bpmn/dataobject/问题清单"
TOOL = "ext_issue_list"


@pytest.fixture
def graph():
    try:
        import ke_neo4j
        ke_neo4j.query("RETURN 1 AS ok", {}, timeout=8)
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("Neo4j 不可用：%r" % (str(exc)[:80],))
    return ke_neo4j


@pytest.fixture
def kb_id(graph):
    """临时 kb（只用图，不建 PG 库 ⇒ 用完 DETACH DELETE）。"""
    kb = "_test_binding_%s" % uuid.uuid4().hex[:8]
    yield kb
    graph.query("MATCH (n:BodhiInstance {kb_id:$kb}) DETACH DELETE n", {"kb": kb}, timeout=20)


@pytest.fixture
def store():
    import ke_ext_store
    return ke_ext_store


@pytest.fixture
def pg_kb(graph):
    """真库（PG 行 + 图节点），用完硬删；**不走 MCP**（避免等服务端缓存可见）。"""
    try:
        import ke_db
        ke_db.psql_csv("SELECT 1 AS ok")
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("PostgreSQL 不可用：%r" % (str(exc)[:80],))
    name = "_test_toolsync_%s" % uuid.uuid4().hex[:8]
    out = ke_db.psql(
        "INSERT INTO knowledge_bases (name, description, tenant_id, embedding_model_id, "
        "summary_model_id) SELECT %s, %s, 10000, embedding_model_id, summary_model_id "
        "FROM knowledge_bases LIMIT 1 RETURNING id;"
        % (ke_db.sql_str(name), ke_db.sql_str("契约同步回归（自动清理）")), stdin=True)
    kid_m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", out, re.I)
    if not kid_m:
        pytest.skip("建库失败（回执无 id）：%r" % (out[:120],))
    kid = kid_m.group(0)
    yield kid
    try:
        ke_db.psql("DELETE FROM wiki_pages WHERE knowledge_base_id = %s;" % ke_db.sql_str(kid))
        ke_db.psql("DELETE FROM knowledge_bases WHERE id = %s;" % ke_db.sql_str(kid))
    finally:
        graph.query("MATCH (n:BodhiInstance {kb_id:$kb}) DETACH DELETE n", {"kb": kid}, timeout=20)


def _mk_object(graph, kb, attrs=""):
    """建对象节点；`attrs` 非空时**故意写旧属性**（验证"关系优先、旧属性不再生效"）。"""
    q = ("MERGE (o:BodhiInstance {kb_id:$kb, slug:$slug}) "
         "SET o.page_type='bpmn:DataObject', o.module='bpmn', o.name='问题清单'")
    params = {"kb": kb, "slug": OBJ}
    if attrs:
        q += (", o.dataObjectInterface=$i, o.dataObjectAccessMode='query,maintain;maintain;query', "
              "    o.dataObjectKeyFields='review_no,issue_no;review_no,doc_title;doc_id'")
        params["i"] = attrs
    graph.query(q, params, timeout=10)


def test_relation_wins_over_stale_attributes(graph, kb_id, store):
    """有边 ⇒ 只认关系（旧属性里 3 个工具不再影响 binding_count）。"""
    _mk_object(graph, kb_id, attrs="ext_issue_list,ext_doc_push,ext_doc_parse")
    assert store.set_binding(kb_id, OBJ, TOOL)["ok"]

    got = store.resolve_binding(TOOL, kb_id, OBJ)
    assert got["ok"], got
    assert got["binding"]["tool"] == TOOL
    assert got["binding_count"] == 1, "旧属性里的其余 2 个工具不该再生效"


def test_unbind_means_no_binding(graph, kb_id, store):
    """删边 ⇒ **本库内**没有任何绑定（旧属性不再生效；跨库兜底另算）。"""
    _mk_object(graph, kb_id, attrs="ext_issue_list,ext_doc_push")
    assert store.set_binding(kb_id, OBJ, TOOL)["ok"]
    assert store.delete_binding(kb_id, OBJ, TOOL)["ok"]

    assert store.list_bindings(kb_id, OBJ) == []
    attrs = store._attrs_of(kb_id, OBJ)                     # noqa: SLF001
    assert not any(v.strip() for v in attrs.values()), attrs

    # `resolve_binding` 会跨库兜底（同 slug 在真实业务库里可能确有绑定）⇒ 只断言"不是本库"
    got = store.resolve_binding(TOOL, kb_id, OBJ)
    assert (not got["ok"]) or got.get("binding_kb") != kb_id, got



def test_objects_by_tool_uses_relation(graph, kb_id, store):
    """反查（不给 object）走关系：只有真的绑了该工具的对象被返回。"""
    _mk_object(graph, kb_id)
    assert store.set_binding(kb_id, OBJ, TOOL)["ok"]

    cands = store._objects_by_tool(kb_id, TOOL)          # noqa: SLF001
    assert [c["slug"] for c in cands] == [OBJ]

    got = store.resolve_binding(TOOL, kb_id, "")          # object 省略 ⇒ 反查唯一
    assert got["ok"] and got["object"]["slug"] == OBJ


def test_binding_payload_lists_bound_and_candidates(graph, kb_id, store):
    """面板口径：`bindings` / `unbound_tools` 分离（工具存在但未绑 ⇒ 出现在候选）。"""
    from srv import ext_api
    _mk_object(graph, kb_id)
    for t in (TOOL, "ext_catalog_query"):
        graph.query("MERGE (t:BodhiInstance {kb_id:$kb, slug:$slug}) "
                    "SET t.page_type='agent:Tool', t.name=$name, t.toolModes='query,maintain'",
                    {"kb": kb_id, "slug": "agent/tool/%s" % t, "name": t}, timeout=10)
    assert store.set_binding(kb_id, OBJ, TOOL)["ok"]

    got = ext_api.binding_payload({"object": OBJ, "kb_id": kb_id})
    assert got["ok"] and got["bound"] is True
    assert [b["tool"] for b in got["bindings"]] == [TOOL]
    assert [t["tool"] for t in got["unbound_tools"]] == ["ext_catalog_query"]


def test_mapping_roundtrip(graph, kb_id, store):
    """映射写在**边**上：写完确认 ⇒ confirmed，列序以 DataObject 为准。"""
    _mk_object(graph, kb_id)
    assert store.set_binding(kb_id, OBJ, TOOL)["ok"]
    fields = [{"field": "issue_no", "label": "编号"},
              {"field": "desc", "label": "问题描述"}]
    out = store.set_mapping(kb_id, OBJ, TOOL, fields)
    assert out["ok"] and out["columns"] == 2

    binds = store.list_bindings(kb_id, OBJ)
    assert binds[0]["status"] == "confirmed"
    fmap = json.loads(binds[0]["field_map"])
    assert fmap["order"] == ["issue_no", "desc"]
    assert fmap["items"][1]["label"] == "问题描述"


def test_mapping_requires_binding(graph, kb_id, store):
    """没绑定就写映射 ⇒ `no_binding`（不许凭空产生映射）。"""
    _mk_object(graph, kb_id)
    out = store.set_mapping(kb_id, OBJ, TOOL, [{"field": "issue_no"}])
    assert out["ok"] is False and out["error"] == "no_binding"


def test_tool_page_sync_creates_page_and_service_relation(graph, pg_kb):
    """契约同步：工具页 + MCP 服务页 + `toolProvidedByMcp` 关系；重复同步幂等。"""
    import ke_ext_tools as T
    kb_id = pg_kb
    dry = T.sync("it_platform", kb_id, tools=[TOOL], dry_run=True)
    if not dry.get("ok"):
        pytest.skip("外部服务不可用：%r" % (dry.get("error"),))
    assert dry["synced"] and dry["synced"][0]["columns"] > 0

    out = T.sync("it_platform", kb_id, tools=[TOOL])
    assert out["ok"], out
    assert out["synced"], out

    rows = graph.query(
        "MATCH (t:BodhiInstance {kb_id:$kb, slug:$slug}) "
        "OPTIONAL MATCH (t)-[:`agent:toolProvidedByMcp`]->(s:BodhiInstance) "
        "RETURN coalesce(t.name,'') AS name, coalesce(t.toolSchemaHash,'') AS hash, "
        "       coalesce(s.slug,'') AS svc",
        {"kb": kb_id, "slug": "agent/tool/%s" % TOOL}, timeout=10)
    assert rows and rows[0]["name"] == TOOL
    assert rows[0]["hash"].startswith("sha256:")
    assert rows[0]["svc"].startswith("agent/mcpservice/")

    again = T.sync("it_platform", kb_id, tools=[TOOL])
    assert again["ok"] and again["synced"], again



def test_refresh_rejects_unmodeled_tool(graph, kb_id, store):
    """`tool/refresh` 只作用于**已建模**的工具（本库没有 ⇒ 拒绝并给指引）。"""
    from srv import ext_api
    out = ext_api.tool_refresh_payload({"kb_id": kb_id, "tool": "not_modeled_tool"})
    assert out["ok"] is False and out["error"] == "tool_not_modeled"


