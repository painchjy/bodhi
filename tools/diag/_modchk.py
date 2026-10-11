"""BPMN 模块是否注册 + 新类/新关系是否进编译产物。"""
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
man = json.loads((REPO / "artifacts" / "manifest.json").read_text(encoding="utf-8"))
print("module_keys =", man.get("module_keys"))
print("totals =", man.get("totals"))
for m in man.get("modules", []):
    print("  module %-8s label=%-30s short=%s" % (m.get("key"), m.get("label"), m.get("short_label")))

idx = json.loads((REPO / "artifacts" / "weknora" / "ontology_index.json").read_text(encoding="utf-8"))
WANT_C = ("bpmn:Activity", "bpmn:Task", "bpmn:DataObject", "bpmn:Property",
          "bmm:Report", "bmm:RequirementAnalysisReport", "agent:EvaluationPlan")
WANT_R = ("bpmn:processHasActivity", "bpmn:activityProducesReport", "bpmn:activityUsesEntity",
          "bpmn:dataObjectHasProperty", "bpmn:taskExecutesRule",
          "agent:skillImplementsActivity", "agent:agentPerformsTask")
for mod in idx.get("models", []):
    names = [c["name"] for c in (mod.get("classes") or [])]
    print("  model %-6s classes=%2d  关注类=%s" % (mod.get("key"), len(names),
                                                   [n for n in names if n in WANT_C]))
for mod in idx.get("models", []):
    for r in (mod.get("relations") or []):
        if r.get("name") in WANT_R:
            print("  rel %-34s domain=%s range=%s" % (r["name"], r.get("domain"), r.get("range")))
