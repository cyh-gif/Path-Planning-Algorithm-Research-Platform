from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError


LOGGER = logging.getLogger(__name__)


class MangoAssistantService:
    """芒小果：水果运输与当季推荐问答助手（通义 API）。"""

    def __init__(
        self,
        agent_name: str,
        api_key: str,
        endpoint: str,
        model: str,
        timeout_s: float = 20.0,
        retry: int = 1,
    ) -> None:
        self.agent_name = (agent_name or "芒小果").strip() or "芒小果"
        self.api_key = (api_key or "").strip()
        self.endpoint = (
            endpoint or "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
        ).strip()
        self.model = (model or "qwen-plus").strip() or "qwen-plus"
        self.timeout_s = max(1.0, float(timeout_s))
        self.retry = max(0, int(retry))

    def chat(self, user_text: str, history: list[dict[str, str]] | None = None) -> str:
        """调用通义聊天接口；失败时返回本地兜底答案。"""
        message = user_text.strip()
        if not message:
            return "我在，先告诉我你想问什么水果吧。"

        if not self.api_key:
            return self.build_local_reply(message, reason="未配置通义 API Key。")

        payload = {
            "model": self.model,
            "messages": self._build_messages(message, history or []),
            "temperature": 0.55,
            "top_p": 0.85,
        }

        tries = self.retry + 1
        last_error: Exception | None = None
        for idx in range(tries):
            try:
                data = self._request_chat(payload)
                text = self._extract_reply_text(data)
                if text:
                    return text
                raise ValueError("通义返回内容为空。")
            except Exception as exc:  # pragma: no cover
                last_error = exc
                if idx < tries - 1:
                    continue

        LOGGER.warning("芒小果请求失败，使用本地兜底回复: %s", last_error)
        return self.build_local_reply(message, reason=str(last_error) if last_error else "")

    def build_local_reply(self, user_text: str, reason: str = "") -> str:
        """通义不可用时给出可读、可用的本地推荐。"""
        now = datetime.now()
        season = self._season_name(now.month)
        rec = self._season_recommendations(now.month)
        reason_text = self._clean_reason(reason)
        reason_hint = f"（联网暂不可用：{reason_text}）\n" if reason_text else ""

        if "这个季节" in user_text or "推荐" in user_text or "水果" in user_text:
            return (
                f"{reason_hint}现在是{season}，我给你推荐这几种更应季的水果："
                f"{'、'.join(rec)}。\n"
                "如果你告诉我更看重口感、价格还是耐储运，我可以继续细化。"
            )

        return (
            f"{reason_hint}我是{self.agent_name}，可以帮你做当季水果推荐、"
            "运输耐受性对比和简单营养建议。你可以直接问：这个季节推荐吃什么水果？"
        )

    def _build_messages(self, user_text: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
        """构建兼容 OpenAI Chat Completions 的消息体。"""
        now = datetime.now()
        season = self._season_name(now.month)
        season_list = "、".join(self._season_recommendations(now.month))
        system_prompt = (
            f"你是中文水果顾问助手，名字叫“{self.agent_name}”。"
            "回答要简洁、友好、专业，优先给出可执行建议。"
            "当用户问“这个季节吃什么水果”时，结合中国季节特征回答。"
            f"当前季节参考：{season}，可优先推荐：{season_list}。"
            "不要编造医疗结论，不要输出与水果无关的大段内容。"
        )

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for row in history[-12:]:
            role = str(row.get("role", "")).strip().lower()
            content = str(row.get("content", "")).strip()
            if role not in {"user", "assistant"} or not content:
                continue
            messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_text})
        return messages

    def _request_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url=self.endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = str(exc)
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"网络错误: {exc}") from exc

    def _extract_reply_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"通义返回结构异常: {payload}")

        message = choices[0].get("message", {})
        if not isinstance(message, dict):
            raise ValueError("通义返回缺少 message 字段。")

        content = message.get("content", "")
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                text = item.get("text")
                if isinstance(text, str) and text.strip():
                    chunks.append(text.strip())
            return "\n".join(chunks).strip()

        return ""

    def _clean_reason(self, reason: str) -> str:
        text = str(reason or "").strip().replace("\n", " ")
        if len(text) > 120:
            return text[:117] + "..."
        return text

    def _season_name(self, month: int) -> str:
        if month in {3, 4, 5}:
            return "春季"
        if month in {6, 7, 8}:
            return "夏季"
        if month in {9, 10, 11}:
            return "秋季"
        return "冬季"

    def _season_recommendations(self, month: int) -> list[str]:
        if month in {3, 4, 5}:
            return ["草莓", "枇杷", "菠萝", "樱桃", "桑葚"]
        if month in {6, 7, 8}:
            return ["西瓜", "桃子", "荔枝", "葡萄", "芒果"]
        if month in {9, 10, 11}:
            return ["苹果", "梨", "石榴", "柿子", "柚子"]
        return ["橙子", "砂糖橘", "苹果", "猕猴桃", "甘蔗"]
