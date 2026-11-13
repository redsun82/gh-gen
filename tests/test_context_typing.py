import json
from pathlib import Path
import queue
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]


def test_context_type_contract():
    fixture = ROOT / "tests/typing/contexts.py"
    expected = {
        index
        for index, line in enumerate(fixture.read_text().splitlines())
        if "# expect-error" in line
    }
    result = subprocess.run(
        ["pyright", "--outputjson", "--pythonpath", sys.executable, str(fixture)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    diagnostics = json.loads(result.stdout)["generalDiagnostics"]
    assert {item["range"]["start"]["line"] for item in diagnostics} == expected
    assert all(item.get("rule") == "reportAttributeAccessIssue" for item in diagnostics)


def test_actual_nested_completion(tmp_path):
    source = "from ghgen.syntax import github\n\ngithub.event.pull_request.head."
    document = tmp_path / "completion.py"
    document.write_text(source)
    process = subprocess.Popen(
        ["pyright-langserver", "--stdio"],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    messages = queue.Queue()

    def receive():
        while True:
            headers = {}
            for line in iter(process.stdout.readline, b"\r\n"):
                if not line:
                    return
                key, value = line.decode().split(":", 1)
                headers[key.lower()] = value.strip()
            messages.put(
                json.loads(process.stdout.read(int(headers["content-length"])))
            )

    reader = threading.Thread(target=receive, daemon=True)
    reader.start()

    def send(method, params=None, request_id=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if request_id is not None:
            message["id"] = request_id
        content = json.dumps(message).encode()
        process.stdin.write(
            f"Content-Length: {len(content)}\r\n\r\n".encode() + content
        )
        process.stdin.flush()

    def response(request_id):
        while True:
            message = messages.get(timeout=60)
            if message.get("id") == request_id:
                assert "error" not in message, message
                return message["result"]

    try:
        send(
            "initialize",
            {
                "processId": None,
                "rootUri": ROOT.as_uri(),
                "capabilities": {},
                "initializationOptions": {},
            },
            1,
        )
        response(1)
        send("initialized", {})
        send(
            "workspace/didChangeConfiguration",
            {
                "settings": {
                    "python": {
                        "pythonPath": sys.executable,
                        "analysis": {
                            "extraPaths": [str(ROOT / "src")],
                        },
                    },
                }
            },
        )
        send(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": document.as_uri(),
                    "languageId": "python",
                    "version": 1,
                    "text": source,
                }
            },
        )
        send(
            "textDocument/completion",
            {
                "textDocument": {"uri": document.as_uri()},
                "position": {"line": 2, "character": len(source.splitlines()[2])},
            },
            2,
        )
        completion = response(2)
        items = completion["items"] if isinstance(completion, dict) else completion
        assert {"sha", "ref", "repo", "label"} <= {item["label"] for item in items}
    finally:
        process.terminate()
        process.wait(timeout=10)
        reader.join(timeout=10)
        process.stdin.close()
        process.stdout.close()
        process.stderr.close()
