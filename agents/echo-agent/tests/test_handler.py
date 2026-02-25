"""
echo-agent unit tests.

Golden test cases: 3 per invocation mode (sync, streaming, async).
See tests/golden/invoke_cases.json for golden test dataset.

Test strategy:
  sync      — call _sync_echo() directly; assert full response dict.
  streaming — call _stream_echo(), collect all yielded chunks; assert sequence.
  async     — patch invoke.add_async_task / complete_async_task; call
               _async_echo(); assert immediate response and task lifecycle.

Implemented in TASK-020.
"""

import json
import sys
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Add agent root to sys.path so tests can import handler without a package install.
_AGENT_ROOT = Path(__file__).parent.parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))

import handler as agent_handler  # noqa: E402  (must be after sys.path modification)
from handler import _async_echo, _stream_echo, _sync_echo, invoke  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_GOLDEN_PATH = Path(__file__).parent / "golden" / "invoke_cases.json"


@pytest.fixture(scope="module")
def golden() -> dict[str, Any]:
    """Load the golden test dataset once per test session."""
    return json.loads(_GOLDEN_PATH.read_text())


# ---------------------------------------------------------------------------
# Sync mode — 3 golden cases
# ---------------------------------------------------------------------------


class TestSyncEcho:
    """Sync mode: handler returns a dict immediately.

    pyproject.toml invocation_mode = "sync" — Bridge Lambda waits for
    the full response body before returning to the client.
    """

    def test_sync_hello(self, golden: dict[str, Any]) -> None:
        """Golden case sync-hello: simple greeting echoed synchronously."""
        case = next(c for c in golden["sync"] if c["id"] == "sync-hello")
        inp = case["input"]
        result = _sync_echo(inp["prompt"], inp["appid"], inp["tenantId"])
        assert result == case["expected"]

    def test_sync_empty(self, golden: dict[str, Any]) -> None:
        """Golden case sync-empty: empty prompt echoed synchronously."""
        case = next(c for c in golden["sync"] if c["id"] == "sync-empty")
        inp = case["input"]
        result = _sync_echo(inp["prompt"], inp["appid"], inp["tenantId"])
        assert result == case["expected"]

    def test_sync_sentence(self, golden: dict[str, Any]) -> None:
        """Golden case sync-sentence: multi-word sentence echoed synchronously."""
        case = next(c for c in golden["sync"] if c["id"] == "sync-sentence")
        inp = case["input"]
        result = _sync_echo(inp["prompt"], inp["appid"], inp["tenantId"])
        assert result == case["expected"]


# ---------------------------------------------------------------------------
# Streaming mode — 3 golden cases
# ---------------------------------------------------------------------------


class TestStreamEcho:
    """Streaming mode: handler returns a generator; SDK converts to SSE.

    pyproject.toml invocation_mode = "streaming" — Bridge Lambda relays
    SSE chunks as they arrive via Lambda response streaming.

    Each word in the prompt becomes one chunk dict. A final {"done": True}
    chunk signals end-of-stream.
    """

    def _collect(self, prompt: str, appid: str, tenant_id: str) -> list[dict[str, Any]]:
        """Collect all chunks from _stream_echo into a list."""
        return list(_stream_echo(prompt, appid, tenant_id))

    def test_streaming_single_word(self, golden: dict[str, Any]) -> None:
        """Golden case streaming-single-word: one word yields one chunk + done."""
        case = next(c for c in golden["streaming"] if c["id"] == "streaming-single-word")
        inp = case["input"]
        chunks = self._collect(inp["prompt"], inp["appid"], inp["tenantId"])
        assert chunks == case["expected_chunks"]
        # Exactly one word chunk plus one done-sentinel
        word_chunks = [c for c in chunks if "chunk" in c]
        assert len(word_chunks) == case["expected_word_count"]

    def test_streaming_two_words(self, golden: dict[str, Any]) -> None:
        """Golden case streaming-two-words: two words yield two chunks + done."""
        case = next(c for c in golden["streaming"] if c["id"] == "streaming-two-words")
        inp = case["input"]
        chunks = self._collect(inp["prompt"], inp["appid"], inp["tenantId"])
        assert chunks == case["expected_chunks"]
        word_chunks = [c for c in chunks if "chunk" in c]
        assert len(word_chunks) == case["expected_word_count"]

    def test_streaming_empty(self, golden: dict[str, Any]) -> None:
        """Golden case streaming-empty: empty prompt yields empty-string chunk + done."""
        case = next(c for c in golden["streaming"] if c["id"] == "streaming-empty")
        inp = case["input"]
        chunks = self._collect(inp["prompt"], inp["appid"], inp["tenantId"])
        assert chunks == case["expected_chunks"]
        # Even for empty input a done sentinel must be emitted
        assert any(c.get("done") is True for c in chunks)


# ---------------------------------------------------------------------------
# Async mode — 3 golden cases
# ---------------------------------------------------------------------------


class TestAsyncEcho:
    """Async mode: handler returns 202-style response immediately.

    pyproject.toml invocation_mode = "async" — Bridge Lambda returns 202
    to the client. The agent session stays HealthyBusy (via add_async_task)
    while background work runs; /ping reverts to Healthy after
    complete_async_task() is called.

    Mandatory pattern (ADR-010):
      task_id = app.add_async_task(name, metadata)
      # start daemon thread
      # ... do work ...
      app.complete_async_task(task_id)
    """

    def _call_async(
        self,
        prompt: str,
        appid: str,
        tenant_id: str,
        fake_task_id: int = 42,
        timeout: float = 5.0,
    ) -> tuple[dict[str, Any], MagicMock, MagicMock]:
        """Call _async_echo with patched task lifecycle methods.

        Uses a threading.Event so the mock stays active until the daemon
        thread calls complete_async_task — regardless of prompt length.

        Returns (response, mock_add, mock_complete).
        """
        done_event = threading.Event()

        with (
            patch.object(invoke, "add_async_task", return_value=fake_task_id) as mock_add,
            patch.object(invoke, "complete_async_task") as mock_complete,
        ):
            # Signal the event when complete_async_task is called so we
            # know the daemon thread has finished before exiting the with-block.
            mock_complete.side_effect = lambda _tid: done_event.set()
            result = _async_echo(prompt, appid, tenant_id)
            assert done_event.wait(timeout=timeout), (
                f"background task did not complete within {timeout}s"
            )

        return result, mock_add, mock_complete

    def test_async_hello(self, golden: dict[str, Any]) -> None:
        """Golden case async-hello: greeting accepted, correct response keys."""
        case = next(c for c in golden["async"] if c["id"] == "async-hello")
        inp = case["input"]
        result, mock_add, mock_complete = self._call_async(
            inp["prompt"], inp["appid"], inp["tenantId"]
        )
        expected = case["expected"]
        assert result["accepted"] is True
        assert result["echo"] == expected["echo"]
        assert result["mode"] == expected["mode"]
        assert result["appid"] == expected["appid"]
        assert result["tenantId"] == expected["tenantId"]
        assert "task_id" in result  # task_id is dynamic; just check presence

    def test_async_empty(self, golden: dict[str, Any]) -> None:
        """Golden case async-empty: empty prompt accepted immediately."""
        case = next(c for c in golden["async"] if c["id"] == "async-empty")
        inp = case["input"]
        result, mock_add, mock_complete = self._call_async(
            inp["prompt"], inp["appid"], inp["tenantId"]
        )
        assert result["accepted"] is True
        assert result["echo"] == case["expected"]["echo"]
        assert result["mode"] == "async"

    def test_async_sentence(self, golden: dict[str, Any]) -> None:
        """Golden case async-sentence: long prompt accepted; task lifecycle correct."""
        case = next(c for c in golden["async"] if c["id"] == "async-sentence")
        inp = case["input"]
        fake_id = 999
        result, mock_add, mock_complete = self._call_async(
            inp["prompt"], inp["appid"], inp["tenantId"], fake_task_id=fake_id
        )
        # Response must be the immediate 202-style ack
        assert result["accepted"] is True
        assert result["echo"] == case["expected"]["echo"]
        assert result["tenantId"] == case["expected"]["tenantId"]
        # add_async_task must have been called once with the task name
        mock_add.assert_called_once()
        add_args = mock_add.call_args
        assert add_args[0][0] == "echo-background"  # positional name arg
        # complete_async_task must eventually be called with the task_id
        mock_complete.assert_called_once_with(fake_id)


# ---------------------------------------------------------------------------
# Handler dispatch — ensure top-level handler() routes correctly
# ---------------------------------------------------------------------------


class TestHandlerDispatch:
    """Verify the @invoke.entrypoint function dispatches to the right mode."""

    def _make_context(self) -> Any:
        """Return a minimal mock context (session_id not used by handler)."""
        ctx = MagicMock()
        ctx.session_id = "test-session"
        return ctx

    def test_dispatch_defaults_to_sync(self) -> None:
        """handler() with no 'mode' key defaults to sync echo."""
        payload: dict[str, Any] = {"prompt": "hi"}
        result = agent_handler.handler(payload, self._make_context())
        assert isinstance(result, dict)
        assert result["mode"] == "sync"
        assert result["echo"] == "hi"

    def test_dispatch_streaming_returns_generator(self) -> None:
        """handler() with mode=streaming returns a generator."""
        import types

        payload: dict[str, Any] = {"prompt": "stream me", "mode": "streaming"}
        result = agent_handler.handler(payload, self._make_context())
        assert isinstance(result, types.GeneratorType)
        chunks = list(result)
        assert any(c.get("done") is True for c in chunks)

    def test_dispatch_async_returns_dict(self) -> None:
        """handler() with mode=async returns an immediate dict response."""
        payload: dict[str, Any] = {"prompt": "background job", "mode": "async"}
        done_event = threading.Event()
        with (
            patch.object(invoke, "add_async_task", return_value=1) as mock_add,
            patch.object(invoke, "complete_async_task") as mock_complete,
        ):
            mock_complete.side_effect = lambda _tid: done_event.set()
            result = agent_handler.handler(payload, self._make_context())
            assert done_event.wait(timeout=5.0), "background task did not complete"
        assert isinstance(result, dict)
        assert result["accepted"] is True
        mock_add.assert_called_once()
