import dataclasses
import json
from pathlib import Path
import subprocess
import sys

import pytest

from ghgen.contexts import Contexts, github
from ghgen.expr import instantiate, reftree
from ghgen.schema import catalog
from ghgen.syntax import (
    GenerationError,
    action,
    container,
    env,
    fromJson,
    hashFiles,
    if_,
    job,
    name,
    on,
    run,
    run_name,
    secrets,
    workflow,
)
from ghgen.workflow import On
from scripts import update_contexts as generator
from scripts.update_contexts import Compiler, availability, compact, fixed_contexts

ROOT = Path(__file__).resolve().parents[1]


def build(body):
    return workflow(body).worfklow


def test_nested_paths_and_rendering():
    assert (
        instantiate(github.event.pull_request.head.sha)
        == "${{ github.event.pull_request.head.sha }}"
    )
    indexed = github.event["pull_request"]["labels"][0].name
    assert (
        instantiate(indexed) == "${{ github.event['pull_request']['labels'][0].name }}"
    )
    assert reftree(indexed)["github"]["event"]["pull_request"]["labels"]["*"] == {
        "name": {}
    }
    filtered = github.event.pull_request.labels._.name
    assert instantiate(filtered) == "${{ github.event.pull_request.labels.*.name }}"
    assert reftree(filtered)["github"]["event"]["pull_request"]["labels"]["*"] == {
        "name": {}
    }
    assert instantiate(github.event.pull_request._links.self.href).endswith(
        "._links.self.href }}"
    )


@pytest.mark.parametrize(
    "access",
    [
        lambda: github.event.pull_request.hed,
        lambda: github.event.pull_request["hed"],
        lambda: github.event.pull_request.labels[0].nmae,
        lambda: github.event.pull_request.labels._.nmae,
        lambda: github.event.pull_request.head.sha.typo,
    ],
)
def test_shape_typos(access):
    with pytest.raises(ValueError, match="not available"):
        access()


def test_deep_open_payload_is_lazy():
    value = github.event.client_payload
    for _ in range(30):
        value = value.parent
    assert instantiate(value.owner.login).endswith(".parent.owner.login }}")


def test_open_payloads_and_json():
    value = github.event.client_payload.custom.deeply.nested
    assert (
        instantiate(value) == "${{ github.event.client_payload.custom.deeply.nested }}"
    )
    assert (
        instantiate(fromJson("{}").custom[0].name)
        == "${{ fromJson('{}').custom[0].name }}"
    )


def test_step_result_migration():
    with pytest.warns(DeprecationWarning, match="conclusion"):
        value = Contexts.steps.build.result
    assert instantiate(value) == "${{ steps.build.conclusion }}"
    with pytest.warns(DeprecationWarning, match="conclusion"):
        indexed = Contexts.steps["build"]["result"]
    assert instantiate(indexed) == "${{ steps['build']['conclusion'] }}"


def test_fixed_fields():
    for value in (
        github.action_status,
        github.artifacts,
        github.artifacts_list,
        Contexts.job.check_run_id,
        Contexts.job.workflow_repository,
        Contexts.job.workflow_file_path,
        Contexts.job.workflow_ref,
        Contexts.job.workflow_sha,
    ):
        assert instantiate(value).startswith("${{ ")


def test_trigger_coverage():
    assert {field.name for field in dataclasses.fields(On)} - {
        "inputs",
        "workflow_call",
    } <= catalog()["events"].keys()


def test_union_of_triggers_and_late_declarations():
    def body():
        @job
        def example():
            run(github.event.pull_request.head.sha)

        on.push().pull_request()

    build(body)


@pytest.mark.parametrize(
    "expression",
    [
        lambda: github.event.pull_request.head.sha,
        lambda: github.event["pull_request"]["head"].sha,
        lambda: github.event.pull_request.labels._.name,
        lambda: github.event.pull_request.labels[0].name,
    ],
)
def test_wrong_trigger(expression):
    def body():
        on.push()
        run(expression())

    with pytest.raises(GenerationError, match="unavailable for configured triggers"):
        build(body)


def test_event_failure_retains_source_location():
    def body():
        on.push()
        run(github.event.pull_request.head.sha)

    with pytest.raises(GenerationError) as caught:
        build(body)
    error = caught.value.errors[0]
    assert error.filename == __file__
    assert error.lineno == body.__code__.co_firstlineno + 2


def test_legacy_fstring_indexed_paths():
    def body():
        on.push()
        run(f"echo {github.event['pull_request']['labels'][0].name}")

    with pytest.raises(GenerationError, match="unavailable"):
        build(body)


def test_overwritten_event_reference_is_not_checked():
    def body():
        on.push()
        run(github.event.pull_request.head.sha).run("echo replaced")

    build(body)


def test_activity_defaults_and_explicit_types():
    def default():
        on.pull_request()
        run(github.event.label.name)

    with pytest.raises(GenerationError, match="unavailable"):
        build(default)

    def labeled():
        on.pull_request(types=["labeled"])
        run(github.event.label.name)

    build(labeled)

    def target():
        on.event("pull_request_target", types=["labeled"])
        run(github.event.label.name)

    build(target)


def test_workflow_run_activity_uses_underscores():
    def body():
        on.event(
            "workflow_run",
            workflows=["CI"],
            types=["in_progress"],
            branches_ignore=["releases/**"],
        )
        run(github.event.workflow_run.head_sha)

    generated = build(body).asdict()
    assert generated["on"]["workflow_run"] == {
        "workflows": ["CI"],
        "types": ["in_progress"],
        "branches-ignore": ["releases/**"],
    }


def test_workflow_run_does_not_accept_path_filters():
    def body():
        on.event("workflow_run", workflows=["CI"], paths=["src/**"])
        run("")

    with pytest.raises(GenerationError, match="unknown setting `paths`"):
        build(body)


def test_dispatch_inputs_are_declared():
    def correct():
        on.workflow_dispatch.input(id="choice")
        run(github.event.inputs.choice)

    build(correct)

    def wrong():
        on.workflow_dispatch()
        run(github.event.inputs.missing)

    with pytest.raises(GenerationError, match="unavailable"):
        build(wrong)


def test_dynamic_dispatch_payload():
    def body():
        on.event("repository_dispatch", types=["custom.created"])
        run(github.event.client_payload.custom.deeply.nested)

    build(body)


def test_unknown_caller_event():
    def reusable():
        on.workflow_call()
        run(github.event.pull_request.head.sha)

    build(reusable)

    def composite():
        name("Example")
        from ghgen.syntax import description

        description("Example action")
        run(github.event.pull_request.head.sha)

    action(composite).action


def test_schedule_payload():
    def body():
        on.event("schedule", cron="0 0 * * *")
        run(github.event.schedule)
        run(github.event.repository.full_name)
        run(github.event.sender.login)

    generated = build(body).asdict()
    assert generated["on"]["schedule"] == [{"cron": "0 0 * * *"}]


@pytest.mark.parametrize(
    "value", [lambda: github.action_path, lambda: github.action_status]
)
def test_composite_only_contexts(value):
    def body():
        on.push()
        run(value())

    with pytest.raises(GenerationError, match="only available in composite"):
        build(body)


@pytest.mark.parametrize(
    "value", [lambda: github.action_ref, lambda: github.action_repository]
)
def test_action_metadata_is_not_interpolated_directly_in_run(value):
    def body():
        on.push()
        run(value())

    with pytest.raises(GenerationError, match="must be passed through"):
        build(body)


def test_job_proxy_bracket_paths_remain_checked():
    with pytest.raises(ValueError, match="not available"):
        job["container"]["invalid"]


def test_head_ref_requires_pr_trigger():
    def body():
        on.push()
        run(github.head_ref)

    with pytest.raises(GenerationError, match="requires a pull_request"):
        build(body)


def test_function_availability():
    def wrong():
        on.push()
        run_name(hashFiles("*.py"))
        run("")

    with pytest.raises(GenerationError, match="hashFiles.*cannot be used"):
        build(wrong)

    def correct():
        on.push()
        run(t"echo {hashFiles('*.py')}")

    build(correct)


def test_secrets_are_not_allowed_in_conditions():
    def body():
        on.push()
        run("").if_(secrets.TOKEN)

    with pytest.raises(GenerationError, match="secrets.*cannot be used"):
        build(body)


def test_legacy_fstring_function_availability():
    def body():
        on.push()
        run_name(f"{hashFiles('*.py')}")
        run("")

    with pytest.raises(GenerationError, match="hashFiles.*cannot be used"):
        build(body)


def test_indexed_function_availability():
    def body():
        on.pull_request()
        run_name(github.event.pull_request.labels[hashFiles("*.py")].name)
        run("")

    with pytest.raises(GenerationError, match="hashFiles.*cannot be used"):
        build(body)


def test_unknown_activity_type():
    def body():
        on.pull_request(types=["opend"])
        run("")

    with pytest.raises(GenerationError, match="unknown activity"):
        build(body)


def test_webhook_only_activity_is_not_a_workflow_activity():
    def body():
        on.event("merge_group", types=["destroyed"])
        run("")

    with pytest.raises(GenerationError, match="unknown activity"):
        build(body)


def test_env_in_container_environment():
    def body():
        on.push()

        @job
        def example():
            env(FOO="bar")
            container("alpine").env(FOO=env.FOO)
            run("")

    build(body)


def test_container_builder_does_not_reuse_another_workflow():
    def first():
        on.push()
        container("alpine")
        run("")

    def second():
        on.push()
        container("debian")
        run("")

    one, two = build(first), build(second)
    assert next(iter(one.jobs.values())).container.image == "alpine"
    assert next(iter(two.jobs.values())).container.image == "debian"


def test_index_expression_references_are_validated():
    def body():
        on.push()
        if_(github.event.commits[secrets.INDEX].message)
        run("")

    with pytest.raises(GenerationError, match="secrets.*cannot be used"):
        build(body)


def test_offline_regeneration():
    subprocess.run(
        [sys.executable, "scripts/update_contexts.py", "--check"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    for source in catalog()["sources"].values():
        assert len(source["sha256"]) == 64
        assert "/main/" not in source["url"]


@pytest.fixture
def generator_outputs(monkeypatch, tmp_path):
    snapshot = tmp_path / "_context_data.json"
    generated = tmp_path / "_context_types.py"
    original = {"nodes": [], "sources": {"revision": "old"}}
    snapshot.write_text(json.dumps(original, sort_keys=True, indent=2) + "\n")
    generated.write_text("# generated\n")
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    monkeypatch.setattr(generator, "SNAPSHOT", snapshot)
    monkeypatch.setattr(generator, "GENERATED", generated)
    monkeypatch.setattr(generator, "render", lambda data: "# generated\n")
    monkeypatch.setattr(sys, "argv", ["update_contexts.py", "--refresh"])
    return snapshot, generated, original


def test_refresh_ignores_provenance_only_changes(generator_outputs, monkeypatch):
    snapshot, generated, original = generator_outputs
    before = snapshot.read_bytes(), generated.read_bytes()
    monkeypatch.setattr(
        generator, "refresh", lambda: {**original, "sources": {"revision": "new"}}
    )
    assert generator.main() == 0
    assert (snapshot.read_bytes(), generated.read_bytes()) == before


@pytest.mark.parametrize("stage", ["refresh", "render"])
def test_refresh_failure_preserves_outputs(generator_outputs, monkeypatch, stage):
    snapshot, generated, original = generator_outputs
    before = snapshot.read_bytes(), generated.read_bytes()
    monkeypatch.setattr(generator, "refresh", lambda: {**original, "nodes": [{}]})

    def fail(*args):
        raise ValueError("upstream format changed")

    monkeypatch.setattr(generator, stage, fail)
    with pytest.raises(ValueError, match="upstream format"):
        generator.main()
    assert (snapshot.read_bytes(), generated.read_bytes()) == before


def test_schema_composition_and_recursion():
    compiler = Compiler(
        {
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "parent": {"$ref": "#/components/schemas/Node"},
                            "items": {"type": "array", "items": {"type": "string"}},
                            "optional": {
                                "anyOf": [
                                    {"type": "null"},
                                    {
                                        "type": "object",
                                        "properties": {"name": {"type": "string"}},
                                    },
                                ]
                            },
                        },
                    },
                }
            }
        }
    )
    root = compiler.combine(
        [compiler.schema({"$ref": "#/components/schemas/Node"}, "Root")]
    )
    node = compiler.nodes[root]
    assert node["fields"]["parent"] == root
    optional = compiler.nodes[node["fields"]["optional"]]
    assert "name" in optional["fields"]
    assert "null" in optional["types"]
    assert "item" in compiler.nodes[node["fields"]["items"]]


def test_compaction_preserves_recursive_edges():
    data = compact(
        {
            "root": 0,
            "dynamic": 1,
            "events": {"event": {"default": 2}},
            "nodes": [
                {"name": "First", "fields": {"parent": 0}},
                {"name": "Dynamic", "open": True},
                {"name": "Second", "fields": {"parent": 2}},
            ],
        }
    )
    assert len(data["nodes"]) == 2
    assert data["events"]["event"]["default"] == data["root"]


def test_unrecognized_schema_and_documentation_fail():
    compiler = Compiler({})
    with pytest.raises(ValueError, match="unsupported"):
        compiler.schema({"prefixItems": []}, "Tuple")
    with pytest.raises(ValueError, match="unexpected root"):
        fixed_contexts(compiler, "")
    with pytest.raises((ValueError, IndexError)):
        availability("")
