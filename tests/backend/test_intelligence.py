import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import boto3
import httpx
import yaml
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from openai import AuthenticationError, OpenAI

from backend.src.utilities.llm_client import LLMClient, ModelConfig


class LLMClientTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_path = Path(self.directory.name) / "model.yaml"
        self.requests = []

    def write_config(self, **changes):
        values = {
            "provider": "ollama",
            "api_style": "openai",
            "model": "test-model",
            "base_url": "http://ollama.test/v1",
        }
        values.update(changes)
        self.config_path.write_text(yaml.safe_dump(values), encoding="utf-8")
        return self.config_path

    def ollama_client(self, *, content="Answer", finish_reason="stop", status=200):
        def respond(request):
            self.requests.append(request)
            return httpx.Response(
                status,
                json={
                    "id": "test-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "test-model",
                    "choices": [{
                        "index": 0,
                        "finish_reason": finish_reason,
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "reasoning": "Internal reasoning, not the answer.",
                        },
                    }],
                } if status == 200 else {"error": {"message": "Invalid API key"}},
            )

        sdk = OpenAI(
            api_key="test-key",
            base_url="http://ollama.test/v1",
            http_client=httpx.Client(transport=httpx.MockTransport(respond)),
        )
        self.addCleanup(sdk.close)
        with patch.dict(os.environ, {"OLLAMA_API_KEY": "test-key"}), patch(
            "backend.src.utilities.llm_client.OpenAI", return_value=sdk
        ) as factory:
            client = LLMClient(self.config_path)
        self.addCleanup(client.close)
        return client, factory

    def bedrock_client(self, *, thinking_level=None):
        self.write_config(
            provider="bedrock",
            api_style="converse",
            model="anthropic.test-model",
            base_url=None,
            region="eu-west-2",
            aws_profile="test-profile",
            thinking_level=thinking_level,
        )
        sdk = boto3.Session(
            aws_access_key_id="test",
            aws_secret_access_key="test",
        ).client("bedrock-runtime", region_name="eu-west-2")
        stubber = Stubber(sdk)
        stubber.activate()
        self.addCleanup(stubber.deactivate)
        session = Mock()
        session.client.return_value = sdk
        with patch(
            "backend.src.utilities.llm_client.boto3.Session", return_value=session
        ) as factory:
            client = LLMClient(self.config_path)
        self.addCleanup(client.close)
        factory.assert_called_once_with(profile_name="test-profile")
        session.client.assert_called_once_with(
            "bedrock-runtime", region_name="eu-west-2", endpoint_url=None
        )
        return client, stubber

    def bedrock_request(self):
        return {
            "modelId": "anthropic.test-model",
            "messages": [{"role": "user", "content": [{"text": "Question"}]}],
            "inferenceConfig": {"maxTokens": 1500},
        }

    def bedrock_response(self, *, stop_reason="end_turn", content=None):
        return {
            "output": {
                "message": {
                    "role": "assistant",
                    "content": content if content is not None else [
                        {"reasoningContent": {
                            "reasoningText": {
                                "text": "Internal reasoning.",
                                "signature": "test-signature",
                            }
                        }},
                        {"text": "First "},
                        {"text": "answer"},
                    ],
                }
            },
            "stopReason": stop_reason,
            "usage": {"inputTokens": 5, "outputTokens": 10, "totalTokens": 15},
            "metrics": {"latencyMs": 1},
        }

    def test_ollama_request_omits_thinking_and_returns_only_answer(self):
        self.write_config(max_output_tokens=42)
        client, factory = self.ollama_client()
        self.assertEqual(
            client.generate("Question", system_prompt="Instructions"), "Answer"
        )
        factory.assert_called_once_with(
            base_url="http://ollama.test/v1", api_key="test-key"
        )
        self.assertEqual(self.requests[0].url.path, "/v1/chat/completions")
        self.assertEqual(json.loads(self.requests[0].content), {
            "model": "test-model",
            "messages": [
                {"role": "system", "content": "Instructions"},
                {"role": "user", "content": "Question"},
            ],
            "max_tokens": 42,
            "stream": False,
        })

    def test_ollama_thinking_levels_and_no_implicit_history(self):
        for level in ("low", "medium", "high"):
            with self.subTest(level=level):
                self.write_config(thinking_level=level)
                client, _ = self.ollama_client()
                client.generate("First")
                client.generate("Second")
                request = json.loads(self.requests[-1].content)
                self.assertEqual(request["reasoning_effort"], level)
                self.assertEqual(
                    request["messages"], [{"role": "user", "content": "Second"}]
                )

    def test_ollama_reads_configured_secret_from_environment(self):
        self.write_config(api_key_env="TEST_OLLAMA_API_KEY")
        with patch.dict(os.environ, {"TEST_OLLAMA_API_KEY": "secret"}):
            _, factory = self.ollama_client()
        self.assertEqual(factory.call_args.kwargs["api_key"], "secret")

    def test_missing_or_empty_configured_secret_is_an_error(self):
        self.write_config(api_key_env="TEST_OLLAMA_API_KEY")
        for value in (None, "", " "):
            with self.subTest(value=value), patch.dict(os.environ, clear=True):
                if value is not None:
                    os.environ["TEST_OLLAMA_API_KEY"] = value
                with self.assertRaisesRegex(ValueError, "TEST_OLLAMA_API_KEY"):
                    LLMClient(self.config_path)

    def test_ollama_rejects_incomplete_blocked_or_tool_responses(self):
        self.write_config()
        for reason in ("length", "content_filter", "tool_calls"):
            with self.subTest(reason=reason):
                client, _ = self.ollama_client(finish_reason=reason)
                with self.assertRaisesRegex(RuntimeError, reason):
                    client.generate("Question")

    def test_ollama_rejects_empty_answer(self):
        self.write_config()
        for content in (None, "", " "):
            with self.subTest(content=content):
                client, _ = self.ollama_client(content=content)
                with self.assertRaisesRegex(RuntimeError, "no final answer"):
                    client.generate("Question")

    def test_ollama_authentication_errors_propagate(self):
        self.write_config()
        client, _ = self.ollama_client(status=401)
        with self.assertRaises(AuthenticationError):
            client.generate("Question")

    def test_bedrock_omits_system_and_thinking_when_not_set(self):
        client, stubber = self.bedrock_client()
        stubber.add_response(
            "converse", self.bedrock_response(), self.bedrock_request()
        )
        self.assertEqual(client.generate("Question"), "First answer")
        stubber.assert_no_pending_responses()

    def test_ollama_sends_multiple_images_in_order_without_changing_text_calls(self):
        self.write_config()
        client, _ = self.ollama_client()
        first = Path(self.directory.name) / "page.JPG"
        second = Path(self.directory.name) / "page.png"
        first.write_bytes(b"first image")
        second.write_bytes(b"second image")
        self.assertEqual(client.generate(
            "Question", system_prompt="Instructions", attachments=[str(first), second]
        ), "Answer")
        request = json.loads(self.requests[-1].content)
        self.assertEqual(request["messages"], [
            {"role": "system", "content": "Instructions"},
            {"role": "user", "content": [
                {"type": "text", "text": "Question"},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(b"first image").decode(),
                }},
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(b"second image").decode(),
                }},
            ]},
        ])
        client.generate("Next", attachments=[])
        self.assertEqual(json.loads(self.requests[-1].content)["messages"], [
            {"role": "user", "content": "Next"},
        ])

    def test_ollama_rejects_pdf_before_request(self):
        self.write_config()
        client, _ = self.ollama_client()
        with self.assertRaisesRegex(ValueError, "render PDF pages"):
            client.generate("Question", attachments=["application.pdf"])
        self.assertEqual(self.requests, [])

    def test_missing_empty_and_scalar_attachments_fail_before_request(self):
        self.write_config()
        client, _ = self.ollama_client()
        missing = Path(self.directory.name) / "missing.png"
        with self.assertRaises(FileNotFoundError):
            client.generate("Question", attachments=[missing])
        empty = Path(self.directory.name) / "empty.png"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            client.generate("Question", attachments=[empty])
        for scalar in (str(empty), empty):
            with self.subTest(scalar=scalar), self.assertRaisesRegex(ValueError, "sequence"):
                client.generate("Question", attachments=scalar)
        self.assertEqual(self.requests, [])

    def test_bedrock_sends_images_and_documents_in_one_request(self):
        client, stubber = self.bedrock_client()
        image = Path(self.directory.name) / "site.jpeg"
        pdf = Path(self.directory.name) / "untrusted filename.pdf"
        text_file = Path(self.directory.name) / "evidence.txt"
        image.write_bytes(b"image bytes")
        pdf.write_bytes(b"pdf bytes")
        text_file.write_bytes(b"text bytes")
        expected = self.bedrock_request()
        expected["messages"][0]["content"].extend([
            {"image": {"format": "jpeg", "source": {"bytes": b"image bytes"}}},
            {"document": {
                "format": "pdf", "name": "Attachment 2",
                "source": {"bytes": b"pdf bytes"}, "citations": {"enabled": True},
            }},
            {"document": {
                "format": "txt", "name": "Attachment 3",
                "source": {"bytes": b"text bytes"},
            }},
        ])
        stubber.add_response("converse", self.bedrock_response(), expected)
        self.assertEqual(client.generate(
            "Question", attachments=[image, pdf, text_file]
        ), "First answer")
        stubber.assert_no_pending_responses()

    def test_bedrock_returns_text_from_citation_blocks(self):
        client, stubber = self.bedrock_client()
        stubber.add_response("converse", self.bedrock_response(content=[
            {"text": "Answer: "},
            {"citationsContent": {"content": [{"text": "document evidence"}], "citations": []}},
        ]), self.bedrock_request())
        self.assertEqual(client.generate("Question"), "Answer: document evidence")
        stubber.assert_no_pending_responses()

    def test_bedrock_rejects_unsupported_attachment_format(self):
        client, stubber = self.bedrock_client()
        with self.assertRaisesRegex(ValueError, "Unsupported bedrock attachment"):
            client.generate("Question", attachments=["archive.zip"])
        stubber.assert_no_pending_responses()

    def test_bedrock_system_and_each_adaptive_thinking_level(self):
        for level in ("low", "medium", "high"):
            with self.subTest(level=level):
                client, stubber = self.bedrock_client(thinking_level=level)
                expected = self.bedrock_request()
                expected["system"] = [{"text": "Instructions"}]
                expected["additionalModelRequestFields"] = {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": level},
                }
                stubber.add_response("converse", self.bedrock_response(), expected)
                self.assertEqual(
                    client.generate("Question", system_prompt="Instructions"),
                    "First answer",
                )
                stubber.assert_no_pending_responses()

    def test_bedrock_errors_propagate_including_unsupported_thinking(self):
        client, stubber = self.bedrock_client(thinking_level="high")
        expected = self.bedrock_request()
        expected["additionalModelRequestFields"] = {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": "high"},
        }
        stubber.add_client_error(
            "converse",
            service_error_code="ValidationException",
            service_message="This model does not support adaptive thinking",
            expected_params=expected,
        )
        with self.assertRaisesRegex(ClientError, "does not support"):
            client.generate("Question")

    def test_bedrock_rejects_incomplete_or_blocked_answers(self):
        for reason in ("max_tokens", "guardrail_intervened", "content_filtered", "tool_use"):
            with self.subTest(reason=reason):
                client, stubber = self.bedrock_client()
                stubber.add_response(
                    "converse",
                    self.bedrock_response(stop_reason=reason),
                    self.bedrock_request(),
                )
                with self.assertRaisesRegex(RuntimeError, reason):
                    client.generate("Question")

    def test_bedrock_rejects_missing_final_text(self):
        client, stubber = self.bedrock_client()
        stubber.add_response(
            "converse", self.bedrock_response(content=[]), self.bedrock_request()
        )
        with self.assertRaisesRegex(RuntimeError, "no final answer"):
            client.generate("Question")

    def test_invalid_prompts_fail_before_any_request(self):
        self.write_config()
        client, _ = self.ollama_client()
        for prompt in ("", " ", None, 42):
            with self.subTest(prompt=prompt):
                with self.assertRaisesRegex(ValueError, "prompt"):
                    client.generate(prompt)
        for system_prompt in ("", " ", 42):
            with self.subTest(system_prompt=system_prompt):
                with self.assertRaisesRegex(ValueError, "system_prompt"):
                    client.generate("Question", system_prompt=system_prompt)
        self.assertEqual(self.requests, [])

    def test_invalid_config_values(self):
        invalid = [
            {"provider": "anthropic"},
            {"api_style": "anthropic"},
            {"model": ""},
            {"model": 123},
            {"max_output_tokens": 0},
            {"max_output_tokens": -1},
            {"max_output_tokens": True},
            {"max_output_tokens": 1.5},
            {"thinking_level": "maximum"},
            {"thinking_level": False},
            {"base_url": "not-a-url"},
            {"base_url": None},
            {"base_url": 123},
            {"api_key_env": ""},
            {"region": "eu-west-2"},
            {"aws_profile": "test"},
            {"extra_option": "unknown"},
            {"provider": "bedrock", "api_style": "converse"},
            {
                "provider": "bedrock",
                "api_style": "converse",
                "region": "eu-west-2",
                "api_key_env": "ANTHROPIC_API_KEY",
            },
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                self.write_config(**changes)
                with self.assertRaises(ValueError):
                    ModelConfig.from_yaml(self.config_path)

    def test_yaml_mapping_required_and_missing_fields_reported(self):
        for content in ("", "[]", "text", "model: test", "provider: ["):
            with self.subTest(content=content):
                self.config_path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    ModelConfig.from_yaml(self.config_path)

    def test_missing_config_file_is_not_silently_replaced(self):
        with self.assertRaises(FileNotFoundError):
            ModelConfig.from_yaml(self.config_path)

    def test_context_manager_closes_client_on_failure(self):
        self.write_config()
        client, _ = self.ollama_client()
        with patch.object(client, "close") as close:
            with self.assertRaisesRegex(RuntimeError, "caller failed"):
                with client:
                    raise RuntimeError("caller failed")
            close.assert_called_once_with()

    def test_bundled_configs_are_valid(self):
        root = Path(__file__).resolve().parents[1]
        for provider in ("ollama", "bedrock"):
            config = ModelConfig.from_yaml(
                root / "environment" / "model_configs" / f"{provider}.yaml"
            )
            self.assertEqual(config.provider, provider)
            self.assertIsNone(config.thinking_level)


if __name__ == "__main__":
    unittest.main()
