# -*- coding: utf-8 -*-
"""Unified LLM client for sky-with-you.
Supports two providers:
  - openrouter: via OpenAI SDK (original sky-with-you behavior)
  - deepseek:   direct HTTP (ported from sky-companion, handles V4 thinking param)
"""
import re
import requests

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


class LLMClient:
    """Unified chat completion client.

    Args:
        provider: "openrouter" or "deepseek"
        api_key:  API key string
        base_url: API base URL
        model:    model name string
    """

    def __init__(self, provider="openrouter", api_key="", base_url="", model=""):
        self.provider = (provider or "openrouter").lower().strip()
        self.api_key = api_key or ""
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or ""
        self._sdk_client = None
        # 最近一次底层原始 message(dict，可能含 reasoning_content 等字段)，仅供 ai_trace 排查
        self.last_raw_message = None

        if self.provider == "openrouter" and OpenAI is not None and self.api_key:
            # 显式超时与重试：SDK 默认 timeout 长达 600s，挂机场景网络挂起会让 AI 线程僵住，
            # 与 deepseek 直连路径(25s)对齐到 30s、最多重试 1 次；超时抛错由 ai_loop 捕获。
            self._sdk_client = OpenAI(
                api_key=self.api_key, base_url=self.base_url,
                timeout=30.0, max_retries=1,
            )

    # ── public API ────────────────────────────────────────────────────

    def chat(self, messages, temperature=0.7, max_tokens=300):
        """Send a chat completion request.

        Args:
            messages: list of {"role": "...", "content": "..."} dicts,
                      OR a plain string (converted to single user message).
            temperature: sampling temperature.
            max_tokens:  maximum tokens in response.

        Returns:
            str: the assistant's reply text.
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        if self.provider == "deepseek" and self._use_direct_http():
            return self._chat_http(messages, temperature, max_tokens)
        return self._chat_sdk(messages, temperature, max_tokens)

    # ── OpenAI SDK path (openrouter / generic) ───────────────────────

    def _chat_sdk(self, messages, temperature, max_tokens):
        if self._sdk_client is None:
            raise RuntimeError("OpenAI SDK not available or API key not set for provider=" + self.provider)
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        extra = self._extra_body()
        if extra:
            kwargs["extra_body"] = extra
        try:
            resp = self._sdk_client.chat.completions.create(**kwargs)
            msg0 = resp.choices[0].message
            try:
                self.last_raw_message = msg0.model_dump()
            except Exception:
                self.last_raw_message = {"content": getattr(msg0, "content", None)}
            return msg0.content or ""
        except Exception as e:
            raise RuntimeError(self._format_api_error(e))

    # ── Direct HTTP path (DeepSeek) ───────────────────────────────────

    def _use_direct_http(self):
        base = (self.base_url or "").lower()
        return "api.deepseek.com" in base or self.provider == "deepseek"

    def _chat_http(self, messages, temperature, max_tokens):
        url = self._chat_url()
        extra_body = self._extra_body()
        if extra_body and extra_body.get("thinking", {}).get("type") == "enabled":
            max_tokens = max(max_tokens, 260)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if extra_body:
            payload.update(extra_body)
        headers = {
            "Authorization": "Bearer " + self.api_key,
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=25)
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:1000]}")
            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            self.last_raw_message = msg  # 完整原始字段（若开 thinking 可能带 reasoning_content）
            return msg.get("content") or ""
        except requests.RequestException as e:
            raise RuntimeError(f"HTTP request failed: {e}")

    def _chat_url(self):
        base = (self.base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    # ── shared helpers ─────────────────────────────────────────────────

    def _extra_body(self):
        """Generate extra_body for DeepSeek V4 thinking control."""
        model = (self.model or "").lower()
        base = (self.base_url or "").lower()
        if ("deepseek" in model or "deepseek" in base) and ("v4" in model or "flash" in model):
            return {"thinking": {"type": "disabled"}}
        return None

    def _format_api_error(self, err):
        msg = str(err)
        try:
            body = getattr(getattr(err, "response", None), "text", "")
            if body:
                msg = msg + " | " + body
        except Exception:
            pass
        msg = re.sub(r"sk-[A-Za-z0-9_\-]+", "sk-***", msg)
        msg = re.sub(r"Bearer\s+[A-Za-z0-9_\-\.]+", "Bearer ***", msg, flags=re.I)
        return msg[:180]
