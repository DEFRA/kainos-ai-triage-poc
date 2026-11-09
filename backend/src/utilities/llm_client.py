"""Synchronous generation with optional attachments through Ollama or Bedrock."""

import base64
import os
from dataclasses import dataclass, fields
from pathlib import Path
from types import TracebackType
from typing import Optional, Sequence, Union
from urllib.parse import urlsplit

import boto3
import yaml
from dotenv import load_dotenv
from openai import NOT_GIVEN, OpenAI
from openai.types.chat import ChatCompletionContentPartParam, ChatCompletionMessageParam


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    api_style: str
    model: str
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    region: Optional[str] = None
    aws_profile: Optional[str] = None
    max_output_tokens: int = 1500
    thinking_level: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("provider", "api_style", "model"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string.")
        for name in ("base_url", "api_key_env", "region", "aws_profile"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(f"{name} must be a non-empty string when set.")
        if (self.provider, self.api_style) not in (
            ("ollama", "openai"),
            ("bedrock", "converse"),
        ):
            raise ValueError(
                "Supported provider/api_style pairs are "
                "ollama/openai and bedrock/converse."
            )
        if type(self.max_output_tokens) is not int or self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be a positive integer.")
        if self.thinking_level is not None and self.thinking_level not in (
            "low", "medium", "high"
        ):
            raise ValueError("thinking_level must be low, medium, or high.")
        if self.base_url is not None:
            url = urlsplit(self.base_url)
            if url.scheme not in ("http", "https") or not url.hostname:
                raise ValueError("base_url must be an absolute HTTP(S) URL.")
        if self.provider == "ollama":
            if self.base_url is None:
                raise ValueError("Ollama requires base_url, including /v1.")
            if self.region is not None or self.aws_profile is not None:
                raise ValueError("region and aws_profile are only for Bedrock.")
        else:
            if self.region is None:
                raise ValueError("Bedrock requires region.")
            if self.api_key_env is not None:
                raise ValueError(
                    "Bedrock uses the AWS credential chain, not api_key_env."
                )

    @classmethod
    def from_yaml(cls, config_path: Union[str, Path]) -> "ModelConfig":
        path = Path(config_path)
        try:
            with path.open(encoding="utf-8") as config_file:
                values = yaml.safe_load(config_file)
        except yaml.YAMLError as error:
            raise ValueError(f"Invalid model YAML in {path}: {error}") from error
        if not isinstance(values, dict):
            raise ValueError(f"Model config {path} must contain a YAML mapping.")
        allowed = {field.name for field in fields(cls)}
        if any(key not in allowed for key in values):
            raise ValueError(
                f"Model config {path} contains unknown fields; "
                f"allowed fields: {', '.join(sorted(allowed))}."
            )
        missing = {"provider", "api_style", "model"} - values.keys()
        if missing:
            raise ValueError(
                f"Model config {path} is missing: {', '.join(sorted(missing))}."
            )
        return cls(**values)


class LLMClient:
    """Load a model config once and submit independent multimodal requests."""

    def __init__(self, config_path: Union[str, Path]) -> None:
        config_path = Path(config_path)
        load_dotenv(config_path.parent.parent / ".env")
        self.config = ModelConfig.from_yaml(config_path)
        self._ollama: Optional[OpenAI] = None
        self._bedrock = None
        if self.config.provider == "ollama":
            api_key_env = self.config.api_key_env or "OLLAMA_API_KEY"
            api_key = os.environ.get(api_key_env)
            if api_key is None or not api_key.strip():
                raise ValueError(
                    f"Environment variable {api_key_env} "
                    "must contain the Ollama endpoint's API key."
                )
            self._ollama = OpenAI(
                base_url=self.config.base_url,
                api_key=api_key,
            )
        else:
            session = boto3.Session(profile_name=self.config.aws_profile)
            self._bedrock = session.client(
                "bedrock-runtime",
                region_name=self.config.region,
                endpoint_url=self.config.base_url,
            )

    def generate(
        self,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        attachments: Optional[Sequence[Union[str, Path]]] = None,
    ) -> str:
        """Return final answer text, raising on empty or incomplete responses.

        Attachments are ordered local file paths. Ollama accepts JPEG, PNG,
        and WebP images; Bedrock also accepts GIF and supported documents.
        PDFs require page rendering before an Ollama call. Prompt loading,
        PDF preparation, and output parsing belong to the caller. Provider
        SDK errors propagate unchanged, including model capability limits.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string.")
        if system_prompt is not None and (
            not isinstance(system_prompt, str) or not system_prompt.strip()
        ):
            raise ValueError("system_prompt must be non-empty when set.")

        if isinstance(attachments, (str, Path)):
            raise ValueError("attachments must be a sequence of file paths.")
        image_formats = {"jpeg", "png", "webp"}
        document_formats = {"pdf", "csv", "doc", "docx", "xls", "xlsx", "html", "txt", "md"}
        allowed_formats = image_formats
        if self.config.provider == "bedrock":
            image_formats = image_formats | {"gif"}
            allowed_formats = image_formats | document_formats
        files = []
        for attachment in attachments or ():
            path = Path(attachment)
            file_format = path.suffix.lower().lstrip(".")
            if file_format == "jpg":
                file_format = "jpeg"
            if file_format not in allowed_formats:
                raise ValueError(
                    f"Unsupported {self.config.provider} attachment format for {path.name!r}. "
                    "Ollama requires images; render PDF pages before sending them. "
                    "Bedrock accepts supported image and document formats."
                )
            content = path.read_bytes()
            if not content:
                raise ValueError(f"Attachment {path.name!r} is empty.")
            files.append((file_format, content))

        if self._ollama is not None:
            messages: list[ChatCompletionMessageParam] = []
            if system_prompt is not None:
                messages.append({"role": "system", "content": system_prompt})
            if files:
                content_parts: list[ChatCompletionContentPartParam] = [
                    {"type": "text", "text": prompt}
                ]
                for file_format, content in files:
                    encoded = base64.b64encode(content).decode("ascii")
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/{file_format};base64,{encoded}",
                        },
                    })
                messages.append({"role": "user", "content": content_parts})
            else:
                messages.append({"role": "user", "content": prompt})
            response = self._ollama.chat.completions.create(
                model=self.config.model,
                messages=messages,
                max_tokens=self.config.max_output_tokens,
                reasoning_effort=(
                    self.config.thinking_level
                    if self.config.thinking_level is not None else NOT_GIVEN
                ),
                stream=False,
            )
            if not response.choices:
                raise RuntimeError("Ollama returned no response choices.")
            choice = response.choices[0]
            if choice.finish_reason != "stop" or choice.message.refusal:
                raise RuntimeError(
                    "Ollama did not return a complete answer "
                    f"(finish_reason={choice.finish_reason})."
                )
            text = choice.message.content
        else:
            if self._bedrock is None:
                raise RuntimeError("The LLM client is not connected.")
            content_blocks = [{"text": prompt}]
            for index, (file_format, content) in enumerate(files, start=1):
                if file_format in image_formats:
                    content_blocks.append({
                        "image": {
                            "format": file_format,
                            "source": {"bytes": content},
                        },
                    })
                else:
                    document = {
                        "format": file_format,
                        "name": f"Attachment {index}",
                        "source": {"bytes": content},
                    }
                    if file_format == "pdf":
                        document["citations"] = {"enabled": True}
                    content_blocks.append({"document": document})
            optional_fields = {}
            if system_prompt is not None:
                optional_fields["system"] = [{"text": system_prompt}]
            if self.config.thinking_level is not None:
                optional_fields["additionalModelRequestFields"] = {
                    "thinking": {"type": "adaptive"},
                    "output_config": {"effort": self.config.thinking_level},
                }
            result = self._bedrock.converse(
                modelId=self.config.model,
                messages=[{"role": "user", "content": content_blocks}],
                inferenceConfig={"maxTokens": self.config.max_output_tokens},
                **optional_fields,
            )
            if result["stopReason"] not in ("end_turn", "stop_sequence"):
                raise RuntimeError(
                    "Bedrock did not return a complete answer "
                    f"(stopReason={result['stopReason']})."
                )
            text_parts = []
            for block in result["output"]["message"]["content"]:
                if "text" in block:
                    text_parts.append(block["text"])
                elif "citationsContent" in block:
                    text_parts.extend(
                        part["text"]
                        for part in block["citationsContent"].get("content", [])
                        if "text" in part
                    )
            text = "".join(text_parts)

        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(
                f"{self.config.provider} returned no final answer text."
            )
        return text

    def close(self) -> None:
        if self._ollama is not None:
            self._ollama.close()
        if self._bedrock is not None:
            self._bedrock.close()

    def __enter__(self) -> "LLMClient":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.close()