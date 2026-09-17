#!/usr/bin/env python
"""ontology-compiler 回归测试（纯 stdlib unittest，不依赖 pytest）。

跑法（仓库根目录）
------------------
    python -m unittest discover -s tools/ontology-compiler/tests -t tools/ontology-compiler -v
    python tools/ontology-compiler/tests/test_compiler.py

测试约定
--------
* 涉及产物的用例一律固定时间戳 `PINNED`（`generated_at=`），否则 `generated_at` 每次都变，
  sha256 / 逐字节比对永远失败；
* 不碰仓库里的 `artifacts/`：所有编译都落到临时目录（`tempfile.TemporaryDirectory`）；
* 断言「契约」而非逐字节快照：本体仍在演进，硬编码产物全文只会天天变红；
  真正逐字节可复现由 `test_second_compile_is_byte_identical` 用 sha256 差异来守。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
COMPILER_DIR = TESTS_DIR.parent
REPO_ROOT = COMPILER_DIR.parents[1]
CLI = COMPILER_DIR / "compile.py"
if str(COMPILER_DIR) not in sys.path:
    sys.path.insert(0, str(COMPILER_DIR))

from ontology_compiler import (  # noqa: E402
    COMPILER_VERSION,
    GENERATED_AT_ENV,
    compile_all,
    load_ontology,
    validate_ontology,
)
from ontology_compiler.config import ARTIFACT_SCHEMA_VERSION, NS, build_modules  # noqa: E402
from ontology_compiler.loader import resolved_generated_at  # noqa: E402
from ontology_compiler.validate import SEVERITY_ERROR, count_by_severity  # noqa: E402

PINNED = "2026-01-01T00:00:00Z"
MODULE_KEYS = ["bmm", "ea", "ea-service", "ea-ownership", "bmm-fd"]
BMM_NS = NS["bmm"]

# 生成器占位符（Neo4j 的 `{{BMM_NS}}` 之类）。README.md 里是**取值示例表格**，故排除。
PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")

# 每个模块都该有的产物（`{module}` 占位）
PER_MODULE_ARTIFACTS = [
    "weknora/extract_config.{module}.json",
    "json_schema/extraction_result.{module}.schema.json",
    "prompts/{module}_extraction.md",
]
# 全库共享的产物
SHARED_ARTIFACTS = [
    "manifest.json",
    "mapping/label_map.json",
    "json_schema/index.json",
    "json_schema/extraction_result.schema.json",
    "json_schema/knowledge_point.schema.json",
    "shacl/generated.shapes.ttl",
    "shacl/index.json",
    "neo4j/00_constraints.cypher",
    "neo4j/10_ontology.cypher",
    "neo4j/20_cross_layer_queries.cypher",
    "weknora/provenance.json",
]


class CompileFixture(unittest.TestCase):
    """公共装置：临时输出目录 + 固定时间戳的编译。"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="bodhi_artifacts_")
        self.addCleanup(tmp.cleanup)
        self.out = Path(tmp.name)

    def compile(self, **kwargs):
        """在临时目录编译；默认断言「无 ERROR」，返回 `CompileResult`。"""
        kwargs.setdefault("out_dir", self.out)
        kwargs.setdefault("generated_at", PINNED)
        result = compile_all(**kwargs)
        counts = count_by_severity(result.problems)
        self.assertEqual(
            counts[SEVERITY_ERROR],
            0,
            "本体存在 error 级问题，产物会被阻断：%s"
            % [p.as_dict() for p in result.problems if p.severity == SEVERITY_ERROR][:5],
        )
        return result

    def read(self, relative: str) -> str:
        return (self.out / relative).read_text(encoding="utf-8")

    def read_json(self, relative: str) -> dict:
        return json.loads(self.read(relative))


class LoadOntologyTests(unittest.TestCase):
    """载入层：TBox -> IR。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_ontology(generated_at=PINNED)

    def test_terms_are_really_loaded(self) -> None:
        """回归哨兵：rdflib 7.x 用 str 谓词查询会**静默返回空集**（曾出现「类 0 / 属性 0」）。"""
        stats = self.bundle.stats()
        self.assertEqual(stats["modules"], len(MODULE_KEYS))
        self.assertGreaterEqual(stats["classes"], 40)
        self.assertGreaterEqual(stats["object_properties"], 50)
        self.assertGreaterEqual(stats["data_properties"], 10)

    def test_known_class_keeps_labels_and_module(self) -> None:
        cls = self.bundle.classes[BMM_NS + "DesiredResult"]
        self.assertEqual(cls.module, "bmm")
        self.assertEqual(cls.local, "DesiredResult")
        self.assertEqual(cls.label, "预期成果")
        self.assertEqual(cls.prefixed, "bmm:DesiredResult")

    def test_every_module_owns_terms(self) -> None:
        for key in MODULE_KEYS:
            with self.subTest(module=key):
                self.assertTrue(self.bundle.sorted_classes(key), "模块 %s 没有解析到任何类" % key)

    def test_enum_members_are_captured(self) -> None:
        enums = [cls for cls in self.bundle.sorted_classes() if cls.is_enum]
        self.assertGreaterEqual(len(enums), 2)
        for enum in enums:
            with self.subTest(enum=enum.prefixed):
                self.assertTrue(enum.members, "枚举类 %s 没有任何取值" % enum.prefixed)
                self.assertTrue(enum.members[0].label, "枚举取值缺中文标签：%s" % enum.members[0].prefixed)

    def test_properties_have_domain_and_range(self) -> None:
        object_props = [p for p in self.bundle.sorted_properties() if p.is_object]
        self.assertTrue(object_props)
        with_domain = [p for p in object_props if p.domain_iris]
        self.assertGreater(len(with_domain), len(object_props) * 0.8)

    def test_generated_at_precedence(self) -> None:
        """显式参数 > 环境变量 > 当前时间。"""
        original = os.environ.get(GENERATED_AT_ENV)
        if original is not None:
            self.addCleanup(os.environ.__setitem__, GENERATED_AT_ENV, original)
        else:
            self.addCleanup(os.environ.pop, GENERATED_AT_ENV, None)
        os.environ[GENERATED_AT_ENV] = "2020-02-02T00:00:00Z"
        self.assertEqual(resolved_generated_at(), "2020-02-02T00:00:00Z")
        self.assertEqual(resolved_generated_at(PINNED), PINNED)
        self.assertEqual(load_ontology(generated_at=PINNED).generated_at, PINNED)
        os.environ.pop(GENERATED_AT_ENV, None)
        self.assertRegex(resolved_generated_at(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ValidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = load_ontology(generated_at=PINNED)

    def test_no_error_level_problems(self) -> None:
        counts = count_by_severity(validate_ontology(self.bundle))
        self.assertEqual(counts[SEVERITY_ERROR], 0, "本体不该有 error 级问题")

    def test_info_checks_can_be_skipped(self) -> None:
        verbose = validate_ontology(self.bundle)
        quiet = validate_ontology(self.bundle, include_info=False)
        self.assertTrue(verbose)
        self.assertLessEqual(len(quiet), len(verbose))
        self.assertFalse([p for p in quiet if p.severity == "info"])

    def test_problems_expose_stable_shape(self) -> None:
        for problem in validate_ontology(self.bundle):
            with self.subTest(code=problem.code):
                payload = problem.as_dict()
                self.assertIn("severity", payload)
                self.assertIn("code", payload)
                self.assertIn("message", payload)

    def test_module_keys_are_known(self) -> None:
        known = set(build_modules())
        for problem in validate_ontology(self.bundle):
            if problem.module:
                with self.subTest(code=problem.code, module=problem.module):
                    self.assertIn(problem.module, known)


def _disk_path(recorded: str) -> Path:
    """产物记录可能是绝对路径（临时目录）或仓库相对路径，统一还原成磁盘路径。"""
    path = Path(recorded)
    return path if path.is_absolute() else REPO_ROOT / recorded


class CompileTests(CompileFixture):
    """编译层：产物集合、manifest 自洽性、可复现性、模块裁剪。"""

    def test_writes_every_layer(self) -> None:
        result = self.compile()
        self.assertTrue(result.ok)
        self.assertTrue(result.wrote_artifacts)
        self.assertEqual(result.module_keys, MODULE_KEYS)
        expected = [
            relative.format(module=key)
            for key in MODULE_KEYS
            for relative in PER_MODULE_ARTIFACTS
        ] + SHARED_ARTIFACTS
        for relative in expected:
            with self.subTest(artifact=relative):
                self.assertTrue((self.out / relative).is_file(), "缺产物 %s" % relative)

    def test_manifest_matches_disk(self) -> None:
        result = self.compile()
        manifest = self.read_json("manifest.json")
        self.assertEqual(manifest["generated_at"], PINNED)
        self.assertEqual(manifest["compiler_version"], COMPILER_VERSION)
        self.assertEqual(manifest["module_keys"], MODULE_KEYS)
        self.assertEqual(len(manifest["artifacts"]), len(result.artifacts))
        recorded = {item["path"]: item for item in manifest["artifacts"]}
        for entry in result.artifacts:
            with self.subTest(path=entry.path):
                item = recorded[entry.path]
                full = _disk_path(entry.path)
                digest = hashlib.sha256(full.read_bytes()).hexdigest()
                self.assertEqual(item["sha256"], digest)
                self.assertEqual(item["bytes"], full.stat().st_size)
                self.assertTrue(item["emitter"])

    def test_no_leftover_placeholders(self) -> None:
        """占位符必须在发射阶段就被替换掉（README.md 只做取值示例，故豁免）。"""
        self.compile()
        offenders: list[str] = []
        for path in sorted(self.out.rglob("*")):
            if not path.is_file() or path.name == "manifest.json" or path.suffix == ".md":
                continue
            if PLACEHOLDER.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(self.out)))
        self.assertEqual(offenders, [], "产物里残留未替换的生成器占位符")

    def test_second_compile_is_byte_identical(self) -> None:
        """固定 generated_at 后，第二次编译必须报告全部 unchanged（逐字节可复现）。"""
        self.compile()
        second = self.compile(diff=True)
        self.assertTrue(second.diff, "第二次编译应能读到上次的 manifest 并给出差异")
        statuses = sorted({row["status"] for row in second.diff})
        self.assertEqual(statuses, ["unchanged"], "固定时间戳后产物仍不稳定")

    def test_module_selection_limits_output(self) -> None:
        result = self.compile(module_selection="bmm")
        self.assertEqual(result.module_keys, ["bmm"])
        self.assertTrue((self.out / "json_schema/extraction_result.bmm.schema.json").is_file())
        self.assertFalse((self.out / "json_schema/extraction_result.ea.schema.json").exists())
        self.assertFalse((self.out / "prompts/ea_extraction.md").exists())
        self.assertEqual(self.read_json("manifest.json")["module_keys"], ["bmm"])

    def test_prune_removes_stale_artifacts(self) -> None:
        """先全量编译，再只编译 bmm：ea 的模块级产物应被清理。"""
        self.compile()
        stale = self.out / "prompts/ea_extraction.md"
        self.assertTrue(stale.is_file())
        result = self.compile(module_selection="bmm")
        self.assertFalse(stale.exists())
        self.assertTrue(result.removed)


class ArtifactContentTests(CompileFixture):
    """产物内容：标签表 / WeKnora / SHACL / Neo4j / 提示词。"""

    def test_label_map_links_chinese_labels(self) -> None:
        self.compile()
        label_map = self.read_json("mapping/label_map.json")
        self.assertGreaterEqual(label_map["counts"]["terms"], 100)
        term = label_map["terms"]["bmm:Assessment"]
        self.assertEqual(term["type"], "class")
        self.assertEqual(term["module"], "bmm")
        self.assertEqual(term["labels"]["zh"], "评估")
        self.assertEqual(label_map["alias_index"]["评估"], "bmm:Assessment")
        self.assertIn("conflicts", label_map)

    def test_weknora_config_uses_exactly_the_upstream_keys(self) -> None:
        """上游 `ExtractConfig` 只认这 6 个键；多写的键会被 `json.Unmarshal` **静默丢弃**。"""
        self.compile()
        config = self.read_json("weknora/extract_config.bmm.json")
        self.assertEqual(
            list(config),
            ["enabled", "text", "tags", "nodes", "relations", "custom_instructions"],
        )
        self.assertIs(config["enabled"], True)
        self.assertIn("bmm:DesiredResult", config["text"])
        self.assertTrue(config["custom_instructions"], "硬约束要进系统提示词，不能留空")
        self.assertIn("evidence", config["custom_instructions"])

    def test_weknora_nodes_carry_attributes_not_description(self) -> None:
        """`GraphNode` = {name, chunks, attributes}：定义必须放 `attributes`（`description` 不存在）。"""
        self.compile()
        config = self.read_json("weknora/extract_config.bmm.json")
        names = [node["name"] for node in config["nodes"]]
        self.assertIn("bmm:Assessment", names)
        self.assertTrue(all(name.startswith("bmm:") for name in names), "节点名必须带模块前缀")
        for node in config["nodes"]:
            with self.subTest(node=node["name"]):
                self.assertEqual(set(node), {"name", "attributes"}, "多写的键会被上游丢弃")
                self.assertTrue(node["attributes"], "节点没有任何属性，few-shot 示例就没有信息量")
        # 枚举取值也要作为可检索节点出现（受控词表不落库等于抽不出来）
        self.assertTrue(
            any("枚举取值" in attr for node in config["nodes"] for attr in node["attributes"]),
            "枚举取值节点缺失或没被标注",
        )

    def test_weknora_relations_carry_domain_range_and_type(self) -> None:
        """`GraphRelation` = {node1, node2, type}；`type` 必须落在 `tags`（关系类型白名单）里。"""
        self.compile()
        config = self.read_json("weknora/extract_config.bmm.json")
        self.assertGreaterEqual(len(config["tags"]), 30)
        self.assertNotIn("bmm", config["tags"], "tags 是关系类型白名单，不是知识库标签")
        self.assertNotIn("BMM 业务动机模型", config["tags"])
        self.assertTrue(
            all(rel["type"] in config["tags"] for rel in config["relations"]),
            "relations[].type 与 tags 不同集合：上游 RemoveUnknownRelation 会把它们全过滤掉",
        )
        self.assertEqual(
            {rel["type"] for rel in config["relations"]},
            set(config["tags"]),
            "每个对象属性都应有一条示例（缺 domain/range 的会被 E6 拦下，故此处应相等）",
        )
        for relation in config["relations"]:
            with self.subTest(type=relation["type"]):
                self.assertEqual(set(relation), {"node1", "node2", "type"})
                for endpoint in ("node1", "node2"):
                    self.assertIn(":", relation[endpoint], "端点必须是本体里的具名类")
                    self.assertNotIn("未声明", relation[endpoint], "缺 domain/range 时不能给示例")

    def test_weknora_has_no_legacy_flat_variant(self) -> None:
        """回归哨兵：`.flat.json`（`nodes`/`relations` 为字符串数组）与上游不兼容，已下线。

        上游 `Nodes []*GraphNode` 收到 `["bmm:Resource"]` 会 unmarshal 报错，整份配置作废。
        """
        self.compile()
        leftovers = sorted(path.name for path in (self.out / "weknora").iterdir() if "flat" in path.name)
        self.assertEqual(leftovers, [], "不兼容的 flat 形态又回来了")

    def test_weknora_provenance_points_back_to_ttl(self) -> None:
        self.compile()
        provenance = self.read_json("weknora/provenance.json")
        self.assertEqual(provenance["generated_at"], PINNED)
        self.assertEqual(provenance["artifact_schema_version"], ARTIFACT_SCHEMA_VERSION)
        self.assertEqual(
            list(provenance["config_keys"]),
            ["enabled", "text", "tags", "nodes", "relations", "custom_instructions"],
        )
        self.assertTrue(provenance["modules"])
        # 字段名的依据必须留痕：否则下次没人敢确认「这个名字对不对」
        self.assertTrue(provenance["upstream"]["evidence"])
        self.assertEqual(set(provenance["field_contract"]), set(provenance["config_keys"]))

    def test_shacl_shapes_cover_generated_and_authored(self) -> None:
        self.compile()
        index = self.read_json("shacl/index.json")
        self.assertTrue(index["generated"])
        self.assertTrue(index["authored"])
        self.assertTrue((self.out / "shacl/authored/ownership.shapes.ttl").is_file())
        shapes = self.read("shacl/generated.shapes.ttl")
        self.assertIn("sh:NodeShape", shapes)
        self.assertIn("targetClass bmm:Assessment", shapes)
        self.assertIn("@prefix bmm: <%s>" % BMM_NS, shapes)

    def test_json_schema_contracts(self) -> None:
        self.compile()
        index = self.read_json("json_schema/index.json")
        self.assertEqual(len(index["module_contracts"]), len(MODULE_KEYS))
        schema = self.read_json("json_schema/extraction_result.bmm.schema.json")
        self.assertIn("properties", schema)
        combined = self.read_json("json_schema/extraction_result.schema.json")
        self.assertIn("bmm", json.dumps(combined, ensure_ascii=False))

    def test_neo4j_cypher_is_rendered(self) -> None:
        self.compile()
        constraints = self.read("neo4j/00_constraints.cypher")
        self.assertIn("CREATE CONSTRAINT", constraints)
        ontology = self.read("neo4j/10_ontology.cypher")
        self.assertIn(BMM_NS, ontology)
        queries = self.read("neo4j/20_cross_layer_queries.cypher")
        self.assertIn(BMM_NS, queries)
        self.assertIn(NS["ea"], queries)

    def test_prompts_are_self_contained(self) -> None:
        self.compile()
        prompt = self.read("prompts/bmm_extraction.md")
        self.assertIn("bmm:DesiredResult", prompt)
        self.assertIn("预期成果", prompt)


def run_cli(*arguments: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    """以子进程跑 compile.py（模拟 CI 调用），返回带 rc/stdout/stderr 的结果。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop(GENERATED_AT_ENV, None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(CLI), *arguments],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


class CliTests(unittest.TestCase):
    """CLI 契约：退出码 + `--json` 结构（CI 直接依赖）。"""

    def test_list_json(self) -> None:
        result = run_cli("list", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual([row["key"] for row in payload["modules"]], MODULE_KEYS)
        self.assertTrue(all(row["files_present"] for row in payload["modules"]))

    def test_list_stats_reports_counts(self) -> None:
        result = run_cli("list", "--stats", "--json")
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {row["key"]: row for row in json.loads(result.stdout)["modules"]}
        self.assertGreaterEqual(rows["bmm"]["stats"]["classes"], 10)
        self.assertTrue(rows["bmm"]["stats"]["referenced_modules"])

    def test_validate_exit_codes(self) -> None:
        ok = run_cli("validate", "--summary")
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        strict = run_cli("validate", "--summary", "--fail-on-warn")
        self.assertEqual(strict.returncode, 1, "有 warn 时 --fail-on-warn 必须退出 1")
        self.assertIn("warn", strict.stdout.lower())

    def test_unknown_module_is_input_error(self) -> None:
        result = run_cli("validate", "--module", "nosuch")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("未知模块", result.stderr)

    def test_unknown_flag_is_usage_error(self) -> None:
        result = run_cli("compile", "--nope")
        self.assertEqual(result.returncode, 2)

    def test_compile_json_then_diff_is_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bodhi_cli_") as tmp:
            first = run_cli("compile", "--out", tmp, "--generated-at", PINNED, "--json")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            payload = json.loads(first.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["wrote_artifacts"])
            self.assertEqual(payload["module_keys"], MODULE_KEYS)
            self.assertEqual(payload["generated_at"], PINNED)
            self.assertTrue(payload["artifacts"])
            for entry in payload["artifacts"]:
                with self.subTest(path=entry["path"]):
                    self.assertTrue(_disk_path(entry["path"]).is_file())

            second = run_cli("compile", "--out", tmp, "--generated-at", PINNED, "--diff")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(
                re.findall(r"^  (added|changed|removed) ", second.stdout, re.MULTILINE),
                [],
                "固定 generated_at 后产物仍不稳定：\n%s" % second.stdout,
            )
            self.assertIn("unchanged %d" % len(payload["artifacts"]), second.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
