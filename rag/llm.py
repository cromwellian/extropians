"""LLM backends for the archive chat.

Three are supported, all exposing stream_completion(system, prompt) as an
iterator of text chunks:

  anthropic  Anthropic SDK, when an API key is in the environment.
  local      Any OpenAI-compatible server on localhost - LM Studio, Ollama,
             llama.cpp's server, vLLM, and friends.
  cli        The `claude` CLI in headless mode, using an existing Claude
             Code login. No API key or local model needed.

Selection is automatic (in that order) but can be pinned with EXTRO_LLM.
"""
import json
import os
import shutil
import subprocess

MODEL = os.environ.get("EXTRO_MODEL", "claude-opus-5")
CLI_MODEL = os.environ.get("EXTRO_CLI_MODEL", "sonnet")

# Which backend to use: auto | anthropic | local | cli
BACKEND = os.environ.get("EXTRO_LLM", "auto").strip().lower()

# OpenAI-compatible endpoints probed when EXTRO_LOCAL_URL is unset.
# LM Studio defaults to 1234; Ollama serves an OpenAI shim on 11434.
DEFAULT_LOCAL_URLS = ["http://localhost:1234/v1", "http://localhost:11434/v1"]
LOCAL_URL = os.environ.get("EXTRO_LOCAL_URL", "").rstrip("/")
LOCAL_MODEL = os.environ.get("EXTRO_LOCAL_MODEL", "")

_local_cache = None  # (url, model) once discovered, or False if unavailable


def _have_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or
                os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _claude_bin():
    for cand in (os.path.expanduser("~/.claude/local/claude"),
                 shutil.which("claude")):
        if cand and os.path.exists(cand):
            return cand
    return None


def _probe_local(force=False):
    """Find a reachable OpenAI-compatible server; return (url, model)."""
    global _local_cache
    if _local_cache is not None and not force:
        return _local_cache or None
    import httpx

    urls = [LOCAL_URL] if LOCAL_URL else DEFAULT_LOCAL_URLS
    for url in urls:
        try:
            r = httpx.get(f"{url}/models", timeout=1.5)
            r.raise_for_status()
            models = [m.get("id") for m in r.json().get("data", [])
                      if m.get("id")]
        except Exception:
            continue
        model = LOCAL_MODEL or (models[0] if models else "")
        if not model:
            continue
        # A configured model name that the server doesn't list is a
        # user error worth surfacing rather than silently substituting.
        if LOCAL_MODEL and models and LOCAL_MODEL not in models:
            print(f"WARNING: EXTRO_LOCAL_MODEL={LOCAL_MODEL!r} not served by "
                  f"{url} (has: {', '.join(models[:5])})")
        _local_cache = (url, model)
        return _local_cache
    _local_cache = False
    return None


def _resolve():
    """Return (kind, detail) for the backend that will actually be used."""
    if BACKEND == "anthropic":
        return ("anthropic", MODEL) if _have_api_key() else ("none", "no API key")
    if BACKEND == "cli":
        return ("cli", CLI_MODEL) if _claude_bin() else ("none", "no claude CLI")
    if BACKEND == "local":
        got = _probe_local()
        return ("local", got[1]) if got else ("none", "no local server")
    # auto: prefer the stronger backend. A local server is only picked up
    # when nothing else is configured, so having LM Studio running for some
    # unrelated reason never silently downgrades answer quality - opt in
    # with EXTRO_LLM=local.
    if _have_api_key():
        return "anthropic", MODEL
    if _claude_bin():
        return "cli", CLI_MODEL
    got = _probe_local()
    if got:
        return "local", got[1]
    return "none", "none"


def backend_name():
    kind, detail = _resolve()
    return {
        "anthropic": f"anthropic-sdk:{detail}",
        "local": f"local:{detail}",
        "cli": f"claude-cli:{detail}",
    }.get(kind, "none")


def context_budget():
    """(max_sources, chars_per_source) for the active backend.

    Local models usually have far smaller context windows than the hosted
    ones, so packing 14 full messages would overflow them and get the
    prompt silently truncated from the front - which is exactly where the
    instructions live. Give local backends a smaller, safer budget.
    """
    kind, _ = _resolve()
    if kind == "local":
        try:
            return (int(os.environ.get("EXTRO_LOCAL_SOURCES", "6")),
                    int(os.environ.get("EXTRO_LOCAL_SOURCE_CHARS", "1200")))
        except ValueError:
            return 6, 1200
    return 14, 3000


def stream_completion(system, prompt):
    kind, detail = _resolve()
    if kind == "anthropic":
        yield from _stream_sdk(system, prompt)
    elif kind == "local":
        yield from _stream_local(system, prompt)
    elif kind == "cli":
        yield from _stream_cli(system, prompt)
    else:
        raise RuntimeError(
            "No LLM backend available. Set ANTHROPIC_API_KEY, start a local "
            "OpenAI-compatible server (LM Studio or `ollama serve`), or "
            f"install the claude CLI. ({detail})")


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


def _stream_local(system, prompt):
    """Stream from an OpenAI-compatible /chat/completions endpoint."""
    import httpx

    got = _probe_local()
    if not got:
        raise RuntimeError("local LLM server is no longer reachable")
    url, model = got
    payload = {
        "model": model,
        "stream": True,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
    }
    # No read timeout: a local model can take a long time before its first
    # token, especially while the weights are still loading.
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=None)
    with httpx.stream("POST", f"{url}/chat/completions", json=payload,
                      timeout=timeout) as r:
        if r.status_code >= 400:
            r.read()
            raise RuntimeError(f"local LLM error {r.status_code}: "
                               f"{r.text[:300]}")
        for line in r.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                choices = json.loads(data).get("choices") or [{}]
                delta = choices[0].get("delta") or {}
            except (json.JSONDecodeError, IndexError, AttributeError):
                continue
            text = delta.get("content")
            if text:
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
