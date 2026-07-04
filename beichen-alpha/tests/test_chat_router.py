import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from beichen_alpha.chat import ChatMessage, FeishuEventAdapter, handle_chat_message


class ChatRouterLlmTest(unittest.TestCase):
    def test_chat_router_uses_custom_llm_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            response = handle_chat_message(
                ChatMessage("你可以分析股票吗"),
                project_dir=root,
                llm_responder=lambda text, project_dir: f"可以分析：{text} @ {project_dir.name}",
            )

        self.assertEqual(response.intent, "llm_chat")
        self.assertIn("可以分析：你可以分析股票吗", response.text)

    def test_chat_router_reports_llm_not_configured(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.dict(os.environ, {"BEICHEN_CHAT_LLM_ENABLED": ""}, clear=False):
                response = handle_chat_message(ChatMessage("你可以分析股票吗"), project_dir=root)

        self.assertEqual(response.intent, "fallback")
        self.assertIn("自定义对话还没有启用大模型", response.text)
        self.assertNotIn("config/local.env", response.text)
        self.assertNotIn("BEICHEN_LLM_API_KEY", response.text)


class FeishuDedupeTest(unittest.TestCase):
    def test_feishu_adapter_ignores_duplicate_message_id(self):
        sent: list[tuple[str, str]] = []

        class FakeClient:
            def enabled(self):
                return True

            def reply_text(self, message_id: str, text: str):
                sent.append((message_id, text))
                return {"code": 0, "msg": "ok"}

        payload = {
            "event": {
                "message": {
                    "message_id": "om_duplicate",
                    "chat_id": "oc_test",
                    "message_type": "text",
                    "content": json.dumps({"text": "你可以分析股票吗"}, ensure_ascii=False),
                },
                "sender": {"sender_id": {"open_id": "ou_test"}},
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = FeishuEventAdapter(project_dir=tmpdir, api_client=FakeClient())
            first = adapter.handle_event(payload)
            second = adapter.handle_event(payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.payload["msg"], "duplicate_ignored")
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
