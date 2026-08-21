from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

from voice_agent_v2.contracts import AudioFormat, StageFailure
from voice_agent_v2.local_lfm import MODEL_ALIAS, LocalLFMProvider
from voice_agent_v2.real_turn import RealTurnController
from voice_agent_v2.realtime import RealtimeSession
from voice_agent_v2.tracer import CancellationToken


def load_runtime():
    rtc = types.ModuleType("livekit.rtc")
    rtc.TrackKind = types.SimpleNamespace(KIND_AUDIO="audio")
    rtc.TrackSource = types.SimpleNamespace(SOURCE_MICROPHONE="microphone")
    api = types.ModuleType("livekit.api")
    livekit = types.ModuleType("livekit")
    livekit.api = api
    livekit.rtc = rtc
    sys.modules["livekit"] = livekit
    sys.modules["livekit.api"] = api
    sys.modules["livekit.rtc"] = rtc
    sys.modules.pop("voice_agent_v2.livekit_runtime", None)
    return importlib.import_module("voice_agent_v2.livekit_runtime")


class ResidentAdapter:
    def __init__(self, process_id: int) -> None:
        self.process_id = process_id
        self.warmups = 0
        self.request_cancellations = 0
        self.destructive_cancellations = 0
        self.closed = 0
        self.observations: list[dict[str, object]] = []

    def start(self, _cancellation: CancellationToken | None = None) -> dict[str, object]:
        return {"process_id": self.process_id}

    def warmup(self, _cancellation: CancellationToken | None = None) -> dict[str, object]:
        self.warmups += 1
        return {"process_id": self.process_id, "discarded": True}

    def cancel_request(self) -> None:
        self.request_cancellations += 1

    def cancel(self) -> None:
        self.destructive_cancellations += 1
        self.process_id = None

    def close(self) -> None:
        self.closed += 1
        self.process_id = None


class ResidentSTT(ResidentAdapter):
    version = "voice-agent.stt.v1"

    def transcribe(self, **_arguments) -> str:
        return "Проверка резидентного процесса."


class ResidentLLM(ResidentAdapter):
    version = "voice-agent.llm-provider.v1"
    provider_mode = "local"
    provider_identity = "local/test-resident-lfm"
    supports_visible_handoff = True
    supports_handoff_abort = True

    def __init__(self, process_id: int) -> None:
        super().__init__(process_id)
        self.complete_next = False
        self.fail_next = False
        self.entered = threading.Event()
        self.reset_count = 0
        self._contexts: dict[str, tuple[dict[str, str], ...]] = {}

    def readiness(self, _cancellation=None) -> dict[str, object]:
        return {"ready": True, "process_id": self.process_id}

    def snapshot_session(self, session_id: str) -> tuple[dict[str, str], ...]:
        return self._contexts.get(session_id, ())

    def restore_session(self, session_id: str, snapshot) -> None:
        self._contexts[session_id] = tuple(snapshot)

    def reset_session(self, session_id: str) -> None:
        self.reset_count += 1
        self._contexts.pop(session_id, None)

    def respond_with_handoff(
        self, *, on_sentence, on_visible_sentence, cancellation, **_arguments
    ) -> str:
        sentence = "Резидентный ответ."
        on_visible_sentence(sentence)
        on_sentence(sentence)
        self.entered.set()
        if self.fail_next:
            self.fail_next = False
            raise StageFailure("llm_provider", "selected_provider_protocol_error")
        if self.complete_next:
            self.complete_next = False
            return sentence
        deadline = time.monotonic() + 1
        while not cancellation.cancelled and time.monotonic() < deadline:
            time.sleep(0.001)
        raise StageFailure("llm_provider", "selected_provider_cancelled")


class ResidentTTS(ResidentAdapter):
    version = "voice-agent.tts.v1"
    output_format = AudioFormat()

    def stream_synthesize(self, *, cancellation, **_arguments):
        if cancellation.cancelled:
            raise StageFailure("tts", "selected_tts_cancelled")
        yield b"\0\0" * 320


class ResidentAudio:
    def __init__(self) -> None:
        self.writes: list[str] = []
        self.cleared: set[str] = set()
        self.stale_writes: list[str] = []

    async def write(self, turn_id: str, _pcm: bytes, cancelled) -> bool:
        if turn_id in self.cleared:
            self.stale_writes.append(turn_id)
        if cancelled():
            return False
        self.writes.append(turn_id)
        return True

    async def clear(self, turn_id: str) -> str:
        self.cleared.add(turn_id)
        return "resident-publication"

    async def abandon(self, _turn_id: str) -> str:
        return "resident-publication"

    async def finish(self, _turn_id: str, cancelled) -> bool:
        return not cancelled()


class MemoryEvents:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def send(self, event: dict[str, object]) -> None:
        self.events.append(event)


class ResidentLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_agent_decision_keeps_resident_stack_ready_while_global_loss_blocks_it(self) -> None:
        runtime = load_runtime()
        runner = object.__new__(runtime.LiveTurnRunner)
        runner.stt = ResidentSTT(3101)
        runner.llm = LocalLFMProvider(connection_factory=lambda *_args, **_kwargs: None)
        runner.tts = ResidentTTS(3103)
        runner.warmup_metadata = {
            "stt": {"discarded": True},
            "lfm_ready": {
                "ready": True,
                "provider_mode": runner.llm.provider_mode,
                "provider_identity": runner.llm.provider_identity,
                "selected_alias": MODEL_ALIAS,
                "external_transfer": False,
                "automatic_fallback": False,
            },
            "tts": {"discarded": True},
        }
        runner._started = True
        runner.llm._set_runtime_health(
            live=True, ready=True, compatible=True, reason_code=None,
        )

        with (
            patch.object(
                runner.llm, "_execute", return_value={"text": "not-json-private"},
            ),
            self.assertRaises(StageFailure) as invalid,
        ):
            runner.llm.agent_decision(request="bounded-request")

        self.assertEqual(invalid.exception.code, "agent_decision_invalid")
        self.assertTrue(runner.ready_for_admission())
        self.assertTrue(all(
            component.readiness == "ready"
            for component in runner.readiness_components()
        ))

        runner.llm._record_runtime_failure("selected_provider_protocol_error")
        self.assertFalse(runner.ready_for_admission())
        llm_health = next(
            component for component in runner.readiness_components()
            if component.component == "selected_llm"
        )
        self.assertEqual(llm_health.liveness, "alive")
        self.assertEqual(llm_health.readiness, "unready")
        self.assertFalse(llm_health.compatible)

    async def test_local_lfm_compatibility_failure_is_alive_unready_and_blocks_admission(self) -> None:
        runtime = load_runtime()
        runner = object.__new__(runtime.LiveTurnRunner)
        runner.stt = ResidentSTT(3101)
        runner.llm = LocalLFMProvider(connection_factory=lambda *_args, **_kwargs: None)
        runner.tts = ResidentTTS(3103)
        runner.warmup_metadata = {
            "stt": {"discarded": True},
            "lfm_ready": {
                "ready": True,
                "provider_mode": runner.llm.provider_mode,
                "provider_identity": runner.llm.provider_identity,
                "selected_alias": MODEL_ALIAS,
                "external_transfer": False,
                "automatic_fallback": False,
            },
            "tts": {"discarded": True},
        }
        runner._started = True
        runner.llm._set_runtime_health(
            live=True,
            ready=False,
            compatible=False,
            reason_code="selected_provider_identity_mismatch",
        )

        llm_health = next(
            component for component in runner.readiness_components()
            if component.component == "selected_llm"
        )
        self.assertEqual(llm_health.liveness, "alive")
        self.assertEqual(llm_health.readiness, "unready")
        self.assertFalse(llm_health.compatible)
        self.assertEqual(
            llm_health.reason_code,
            "selected_provider_identity_mismatch",
        )
        self.assertFalse(runner.ready_for_admission())

    async def test_local_lfm_transport_loss_preserves_verified_compatibility(self) -> None:
        runtime = load_runtime()
        runner = object.__new__(runtime.LiveTurnRunner)
        runner.stt = ResidentSTT(3101)
        runner.llm = LocalLFMProvider(connection_factory=lambda *_args, **_kwargs: None)
        runner.tts = ResidentTTS(3103)
        runner.warmup_metadata = {
            "stt": {"discarded": True},
            "lfm_ready": {
                "ready": True,
                "provider_mode": runner.llm.provider_mode,
                "provider_identity": runner.llm.provider_identity,
                "selected_alias": MODEL_ALIAS,
                "external_transfer": False,
                "automatic_fallback": False,
            },
            "tts": {"discarded": True},
        }
        runner._started = True
        runner.llm._set_runtime_health(
            live=False,
            ready=False,
            compatible=True,
            reason_code="local_lfm_transport_error",
        )

        llm_health = next(
            component for component in runner.readiness_components()
            if component.component == "selected_llm"
        )
        self.assertEqual(llm_health.liveness, "dead")
        self.assertEqual(llm_health.readiness, "unready")
        self.assertTrue(llm_health.compatible)
        self.assertEqual(llm_health.reason_code, "local_lfm_transport_error")
        self.assertFalse(runner.ready_for_admission())

    async def test_thirty_interruptions_preserve_warmed_processes_and_future_turns(self) -> None:
        runtime = load_runtime()
        runner = object.__new__(runtime.LiveTurnRunner)
        runner.stt = ResidentSTT(3101)
        runner.llm = ResidentLLM(3102)
        runner.tts = ResidentTTS(3103)
        runner.controller = RealTurnController(runner.stt, runner.llm, runner.tts)
        runner._snapshots = {}
        runner._startup_cancellation = CancellationToken()
        runner._start_lock = threading.Lock()
        runner.warmup_metadata = None
        runner._started = False

        runner.start()
        initial_ids = (runner.stt.process_id, runner.llm.process_id, runner.tts.process_id)
        self.assertEqual((runner.stt.warmups, runner.llm.warmups, runner.tts.warmups), (1, 1, 1))

        events = MemoryEvents()
        audio = ResidentAudio()
        session = RealtimeSession(
            session_id="session-resident",
            runner=runner,
            event_sink=events,
            audio_sink=audio,
        )
        first_signal_ms: list[float] = []
        for _index in range(30):
            runner.llm.entered.clear()
            started = time.monotonic()
            turn_id = await session.submit_utterance(b"\0\0" * 320)
            deadline = time.monotonic() + 0.5
            while turn_id not in audio.writes and time.monotonic() < deadline:
                await asyncio.sleep(0.001)
            self.assertIn(turn_id, audio.writes)
            first_signal_ms.append((time.monotonic() - started) * 1000)
            self.assertTrue(await asyncio.to_thread(runner.llm.entered.wait, 0.5))
            await session.interrupt()
            await asyncio.wait_for(session.wait_for_cleanup(), 0.5)
            self.assertEqual(
                (runner.stt.process_id, runner.llm.process_id, runner.tts.process_id),
                initial_ids,
            )

        runner.llm.fail_next = True
        failed_turn = await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)
        self.assertEqual(events.events[-1]["type"], "turn.failed")
        self.assertEqual(events.events[-1]["turn_id"], failed_turn)
        self.assertEqual(
            (runner.stt.process_id, runner.llm.process_id, runner.tts.process_id),
            initial_ids,
        )

        reconnect = json.dumps({
            "schema_version": "voice-agent.client-control.v1",
            "session_id": "session-resident",
            "stream_epoch": 1,
            "sequence": 1,
            "type": "client.reconnected",
        }).encode()
        self.assertTrue(await session.handle_client_control(reconnect))
        self.assertTrue(await session.handle_client_control(reconnect))
        self.assertEqual(runner.llm.reset_count, 1)
        self.assertEqual(
            (runner.stt.process_id, runner.llm.process_id, runner.tts.process_id),
            initial_ids,
        )

        runner.llm.complete_next = True
        completed_turn = await session.submit_utterance(b"\0\0" * 320)
        await asyncio.wait_for(session.wait_for_cleanup(), 0.5)
        self.assertEqual(events.events[-1]["type"], "turn.completed")
        self.assertIn(completed_turn, audio.writes)
        self.assertFalse(audio.stale_writes)
        self.assertLess(max(first_signal_ms), max(250.0, first_signal_ms[0] + 100.0))
        self.assertEqual((runner.stt.warmups, runner.llm.warmups, runner.tts.warmups), (1, 1, 1))
        self.assertEqual(
            (runner.stt.destructive_cancellations, runner.llm.destructive_cancellations,
             runner.tts.destructive_cancellations),
            (0, 0, 0),
        )
        self.assertEqual(runner.stt.request_cancellations, 0)

        runner.close("session-resident")
        self.assertEqual(
            (runner.stt.process_id, runner.llm.process_id, runner.tts.process_id),
            initial_ids,
        )
        runner.shutdown()
        self.assertEqual(
            (runner.stt.process_id, runner.llm.process_id, runner.tts.process_id),
            (None, None, None),
        )


if __name__ == "__main__":
    unittest.main()
