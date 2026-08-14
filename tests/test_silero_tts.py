from __future__ import annotations

import queue
import threading
import time
import types
import unittest

from voice_agent_v2.audio import (
    INPUT_AUDIO_FORMAT,
    OUTPUT_DELIVERY_BLOCK_BYTES,
    OUTPUT_FRAME_BYTES,
    OUTPUT_MEDIA_MAX_BYTES,
    TTS_OUTPUT_AUDIO_FORMAT,
)
from voice_agent_v2.contracts import (
    LLM_VERSION,
    STT_VERSION,
    TTS_V2_VERSION,
    StageFailure,
    TTSRequestKey,
)
from voice_agent_v2.process_adapter import AdapterRequestError
from voice_agent_v2.silero_tts import (
    MODEL_IDENTITY,
    MODEL_SHA256,
    MODEL_SIZE,
    SileroWorkerPool,
)
from voice_agent_v2.real_turn import RealTurnController
from voice_agent_v2.tracer import CancellationToken
from voice_agent_v2.tts_text import (
    HARD_MAX_CHARS,
    RussianTTSSegmenter,
    shape_russian_tts,
)


class FakeChild:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.running = True

    def poll(self):
        return None if self.running else 0


class ProcessCoordinator:
    def __init__(self) -> None:
        self.created: list[FakeProcess] = []
        self.gates: dict[str, threading.Event] = {}
        self.entered: queue.Queue[tuple[str, str]] = queue.Queue()
        self.fail_requests: set[str] = set()
        self._pid = 7000

    def factory(self, worker_id, _log_path, _environment):
        self._pid += 1
        process = FakeProcess(worker_id, self._pid, self)
        self.created.append(process)
        return process


class FakeProcess:
    def __init__(self, worker_id: str, pid: int, coordinator: ProcessCoordinator) -> None:
        self.worker_id = worker_id
        self.process = FakeChild(pid)
        self.coordinator = coordinator

    def start(self, _timeout_seconds: float) -> dict:
        return {
            "event": "ready",
            "protocol_version": "voice-agent.silero-worker.v1",
            "worker_id": self.worker_id,
            "pid": self.process.pid,
            "model_identity": MODEL_IDENTITY,
            "model_size_bytes": MODEL_SIZE,
            "model_sha256": MODEL_SHA256,
            "speaker": "kseniya",
            "native_sample_rates_hz": [8_000, 24_000, 48_000],
            "output_audio": TTS_OUTPUT_AUDIO_FORMAT.as_dict(),
            "complete_waveform": True,
            "cooperative_cancel": False,
            "max_parallel_requests": 1,
            "intraop_threads": 2,
            "interop_threads": 1,
        }

    def stream(self, request: dict, _timeout_seconds: float):
        if request.get("protocol_version") != "voice-agent.silero-worker.v1":
            raise AssertionError("unexpected worker protocol")
        request_id = request["request_id"]
        key = request["key"]
        gate = self.coordinator.gates.get(request_id)
        if gate is not None:
            self.coordinator.entered.put((self.worker_id, request_id))
            if not gate.wait(2):
                raise RuntimeError("test gate timed out")
        if request_id in self.coordinator.fail_requests:
            raise AdapterRequestError("injected")
        pcm = bytes([self.process.pid % 251, 0]) * 960
        yield {
            "protocol_version": "voice-agent.silero-worker.v1",
            "event": "chunk",
            "request_id": request_id,
            "key": key,
            "sequence": 0,
            "bytes": len(pcm),
            "pcm_base64": __import__("base64").b64encode(pcm).decode("ascii"),
        }
        yield {
            "protocol_version": "voice-agent.silero-worker.v1",
            "event": "final",
            "request_id": request_id,
            "key": key,
            "status": "success",
            "model_identity": MODEL_IDENTITY,
            "speaker": "kseniya",
            "encoding": "pcm_s16le",
            "sample_rate_hz": 48_000,
            "channels": 1,
            "sample_width_bytes": 2,
            "chunk_count": 1,
            "audio_bytes": len(pcm),
            "samples": len(pcm) // 2,
            "duration_ms": 20.0,
            "latency_ms": 1.0,
            "terminal_count": 1,
        }

    def close(self) -> None:
        self.process.running = False


class RussianSegmentationTests(unittest.TestCase):
    def test_sentence_clause_decimal_abbreviation_and_final_tail(self) -> None:
        segmenter = RussianTTSSegmenter()
        text = (
            "Температура равна 3,14 градуса, поэтому обычная пауза здесь подходит; "
            "затем следует второе законченное предложение. Короткий хвост"
        )
        emitted = segmenter.feed(text)
        emitted += segmenter.finish()

        self.assertEqual(" ".join(emitted), text)
        self.assertTrue(all(0 < len(segment) <= HARD_MAX_CHARS for segment in emitted))
        self.assertTrue(any("3,14" in segment for segment in emitted))
        self.assertGreaterEqual(len(emitted), 2)

        abbreviation = RussianTTSSegmenter()
        pieces = abbreviation.feed(
            "В 09:30 файл т. е. уже готов, а значение 2.5 остаётся неизменным."
        ) + abbreviation.finish()
        self.assertNotIn("В 09:", pieces)
        self.assertEqual(" ".join(pieces), "В 09:30 файл т. е. уже готов, а значение 2.5 остаётся неизменным.")

    def test_no_ordinary_whitespace_cut_and_hard_cap_never_splits_word(self) -> None:
        segmenter = RussianTTSSegmenter()
        first = "слово " * 39
        self.assertEqual(segmenter.feed(first), ())
        emitted = segmenter.feed("ещё слово")
        self.assertEqual(len(emitted), 1)
        self.assertLessEqual(len(emitted[0]), HARD_MAX_CHARS)
        self.assertFalse(emitted[0].endswith("слов"))

        impossible = RussianTTSSegmenter()
        with self.assertRaises(StageFailure) as failure:
            impossible.feed("а" * (HARD_MAX_CHARS + 1))
        self.assertEqual(failure.exception.code, "tts_segment_has_no_safe_boundary")

    def test_shaping_is_hidden_deterministic_plain_text(self) -> None:
        visible = "Сегодня 14.08.2026, встреча в 09:30. Я открою замок; PDF готов."
        first = shape_russian_tts(visible)
        second = shape_russian_tts(visible)
        self.assertEqual(first, second)
        self.assertEqual(first.visible_text, visible)
        self.assertIn("четырнадцатое августа две тысячи двадцать шестого года", first.synthesis_text)
        self.assertIn("зам+ок", first.synthesis_text)
        self.assertIn("Пи-Ди-Эф", first.synthesis_text)
        self.assertNotEqual(first.synthesis_text, visible)
        self.assertEqual(shape_russian_tts("Все готовы.").synthesis_text, "Все готовы.")
        with self.assertRaises(StageFailure) as failure:
            shape_russian_tts("<speak>Секрет.</speak>")
        self.assertEqual(failure.exception.code, "tts_plain_text_required")


class FakeSTT:
    version = STT_VERSION

    def transcribe(self, **_arguments) -> str:
        return "Проверочный вопрос."


class FakeVisibleLLM:
    version = LLM_VERSION
    provider_mode = "local"
    provider_identity = "deterministic-local"
    supports_visible_handoff = True
    visible_handoff_is_cumulative = True

    def __init__(self, pieces: tuple[str, ...]) -> None:
        self.pieces = pieces

    def respond_with_handoff(self, *, on_sentence, on_visible_sentence, **_arguments) -> str:
        cumulative = ""
        for piece in self.pieces:
            cumulative += piece
            on_visible_sentence(cumulative)
            on_sentence(piece)
        return cumulative


class FakeTTSV2:
    version = TTS_V2_VERSION
    identity = MODEL_IDENTITY
    speaker = "kseniya"
    output_format = TTS_OUTPUT_AUDIO_FORMAT
    capabilities = {"cooperative_cancel": False}

    def __init__(self, fail_segment: int | None = None) -> None:
        self.fail_segment = fail_segment
        self.calls: list[dict[str, object]] = []

    def create_turn_budget(self):
        return types.SimpleNamespace()

    def stream_synthesize(self, **arguments):
        recorded = dict(arguments)
        recorded["synthesis_text"] = shape_russian_tts(str(arguments["text"])).synthesis_text
        self.calls.append(recorded)
        if arguments["segment_index"] == self.fail_segment:
            raise StageFailure("tts", "silero_synthesis_failed")
        yield b"\0\0" * 2_880

    def invalidate_turn(self, *_arguments) -> None:
        return None


class RealTurnTTSV2Tests(unittest.TestCase):
    def pieces(self) -> tuple[str, ...]:
        return (
            "На двери висит замок, а рядом находится старинный замок.",
            " Сегодня 14.08.2026 года встреча начнётся в 09:30.",
        )

    def test_visible_history_is_original_while_v2_segments_are_correlated_and_shaped(self) -> None:
        tts = FakeTTSV2()
        controller = RealTurnController(FakeSTT(), FakeVisibleLLM(self.pieces()), tts)
        observed_audio: list[tuple[int, bytes]] = []
        result = controller.run_turn(
            session_id="session-test",
            turn_id="turn-test",
            input_pcm=b"\0\0" * 320,
            stream_epoch=3,
            turn_generation=7,
            request_id="request-test",
            audio_observer=lambda index, pcm: observed_audio.append((index, pcm)),
        )

        self.assertEqual(result.terminal_event["type"], "turn.completed")
        self.assertTrue(all(event["schema_version"] == "voice-agent.event-envelope.v2" for event in result.events))
        self.assertTrue(all(event["stream_epoch"] == 3 for event in result.events))
        self.assertTrue(all(event["turn_generation"] == 7 for event in result.events))
        self.assertTrue(all(event["request_id"] == "request-test" for event in result.events))
        visible = [event["payload"]["response"] for event in result.events if event["type"] == "llm.visible"]
        self.assertEqual(visible[-1], "".join(self.pieces()))
        self.assertNotIn("+", visible[-1])
        self.assertEqual([call["segment_index"] for call in tts.calls], list(range(len(tts.calls))))
        self.assertTrue(all(call["stream_epoch"] == 3 for call in tts.calls))
        self.assertTrue(any("зам+ок" in str(call["synthesis_text"]) for call in tts.calls))
        self.assertEqual(sum(len(pcm) for _index, pcm in observed_audio), result.terminal_event["payload"]["output_bytes"])

    def test_tts_failure_keeps_visible_text_and_accepted_prefix_without_completion(self) -> None:
        tts = FakeTTSV2(fail_segment=1)
        controller = RealTurnController(FakeSTT(), FakeVisibleLLM(self.pieces()), tts)
        observed_audio: list[bytes] = []
        result = controller.run_turn(
            session_id="session-test",
            turn_id="turn-test",
            input_pcm=b"\0\0" * 320,
            stream_epoch=1,
            turn_generation=1,
            request_id="request-test",
            audio_observer=lambda _index, pcm: observed_audio.append(pcm),
        )

        self.assertEqual(result.terminal_event["type"], "turn.failed")
        self.assertEqual(result.terminal_event["payload"]["code"], "silero_synthesis_failed")
        self.assertNotIn("turn.completed", [event["type"] for event in result.events])
        self.assertTrue(observed_audio)
        visible = [event["payload"]["response"] for event in result.events if event["type"] == "llm.visible"]
        self.assertEqual(visible[-1], "".join(self.pieces()))

    def test_visible_decimal_prefix_remains_byte_exact(self) -> None:
        pieces = ("Значение равно 3.", "14 и не меняется.")
        controller = RealTurnController(FakeSTT(), FakeVisibleLLM(pieces), FakeTTSV2())
        result = controller.run_turn(
            session_id="session-test",
            turn_id="turn-test",
            input_pcm=b"\0\0" * 320,
            request_id="request-test",
        )

        expected = "".join(pieces)
        visible = [
            event["payload"]["response"]
            for event in result.events
            if event["type"] == "llm.visible"
        ]
        self.assertEqual(visible[-1], expected)
        self.assertEqual(
            next(event for event in result.events if event["type"] == "llm.final")[
                "payload"
            ]["response"],
            expected,
        )
        self.assertTrue(all("3. 14" not in prefix for prefix in visible))


class SileroPoolTests(unittest.TestCase):
    def pool(self, coordinator: ProcessCoordinator, *, wait: float = 0.75) -> SileroWorkerPool:
        pool = SileroWorkerPool(
            process_factory=coordinator.factory,
            verify_runtime=lambda: {"verified": True},
            capacity_wait_seconds=wait,
        )
        pool.start()
        return pool

    @staticmethod
    def key(turn: int, segment: int = 0) -> TTSRequestKey:
        return TTSRequestKey(
            "session-test", 1, f"turn-{turn:08d}", turn,
            f"request-{turn:08d}", segment,
        )

    def test_concurrent_start_keeps_direct_callers_unready_until_both_warm(self) -> None:
        coordinator = ProcessCoordinator()
        warmup_release = threading.Event()
        coordinator.gates["warmup-request-1"] = warmup_release
        pool = SileroWorkerPool(
            process_factory=coordinator.factory,
            verify_runtime=lambda: {"verified": True},
        )
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def start() -> None:
            try:
                results.append(pool.start())
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=start) for _ in range(2)]
        try:
            threads[0].start()
            worker_id, request_id = coordinator.entered.get(timeout=1)
            self.assertEqual((worker_id, request_id), ("silero-1", "warmup-request-1"))
            threads[1].start()
            time.sleep(0.02)

            self.assertEqual(pool.ready_count, 0)
            self.assertEqual({slot["state"] for slot in pool.slots}, {"warming"})
            with self.assertRaises(StageFailure) as not_ready:
                pool.synthesize(self.key(1), "Нельзя отправлять до прогрева.", None)
            self.assertEqual(not_ready.exception.code, "silero_pool_not_ready")
            self.assertEqual(len(coordinator.created), 2)

            warmup_release.set()
            for thread in threads:
                thread.join(1)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(pool.ready_count, 2)
            self.assertEqual(len(coordinator.created), 2)
            self.assertEqual(len([p for p in coordinator.created if p.process.poll() is None]), 2)
        finally:
            warmup_release.set()
            for thread in threads:
                thread.join(1)
            pool.close()

    def test_exactly_two_warmed_workers_and_rate_specific_bounds(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        try:
            self.assertEqual(len(coordinator.created), 2)
            self.assertEqual(pool.ready_count, 2)
            self.assertEqual(len(set(pool.process_ids)), 2)
            self.assertEqual(INPUT_AUDIO_FORMAT.sample_rate_hz, 16_000)
            self.assertEqual(TTS_OUTPUT_AUDIO_FORMAT.sample_rate_hz, 48_000)
            self.assertEqual(OUTPUT_FRAME_BYTES, 1_920)
            self.assertEqual(OUTPUT_DELIVERY_BLOCK_BYTES, 5_760)
            self.assertEqual(OUTPUT_MEDIA_MAX_BYTES, 17_280_000)
        finally:
            pool.close()

    def test_same_turn_is_serial_while_obsolete_and_current_overlap(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        try:
            first_gate = threading.Event()
            second_gate = threading.Event()
            coordinator.gates["request-00000001"] = first_gate
            coordinator.gates["request-00000002"] = second_gate
            old_token = CancellationToken()
            results: dict[str, object] = {}

            def old() -> None:
                try:
                    results["old"] = pool.synthesize(self.key(1), "Старый ответ.", old_token)
                except StageFailure as error:
                    results["old"] = error.code

            def current() -> None:
                results["current"] = pool.synthesize(self.key(2), "Новый ответ.", None)

            old_thread = threading.Thread(target=old)
            old_thread.start()
            old_worker, _ = coordinator.entered.get(timeout=1)
            current_thread = threading.Thread(target=current)
            current_thread.start()
            current_worker, _ = coordinator.entered.get(timeout=1)
            self.assertNotEqual(old_worker, current_worker)
            old_token.cancel()
            first_gate.set()
            second_gate.set()
            old_thread.join(1)
            current_thread.join(1)
            self.assertEqual(results["old"], "selected_tts_cancelled")
            self.assertIsInstance(results["current"], tuple)
            self.assertEqual(pool.counters["stale_after_worker"], 1)
            pool.release_turn("session-test", 1, "turn-00000001", 1)
            pool.release_turn("session-test", 1, "turn-00000002", 2)
            self.assertEqual(pool.retained_turn_count, 0)

            serial_one = threading.Event()
            serial_two = threading.Event()
            coordinator.gates["serial-one"] = serial_one
            coordinator.gates["serial-two"] = serial_two
            first_key = TTSRequestKey("session-test", 1, "turn-serial", 9, "serial-one", 0)
            second_key = TTSRequestKey("session-test", 1, "turn-serial", 9, "serial-two", 1)
            threads = [
                threading.Thread(target=pool.synthesize, args=(first_key, "Первый.", None)),
                threading.Thread(target=pool.synthesize, args=(second_key, "Второй.", None)),
            ]
            threads[0].start()
            coordinator.entered.get(timeout=1)
            threads[1].start()
            with self.assertRaises(queue.Empty):
                coordinator.entered.get(timeout=0.05)
            serial_one.set()
            _worker, request_id = coordinator.entered.get(timeout=1)
            self.assertEqual(request_id, "serial-two")
            serial_two.set()
            for thread in threads:
                thread.join(1)
            pool.release_turn("session-test", 1, "turn-serial", 9)
            self.assertEqual(pool.retained_turn_count, 0)
            self.assertEqual(len(coordinator.created), 2)
        finally:
            pool.close()

    def test_both_busy_waits_at_most_750ms_without_third_or_retry(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        gates = [threading.Event(), threading.Event()]
        coordinator.gates["request-00000001"] = gates[0]
        coordinator.gates["request-00000002"] = gates[1]
        workers = [
            threading.Thread(target=pool.synthesize, args=(self.key(1), "Один.", None)),
            threading.Thread(target=pool.synthesize, args=(self.key(2), "Два.", None)),
        ]
        try:
            for worker in workers:
                worker.start()
                coordinator.entered.get(timeout=1)
            started = time.monotonic()
            with self.assertRaises(StageFailure) as failure:
                pool.synthesize(self.key(3), "Три.", None)
            elapsed = time.monotonic() - started
            self.assertEqual(failure.exception.code, "silero_capacity_timeout")
            self.assertGreaterEqual(elapsed, 0.70)
            self.assertLess(elapsed, 0.95)
            self.assertEqual(len(coordinator.created), 2)
            self.assertEqual(pool.counters["capacity_timeouts"], 1)
        finally:
            for gate in gates:
                gate.set()
            for worker in workers:
                worker.join(1)
            pool.close()

    def test_failed_recovery_warmup_keeps_replacement_quarantined(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        try:
            pool.quarantine_worker_for_controlled_check("silero-1")
            coordinator.fail_requests.add("recovery-request-1")

            with self.assertRaises(StageFailure) as failure:
                pool.recover()

            self.assertEqual(failure.exception.code, "silero_recovery_failed")
            self.assertEqual(pool.ready_count, 1)
            self.assertEqual(
                next(slot for slot in pool.slots if slot["worker_id"] == "silero-1")[
                    "state"
                ],
                "unhealthy",
            )
            self.assertEqual(len(pool.process_ids), 1)
        finally:
            pool.close()

    def test_recovery_waits_until_quarantined_process_has_fully_stopped(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        close_entered = threading.Event()
        close_release = threading.Event()
        quarantine = None
        try:
            old_process = coordinator.created[0]
            original_close = old_process.close

            def blocking_close() -> None:
                close_entered.set()
                if not close_release.wait(1):
                    raise RuntimeError("test close gate timed out")
                original_close()

            old_process.close = blocking_close
            quarantine = threading.Thread(
                target=pool.quarantine_worker_for_controlled_check,
                args=("silero-1",),
            )
            quarantine.start()
            self.assertTrue(close_entered.wait(0.5))
            self.assertEqual(len(coordinator.created), 2)
            self.assertEqual(
                len([process for process in coordinator.created if process.process.poll() is None]),
                2,
            )

            with self.assertRaises(StageFailure) as unavailable:
                pool.recover()

            self.assertEqual(unavailable.exception.code, "silero_recovery_not_admissible")
            self.assertEqual(len(coordinator.created), 2)
            close_release.set()
            quarantine.join(1)
            self.assertFalse(quarantine.is_alive())

            pool.recover()
            self.assertEqual(len(coordinator.created), 3)
            self.assertEqual(
                len([process for process in coordinator.created if process.process.poll() is None]),
                2,
            )
        finally:
            close_release.set()
            if quarantine is not None:
                quarantine.join(1)
            pool.close()

    def test_recovery_stays_unready_through_warmup_and_rejects_concurrent_recovery(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        warmup_release = threading.Event()
        coordinator.gates["recovery-request-1"] = warmup_release
        outcomes: list[dict[str, object]] = []
        try:
            pool.quarantine_worker_for_controlled_check("silero-1")
            recovery = threading.Thread(target=lambda: outcomes.append(pool.recover()))
            recovery.start()
            worker_id, request_id = coordinator.entered.get(timeout=1)
            self.assertEqual((worker_id, request_id), ("silero-1", "recovery-request-1"))
            self.assertEqual(pool.ready_count, 1)
            self.assertEqual(
                next(slot for slot in pool.slots if slot["worker_id"] == "silero-1")[
                    "state"
                ],
                "recovering",
            )
            with self.assertRaises(StageFailure) as concurrent:
                pool.recover()
            self.assertEqual(concurrent.exception.code, "silero_recovery_not_admissible")
            self.assertEqual(len(coordinator.created), 3)

            warmup_release.set()
            recovery.join(1)
            self.assertFalse(recovery.is_alive())
            self.assertEqual(len(outcomes), 1)
            self.assertEqual(pool.ready_count, 2)
            self.assertEqual(len([p for p in coordinator.created if p.process.poll() is None]), 2)
        finally:
            warmup_release.set()
            pool.close()

    def test_failure_has_no_retry_and_explicit_recovery_never_co_starts_third(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        try:
            coordinator.fail_requests.add("request-00000001")
            with self.assertRaises(StageFailure) as failure:
                pool.synthesize(self.key(1), "Ошибка.", None)
            self.assertEqual(failure.exception.code, "silero_synthesis_failed")
            self.assertEqual(len(coordinator.created), 2)
            self.assertEqual(pool.ready_count, 2)

            old_pids = set(pool.process_ids)
            pool.quarantine_worker_for_controlled_check("silero-1")
            self.assertEqual(pool.ready_count, 1)
            with self.assertRaises(StageFailure) as unavailable:
                pool.require_ready()
            self.assertEqual(unavailable.exception.code, "silero_pool_not_ready")
            recovered = pool.recover()
            self.assertEqual(recovered["worker_count"], 2)
            self.assertEqual(pool.ready_count, 2)
            self.assertEqual(len(pool.process_ids), 2)
            self.assertNotEqual(set(pool.process_ids), old_pids)
            self.assertEqual(len([p for p in coordinator.created if p.process.poll() is None]), 2)
        finally:
            pool.close()


if __name__ == "__main__":
    unittest.main()
