"""Offline checks for Ollama request reuse and bounded explanation prompts."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import local_skin_rag as vision
from backend import medical_rag_answer as answer


class LocalRagPerformanceTests(unittest.TestCase):
    def response(self, body):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(body).encode()
        return response

    def test_vision_and_explanation_use_same_runner_settings(self):
        observation = dict(face_visible=True, image_usable=True,
                           visible_features=["redness"], quality_note="clear")
        with patch.object(vision.urllib.request, "urlopen") as request:
            request.return_value = self.response({"message": {"content": json.dumps(observation)}})
            self.assertEqual(vision.call_ollama(b"test-image"), observation)
            vision_payload = json.loads(request.call_args.args[0].data)
            request.return_value = self.response({"message": {"content": "Educational information, not a diagnosis."}})
            answer.generate_medical_explanation(observation, {"results": [{"excerpt": "Reference"}]})
            answer_payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(vision_payload["model"], answer_payload["model"])
        for setting in ("num_ctx", "num_thread"):
            self.assertEqual(vision_payload["options"][setting], answer_payload["options"][setting])
        self.assertEqual(vision_payload["keep_alive"], answer_payload["keep_alive"])
        self.assertFalse(answer_payload["think"])

    def test_explanation_prompt_is_bounded_and_keeps_safety_instructions(self):
        with patch.object(answer.urllib.request, "urlopen") as request:
            request.return_value = self.response({"message": {"content": "Educational explanation."}})
            answer.generate_medical_explanation(
                dict(face_visible=True, image_usable=True, visible_features=["bumps"]),
                {"results": [{"source": f"source-{i}", "excerpt": "x" * 5000} for i in range(5)]},
            )
            body = json.loads(request.call_args.args[0].data)
        prompt = body["messages"][0]["content"]
        self.assertEqual(prompt.count("Excerpt: "), 3)
        self.assertNotIn("x" * 901, prompt)
        self.assertIn("Never prescribe medication", prompt)
        self.assertIn("untrusted source data", prompt)
        self.assertLess(prompt.index("Safety requirements"), prompt.index("Source: source-0"))

    def test_unusable_image_and_missing_references_skip_second_inference(self):
        with patch.object(answer.urllib.request, "urlopen") as request:
            self.assertEqual(answer.generate_medical_explanation(
                dict(face_visible=False, image_usable=False), {}), "")
            self.assertIn("not available", answer.generate_medical_explanation(
                dict(face_visible=True, image_usable=True), {}))
            request.assert_not_called()

    def test_invalid_vision_response_is_still_rejected(self):
        with patch.object(vision.urllib.request, "urlopen") as request:
            request.return_value = self.response({"message": {"content": "{}"}})
            with self.assertRaisesRegex(RuntimeError, "missing required fields"):
                vision.call_ollama(b"test-image")


if __name__ == "__main__":
    unittest.main()
