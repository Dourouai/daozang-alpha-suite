from __future__ import annotations

import json
import os
import struct
import sys
import urllib.request
from base64 import b64decode
from dataclasses import dataclass
from hashlib import sha256
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
        result = self.post_json(
            f"{self.base_url}/im/v1/messages/{message_id}/reply",
            {
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            auth=True,
        )
        code = result.get("code", 0)
        if str(code) != "0":
            raise RuntimeError(f"feishu reply failed code={code} msg={result.get('msg', '')}")
        return result

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
        encrypt_key: str | None = None,
        api_client: FeishuOpenApiClient | None = None,
        webhook_sender: Callable[[str], dict[str, Any]] | None = None,
        allow_webhook_fallback: bool | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.verify_token = verify_token if verify_token is not None else os.environ.get("FEISHU_EVENT_VERIFY_TOKEN", "")
        self.encrypt_key = encrypt_key if encrypt_key is not None else os.environ.get("FEISHU_ENCRYPT_KEY", "")
        self.api_client = api_client or FeishuOpenApiClient()
        self.webhook_sender = webhook_sender or send_text
        self.allow_webhook_fallback = (
            allow_webhook_fallback
            if allow_webhook_fallback is not None
            else os.environ.get("FEISHU_CHAT_ALLOW_WEBHOOK_FALLBACK", "").lower() == "true"
        )

    def handle_event(self, payload: dict[str, Any]) -> FeishuEventResult:
        encrypted_payload = "encrypt" in payload
        try:
            payload = self.decrypt_payload_if_needed(payload)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "feishu_decrypt_error",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "payload_keys": sorted(payload.keys()),
                        "encrypt_len": len(str(payload.get("encrypt", ""))),
                    },
                    ensure_ascii=False,
                ),
                file=sys.stderr,
                flush=True,
            )
            return FeishuEventResult(
                400,
                {
                    "error": "invalid encrypted feishu payload",
                    "detail": f"{type(exc).__name__}: {exc}",
                },
            )
        if "challenge" in payload:
            return FeishuEventResult(200, {"challenge": payload["challenge"]})
        if not self.verify_payload(payload, encrypted_payload=encrypted_payload):
            self.log_event(
                "feishu_verify_token_error",
                {
                    "payload_keys": sorted(payload.keys()),
                    "header_keys": sorted((payload.get("header") or {}).keys()),
                    "has_token": bool(payload.get("token") or (payload.get("header") or {}).get("token")),
                    "encrypted": encrypted_payload,
                },
            )
            return FeishuEventResult(403, {"error": "invalid verify token"})
        message = self.extract_message(payload)
        if message is None:
            event = payload.get("event") or {}
            raw_message = event.get("message") or {}
            header = payload.get("header") or {}
            self.log_event(
                "feishu_chat_event_ignored",
                {
                    "event_type": header.get("event_type") or "",
                    "has_message": bool(raw_message),
                    "message_type": raw_message.get("message_type") or "",
                },
            )
            return FeishuEventResult(200, {"code": 0, "msg": "ignored"})
        if self.is_duplicate_message(message):
            self.log_event(
                "feishu_chat_message_duplicate",
                {
                    "has_message_id": bool(message.message_id),
                    "has_chat_id": bool(message.chat_id),
                },
            )
            return FeishuEventResult(200, {"code": 0, "msg": "duplicate_ignored"})
        response = handle_chat_message(message, self.project_dir)
        self.log_event(
            "feishu_chat_message_received",
            {
                "intent": response.intent,
                "has_message_id": bool(message.message_id),
                "has_chat_id": bool(message.chat_id),
                "has_user_id": bool(message.user_id),
            },
        )
        try:
            delivery = self.send_response(message, response.text)
            self.log_event(
                "feishu_chat_reply_sent",
                {
                    "intent": response.intent,
                    "delivery_code": delivery.get("code", 0),
                    "delivery_msg": delivery.get("msg", ""),
                },
            )
            payload = {"code": 0, "msg": "ok"}
        except Exception as exc:
            self.log_event(
                "feishu_chat_reply_error",
                {
                    "intent": response.intent,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            payload = {
                "code": 0,
                "msg": "handled_without_delivery",
                "send_error": f"{type(exc).__name__}: {exc}",
            }
        return FeishuEventResult(200, payload, response=response)

    def log_event(self, event: str, payload: dict[str, Any]) -> None:
        print(
            json.dumps({"event": event, **payload}, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )

    def verify_payload(self, payload: dict[str, Any], encrypted_payload: bool = False) -> bool:
        if not self.verify_token:
            return True
        header = payload.get("header") or {}
        token = payload.get("token") or header.get("token") or ""
        if not token and encrypted_payload:
            return True
        return token == self.verify_token

    def decrypt_payload_if_needed(self, payload: dict[str, Any]) -> dict[str, Any]:
        encrypted = payload.get("encrypt")
        if not encrypted:
            return payload
        if not self.encrypt_key:
            raise RuntimeError("FEISHU_ENCRYPT_KEY is required for encrypted daocang callbacks")
        return decrypt_feishu_payload(str(encrypted), self.encrypt_key)

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

    def send_response(self, message: ChatMessage, text: str) -> dict[str, Any]:
        if self.api_client.enabled() and message.message_id:
            return self.api_client.reply_text(message.message_id, text)
        if self.allow_webhook_fallback:
            return self.webhook_sender(text)
        raise RuntimeError("FEISHU_APP_ID and FEISHU_APP_SECRET are required for daocang chat replies")

    def is_duplicate_message(self, message: ChatMessage) -> bool:
        if not message.message_id:
            return False
        path = self.project_dir / "data/runtime/feishu_message_dedupe.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        seen = set()
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()[-500:]
            for line in lines:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                seen.add(str(payload.get("message_id") or ""))
        if message.message_id in seen:
            return True
        with path.open("a", encoding="utf-8") as file:
            file.write(
                json.dumps(
                    {
                        "message_id": message.message_id,
                        "chat_id": message.chat_id,
                        "user_id": message.user_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
        return False


def parse_message_content(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(str(raw))
    except json.JSONDecodeError:
        return {"text": str(raw)}


def decrypt_feishu_payload(encrypted: str, encrypt_key: str) -> dict[str, Any]:
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ModuleNotFoundError as exc:
        raise RuntimeError("cryptography is required for encrypted daocang callbacks") from exc

    decoded = decode_base64(encrypted)
    keys = [sha256(encrypt_key.encode("utf-8")).digest()]
    raw_key = encrypt_key.encode("utf-8")
    if len(raw_key) in {16, 24, 32}:
        keys.append(raw_key)

    errors: list[str] = []
    for key in keys:
        candidates = [(key[:16], decoded)]
        if len(decoded) > 16:
            candidates.append((decoded[:16], decoded[16:]))
        for iv, body in candidates:
            try:
                cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
                decryptor = cipher.decryptor()
                padded = decryptor.update(body) + decryptor.finalize()
                return parse_decrypted_feishu_json(pkcs7_unpad(padded))
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
    raise ValueError("; ".join(errors[-4:]))


def decode_base64(value: str) -> bytes:
    normalized = value.strip()
    normalized += "=" * (-len(normalized) % 4)
    try:
        return b64decode(normalized)
    except Exception:
        from base64 import urlsafe_b64decode

        return urlsafe_b64decode(normalized)


def parse_decrypted_feishu_json(value: bytes) -> dict[str, Any]:
    variants = [value]
    if len(value) > 16:
        variants.append(value[16:])
    if len(value) >= 20:
        message_len = struct.unpack(">I", value[16:20])[0]
        if 0 < message_len <= len(value) - 20:
            variants.append(value[20 : 20 + message_len])

    errors: list[str] = []
    for item in variants:
        try:
            return json.loads(item.decode("utf-8"))
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
    raise ValueError("; ".join(errors))


def pkcs7_unpad(value: bytes) -> bytes:
    if not value:
        raise ValueError("empty decrypted payload")
    pad_len = value[-1]
    if pad_len < 1 or pad_len > 16:
        raise ValueError("invalid padding")
    if value[-pad_len:] != bytes([pad_len]) * pad_len:
        raise ValueError("invalid padding bytes")
    return value[:-pad_len]
