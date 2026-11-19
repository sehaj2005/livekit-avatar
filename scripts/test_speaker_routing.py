"""Exercise the agent's real routing callbacks without cloud services or SDK startup."""
import ast
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock


class SpeakerRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        path = Path(__file__).resolve().parents[1] / "agent" / "agent.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        entrypoint = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "avatar_agent")
        names = {"broadcast_floor", "switch_speaker", "on_data_received", "on_active_speakers_changed"}
        functions = [n for n in entrypoint.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        # Recreate the closure containing the actual production callbacks.
        factory = ast.parse(
            "def factory():\n"
            "    active_speaker_id = None\n"
            "    active_human_speakers = ()\n"
            "    manual_floor_until = 0.0\n"
        ).body[0]
        factory.body.extend(functions)
        factory.body.append(ast.parse("return on_data_received, on_active_speakers_changed").body[0])
        module = ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[]))
        self.ready = asyncio.Event()
        self.human = SimpleNamespace(identity="human_test", name="Tester")
        self.room = SimpleNamespace(
            remote_participants={self.human.identity: self.human},
            local_participant=SimpleNamespace(publish_data=AsyncMock()),
        )
        self.session = SimpleNamespace(
            room_io=SimpleNamespace(linked_participant=None, set_participant=Mock()),
            interrupt=Mock(), clear_user_turn=Mock(),
            agent_state="listening",
        )
        self.namespace = dict(
            asyncio=asyncio, json=json, logger=logging.getLogger("test"),
            ctx=SimpleNamespace(room=self.room), session=self.session,
            session_ready_event=self.ready, speaker_lock=asyncio.Lock(),
            pending_floor_requests=set(), FLOOR_TOPIC="mateo-floor",
            time=SimpleNamespace(monotonic=Mock(return_value=100.0)),
        )
        exec(compile(module, str(path), "exec"), self.namespace)
        self.receive, self.active_speakers_changed = self.namespace["factory"]()

    def request(self, **overrides):
        packet = dict(topic="mateo-floor", participant=self.human,
                      data=json.dumps({"type": "request_floor", "identity": "human_spoofed"}).encode())
        packet.update(overrides)
        self.receive(SimpleNamespace(**packet))

    async def settle(self):
        for _ in range(5):
            await asyncio.sleep(0)

    async def test_request_switches_authenticated_sender_and_acknowledges(self):
        self.ready.set()
        self.request()
        await self.settle()
        self.session.room_io.set_participant.assert_called_once_with("human_test")
        payload = self.room.local_participant.publish_data.call_args.args[0]
        self.assertEqual(json.loads(payload)["participant_id"], "human_test")
        self.assertEqual(json.loads(payload)["type"], "floor_changed")

    async def test_startup_request_waits_then_switches(self):
        self.request()
        await self.settle()
        self.session.room_io.set_participant.assert_not_called()
        self.ready.set()
        await self.settle()
        self.session.room_io.set_participant.assert_called_once_with("human_test")

    async def test_duplicate_pending_request_does_not_switch_twice(self):
        self.request()
        self.request()
        self.ready.set()
        await self.settle()
        self.session.room_io.set_participant.assert_called_once()

    async def test_disconnected_sender_is_not_selected(self):
        self.request()
        self.room.remote_participants.clear()
        self.ready.set()
        await self.settle()
        self.session.room_io.set_participant.assert_not_called()

    async def test_other_topics_and_nonhuman_senders_are_ignored(self):
        self.ready.set()
        self.request(topic="other")
        self.request(participant=SimpleNamespace(identity="avatar"))
        self.request(data=b"invalid json")
        await self.settle()
        self.session.room_io.set_participant.assert_not_called()

    async def test_new_active_guest_takes_the_floor_without_clicking(self):
        self.ready.set()
        self.active_speakers_changed([SimpleNamespace(identity="avatar"), self.human])
        await self.settle()
        self.session.room_io.set_participant.assert_called_once_with("human_test")
        self.session.interrupt.assert_not_called()

    async def test_active_guest_does_not_interrupt_mateo_or_another_human(self):
        self.ready.set()
        other = SimpleNamespace(identity="human_other", name="Other guest")
        self.room.remote_participants[other.identity] = other
        self.active_speakers_changed([self.human])
        await self.settle()
        self.active_speakers_changed([other, self.human])
        await self.settle()
        self.session.agent_state = "speaking"
        self.active_speakers_changed([other])
        await self.settle()
        self.session.room_io.set_participant.assert_called_once_with("human_test")

    async def test_explicit_handoff_has_priority(self):
        self.ready.set()
        other = SimpleNamespace(identity="human_other", name="Other guest")
        self.room.remote_participants[other.identity] = other
        self.request()
        await self.settle()
        self.active_speakers_changed([other])
        await self.settle()
        self.session.room_io.set_participant.assert_called_once_with("human_test")


if __name__ == "__main__":
    unittest.main()
