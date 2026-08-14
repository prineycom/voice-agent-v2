from __future__ import annotations

import queue
import threading
import time
import types
import unittest

from voice_agent_v2.audio import OUTPUT_MEDIA_MAX_BYTES
from voice_agent_v2.contracts import LLM_VERSION, STT_VERSION, StageFailure
from voice_agent_v2.v2_audio import (
    INPUT_AUDIO_FORMAT,
    OUTPUT_DELIVERY_BLOCK_BYTES,
    OUTPUT_FRAME_BYTES,
    TTS_OUTPUT_AUDIO_FORMAT,
    TTS_V2_OUTPUT_MEDIA_MAX_BYTES,
)
from voice_agent_v2.v2_contracts import TTSRequestKey, TTS_V2_VERSION
from voice_agent_v2.local_tts import TurnTTSBudget
from voice_agent_v2.process_adapter import AdapterRequestError
from voice_agent_v2.silero_tts import (
    MODEL_IDENTITY,
    MODEL_SHA256,
    MODEL_SIZE,
    SileroKseniyaTTS,
    SileroTurnBudget,
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
        self.ready_overrides: dict[str, dict[str, object]] = {}
        self.error_overrides: dict[str, dict[str, object]] = {}
        self.chunk_overrides: dict[str, dict[str, object]] = {}
        self.final_overrides: dict[str, object] = {}
        self.output_chunks: tuple[bytes, ...] | None = None
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
        ready = {
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
        ready.update(self.coordinator.ready_overrides.get(self.worker_id, {}))
        return ready

    def stream(
        self, request: dict, _timeout_seconds: float, *, error_validator=None
    ):
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
        if request_id in self.coordinator.error_overrides:
            error = {
                "protocol_version": "voice-agent.silero-worker.v1",
                "event": "error",
                "request_id": request_id,
                "key": key,
                "error_class": "synthesis_failed",
            }
            error.update(self.coordinator.error_overrides[request_id])
            if error_validator is not None:
                error_validator(error)
            raise AdapterRequestError(str(error.get("error_class", "adapter_error")))
        chunks = self.coordinator.output_chunks or (
            bytes([self.process.pid % 251, 0]) * 960,
        )
        for sequence, chunk in enumerate(chunks):
            event = {
                "protocol_version": "voice-agent.silero-worker.v1",
                "event": "chunk",
                "request_id": request_id,
                "key": key,
                "sequence": sequence,
                "bytes": len(chunk),
                "pcm_base64": __import__("base64").b64encode(chunk).decode("ascii"),
            }
            event.update(self.coordinator.chunk_overrides.get(request_id, {}))
            yield event
        pcm = b"".join(chunks)
        final = {
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
            "chunk_count": len(chunks),
            "audio_bytes": len(pcm),
            "samples": len(pcm) // 2,
            "duration_ms": round(len(pcm) // 2 / 48_000 * 1_000, 3),
            "latency_ms": 1.0,
            "terminal_count": 1,
        }
        final.update(self.coordinator.final_overrides)
        yield final

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
        for tagged in (
            "<speak>Секрет.</speak>",
            "<тег>Секрет.</тег>",
            "<\u200ctag>Секрет.</\u200ctag>",
            "<незавершённый-тег",
        ):
            with self.subTest(tagged=tagged), self.assertRaises(StageFailure) as failure:
                shape_russian_tts(tagged)
            self.assertEqual(failure.exception.code, "tts_plain_text_required")

    def test_split_tag_is_rejected_before_its_buffer_can_be_segmented(self) -> None:
        segmenter = RussianTTSSegmenter()
        self.assertEqual(segmenter.feed("<spe"), ())
        with self.assertRaises(StageFailure) as failure:
            segmenter.feed("ak>Секрет.</speak>")
        self.assertEqual(failure.exception.code, "tts_plain_text_required")

        unicode_segmenter = RussianTTSSegmenter()
        self.assertEqual(unicode_segmenter.feed("<те"), ())
        with self.assertRaises(StageFailure) as unicode_failure:
            unicode_segmenter.feed("г>Секрет.</тег>")
        self.assertEqual(unicode_failure.exception.code, "tts_plain_text_required")

        xml_name_start_segmenter = RussianTTSSegmenter()
        self.assertEqual(xml_name_start_segmenter.feed("<"), ())
        with self.assertRaises(StageFailure) as xml_name_start_failure:
            xml_name_start_segmenter.feed("\u200ctag>Секрет.</\u200ctag>")
        self.assertEqual(
            xml_name_start_failure.exception.code, "tts_plain_text_required"
        )

        long_prefix = RussianTTSSegmenter()
        unsafe = (
            '<тег атрибут="Это достаточно длинное предложение внутри атрибута. '
            'Оно не должно попасть на синтез до закрытия кавычки'
        )
        self.assertEqual(long_prefix.feed(unsafe), ())
        with self.assertRaises(StageFailure) as delayed_failure:
            long_prefix.feed('">Секрет.</тег>')
        self.assertEqual(delayed_failure.exception.code, "tts_plain_text_required")


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

    def test_concurrent_handoff_events_have_serial_delivery_and_unique_sequences(self) -> None:
        class ConcurrentCallbackLLM(FakeVisibleLLM):
            def __init__(self) -> None:
                super().__init__(("Это достаточно длинная проверочная фраза для синтеза.",))
                self.text = self.pieces[0]
                self.delivery_started = threading.Event()
                self.release_delivery = threading.Event()
                self.callback_started = threading.Event()
                self.errors: list[BaseException] = []

            def respond_with_handoff(
                self, *, on_sentence, on_visible_sentence, **_arguments
            ) -> str:
                def run(callback) -> None:
                    try:
                        callback()
                    except BaseException as error:
                        self.errors.append(error)

                visible = threading.Thread(
                    target=run,
                    args=(lambda: on_visible_sentence(self.text),),
                )
                visible.start()
                if not self.delivery_started.wait(1):
                    raise AssertionError("visible event delivery did not start")

                def synthesize() -> None:
                    self.callback_started.set()
                    on_sentence(self.text)

                speech = threading.Thread(target=run, args=(synthesize,))
                speech.start()
                if not self.callback_started.wait(1):
                    raise AssertionError("speech callback did not start")
                time.sleep(0.05)
                self.release_delivery.set()
                visible.join(1)
                speech.join(1)
                if visible.is_alive() or speech.is_alive():
                    raise AssertionError("concurrent callbacks did not finish")
                if self.errors:
                    raise self.errors[0]
                return self.text

        llm = ConcurrentCallbackLLM()
        overlapping_delivery = threading.Event()
        observed_sequences: list[int] = []

        def observe(event: dict[str, object]) -> None:
            observed_sequences.append(int(event["sequence"]))
            if event["type"] == "llm.visible":
                llm.delivery_started.set()
                if not llm.release_delivery.wait(1):
                    raise AssertionError("visible event delivery was not released")
            elif llm.delivery_started.is_set() and not llm.release_delivery.is_set():
                overlapping_delivery.set()

        result = RealTurnController(FakeSTT(), llm, FakeTTSV2()).run_turn(
            session_id="session-test",
            turn_id="turn-test",
            input_pcm=b"\0\0" * 320,
            request_id="request-test",
            event_observer=observe,
        )

        self.assertEqual(result.terminal_event["type"], "turn.completed")
        self.assertFalse(overlapping_delivery.is_set())
        self.assertEqual(observed_sequences, list(range(1, len(result.events) + 1)))
        self.assertEqual(
            observed_sequences,
            [int(event["sequence"]) for event in result.events],
        )

    def test_invalid_v2_generation_correlation_is_rejected_before_publication(self) -> None:
        invalid_values = (True, 1_000_000_001)
        for field in ("stream_epoch", "turn_generation"):
            for value in invalid_values:
                with self.subTest(field=field, value=value):
                    tts = FakeTTSV2()
                    observed: list[dict[str, object]] = []
                    arguments = {
                        "session_id": "session-test",
                        "turn_id": "turn-test",
                        "input_pcm": b"\0\0" * 320,
                        "request_id": "request-test",
                        "event_observer": observed.append,
                        field: value,
                    }

                    with self.assertRaisesRegex(ValueError, "integers from 1"):
                        RealTurnController(
                            FakeSTT(), FakeVisibleLLM(self.pieces()), tts
                        ).run_turn(**arguments)

                    self.assertEqual(observed, [])
                    self.assertEqual(tts.calls, [])

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

    def test_split_unicode_tag_fails_tts_without_rewriting_visible_text(self) -> None:
        pieces = ("<", "\u200ctag>Секрет.</\u200ctag>")
        tts = FakeTTSV2()
        controller = RealTurnController(FakeSTT(), FakeVisibleLLM(pieces), tts)
        result = controller.run_turn(
            session_id="session-test",
            turn_id="turn-test",
            input_pcm=b"\0\0" * 320,
            request_id="request-test",
        )

        expected = "".join(pieces)
        self.assertEqual(result.terminal_event["type"], "turn.failed")
        self.assertEqual(result.terminal_event["payload"]["code"], "tts_plain_text_required")
        self.assertEqual(tts.calls, [])
        self.assertEqual(
            [
                event["payload"]["response"]
                for event in result.events
                if event["type"] == "llm.visible"
            ][-1],
            expected,
        )
        self.assertEqual(
            next(event for event in result.events if event["type"] == "llm.final")[
                "payload"
            ]["response"],
            expected,
        )

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
            self.assertEqual(OUTPUT_MEDIA_MAX_BYTES, 5_760_000)
            self.assertEqual(TTS_V2_OUTPUT_MEDIA_MAX_BYTES, 17_280_000)

            v1_budget = TurnTTSBudget(deadline=float("inf"))
            v1_budget.consume(OUTPUT_MEDIA_MAX_BYTES)
            with self.assertRaises(StageFailure) as v1_overflow:
                v1_budget.consume(1)
            self.assertEqual(
                v1_overflow.exception.code, "selected_tts_output_out_of_bounds"
            )

            v2_budget = SileroTurnBudget(deadline=float("inf"))
            v2_budget.consume(TTS_V2_OUTPUT_MEDIA_MAX_BYTES)
            with self.assertRaises(StageFailure) as v2_overflow:
                v2_budget.consume(1)
            self.assertEqual(
                v2_overflow.exception.code, "selected_tts_output_out_of_bounds"
            )
        finally:
            pool.close()

    def test_adapter_preserves_worker_chunk_bound_at_public_v2_boundary(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        tts = SileroKseniyaTTS(pool)
        coordinator.output_chunks = (b"\1\0" * 32_768, b"\2\0" * 960)
        try:
            chunks = list(
                tts.stream_synthesize(
                    session_id="session-test",
                    stream_epoch=1,
                    turn_id="turn-00000001",
                    turn_generation=1,
                    request_id="request-00000001",
                    segment_index=0,
                    text="Проверка ограниченных блоков.",
                )
            )

            self.assertEqual(chunks, list(coordinator.output_chunks))
            self.assertTrue(all(0 < len(chunk) <= 65_536 for chunk in chunks))
        finally:
            tts.release_turn("session-test", 1, "turn-00000001", 1)
            pool.close()

    def test_worker_duration_accepts_exact_half_even_microsecond_rounding(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        coordinator.output_chunks = (b"\0\0" * 3,)
        try:
            chunks, metadata = pool.synthesize(
                self.key(1), "Проверка округления длительности.", None
            )

            self.assertEqual(chunks, coordinator.output_chunks)
            self.assertEqual(metadata["samples"], 3)
            self.assertEqual(metadata["duration_ms"], 0.062)
            self.assertEqual(pool.ready_count, 2)
        finally:
            pool.close()

    def test_worker_timing_must_match_samples_and_remain_finite(self) -> None:
        invalid_timings = (
            {"duration_ms": 1.0},
            {"duration_ms": float("nan")},
            {"latency_ms": float("inf")},
        )
        for turn, override in enumerate(invalid_timings, start=1):
            with self.subTest(override=override):
                coordinator = ProcessCoordinator()
                pool = self.pool(coordinator)
                coordinator.final_overrides.update(override)
                try:
                    with self.assertRaises(StageFailure) as failure:
                        pool.synthesize(self.key(turn), "Проверка времени.", None)
                    self.assertEqual(failure.exception.code, "silero_worker_failed")
                    self.assertEqual(pool.counters["requests"], 1)
                    self.assertEqual(len(coordinator.created), 2)
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

    def test_affined_turn_never_migrates_to_an_idle_worker(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator, wait=0.1)
        blocker_gates = [threading.Event(), threading.Event()]
        blocker_threads: list[threading.Thread] = []
        outcomes: list[object] = []
        try:
            first_chunks, _metadata = pool.synthesize(
                self.key(1), "Первый сегмент.", None
            )
            pinned_worker = next(
                process.worker_id
                for process in coordinator.created
                if first_chunks[0][0] == process.process.pid % 251
            )

            for turn, gate in zip((2, 3), blocker_gates):
                key = self.key(turn)
                coordinator.gates[key.request_id] = gate
                thread = threading.Thread(
                    target=lambda item=key: outcomes.append(
                        pool.synthesize(item, "Блокирующий сегмент.", None)
                    )
                )
                blocker_threads.append(thread)
                thread.start()
                worker_id, request_id = coordinator.entered.get(timeout=1)
                self.assertEqual(request_id, key.request_id)
                if turn == 3:
                    self.assertEqual(worker_id, pinned_worker)

            blocker_gates[0].set()
            blocker_threads[0].join(1)
            self.assertFalse(blocker_threads[0].is_alive())
            self.assertEqual(pool.ready_count, 2)

            started = time.monotonic()
            with self.assertRaises(StageFailure) as failure:
                pool.synthesize(self.key(1, 1), "Второй сегмент.", None)
            elapsed = time.monotonic() - started

            self.assertEqual(failure.exception.code, "silero_capacity_timeout")
            self.assertGreaterEqual(elapsed, 0.08)
            self.assertLess(elapsed, 0.30)
            self.assertEqual(len(coordinator.created), 2)
            self.assertEqual(pool.process_start_count, 2)
            with self.assertRaises(queue.Empty):
                coordinator.entered.get(timeout=0.05)
        finally:
            for gate in blocker_gates:
                gate.set()
            for thread in blocker_threads:
                thread.join(1)
            for turn in (1, 2, 3):
                pool.release_turn("session-test", 1, f"turn-{turn:08d}", turn)
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

    def test_cancelled_capacity_waiter_never_dispatches_to_released_worker(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        gates = [threading.Event(), threading.Event()]
        coordinator.gates["request-00000001"] = gates[0]
        coordinator.gates["request-00000002"] = gates[1]
        worker_outcomes: list[object] = []
        workers = [
            threading.Thread(
                target=lambda turn=turn: worker_outcomes.append(
                    pool.synthesize(self.key(turn), f"Ответ {turn}.", None)
                )
            )
            for turn in (1, 2)
        ]
        token = CancellationToken()
        waiter_outcomes: list[str] = []

        def wait_for_worker() -> None:
            try:
                pool.synthesize(self.key(3), "Отменённый ответ.", token)
            except StageFailure as error:
                waiter_outcomes.append(error.code)

        waiter = threading.Thread(target=wait_for_worker)
        try:
            for worker in workers:
                worker.start()
                coordinator.entered.get(timeout=1)
            waiter.start()
            time.sleep(0.02)
            token.cancel()
            waiter.join(0.5)

            self.assertFalse(waiter.is_alive())
            self.assertEqual(waiter_outcomes, ["selected_tts_cancelled"])
            self.assertEqual(pool.counters["requests"], 2)
            with self.assertRaises(queue.Empty):
                coordinator.entered.get(timeout=0.05)
        finally:
            token.cancel()
            for gate in gates:
                gate.set()
            waiter.join(1)
            for worker in workers:
                worker.join(1)
            pool.close()

    def test_idle_process_loss_blocks_admission_until_explicit_recovery(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        tts = SileroKseniyaTTS(pool)
        try:
            lost_process = coordinator.created[0]
            lost_process.process.running = False

            self.assertFalse(tts.ready_for_admission())
            self.assertEqual(pool.ready_count, 1)
            self.assertEqual(
                next(slot for slot in pool.slots if slot["worker_id"] == "silero-1")[
                    "state"
                ],
                "unhealthy",
            )
            with self.assertRaises(StageFailure) as unavailable:
                pool.synthesize(self.key(1), "Нельзя принять новый ход.", None)
            self.assertEqual(unavailable.exception.code, "silero_pool_not_ready")
            self.assertEqual(len(coordinator.created), 2)
            self.assertEqual(
                len([process for process in coordinator.created if process.process.poll() is None]),
                1,
            )

            recovered = pool.recover()

            self.assertEqual(recovered["worker_count"], 2)
            self.assertTrue(tts.ready_for_admission())
            self.assertEqual(len(coordinator.created), 3)
            self.assertEqual(
                len([process for process in coordinator.created if process.process.poll() is None]),
                2,
            )
        finally:
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

    def test_ready_frame_with_extra_field_prevents_pool_readiness(self) -> None:
        coordinator = ProcessCoordinator()
        coordinator.ready_overrides["silero-1"] = {"unexpected": "field"}
        pool = SileroWorkerPool(
            process_factory=coordinator.factory,
            verify_runtime=lambda: {"verified": True},
        )
        try:
            with self.assertRaises(StageFailure) as failure:
                pool.start()

            self.assertEqual(failure.exception.code, "silero_worker_identity_mismatch")
            self.assertEqual(pool.ready_count, 0)
            self.assertEqual(len(coordinator.created), 1)
            self.assertEqual(
                len([p for p in coordinator.created if p.process.poll() is None]), 0
            )
        finally:
            pool.close()

    def test_chunk_or_final_frame_with_extra_field_quarantines_worker(self) -> None:
        for frame in ("chunk", "final"):
            with self.subTest(frame=frame):
                coordinator = ProcessCoordinator()
                pool = self.pool(coordinator)
                try:
                    overrides = (
                        coordinator.chunk_overrides
                        if frame == "chunk"
                        else coordinator.final_overrides
                    )
                    if frame == "chunk":
                        overrides["request-00000001"] = {"unexpected": "field"}
                    else:
                        overrides["unexpected"] = "field"

                    with self.assertRaises(StageFailure) as failure:
                        pool.synthesize(self.key(1), "Ошибка протокола.", None)

                    self.assertEqual(failure.exception.code, "silero_worker_failed")
                    self.assertEqual(pool.ready_count, 1)
                    self.assertEqual(pool.counters["worker_quarantines"], 1)
                    self.assertEqual(len(pool.process_ids), 1)
                finally:
                    pool.close()

    def test_validated_worker_error_is_request_failure_without_quarantine(self) -> None:
        coordinator = ProcessCoordinator()
        pool = self.pool(coordinator)
        try:
            coordinator.error_overrides["request-00000001"] = {}

            with self.assertRaises(StageFailure) as failure:
                pool.synthesize(self.key(1), "Ошибка синтеза.", None)

            self.assertEqual(failure.exception.code, "silero_synthesis_failed")
            self.assertEqual(pool.ready_count, 2)
            self.assertEqual(pool.counters["worker_quarantines"], 0)
            self.assertEqual(len(coordinator.created), 2)
        finally:
            pool.close()

    def test_invalid_worker_error_key_or_shape_quarantines_slot(self) -> None:
        expected_key = self.key(1).as_dict()
        invalid_overrides = (
            {
                "key": TTSRequestKey(
                    "session-test", 1, "turn-00000001", 1,
                    "request-00000001", 9,
                ).as_dict(),
            },
            {"key": {**expected_key, "stream_epoch": True}},
            {"key": {**expected_key, "turn_generation": True}},
            {"key": {**expected_key, "segment_index": False}},
            {"unexpected": "field"},
        )
        for override in invalid_overrides:
            with self.subTest(override=override):
                coordinator = ProcessCoordinator()
                pool = self.pool(coordinator)
                try:
                    coordinator.error_overrides["request-00000001"] = override

                    with self.assertRaises(StageFailure) as failure:
                        pool.synthesize(self.key(1), "Ошибка протокола.", None)

                    self.assertEqual(failure.exception.code, "silero_worker_failed")
                    self.assertEqual(pool.ready_count, 1)
                    self.assertEqual(pool.counters["worker_quarantines"], 1)
                    self.assertEqual(len(pool.process_ids), 1)
                    self.assertEqual(len(coordinator.created), 2)
                finally:
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
