"""Drift-guard: run the README's fenced ``python`` examples through the generator.

Every self-contained example in ``README.md`` is executed the same way the golden
tests are (``workflow()`` -> ``generate_workflow()``) so the docs cannot silently
drift as the DSL evolves.

Which blocks run is decided conservatively:

* a block containing ``@workflow`` is run as written;
* a block containing only ``@job`` is wrapped in a minimal workflow and run;
* anything else (bare snippets, shell blocks) is skipped.

A block can opt out with ``<!-- readme-test: skip -->`` on the line before its fence
(used for the typed-actions example, which needs a generated ``actions`` module), or
request an exact-output check against the following ``yaml`` block with
``<!-- readme-test: expect-yaml -->``.
"""

import pathlib
import textwrap

import pytest

from ghgen.syntax import workflow, WorkflowInfo
from src.ghgen.commands.generate import generate_workflow

README = pathlib.Path(__file__).parent.parent / "README.md"

MARKER = "<!-- readme-test:"


def _seed_globals() -> dict:
    g: dict = {}
    exec("from ghgen.syntax import *", g)
    return g


def _iter_fenced_blocks(text: str):
    """Yield (lang, code, directive) for every fenced block, in order."""
    lines = text.splitlines()
    i = 0
    pending_directive = None
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith(MARKER):
            pending_directive = stripped[len(MARKER) :].rstrip(">-").strip().split()[0]
            i += 1
            continue
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            body = []
            i += 1
            while i < len(lines) and lines[i].strip() != "```":
                body.append(lines[i])
                i += 1
            code = textwrap.dedent("\n".join(body))
            yield lang, code, pending_directive
            pending_directive = None
            i += 1
            continue
        if stripped:
            pending_directive = None
        i += 1


def _wrap_job_block(code: str) -> str:
    indented = textwrap.indent(code, "    ")
    return "@workflow\ndef _readme_example():\n    on.workflow_dispatch()\n" + indented


def _strip_generated_comment(lines: list[str]) -> list[str]:
    return [l for l in lines if not l.startswith("# generated from ")]


def _collect_cases():
    """Return [(id, code, expected_yaml)] for the runnable python blocks."""
    text = README.read_text()
    blocks = list(_iter_fenced_blocks(text))
    cases = []
    for idx, (lang, code, directive) in enumerate(blocks):
        if lang != "python":
            continue
        if directive == "skip":
            continue
        if "@workflow" in code:
            runnable = code
        elif "@job" in code:
            runnable = _wrap_job_block(code)
        else:
            continue

        expected_yaml = None
        if directive == "expect-yaml":
            for lang2, code2, _ in blocks[idx + 1 :]:
                if lang2 == "yaml":
                    expected_yaml = code2
                    break
        # Name the case after the first def in the block for readable pytest ids.
        name = f"block{idx}"
        for line in code.splitlines():
            s = line.strip()
            if s.startswith("def "):
                name = f"block{idx}-{s[4:].split('(')[0]}"
                break
        cases.append((name, runnable, expected_yaml))
    return cases


CASES = _collect_cases()


def test_readme_has_runnable_examples():
    assert CASES, "no runnable python examples found in README.md"


@pytest.mark.parametrize(
    "code,expected_yaml", [(c, y) for _, c, y in CASES], ids=[n for n, _, _ in CASES]
)
def test_readme_example(code, expected_yaml, tmp_path):
    g = _seed_globals()
    exec(compile(code, str(README), "exec"), g)

    workflows = [v for v in g.values() if isinstance(v, WorkflowInfo)]
    assert workflows, "example did not define a @workflow"

    for wf in workflows:
        output = generate_workflow(wf, tmp_path)
        actual = output.read_text().splitlines()
        if expected_yaml is not None:
            assert _strip_generated_comment(actual) == _strip_generated_comment(
                expected_yaml.splitlines()
            )
