"""Test the real agent hooks without starting an SDK worker or cloud session."""
import ast
import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.request import Request


class DocumentVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = SimpleNamespace(
            room_io=SimpleNamespace(linked_participant=SimpleNamespace(identity="human_test")), say=Mock())
        session = self.session
        class Agent:
            def __init__(self, **kwargs):
                self.session = session

            async def on_user_turn_exceeded(self, event):
                session.generate_reply(event.transcript)
        class StopResponse(Exception):
            pass
        self.stop_response = StopResponse
        self.result = {"has_documents": True, "has_matches": True, "answer": "From my-reference.txt: redness may occur."}
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.side_effect = lambda: json.dumps(self.result).encode()
        self.urlopen = Mock(return_value=response)
        path = Path(__file__).resolve().parents[1] / "agent/agent.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        agent_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MateoAssistant")
        namespace = dict(Agent=Agent, StopResponse=StopResponse, Request=Request, urlopen=self.urlopen,
                         asyncio=asyncio, json=json, re=re, logger=logging.getLogger("test-doc-voice"),
                         BACKEND_URL="http://localhost:8000", AGENT_EVENT_KEY="test-key")
        exec(compile(ast.Module(body=[agent_class], type_ignores=[]), str(path), "exec"), namespace)
        self.agent = namespace["MateoAssistant"](room=SimpleNamespace(name="room"))

    async def test_uploaded_documents_bypass_general_llm_and_chat_history(self):
        with self.assertRaises(self.stop_response):
            await self.agent.on_user_turn_completed(None, SimpleNamespace(text_content="What does my document say about redness?"))
        self.session.say.assert_called_once_with(self.result["answer"], allow_interruptions=True, add_to_chat_ctx=False)
        payload = json.loads(self.urlopen.call_args.args[0].data)
        self.assertEqual(payload["participant_id"], "human_test")

    async def test_no_uploads_preserve_normal_conversation(self):
        self.result = {"has_documents": False, "has_matches": False, "answer": "Upload a document first."}
        await self.agent.on_user_turn_completed(None, SimpleNamespace(text_content="Hello"))
        self.session.say.assert_not_called()

    async def test_lookup_failure_does_not_fall_back_to_invented_answer(self):
        self.urlopen.side_effect = OSError("backend unavailable")
        with self.assertRaises(self.stop_response):
            await self.agent.on_user_turn_completed(None, SimpleNamespace(text_content="What does my PDF say about redness?"))
        self.assertIn("cannot access your documents", self.session.say.call_args.args[0])

    async def test_general_question_resumes_conversation_after_document_answer(self):
        with self.assertRaises(self.stop_response):
            await self.agent.on_user_turn_completed(None, SimpleNamespace(text_content="What does my document say about redness?"))
        self.session.say.reset_mock()
        self.urlopen.reset_mock()
        for text in ("What are data structures?", "What's the difference between AI and ML?"):
            await self.agent.on_user_turn_completed(None, SimpleNamespace(text_content=text))
        self.session.say.assert_not_called()
        self.urlopen.assert_not_called()

    async def test_long_general_question_uses_normal_reply(self):
        self.session.generate_reply = Mock()
        text = "Can you explain how arrays and linked lists work?"
        await self.agent.on_user_turn_exceeded(SimpleNamespace(transcript=text))
        self.session.generate_reply.assert_called_once_with(text)
        self.session.say.assert_not_called()
        self.urlopen.assert_not_called()

    async def test_explicit_document_question_reports_no_match(self):
        self.result = {"has_documents": True, "has_matches": False, "answer": "No relevant passages."}
        with self.assertRaises(self.stop_response):
            await self.agent.on_user_turn_completed(None, SimpleNamespace(text_content="What does my uploaded document say about AI?"))
        self.session.say.assert_called_once_with("No relevant passages.", allow_interruptions=True, add_to_chat_ctx=False)

    async def test_speaker_switch_does_not_read_previous_participants_documents(self):
        original = self.urlopen.return_value
        def switch(*args, **kwargs):
            self.session.room_io.linked_participant = SimpleNamespace(identity="human_other")
            return original
        self.urlopen.side_effect = switch
        with self.assertRaises(self.stop_response):
            await self.agent.on_user_turn_completed(None, SimpleNamespace(text_content="What does my PDF say about redness?"))
        self.session.say.assert_not_called()


if __name__ == "__main__":
    unittest.main()
