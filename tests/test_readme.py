"""Drift-guard: run the README's fenced ``python`` examples through the generator.

Every self-contained example in ``README.md`` is executed the same way the golden
tests are (``workflow()`` -> ``generate_workflow()``) so the docs cannot silently
drift as the DSL evolves.

Which blocks run is decided conservatively:

* a block containing ``@workflow`` or ``@action`` is run as written;
* a block containing only ``@job`` is wrapped in a minimal workflow and run;
* anything else (bare snippets, shell blocks) is skipped.

A block can opt out with ``<!-- readme-test: skip -->`` on the line before its fence
(used for the typed-actions example, which needs a generated ``actions`` module), or
request an exact-output check with ``<!-- readme-test: expect-yaml -->``: each generated
file, in definition order, is compared against the ``yaml`` blocks that follow (up to the
next ``python`` block).

Examples must also use t-strings for context interpolation; a block that falls back to
the deprecated f-string path fails the guard.
"""

import pathlib
import runpy
import textwrap
import warnings

import pytest

from ghgen.syntax import ActionInfo, WorkflowInfo
from src.ghgen.commands.generate import generate_action, generate_workflow

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
        if "@workflow" in code or "@action" in code:
            runnable = code
        elif "@job" in code:
            runnable = _wrap_job_block(code)
        else:
            continue

        expected_yaml = None
        if directive == "expect-yaml":
            expected_yaml = []
            for lang2, code2, _ in blocks[idx + 1 :]:
                if lang2 == "python":
                    break
                if lang2 == "yaml":
                    expected_yaml.append(code2)
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
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Run from a real file so assignment-based id inference can read the source.
        source = tmp_path / "readme_example.py"
        source.write_text(code)
        g = runpy.run_path(str(source), init_globals=g)

        outputs = []
        for v in g.values():
            if isinstance(v, WorkflowInfo):
                outputs.append(generate_workflow(v, tmp_path))
            elif isinstance(v, ActionInfo):
                outputs.append(generate_action(v, tmp_path / v.path / "action.yml"))
        assert outputs, "example did not define a @workflow or @action"

        if expected_yaml is not None:
            assert len(outputs) == len(expected_yaml)
            for output, expected in zip(outputs, expected_yaml):
                assert _strip_generated_comment(
                    output.read_text().splitlines()
                ) == _strip_generated_comment(expected.splitlines())

    # Examples must use t-strings, not the deprecated f-string interpolation.
    deprecated = [
        w
        for w in caught
        if issubclass(w.category, DeprecationWarning) and "t-string" in str(w.message)
    ]
    assert not deprecated, f"README example uses deprecated f-strings: {deprecated}"
