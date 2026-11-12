"""版本化对象注册表的加载、校验、依赖 DAG 与配置快照哈希。"""
from __future__ import annotations

import json
from pathlib import Path

from .ids import stable_hash

EVAL_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_TOP_LEVEL = {
    "object_id", "object_type", "version", "status", "owner",
    "effective_scope", "depends_on", "content", "rollback_baseline", "changelog",
}
REQUIRED_SCOPE = {"profiles", "chains", "suites", "environments"}


class RegistryError(RuntimeError):
    pass


class Registry:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else EVAL_ROOT
        self.config_dir = self.root / "config"
        self.index = json.loads((self.config_dir / "registry.json").read_text(encoding="utf-8"))
        self.required_changelog_fields = self.index["changelog_required_fields"]
        self.objects: dict[str, dict] = {}
        self.errors: list[str] = []

        seen = set()
        for entry in self.index["objects"]:
            oid = entry["object_id"]
            if oid in seen:
                self.errors.append(f"重复对象标识 {oid}")
            seen.add(oid)
            path = self.config_dir / entry["file"]
            if not path.exists():
                self.errors.append(f"缺少对象文件 {path}")
                continue
            obj = json.loads(path.read_text(encoding="utf-8"))
            self._validate_object(oid, entry, obj)
            self.objects[oid] = obj
        if not self.errors:
            self._validate_dependencies()
            self._validate_dag()
        if self.errors:
            raise RegistryError("注册表校验失败:\n - " + "\n - ".join(self.errors))
        self._config_hash = stable_hash({
            oid: {
                "version": o["version"], "status": o["status"],
                "effective_scope": o["effective_scope"], "depends_on": o["depends_on"],
                "content": o["content"],
            }
            for oid, o in self.objects.items()
        })

    def _validate_object(self, oid, entry, obj):
        missing = REQUIRED_TOP_LEVEL - set(obj)
        if missing:
            self.errors.append(f"{oid} 缺少顶层字段 {sorted(missing)}")
            return
        for key in ("object_id", "object_type", "version"):
            if obj[key] != entry[key]:
                self.errors.append(f"{oid} 的 {key} 与 registry 索引不一致: {obj[key]} != {entry[key]}")
        missing_scope = REQUIRED_SCOPE - set(obj["effective_scope"])
        if missing_scope:
            self.errors.append(f"{oid} effective_scope 缺少 {sorted(missing_scope)}")
        for i, change in enumerate(obj["changelog"]):
            absent = set(self.required_changelog_fields) - set(change)
            if absent:
                self.errors.append(f"{oid} changelog[{i}] 缺少变更记录必填项 {sorted(absent)}")
            if change.get("rollback_baseline") is None:
                self.errors.append(f"{oid} changelog[{i}] 未指定回滚基线")

    def _validate_dependencies(self):
        for oid, obj in self.objects.items():
            for dep in obj["depends_on"]:
                dep_id = dep.get("object_id")
                dep_ver = dep.get("version")
                if dep_id not in self.objects:
                    self.errors.append(f"{oid} 依赖不存在的对象 {dep_id}")
                elif self.objects[dep_id]["version"] != dep_ver:
                    self.errors.append(
                        f"{oid} 依赖版本不匹配: {dep_id}@{dep_ver} != {self.objects[dep_id]['version']}"
                    )

    def _validate_dag(self):
        graph = {oid: [d["object_id"] for d in o["depends_on"]] for oid, o in self.objects.items()}
        visiting, done = set(), set()

        def visit(node, path):
            if node in visiting:
                self.errors.append("对象依赖存在环: " + " -> ".join(path + [node]))
                return
            if node in done:
                return
            visiting.add(node)
            for nxt in graph.get(node, []):
                if nxt in graph:
                    visit(nxt, path + [node])
            visiting.discard(node)
            done.add(node)

        for node in graph:
            visit(node, [])

    @property
    def config_hash(self) -> str:
        return self._config_hash

    def versions(self) -> dict[str, str]:
        return {oid: obj["version"] for oid, obj in self.objects.items()}

    def object_refs(self) -> list[str]:
        return [f"{oid}@{obj['version']}" for oid, obj in sorted(self.objects.items())]

    def get(self, oid: str) -> dict:
        return self.objects[oid]

    def content(self, oid: str) -> dict:
        return self.objects[oid]["content"]

    def chain_object_refs(self, step: str) -> list[str]:
        """逐链路的对象版本关联：找出 effective_scope.chains 命中该步骤的所有对象。"""
        refs = []
        for oid, obj in sorted(self.objects.items()):
            chains = obj["effective_scope"].get("chains", [])
            if "*" in chains or step in chains:
                refs.append(f"{oid}@{obj['version']}")
        return refs

    def apply_override(self, oid: str, dotted_path: str, raw_value: str) -> dict:
        """实验变体：只在内存中覆盖，不修改版本化对象文件。必须随 run 记录，采纳后再升版本。"""
        import json as _json

        if oid not in self.objects:
            raise RegistryError(f"未知对象 {oid}")
        try:
            value = _json.loads(raw_value)
        except _json.JSONDecodeError:
            value = raw_value
        node = self.objects[oid]["content"]
        parts = dotted_path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        last = parts[-1]
        before = node[int(last)] if isinstance(node, list) else node.get(last)
        if isinstance(node, list):
            node[int(last)] = value
        else:
            node[last] = value
        return {"object_id": oid, "path": dotted_path, "before": before, "after": value}

    def recompute_hash(self) -> str:
        self._config_hash = stable_hash({
            oid: {
                "version": o["version"], "status": o["status"],
                "effective_scope": o["effective_scope"], "depends_on": o["depends_on"],
                "content": o["content"],
            }
            for oid, o in self.objects.items()
        })
        return self._config_hash

    def snapshot(self) -> dict:
        return {
            "registry_id": self.index["registry_id"],
            "registry_version": self.index["version"],
            "config_hash": self.config_hash,
            "objects": self.object_refs(),
        }
