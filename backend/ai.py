"""OpenAI-compatible chat client (DeepSeek by default)."""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .models import AIConfig

log = logging.getLogger(__name__)

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "models": "deepseek-chat,deepseek-reasoner",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "models": "gpt-4o-mini,gpt-4o,gpt-4.1-mini",
    },
    "siliconflow": {
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3",
        "models": "deepseek-ai/DeepSeek-V3,Qwen/Qwen2.5-72B-Instruct",
    },
    "moonshot": {
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "models": "moonshot-v1-8k,moonshot-v1-32k",
    },
    "dashscope": {
        "label": "通义千问 DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "models": "qwen-plus,qwen-turbo,qwen-max",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "model": "",
        "models": "",
    },
}


class AIError(RuntimeError):
    """Raised when the model call fails, with a Chinese, actionable message."""


def chat_endpoint(base_url: str) -> str:
    """Build the /chat/completions URL from whatever the user pasted."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AIError("还没填 API 地址（Base URL）")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _humanize_http_error(status: int, body: str) -> str:
    snippet = (body or "").strip().replace("\n", " ")[:300]
    if status == 401:
        return "API Key 无效或已过期（401），请检查密钥"
    if status == 402:
        return "账户余额不足（402），请到模型平台充值"
    if status == 403:
        return f"访问被拒绝（403），请确认模型权限。{snippet}"
    if status == 404:
        return f"接口地址不存在（404），请检查 Base URL 与模型名。{snippet}"
    if status == 429:
        return "请求太频繁被限流（429），稍后会自动重试"
    if 500 <= status < 600:
        return f"模型服务端错误（{status}），稍后会自动重试。{snippet}"
    return f"接口返回错误（{status}）：{snippet}"


class AIClient:
    """Stateless wrapper: build messages, call the model, return text."""

    def __init__(self, config: AIConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------ public
    def build_messages(
        self,
        *,
        chat: str,
        chat_type: str,
        sender: str,
        user_text: str,
        history: list[dict[str, str]] | None = None,
        room_rules: str = "",
    ) -> list[dict[str, str]]:
        cfg = self.config
        system_parts = [cfg.persona.strip()]

        # 人设补充：自称、口吻、口头禅、底线。空着就不拼。
        identity: list[str] = []
        if cfg.nickname.strip():
            identity.append(f"你叫「{cfg.nickname.strip()}」，这就是你在微信里用的名字。")
        if cfg.speaking_style.strip():
            identity.append(f"说话风格：{cfg.speaking_style.strip()}")
        if cfg.catchphrase.strip():
            identity.append(f"你偶尔会用这句口头禅：{cfg.catchphrase.strip()}")
        if cfg.taboos.strip():
            identity.append(f"绝对不要做这些事：{cfg.taboos.strip()}")
        if identity:
            system_parts.append("\n".join(identity))

        if room_rules.strip():
            system_parts.append(room_rules.strip())

        # 能力声明：模型不知道就会说"我发不了表情包"（实际能发）
        system_parts.append(
            "【能力说明】你能发文字、能发图片、能发表情包，这三件事你都做得到。\n"
            "发表情包的写法：在回复正文里写 [[表情]] 这个标记（系统会自动换成一张真表情包发出去，"
            "标记本身对方看不到）。你可以只写 [[表情]]，也可以先说话再写 [[表情]]。\n"
            "【硬性要求】对方要表情包、或你想表达情绪时，必须写 [[表情]]；"
            "绝对不许说「我发不了表情包 / 表情包发不出去 / 这我做不到 / 我只会打字」这类话，"
            "因为那是错的——你确实能发。没被要求时也不要主动提这件事。"
        )

        scene = "微信群聊" if chat_type == "group" else "微信私聊"
        context = f"当前对话场景：{scene}，会话名：「{chat}」。"
        if chat_type == "group" and sender:
            context += f"这次说话的人是：「{sender}」。群聊里尽量简短，别抢话。"
        system_parts.append(context)
        if cfg.extra_prompt.strip():
            system_parts.append(cfg.extra_prompt.strip())
        system_parts.append(
            f"回复请控制在 {cfg.reply_max_chars} 字以内，不要用 Markdown 语法，"
            "不要加括号动作描写，直接说人话。"
        )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": "\n".join(p for p in system_parts if p)}
        ]
        if cfg.history_rounds > 0 and history:
            import re as _re

            # 历史里如果有「我发不了表情包」这类旧说法，会被模型照抄 → 过滤掉
            refusal = _re.compile(
                r"发不了表情包|表情包发不出去|发不了图片|只能.{0,3}打字|我发不了"
            )
            cleaned = []
            for item in history[-cfg.history_rounds * 2:]:
                if item.get("role") == "assistant" and refusal.search(
                    str(item.get("content") or "")
                ):
                    continue
                cleaned.append(item)
            messages.extend(cleaned)
        messages.append({"role": "user", "content": user_text})
        return messages

# ------------------------------------------------------------------ 识图
    def vision_ready(self) -> bool:
        cfg = self.config
        return bool(cfg.vision_model.strip()) and bool(cfg.api_key.strip())

    def describe_image(self, path: str, prompt: str = "") -> str:
        """把一张本地图片交给视觉模型，返回文字描述（空字符串表示没看懂/没配置）。

        走的是和聊天完全相同的一套接口（base_url / api_key），只把 model 换成
        `ai.vision_model`，所以只要换成一个支持图片的模型名就能用。
        """
        cfg = self.config
        if not self.vision_ready():
            return ""
        import base64
        from pathlib import Path as _Path

        image = _Path(path)
        if not image.exists():
            return ""
        try:
            data = base64.b64encode(image.read_bytes()).decode("ascii")
        except OSError:
            return ""
        suffix = image.suffix.lower().lstrip(".") or "jpeg"
        if suffix == "jpg":
            suffix = "jpeg"
        url = chat_endpoint(cfg.base_url)
        payload = {
            "model": cfg.vision_model.strip(),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt.strip() or cfg.vision_prompt.strip()},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/{suffix};base64,{data}"},
                        },
                    ],
                }
            ],
            "max_tokens": 400,
            "temperature": 0.4,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {cfg.api_key.strip()}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(
                timeout=cfg.timeout, trust_env=cfg.respect_system_proxy
            ) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code >= 400:
                raise AIError(_humanize_http_error(resp.status_code, resp.text))
            body = resp.json()
            text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            return str(text or "").strip()
        except AIError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AIError(f"识图失败：{type(exc).__name__}: {exc}") from exc
    def complete(self, messages: list[dict[str, str]]) -> str:
        url = chat_endpoint(self.config.base_url)
        if not self.config.api_key.strip():
            raise AIError("还没填 API Key，请到「AI 接口」页填写并保存")

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key.strip()}",
            "Content-Type": "application/json",
        }

        last_error: str | None = None
        for attempt in range(3):
            try:
                with httpx.Client(
                    timeout=self.config.timeout,
                    trust_env=self.config.respect_system_proxy,
                ) as client:
                    resp = client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    message = _humanize_http_error(resp.status_code, resp.text)
                    if resp.status_code in (429,) or resp.status_code >= 500:
                        last_error = message
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise AIError(message)
                data = resp.json()
                return self._extract_text(data)
            except httpx.TimeoutException:
                last_error = f"模型接口超时（{self.config.timeout}s），网络可能不通"
                time.sleep(1.0)
            except httpx.HTTPError as exc:
                last_error = (
                    f"连不上模型接口：{exc.__class__.__name__}，请检查 Base URL 和网络"
                    + (
                        "（系统代理开着但代理程序没运行 → 关掉代理，或在「AI 接口」页勾选『使用系统代理』）"
                        if not self.config.respect_system_proxy
                        else ""
                    )
                )
                time.sleep(1.0)
            except ValueError as exc:
                last_error = f"接口返回的不是合法 JSON：{exc}"
                break
        raise AIError(last_error or "调用模型失败")

    def test(self) -> dict[str, Any]:
        started = time.time()
        text = self.complete([{"role": "user", "content": "回复两个字：在的"}])
        return {
            "ok": True,
            "latency_ms": int((time.time() - started) * 1000),
            "model": self.config.model,
            "reply": text,
        }

    # ----------------------------------------------------------------- helpers
    @staticmethod
    def _extract_text(data: dict[str, Any]) -> str:
        try:
            choices = data["choices"]
            message = choices[0]["message"]
            content = message.get("content")
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError(f"接口返回结构异常，缺少 choices/message：{exc}") from exc
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        text = (content or "").strip()
        if not text:
            reasoning = (choices[0]["message"].get("reasoning_content") or "").strip()
            if reasoning:
                return reasoning
            raise AIError("模型返回了空内容，试试换个模型或降低温度")
        return text
