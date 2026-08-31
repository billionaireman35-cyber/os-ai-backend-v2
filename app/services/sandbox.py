import base64
import logging
from anthropic import AsyncAnthropic
from app.core.config import settings

logger = logging.getLogger(__name__)

SANDBOX_MODEL = "claude-sonnet-5"

_client = None

def get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    return _client


def _extract_file_ids(content_blocks) -> list:
    """Pulls file_id entries out of bash_code_execution_tool_result blocks
    (files Claude wrote to $OUTPUT_DIR during the run)."""
    file_ids = []
    for block in content_blocks:
        if getattr(block, "type", None) == "bash_code_execution_tool_result":
            result = block.content
            if getattr(result, "type", None) == "bash_code_execution_result":
                for output_block in result.content:
                    if getattr(output_block, "file_id", None):
                        file_ids.append(output_block.file_id)
    return file_ids


def _extract_text(content_blocks) -> str:
    parts = [b.text for b in content_blocks if getattr(b, "type", None) == "text"]
    return "\n".join(parts)


async def run_sandbox_task(prompt: str, file_bytes: bytes = None, filename: str = None) -> dict:
    """Runs a single code-execution task in Anthropic's sandboxed
    container. Optionally uploads a file (CSV, etc.) for Claude to
    analyze. Returns the text response plus any files Claude generated,
    base64-encoded for direct return in the API response (no local
    persistence - kept out of scope for this MVP)."""
    client = get_client()

    content = [{"type": "text", "text": prompt}]

    if file_bytes is not None:
        file_object = await client.files.upload(
            file=(filename or "upload.dat", file_bytes)
        )
        content.append({"type": "container_upload", "file_id": file_object.id})

    response = await client.messages.create(
        model=SANDBOX_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
        tools=[{"type": "code_execution_20250825", "name": "code_execution"}],
    )

    text = _extract_text(response.content)
    file_ids = _extract_file_ids(response.content)

    generated_files = []
    for file_id in file_ids:
        try:
            meta = await client.files.retrieve_metadata(file_id)
            download = await client.files.download(file_id)
            raw = await download.aread()
            generated_files.append({
                "filename": meta.filename,
                "content_base64": base64.b64encode(raw).decode("ascii"),
            })
        except Exception as e:
            logger.error(f"Failed to download generated file {file_id}: {e}")

    return {
        "response_text": text,
        "files": generated_files,
        "container_id": response.container.id if response.container else None,
    }
