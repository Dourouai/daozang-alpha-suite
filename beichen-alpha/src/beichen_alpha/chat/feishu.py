from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from beichen_alpha.chat.router import ChatMessage, ChatResponse, handle_chat_message
from beichen_alpha.notifiers import send_text


@dataclass(frozen=True)
class FeishuEventResult:
    status_code: int
    payload: dict[str, Any]
    response: ChatResponse | None = None


class FeishuOpenApiClient:
    def __init__(
        self,
        app_id: str | None = None,
        app_secret: str | None = None,
        base_url: str = "https://open.feishu.cn/open-apis",
    ) -> None:
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self.base_url = base_url.rstrip("/")
        self._tenant_access_token: str = ""

    def enabled(self) -> bool:
        return bool(self.app_id and self.app_secret)

    def get_tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }
        result = self.post_json(
            f"{self.base_url}/auth/v3/tenant_access_token/internal",
            payload,
            auth=False,
        )
        token = str(result.get("tenant_access_token") or "")
        if not token:
            raise RuntimeError(f"failed to fetch tenant_access_token: {result}")
        self._tenant_access_token = token
        return token

    def reply_text(self, message_id: str, text: str) -> dict[str, Any]:
        return self.post_json(
            f"{self.base_url}/im/v1/messages/{message_id}/reply",
            {
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            auth=True,
        )

    def post_json(self, url: str, payload: dict[str, Any], auth: bool) -> dict[str, Any]:
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if auth:
            headers["Authorization"] = f"Bearer {self.get_tenant_access_token()}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)


class FeishuEventAdapter:
    def __init__(
        self,
        project_dir: str | Path = ".",
        verify_token: str | None = None,
        api_client: FeishuOpenApiClient | None = None,
        webhook_sender: Callable[[str], dict[str, Any]] | None = None,
        allow_webhook_fallback: bool | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.verify_token = verify_token if verify_token is not None else os.environ.get("FEISHU_EVENT_VERIFY_TOKEN", "")
        self.api_client = api_client or FeishuOpenApiClient()
        self.webhook_sender = webhook_sender or send_text
        self.allow_webhook_fallback = (
            allow_webhook_fallback
            if allow_webhook_fallback is not None
            else os.environ.get("FEISHU_CHAT_ALLOW_WEBHOOK_FALLBACK", "").lower() == "true"
        )

    def handle_event(self, payload: dict[str, Any]) -> FeishuEventResult:
        if "challenge" in payload:
            return FeishuEventResult(200, {"challenge": payload["challenge"]})
        if not self.verify_payload(payload):
            return FeishuEventResult(403, {"error": "invalid verify token"})
        message = self.extract_message(payload)
        if message is None:
            return FeishuEventResult(200, {"code": 0, "msg": "ignored"})
        response = handle_chat_message(message, self.project_dir)
        try:
            self.send_response(message, response.text)
            payload = {"code": 0, "msg": "ok"}
        except Exception as exc:
            payload = {
                "code": 0,
                "msg": "handled_without_delivery",
                "send_error": f"{type(exc).__name__}: {exc}",
            }
        return FeishuEventResult(200, payload, response=response)

    def verify_payload(self, payload: dict[str, Any]) -> bool:
        if not self.verify_token:
            return True
        header = payload.get("header") or {}
        token = payload.get("token") or header.get("token") or ""
        return token == self.verify_token

    def extract_message(self, payload: dict[str, Any]) -> ChatMessage | None:
        event = payload.get("event") or {}
        message = event.get("message") or {}
        if not message:
            return None
        message_type = str(message.get("message_type") or "")
        if message_type and message_type != "text":
            return None
        content = parse_message_content(message.get("content"))
        sender = event.get("sender") or {}
        sender_id = sender.get("sender_id") or {}
        return ChatMessage(
            text=str(content.get("text") or ""),
            user_id=str(sender_id.get("open_id") or sender_id.get("user_id") or ""),
            chat_id=str(message.get("chat_id") or ""),
            message_id=str(message.get("message_id") or ""),
        )

    def send_response(self, message: ChatMessage, text: str) -> None:
        if self.api_client.enabled() and message.message_id:
            self.api_client.reply_text(message.message_id, text)
            return
        if self.allow_webhook_fallback:
            self.webhook_sender(text)
            return
        raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required for daocang chat replies")


def parse_message_content(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return {"text": str(raw)}
