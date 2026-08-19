"""LLM backend: Anthropic SDK if credentials are available, otherwise the
local `claude` CLI in headless mode (uses the user's Claude Code login).

Both backends expose stream_completion(system, prompt) -> iterator of text.
"""
import json
import os
import shutil
import subprocess

MODEL = os.environ.get("EXTRO_MODEL", "claude-opus-5")
CLI_MODEL = os.environ.get("EXTRO_CLI_MODEL", "sonnet")


def _have_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or
                os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _claude_bin():
    for cand in (os.path.expanduser("~/.claude/local/claude"),
                 shutil.which("claude")):
        if cand and os.path.exists(cand):
            return cand
    return None


def backend_name():
    if _have_api_key():
        return f"anthropic-sdk:{MODEL}"
    if _claude_bin():
        return f"claude-cli:{CLI_MODEL}"
    return "none"


def stream_completion(system, prompt):
    if _have_api_key():
        yield from _stream_sdk(system, prompt)
    elif _claude_bin():
        yield from _stream_cli(system, prompt)
    else:
        raise RuntimeError(
            "No LLM backend: set ANTHROPIC_API_KEY or install the claude CLI")


def _stream_sdk(system, prompt):
    import anthropic
    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            yield text


def _stream_cli(system, prompt):
    """Stream via `claude -p` headless mode with stream-json output."""
    env = dict(os.environ)
    for var in ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(var, None)
    cmd = [
        _claude_bin(), "-p",
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", CLI_MODEL,
        # replace (not append to) the coding-agent prompt: this is a
        # document-grounded question answerer, not a coding session
        "--system-prompt", system,
        "--strict-mcp-config",
        "--tools", "",
    ]
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env=env, cwd="/tmp")
    proc.stdin.write(prompt.encode("utf-8"))
    proc.stdin.close()

    got_delta = False
    final_result = None
    for raw in proc.stdout:
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = ev.get("type")
        if t == "stream_event":
            inner = ev.get("event", {})
            if inner.get("type") == "content_block_delta":
                delta = inner.get("delta", {})
                if delta.get("type") == "text_delta":
                    got_delta = True
                    yield delta.get("text", "")
        elif t == "result":
            final_result = ev.get("result")
    proc.wait()
    if not got_delta and final_result:
        yield final_result
