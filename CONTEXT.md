# Domain glossary

- **AI-eyes avatar** — The original, deliberately simple character whose visual identity is expressed primarily through animated eyes.
- **Agent response** — The assistant content produced for one voice turn, including spoken text and, when present, animation intent.
- **Animation cue** — One bounded semantic direction within an animation intent, such as an expression, gaze, or emphasis change.
- **Animation intent** — A small, versioned set of semantic cues associated with an agent response. It describes communicative intent, never frames or raw model parameters.
- **Avatar runtime** — The deterministic component that turns validated animation intent and session state into visible avatar behavior.
- **Barge-in** — A user interruption that occurs while an agent response is being delivered.
- **Control event** — A versioned non-media message that reports or changes session, turn, response, or avatar state.
- **Core MVP** — A locally inferred, interruptible voice conversation with the supported avatar and enough operational behavior to run reliably on the target host.
- **Hard dependency** — A capability whose loss prevents the current operation from completing correctly.
- **Inference service** — A bounded local capability that performs STT, LLM, or TTS inference behind an explicit contract.
- **Realtime session** — The bounded period in which a client and the agent share media, control events, and conversation state.
- **Semantic timing anchor** — A coarse point in a response lifecycle to which an animation cue may be attached, rather than a frame timestamp.
- **Soft dependency** — A capability whose loss permits an explicitly degraded but still correct experience.
- **Tailnet** — The private network formed by devices that are members of the project's Tailscale network.
- **Tracer** — The smallest end-to-end path that crosses the intended boundaries with deterministic substitutes and produces inspectable evidence.
- **Turn correlation ID** — An opaque identifier used to associate all media, events, inference work, and observations belonging to one voice turn.
- **Voice turn** — One user utterance and the resulting agent response, ending in completion, interruption, or failure.
- **Utterance** — A bounded span of user speech treated as input to a voice turn.
