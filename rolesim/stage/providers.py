"""The opt-in chat-completions transport (standard library); model.ChatModel builds on it."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def _reject(value):
    raise ValueError(f"non-standard JSON value: {value}")


def strict_json(raw):
    return json.loads(raw, parse_constant=_reject)


def first_json(raw):
    """(the first JSON value in `raw`, the text after it). Under structured decoding the grammar
    covers that first value; Qwen3 on vLLM has been seen to go on after it (an added token such
    as <tool_response> and a second copy), which is outside the grammar and is dropped."""
    body = raw.lstrip()
    value, end = json.JSONDecoder(parse_constant=_reject).raw_decode(body)
    return value, body[end:].strip()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API redirect refused; configure the final API base URL")


def _env_number(name, default, cast):
    raw = os.environ.get(name, "")
    try:
        value = cast(raw) if raw else default
    except ValueError:
        raise ValueError(f"{name} must be a number") from None
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _json_text(content):
    """Model content as JSON text; a surrounding ```json fence is tolerated, nothing else."""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("model returned no content (the reasoning may have used up max_tokens)")
    body = content.strip()
    if body.startswith("```") and body.endswith("```"):
        body = body[3:-3]
        body = body[4:] if body.startswith("json") else body
    return body.strip()


class ChatBackend:
    """Thinking stays on: the server's reasoning goes to `last_reasoning` (run records only),
    never into another prompt; only the final JSON content is used."""
    label = "chat_completions_unvalidated_psychological_model"

    def __init__(self):
        self.base = os.environ.get("ROLE_SIM_BASE_URL", "").rstrip("/")
        self.model = os.environ.get("ROLE_SIM_MODEL", "")
        self._key = os.environ.get("ROLE_SIM_API_KEY", "")
        parsed = urllib.parse.urlparse(self.base)
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("ROLE_SIM_BASE_URL must be an API base URL without credentials or query")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("Use HTTPS for remote endpoints, HTTP only for local model servers")
        if not self.model:
            raise ValueError("Set ROLE_SIM_MODEL explicitly")
        self.calls = 0
        self.served_models = []   # model names the server reported, as opposed to the configured one
        self.trailing = []        # lengths of text dropped after the first JSON value (structured decoding)
        self.last_reasoning = None
        # Qwen3's recommended sampling for thinking mode; reasoning tokens count against max_tokens.
        # Structured decoding: the output schema goes to the server as response_format
        # json_schema (vLLM, OpenAI). ROLE_SIM_STRUCTURED=0 falls back to plain json_object for
        # servers without it; the reply is checked against the schema either way.
        flag = os.environ.get("ROLE_SIM_STRUCTURED", "1").strip().lower()
        if flag not in {"0", "1", "false", "true", "off", "on", ""}:
            raise ValueError("ROLE_SIM_STRUCTURED must be 1 or 0")
        self.structured = flag not in {"0", "false", "off"}
        self.sampling = {"temperature": .6, "top_p": .95,
                         "max_tokens": _env_number("ROLE_SIM_MAX_TOKENS", 8192, int),
                         "timeout_s": _env_number("ROLE_SIM_TIMEOUT", 600, float),
                         "thinking": "server default; reasoning kept in run records only",
                         "decoding": "json_schema" if self.structured else "json_object"}
        handlers = [NoRedirect()]
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            # Local model servers must not be routed through HTTP(S)_PROXY.
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def _call(self, system, payload):
        return self.chat(system, payload)[0]

    def response_format(self, schema):
        if schema is None or not self.structured:
            return {"type": "json_object"}
        return {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}}

    def chat(self, system, payload, schema=None):
        """One chat-completions call: (parsed JSON content, raw content text). With `schema`
        (and structured decoding on), the server is asked to decode to that JSON schema."""
        self.last_reasoning = self.last_raw = self.last_trailing = None
        body = {"model": self.model, "temperature": self.sampling["temperature"], "top_p": self.sampling["top_p"],
                "max_tokens": self.sampling["max_tokens"], "response_format": self.response_format(schema),
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
        headers = {"Content-Type": "application/json"}
        if self._key:
            headers["Authorization"] = "Bearer " + self._key
        req = urllib.request.Request(self.base + "/chat/completions",
                                     data=json.dumps(body).encode(), headers=headers, method="POST")
        self.calls += 1
        try:
            with self.opener.open(req, timeout=self.sampling["timeout_s"]) as response:
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ValueError("API response too large")
                data = strict_json(raw)
            served = data.get("model") if isinstance(data, dict) else None
            if isinstance(served, str) and served and served not in self.served_models:
                self.served_models.append(served)
            message = data["choices"][0]["message"]
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            self.last_reasoning = reasoning if isinstance(reasoning, str) and reasoning.strip() else None
            self.last_raw = message.get("content") if isinstance(message.get("content"), str) else None
            raw_content = _json_text(message["content"])
            if not (self.structured and schema is not None):
                return strict_json(raw_content), raw_content
            # Structured decoding: the grammar covers the first JSON value; text after it is dropped
            # and counted (the full reply stays in last_raw).
            value, trailing = first_json(raw_content)
            if trailing:
                self.trailing.append(len(trailing))
                self.last_trailing = trailing[:200]
                raw_content = raw_content.strip()[:-len(trailing)].rstrip()
            return value, raw_content
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Model API HTTP {exc.code}; response body omitted to protect credentials") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Model API connection failed; no mock fallback was used") from None
        except (KeyError, IndexError, TypeError):
            raise ValueError("Unsupported chat-completions response shape") from None
