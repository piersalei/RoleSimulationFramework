"""The opt-in chat-completions transport (standard library); model.ChatModel builds on it."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request


def strict_json(raw):
    def reject(value):
        raise ValueError(f"non-standard JSON value: {value}")
    return json.loads(raw, parse_constant=reject)


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
        self.last_reasoning = None
        # Qwen3's recommended sampling for thinking mode; reasoning tokens count against max_tokens.
        self.sampling = {"temperature": .6, "top_p": .95,
                         "max_tokens": _env_number("ROLE_SIM_MAX_TOKENS", 8192, int),
                         "timeout_s": _env_number("ROLE_SIM_TIMEOUT", 600, float),
                         "thinking": "server default; reasoning kept in run records only"}
        handlers = [NoRedirect()]
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            # Local model servers must not be routed through HTTP(S)_PROXY.
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)

    def _call(self, system, payload):
        return self.chat(system, payload)[0]

    def chat(self, system, payload):
        """One chat-completions call: (parsed JSON content, raw content text)."""
        self.last_reasoning = None
        body = {"model": self.model, "temperature": self.sampling["temperature"], "top_p": self.sampling["top_p"],
                "max_tokens": self.sampling["max_tokens"], "response_format": {"type": "json_object"},
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
            raw_content = _json_text(message["content"])
            return strict_json(raw_content), raw_content
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Model API HTTP {exc.code}; response body omitted to protect credentials") from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError("Model API connection failed; no mock fallback was used") from None
        except (KeyError, IndexError, TypeError):
            raise ValueError("Unsupported chat-completions response shape") from None
