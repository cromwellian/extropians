"""LLM backends for the archive chat.

Four are supported, all exposing stream_completion(system, prompt) as an
iterator of text chunks:

  anthropic  Anthropic SDK, when an API key is in the environment.
  gateway    Vercel AI Gateway - hundreds of hosted models behind one
             OpenAI-compatible endpoint. This is the backend that works on
             Vercel with no extra secrets: deployments are issued a
             VERCEL_OIDC_TOKEN automatically once AI Gateway is enabled.
  local      Any OpenAI-compatible server on localhost - LM Studio, Ollama,
             llama.cpp's server, vLLM, and friends.
  cli        The `claude` CLI in headless mode, using an existing Claude
             Code login. No API key or local model needed.

Selection is automatic (in that order) but can be pinned with EXTRO_LLM.
`gateway` and `local` share one OpenAI-compatible implementation and differ
only in where they point and whether they send an Authorization header.
"""
import json
import os
import shutil
import subprocess

MODEL = os.environ.get("EXTRO_MODEL", "claude-opus-5")
CLI_MODEL = os.environ.get("EXTRO_CLI_MODEL", "sonnet")

# Which backend to use: auto | anthropic | gateway | local | cli
BACKEND = os.environ.get("EXTRO_LLM", "auto").strip().lower()

# Vercel AI Gateway. GLM 5.2 is the default: a 1M-token context window at
# roughly a sixth of Opus pricing suits stuffing long archive excerpts.
# Any slug from GET /v1/models works - anthropic/claude-opus-5, openai/...
GATEWAY_URL = os.environ.get(
    "EXTRO_GATEWAY_URL", "https://ai-gateway.vercel.sh/v1").rstrip("/")
GATEWAY_MODEL = os.environ.get("EXTRO_GATEWAY_MODEL", "zai/glm-5.2")

# OpenAI-compatible endpoints probed when EXTRO_LOCAL_URL is unset.
# LM Studio defaults to 1234; Ollama serves an OpenAI shim on 11434.
DEFAULT_LOCAL_URLS = ["http://localhost:1234/v1", "http://localhost:11434/v1"]
LOCAL_URL = os.environ.get("EXTRO_LOCAL_URL", "").rstrip("/")
LOCAL_MODEL = os.environ.get("EXTRO_LOCAL_MODEL", "")

_local_cache = None  # (url, model) once discovered, or False if unavailable


def _have_api_key():
    return bool(os.environ.get("ANTHROPIC_API_KEY") or
                os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _gateway_key():
    """AI Gateway credential: an explicit key, else the Vercel OIDC token."""
    return (os.environ.get("AI_GATEWAY_API_KEY")
            or os.environ.get("VERCEL_OIDC_TOKEN") or "")


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
    if BACKEND == "gateway":
        return (("gateway", GATEWAY_MODEL) if _gateway_key()
                else ("none", "no AI_GATEWAY_API_KEY or VERCEL_OIDC_TOKEN"))
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
    if _gateway_key():
        return "gateway", GATEWAY_MODEL
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
        "gateway": f"vercel-gateway:{detail}",
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
    # Input tokens dominate the bill on metered backends, and this prompt is
    # mostly archive excerpts, so these two knobs are the cost dial for a
    # publicly hosted instance.
    try:
        return (int(os.environ.get("EXTRO_SOURCES", "14")),
                int(os.environ.get("EXTRO_SOURCE_CHARS", "3000")))
    except ValueError:
        return 14, 3000


def stream_completion(system, prompt):
    kind, detail = _resolve()
    if kind == "anthropic":
        yield from _stream_sdk(system, prompt)
    elif kind == "gateway":
        yield from _stream_openai(GATEWAY_URL, _gateway_key(),
                                  GATEWAY_MODEL, system, prompt)
    elif kind == "local":
        got = _probe_local()
        if not got:
            raise RuntimeError("local LLM server is no longer reachable")
        yield from _stream_openai(got[0], "", got[1], system, prompt)
    elif kind == "cli":
        yield from _stream_cli(system, prompt)
    else:
        raise RuntimeError(
            "No LLM backend available. Set ANTHROPIC_API_KEY, enable Vercel "
            "AI Gateway (AI_GATEWAY_API_KEY / VERCEL_OIDC_TOKEN), start a "
            "local OpenAI-compatible server, or install the claude CLI. "
            f"({detail})")


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


class QuotaError(RuntimeError):
    """Backend refused the request for quota reasons (402/429)."""


def _stream_openai(url, api_key, model, system, prompt):
    """Stream from any OpenAI-compatible /chat/completions endpoint.

    Serves both a local server (no api_key) and Vercel AI Gateway (bearer
    token, either an AI Gateway key or the deployment's OIDC token).
    """
    import httpx

    payload = {
        "model": model,
        "stream": True,
        "temperature": 0.2,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
    }
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    # No read timeout: a local model can take a long time before its first
    # token, especially while the weights are still loading.
    timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=None)
    with httpx.stream("POST", f"{url}/chat/completions", json=payload,
                      headers=headers, timeout=timeout) as r:
        if r.status_code >= 400:
            r.read()
            body = r.text[:300]
            # Worth distinguishing: these two are the ones a hosted free
            # instance actually hits, and they need a human-readable answer
            # rather than a raw 4xx.
            if r.status_code == 402:
                raise QuotaError(
                    "The AI credit balance for this deployment is exhausted. "
                    "Search and browsing still work.")
            if r.status_code == 429:
                raise QuotaError(
                    "Rate limited by the model provider - please retry in a "
                    "moment. Search and browsing still work.")
            # AI Gateway gates even its free monthly credits behind a card
            # on file, and answers with 403 customer_verification_required
            # until one is added. That reads as a broken key otherwise.
            if r.status_code == 403 and "customer_verification" in body:
                raise QuotaError(
                    "This deployment's AI Gateway account needs a payment "
                    "method on file before it will serve requests, even on "
                    "the free credits. Search and browsing still work.")
            raise RuntimeError(f"LLM error {r.status_code}: {body}")
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
