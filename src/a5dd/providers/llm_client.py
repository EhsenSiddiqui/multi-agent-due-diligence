"""
LLM client shared by every AI agent (researcher, analyst, judge, challenger).

Two calls:
    complete_json(system, user) -> dict
        One question, one JSON answer (analyst, judge, challenger).
    run_tools(system, user, tools, handler, max_steps) -> list of tool calls made
        A real tool-use loop (researcher): the model chooses a tool, our code runs it,
        the result goes back to the model, and so on until it calls `finish` or the
        step budget runs out. The model decides WHAT to do; code decides what is ALLOWED.

Providers: Anthropic (Claude) and OpenAI, chosen in settings.yaml -> llm.provider.
Each agent role can use its own model (llm.models), e.g. a cheaper model for the
analyst, which reads every source, and a stronger one for the judge.

CACHE: every model response is stored in llm.cache_dir, keyed by the exact request.
Re-running the same case with the same sources costs nothing and gives the same output.

KEYS: set ANTHROPIC_API_KEY or OPENAI_API_KEY in your environment (see README).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Callable

from ..config import Config

# A transport takes (system, messages, tools, model, max_tokens) in the neutral format below
# and returns {"text": str, "tool_calls": [{"id", "name", "input"}]}.
# Neutral message format:
#   {"role": "user", "content": str}
#   {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "input"}]}
#   {"role": "tool", "results": [{"id", "content"}]}
Transport = Callable[[str, list, list, str, int], dict]
TRANSPORTS: dict[str, Transport] = {}   # Tests register a "fake" transport here.

KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class MissingKey(RuntimeError):
    pass


def check_key(cfg: Config) -> None:
    """Fail early, with a clear message, if an LLM agent is switched on but no key is set."""
    provider = cfg.settings["llm"]["provider"]
    env = KEY_ENV.get(provider)
    if env and not os.environ.get(env):
        raise MissingKey(f"The AI agents need {env}. Set it (see README, step 3), "
                         f"or run the rule-based baseline with --rules.")


class LLMClient:
    def __init__(self, cfg: Config, role: str = "default"):
        llm = cfg.settings["llm"]
        self.provider = llm["provider"]
        models = llm.get("models", {}).get(self.provider, {})
        self.model = models.get(role) or models.get("default") or llm.get("model", "")
        self.role = role
        self.max_tokens = int(llm.get("max_tokens", 4000))
        self.cache_dir = cfg.root / llm["cache_dir"]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = None  # Created lazily, only when a call is not cached.

    # ------------------------------------------------------------------ public

    def complete_json(self, system: str, user: str) -> dict:
        """Ask the model for one JSON object."""
        reply = self._cached(system, [{"role": "user", "content": user}], [])
        return parse_json(reply["text"])

    def run_tools(self, system: str, user: str, tools: list[dict],
                  handler: Callable[[str, dict], str], max_steps: int, finish_tool: str = "finish") -> list[dict]:
        """
        Tool-use loop. `tools` are neutral specs: {"name", "description", "parameters": JSON schema}.
        `handler(name, input)` runs one tool and returns its result as text.
        Stops when the model calls `finish_tool`, stops calling tools, or uses `max_steps` turns.
        Returns every tool call made, in order (the agent's own log).
        """
        messages: list[dict] = [{"role": "user", "content": user}]
        made: list[dict] = []
        for step in range(max_steps):
            reply = self._cached(system, messages, tools)
            calls = reply.get("tool_calls", [])
            messages.append({"role": "assistant", "text": reply.get("text", ""), "tool_calls": calls})
            if not calls:
                break
            results = []
            finished = False
            for call in calls:
                made.append(call)
                if call["name"] == finish_tool:
                    finished = True
                    results.append({"id": call["id"], "content": "Research finished."})
                    continue
                try:
                    content = handler(call["name"], call.get("input") or {})
                except Exception as exc:  # A tool error goes back to the model; it can try something else.
                    content = f"ERROR: {type(exc).__name__}: {exc}"
                remaining = max_steps - step - 1
                if remaining <= 2:
                    content += f"\n[Budget: {remaining} turns left. Call {finish_tool} soon.]"
                results.append({"id": call["id"], "content": content})
            messages.append({"role": "tool", "results": results})
            if finished:
                break
        return made

    # ------------------------------------------------------------------ plumbing

    def _cached(self, system: str, messages: list, tools: list) -> dict:
        blob = json.dumps([self.provider, self.model, system, messages, tools], sort_keys=True, default=str)
        key = hashlib.sha256(blob.encode()).hexdigest()[:32]
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        reply = self._transport()(system, messages, tools, self.model, self.max_tokens)
        cache_file.write_text(json.dumps(reply, indent=1), encoding="utf-8")
        return reply

    def _transport(self) -> Transport:
        if self.provider in TRANSPORTS:
            return TRANSPORTS[self.provider]
        if self.provider == "anthropic":
            return self._anthropic
        if self.provider == "openai":
            return self._openai
        raise ValueError(f"Unknown llm.provider {self.provider!r} (use anthropic or openai)")

    # ---- Anthropic (Claude) Messages API with tool use
    def _anthropic(self, system, messages, tools, model, max_tokens) -> dict:
        if self._client is None:
            import anthropic  # Optional dependency: pip install -e ".[live]"
            self._client = anthropic.Anthropic(api_key=os.environ[KEY_ENV["anthropic"]], max_retries=4)
        converted = []
        for m in messages:
            if m["role"] == "user":
                converted.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = ([{"type": "text", "text": m["text"]}] if m.get("text") else [])
                blocks += [{"type": "tool_use", "id": c["id"], "name": c["name"], "input": c.get("input") or {}}
                           for c in m.get("tool_calls", [])]
                converted.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "(no text)"}]})
            else:
                converted.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"]} for r in m["results"]]})
        kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens, system=system, messages=converted)
        if tools:
            kwargs["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]}
                               for t in tools]
        msg = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        calls = [{"id": b.id, "name": b.name, "input": dict(b.input or {})}
                 for b in msg.content if getattr(b, "type", "") == "tool_use"]
        return {"text": text, "tool_calls": calls}

    # ---- OpenAI Chat Completions API with function calling
    def _openai(self, system, messages, tools, model, max_tokens) -> dict:
        if self._client is None:
            import openai  # Optional dependency: pip install -e ".[live]"
            self._client = openai.OpenAI(api_key=os.environ[KEY_ENV["openai"]], max_retries=4)
        converted: list[dict] = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                converted.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": m.get("text") or None}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [{"id": c["id"], "type": "function",
                                            "function": {"name": c["name"], "arguments": json.dumps(c.get("input") or {})}}
                                           for c in m["tool_calls"]]
                converted.append(entry)
            else:
                converted += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
        kwargs: dict[str, Any] = dict(model=model, messages=converted, max_completion_tokens=max_tokens)
        if tools:
            kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
        else:
            kwargs["response_format"] = {"type": "json_object"}
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [{"id": c.id, "name": c.function.name, "input": json.loads(c.function.arguments or "{}")}
                 for c in (msg.tool_calls or [])]
        return {"text": msg.content or "", "tool_calls": calls}


def parse_json(raw: str) -> dict:
    """Accept plain JSON or JSON wrapped in a ```json fence."""
    match = re.search(r"\{.*\}", raw or "", flags=re.DOTALL)
    if not match:
        raise ValueError(f"Model did not return JSON:\n{(raw or '')[:500]}")
    return json.loads(match.group(0))
