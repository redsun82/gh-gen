#!/usr/bin/env python3
"""Refresh GitHub's context catalog, or reproduce its Python types offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import keyword
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen

import black

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src/ghgen"
SNAPSHOT = PACKAGE / "_context_data.json"
GENERATED = PACKAGE / "_context_types.py"
SOURCES = {
    "openapi": (
        "github/rest-api-description",
        "descriptions/api.github.com/api.github.com.json",
    ),
    "contexts": (
        "github/docs",
        "content/actions/reference/workflows-and-actions/contexts.md",
    ),
    "events": (
        "github/docs",
        "content/actions/reference/workflows-and-actions/events-that-trigger-workflows.md",
    ),
    "pull_request_activities": (
        "github/docs",
        "data/reusables/actions/workflow-triggers-pull-request-activity-types.md",
    ),
    "workflow": (
        "actions/languageservices",
        "workflow-parser/src/workflow-v1.0.json",
    ),
}


def download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "gh-gen-context-updater"})
    with urlopen(request, timeout=120) as response:
        return response.read()


class Compiler:
    def __init__(self, spec: dict):
        self.spec = spec
        self.raw: list[dict] = [{"open": True}]
        self.refs: dict[str, int] = {}
        self.nodes: list[dict] = []
        self.combined: dict[tuple[int, ...], int] = {}

    def add(self, node: dict) -> int:
        self.raw.append(node)
        return len(self.raw) - 1

    def schema(self, schema: dict, name: str) -> int:
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in self.refs:
                return self.refs[ref]
            if not ref.startswith("#/components/schemas/"):
                raise ValueError(f"unsupported schema reference: {ref}")
            target = self.spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
            index = self.add({})
            self.refs[ref] = index
            resolved = self.schema(target, ref.rsplit("/", 1)[1])
            self.raw[index] = {"variants": [resolved], "name": name}
            return index
        for unsupported in (
            "prefixItems",
            "patternProperties",
            "unevaluatedProperties",
        ):
            if unsupported in schema:
                raise ValueError(f"{name}: unsupported {unsupported}")
        if not schema:
            return 0
        node: dict = {"name": name}
        if description := schema.get("description"):
            node["description"] = description
        if "type" in schema:
            node["types"] = (
                schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            )
        if schema.get("nullable"):
            node.setdefault("types", []).append("null")
        if "enum" in schema:
            node["values"] = schema["enum"]
        if "properties" in schema:
            node["fields"] = {
                key: self.schema(value, f"{name}_{key}")
                for key, value in schema["properties"].items()
            }
        if "items" in schema:
            node["item"] = self.schema(schema["items"], f"{name}_item")
        additional = schema.get("additionalProperties", False)
        if isinstance(additional, dict) and additional:
            node["map"] = self.schema(additional, f"{name}_value")
        elif additional is True or additional == {}:
            node["open"] = True
        elif schema.get("type") == "object" and not schema.get("properties"):
            node["open"] = True
        variants = [
            self.schema(part, name)
            for kind in ("allOf", "anyOf", "oneOf")
            for part in schema.get(kind, [])
        ]
        if variants:
            node["variants"] = variants
        return self.add(node)

    def combine(self, ids: list[int]) -> int:
        expanded: set[int] = set()

        def expand(index: int):
            if index in expanded:
                return
            expanded.add(index)
            for variant in self.raw[index].get("variants", []):
                expand(variant)

        for index in ids:
            expand(index)
        key = tuple(sorted(expanded))
        if key in self.combined:
            return self.combined[key]
        result = len(self.nodes)
        node: dict = {}
        self.nodes.append(node)
        self.combined[key] = result
        fields: dict[str, list[int]] = {}
        items, maps, types, descriptions, names = [], [], set(), [], []
        for index in key:
            raw = self.raw[index]
            for field, child in raw.get("fields", {}).items():
                fields.setdefault(field, []).append(child)
            if "item" in raw:
                items.append(raw["item"])
            if "map" in raw:
                maps.append(raw["map"])
            if raw.get("open"):
                node["open"] = True
            types.update(raw.get("types", []))
            if raw.get("description"):
                descriptions.append(raw["description"])
            if raw.get("name"):
                names.append(raw["name"])
        if fields:
            node["fields"] = {
                field: self.combine(children)
                for field, children in sorted(fields.items())
            }
        if items:
            node["item"] = self.combine(items)
        if maps:
            node["map"] = self.combine(maps)
        if types:
            node["types"] = sorted(types)
        if descriptions:
            node["description"] = min(descriptions, key=len)
        node["name"] = min(names, key=len) if names else "Dynamic"
        return result

    def properties(self, ids: list[int]) -> dict[str, int]:
        pending, seen, properties = list(ids), set(), {}
        while pending:
            index = pending.pop()
            if index in seen:
                continue
            seen.add(index)
            node = self.raw[index]
            pending.extend(node.get("variants", []))
            for key, child in node.get("fields", {}).items():
                properties.setdefault(key, []).append(child)
        return {
            key: self.add({"variants": children})
            for key, children in properties.items()
        }


def fixed_contexts(compiler: Compiler, documentation: str) -> int:
    tree: dict = {}
    rows = re.findall(
        r"^\| `([a-zA-Z_][\w]*(?:\.[\w<> -]+)*)` \| `(\w+)` \| (.*)",
        documentation,
        re.M,
    )
    for path, kind, description in rows:
        target = tree
        for segment in path.split("."):
            target = target.setdefault("fields", {}).setdefault(segment, {})
        target["types"] = [kind]
        target["description"] = description.rstrip("| ")
    expected = {
        "github",
        "env",
        "vars",
        "job",
        "jobs",
        "steps",
        "runner",
        "secrets",
        "strategy",
        "matrix",
        "needs",
        "inputs",
    }
    if set(tree.get("fields", {})) != expected:
        raise ValueError(
            "context documentation layout changed: unexpected root contexts"
        )
    for root in ("env", "vars", "secrets", "matrix", "inputs"):
        tree["fields"][root]["open"] = True
    required = ("github.event", "steps.<step_id>.conclusion", "job.services")
    for path in required:
        target = tree
        for part in path.split("."):
            if part not in target.get("fields", {}):
                raise ValueError(f"context documentation lost {path}")
            target = target["fields"][part]

    def convert(node: dict, name: str) -> int:
        fields = {}
        result = {k: v for k, v in node.items() if k != "fields"}
        result["name"] = name
        for field, child in node.get("fields", {}).items():
            index = convert(child, f"{name}_{field.strip('<>')}")
            if field.startswith("<"):
                if "map" in result:
                    raise ValueError(f"{name}: multiple dynamic field definitions")
                result["map"] = index
            else:
                fields[field] = index
        if fields:
            result["fields"] = fields
        if result.get("types") == ["object"] and not any(
            key in result for key in ("fields", "map")
        ):
            result["open"] = True
        return compiler.add(result)

    return convert(tree, "Contexts")


def availability(documentation: str) -> list[dict]:
    _, found, documentation = documentation.partition("### Context availability\n")
    if not found:
        raise ValueError("context availability section was not found")
    documentation = documentation.split("\n### ", 1)[0]
    rules = []
    for path, contexts, functions in re.findall(
        r"^\| `([^`]+)` \| `([^`]+)` \| ([^\n]+)", documentation, re.M
    ):
        if path not in ("run-name", "concurrency", "env") and not path.startswith(
            ("jobs.", "on.workflow_call.")
        ):
            continue
        rules.append(
            {
                "path": path,
                "contexts": contexts.split(", "),
                "functions": re.findall(
                    r"\b(?:always|cancelled|success|failure|hashFiles)\b", functions
                ),
            }
        )
    if not any(r["path"] == "jobs.<job_id>.if" for r in rules):
        raise ValueError("context availability table was not found")
    return rules


def trigger_activities(documentation: str) -> dict[str, list[str]]:
    result = {}
    sections = re.split(r"^## `(\w+)`", documentation, flags=re.M)
    for event, body in zip(sections[1::2], sections[2::2]):
        for line in body.splitlines():
            if line.startswith("| [`"):
                column = line.split("|")[2]
                if "{% data" in column:
                    raise ValueError(f"unresolved activity table include for {event}")
                result[event] = re.findall(r"`(\w+)`", column)
                break
    if not {"opened", "synchronize"} <= set(result.get("pull_request", [])):
        raise ValueError("workflow activity documentation layout changed")
    return result


def build_snapshot(contents: dict[str, bytes], provenance: dict) -> dict:
    spec = json.loads(contents["openapi"])
    docs = contents["contexts"].decode()
    event_docs = (
        contents["events"]
        .decode()
        .replace(
            "{% data reusables.actions.workflow-triggers-pull-request-activity-types %}",
            contents["pull_request_activities"].decode().strip(),
        )
    )
    compiler = Compiler(spec)
    fixed = fixed_contexts(compiler, docs)
    trigger_names = set(re.findall(r"^## `(\w+)`", contents["events"].decode(), re.M))
    if not {"push", "pull_request", "workflow_call", "schedule"} <= trigger_names:
        raise ValueError("workflow trigger documentation layout changed")
    event_roots: dict[str, dict[str, int]] = {}
    webhooks = spec.get("webhooks", spec.get("x-webhooks"))
    if not webhooks:
        raise ValueError("OpenAPI description contains no webhooks")
    for name, webhook in sorted(webhooks.items()):
        operation = webhook["post"]
        event = operation["x-github"]["subcategory"]
        if event not in trigger_names:
            continue
        prefix = event.replace("_", "-")
        action = (
            name.removeprefix(prefix).removeprefix("-").replace("-", "_") or "default"
        )
        if event == "repository_dispatch":
            action = "default"
        payload = operation["requestBody"]["content"]["application/json"]["schema"]
        event_roots.setdefault(event, {})[action] = compiler.schema(payload, name)
    # Schedule has no webhook schema; reuse the standard workflow event objects.
    common = compiler.properties(list(event_roots["workflow_dispatch"].values()))
    schedule = compiler.add(
        {
            "name": "ScheduleEvent",
            "fields": {
                "schedule": compiler.add({"types": ["string"]}),
                **{
                    key: common[key]
                    for key in ("repository", "sender")
                    if key in common
                },
            },
        }
    )
    event_roots["schedule"] = {"default": schedule}
    dispatch = compiler.add({"name": "DispatchInputs", "open": True})
    if "workflow_dispatch" not in event_roots:
        raise ValueError("missing workflow_dispatch webhook schema")
    event_roots["pull_request_target"] = event_roots["pull_request"]
    all_events = [
        node for variants in event_roots.values() for node in variants.values()
    ]
    event = compiler.add(
        {
            "name": "Event",
            "variants": all_events,
            "fields": {"inputs": dispatch},
        }
    )
    github = compiler.raw[fixed]["fields"]["github"]
    compiler.raw[github]["fields"]["event"] = event
    # GitHub documents ports as an object without a row for each numeric key.
    job = compiler.raw[fixed]["fields"]["job"]
    services = compiler.raw[job]["fields"]["services"]
    service = compiler.raw[services]["map"]
    ports = compiler.raw[service]["fields"]["ports"]
    compiler.raw[ports]["map"] = compiler.add({"types": ["string"]})
    root = compiler.combine([fixed])
    events = {
        name: {
            activity: compiler.combine([node]) for activity, node in variants.items()
        }
        for name, variants in sorted(event_roots.items())
    }
    workflow_schema = json.loads(contents["workflow"])
    if "definitions" not in workflow_schema:
        raise ValueError("Actions workflow schema layout changed")
    rules = availability(docs)
    cross_checks = {
        "run-name": "run-name",
        "concurrency": "workflow-concurrency",
        "env": "workflow-env",
        "jobs.<job_id>.if": "job-if",
        "jobs.<job_id>.runs-on": "runs-on",
        "jobs.<job_id>.strategy": "strategy",
        "jobs.<job_id>.env": "job-env",
        "jobs.<job_id>.steps.if": "step-if",
        "jobs.<job_id>.steps.env": "step-env",
        "jobs.<job_id>.steps.with": "step-with",
        "on.workflow_call.inputs.<inputs_id>.default": "workflow-call-input-default",
        "on.workflow_call.outputs.<output_id>.value": "workflow-output-context",
    }
    for rule in rules:
        if definition := cross_checks.get(rule["path"]):
            expected = workflow_schema["definitions"][definition]["context"]
            names = {name.split("(")[0] for name in expected}
            actual = set(rule["contexts"]) | set(rule["functions"])
            if names != actual:
                raise ValueError(
                    f"GitHub documentation and Actions schema disagree for {rule['path']}: "
                    f"{sorted(names ^ actual)}"
                )
    snapshot = compact(
        {
            "format": 1,
            "sources": provenance,
            "root": root,
            "dynamic": compiler.combine([0]),
            "nodes": compiler.nodes,
            "events": events,
            "triggers": sorted(trigger_names),
            "activities": trigger_activities(event_docs),
            "defaults": {
                "pull_request": ["opened", "reopened", "synchronize"],
                "pull_request_target": ["opened", "reopened", "synchronize"],
                "merge_group": ["checks_requested"],
            },
            "availability": rules,
            "workflow_contexts": {
                key: definition["context"]
                for key, definition in workflow_schema["definitions"].items()
                if "context" in definition
            },
        }
    )
    nodes = snapshot["nodes"]
    fields = nodes[snapshot["root"]]["fields"]
    for key, index in fields.items():
        nodes[index]["name"] = key.title() + "Context"
    nodes[nodes[fields["github"]]["fields"]["event"]]["name"] = "Event"
    return snapshot


def compact(snapshot: dict) -> dict:
    """Share structurally identical shapes, including recursive components."""
    nodes = snapshot["nodes"]
    labels = [0] * len(nodes)
    while True:
        groups: dict[tuple, int] = {}
        updated = []
        for node in nodes:
            signature = (
                tuple(
                    sorted(
                        (key, labels[child])
                        for key, child in node.get("fields", {}).items()
                    )
                ),
                labels[node["item"]] if "item" in node else None,
                labels[node["map"]] if "map" in node else None,
                node.get("open", False),
                tuple(node.get("types", [])),
            )
            updated.append(groups.setdefault(signature, len(groups)))
        if updated == labels:
            break
        labels = updated
    result: dict[int, dict] = {}
    for index, node in enumerate(nodes):
        label = labels[index]
        if label in result:
            continue
        copied = dict(node)
        if "fields" in copied:
            copied["fields"] = {
                key: labels[child] for key, child in copied["fields"].items()
            }
        for kind in ("item", "map"):
            if kind in copied:
                copied[kind] = labels[copied[kind]]
        result[label] = copied
    snapshot["nodes"] = list(result.values())
    snapshot["root"] = labels[snapshot["root"]]
    snapshot["dynamic"] = labels[snapshot["dynamic"]]
    snapshot["events"] = {
        event: {action: labels[node] for action, node in variants.items()}
        for event, variants in snapshot["events"].items()
    }
    return snapshot


def identifier(name: str) -> str:
    result = re.sub(r"\W", "_", name)
    if not result or result[0].isdigit():
        result = "_" + result
    return result + "_" if keyword.iskeyword(result) else result


def render(snapshot: dict) -> str:
    nodes = snapshot["nodes"]
    root_fields = nodes[snapshot["root"]]["fields"]
    step_id = nodes[root_fields["steps"]]["map"]

    def structured(node: dict) -> bool:
        return any(key in node for key in ("fields", "item", "map", "open"))

    names = {}
    used = set()
    for index, node in enumerate(nodes):
        if not structured(node):
            continue
        base = "".join(part.title() for part in re.split(r"\W+|_", node["name"]))[:75]
        name, suffix = base, 2
        while name in used:
            name, suffix = base + str(suffix), suffix + 1
        used.add(name)
        names[index] = name

    def annotation(index: int) -> str:
        return names.get(index, "RefExpr")

    out = [
        "# Generated by scripts/update_contexts.py; do not edit.",
        "from __future__ import annotations",
        "",
        "from typing import Literal, cast, overload",
        "from .expr import Expr, RefExpr",
        "from .schema import SchemaRef, OpenRef",
        "",
    ]
    for index, name in names.items():
        node = nodes[index]
        base = "OpenRef" if node.get("open") and "map" not in node else "SchemaRef"
        out += [f"class {name}({base}):", f"    _schema_id = {index}"]
        for key, child in node.get("fields", {}).items():
            field = identifier(key)
            if field.startswith("__"):
                raise ValueError(f"unsupported Python field collision: {key}")
            out.append(f"    {field}: {annotation(child)}")
        if index == step_id:
            out.append("    result: RefExpr")
        item = node.get("item", node.get("map"))
        if "map" in node:
            out += [
                "",
                f"    def __getattr__(self, key: str) -> {annotation(node['map'])}:",
                f"        return cast({annotation(node['map'])}, self._attribute(key))",
            ]
        if item is not None:
            out += [
                "",
                "    @property",
                f"    def _(self) -> {annotation(item)}:",
                f"        return cast({annotation(item)}, self._wildcard())",
            ]
        fields = node.get("fields", {})
        if fields:
            out.append("")
            for field, child in fields.items():
                out += [
                    "    @overload",
                    f"    def __getitem__(self, key: Literal[{field!r}]) -> {annotation(child)}: ...",
                ]
            out += [
                "    @overload",
                "    def __getitem__(self, key: str | int | Expr) -> RefExpr: ...",
                "    def __getitem__(self, key: str | int | Expr) -> RefExpr:",
                "        return super().__getitem__(key)",
            ]
        elif item is not None:
            out += [
                "",
                f"    def __getitem__(self, key: str | int | Expr) -> {annotation(item)}:",
                f"        return cast({annotation(item)}, super().__getitem__(key))",
            ]
        out += ["", ""]
    out += ["class Contexts:"]
    for field, child in root_fields.items():
        out.append(f"    {field}: {annotation(child)}")
    aliases = {
        "JobContext": root_fields["job"],
        "StrategyContext": root_fields["strategy"],
        "StepContext": nodes[root_fields["steps"]]["map"],
        "JobResult": nodes[root_fields["needs"]]["map"],
    }
    out.append("")
    for alias, index in aliases.items():
        out.append(f"{alias} = {annotation(index)}")
    out += ["", "TYPES: dict[int, type[RefExpr]] = {"]
    for index in range(len(nodes)):
        out.append(f"    {index}: {annotation(index)},")
    out += ["}", ""]
    return black.format_str(
        "\n".join(out), mode=black.Mode(target_versions={black.TargetVersion.PY314})
    )


def refresh() -> dict:
    contents, provenance, revisions = {}, {}, {}
    for name, (repo, path) in SOURCES.items():
        if repo not in revisions:
            commit = json.loads(
                download(f"https://api.github.com/repos/{repo}/commits/main")
            )
            revisions[repo] = commit["sha"]
        revision = revisions[repo]
        url = f"https://raw.githubusercontent.com/{repo}/{revision}/{path}"
        contents[name] = download(url)
        provenance[name] = {
            "url": url,
            "sha256": hashlib.sha256(contents[name]).hexdigest(),
        }
    return build_snapshot(contents, provenance)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--refresh", action="store_true", help="fetch current upstream definitions"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail without writing if regeneration differs",
    )
    args = parser.parse_args()
    old = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else None
    snapshot = refresh() if args.refresh else old
    if snapshot is None:
        raise ValueError("no pinned snapshot; run with --refresh")
    if args.refresh and old is not None:
        if {k: v for k, v in old.items() if k != "sources"} == {
            k: v for k, v in snapshot.items() if k != "sources"
        }:
            snapshot = old
    outputs = {
        SNAPSHOT: json.dumps(snapshot, sort_keys=True, indent=2) + "\n",
        GENERATED: render(snapshot),
    }
    changed = [
        path
        for path, content in outputs.items()
        if not path.exists() or path.read_text() != content
    ]
    for path in changed:
        print(
            f"{'Out of date' if args.check else 'Updating'}: {path.relative_to(ROOT)}"
        )
    if args.check:
        return int(bool(changed))
    for path in changed:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(outputs[path])
        temporary.replace(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
