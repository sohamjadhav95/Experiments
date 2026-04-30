import json
import asyncio
import httpx
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pathlib import Path

app = FastAPI(title="Dialectic Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── API Config ───────────────────────────────────────────────────────────────
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

NVIDIA_API_KEY = "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"

AGENTS = {
    "proposer": {
        "model": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "key":   NVIDIA_API_KEY,
    },
    "challenger": {
        "model": "mistralai/mistral-large-3-675b-instruct-2512",
        "key":   NVIDIA_API_KEY,
    },
}

# ── Prompts ──────────────────────────────────────────────────────────────────
PROMPTS = {
    "proposer_init": """/think

ROLE: Proposer (Expert Thinker)

TASK: Propose a strong, well-reasoned position on the given topic.

RULES:
- Take a CLEAR STANCE — neutrality is not allowed
- Use logic and evidence, not appeal to authority
- State your assumptions explicitly — they will be attacked
- You must defend your position even under strong criticism
- Do not concede ground too easily

OUTPUT FORMAT (follow exactly):
1. POSITION
[Your clear stance in 1-2 sentences]

2. KEY ARGUMENTS
[3-4 bullet points, each a distinct logical argument]

3. ASSUMPTIONS
[Explicit assumptions your position rests on]

4. POTENTIAL WEAKNESSES
[Honest self-critique — what could break your argument]""",

    "proposer_refine": """/think

ROLE: Proposer (Refinement Mode)

TASK: Improve your previous argument in light of the critique you received.

RULES:
- Do NOT repeat old arguments unchanged — show actual improvement
- Directly address each attack on your assumptions
- Strengthen areas that were exposed as weak
- Modify assumptions if the critique genuinely exposed a flaw
- Stay consistent with your core position

OUTPUT FORMAT (follow exactly):
1. UPDATED POSITION
[Refined stance — what changed and why]

2. IMPROVEMENTS MADE
[Specific responses to the challenger's strongest attacks]

3. REMAINING UNCERTAINTY
[What you genuinely cannot resolve — be honest]""",

    "challenger": """ROLE: Challenger (Ruthless Critic)

TASK: Your sole job is to break the proposer's argument.

RULES:
- You assume the proposer is WRONG unless logically forced to concede
- Do NOT agree unless the argument is airtight — it never is
- Attack assumptions first — they are always the weakest link
- Identify hidden premises and unstated dependencies
- Provide counter-explanations, not just rebuttals
- Your goal is to DISPROVE, not to discuss

OUTPUT FORMAT (follow exactly):
1. CORE FLAWS
[The 2-3 most fatal problems with the argument]

2. ATTACKS ON ASSUMPTIONS
[Systematically dismantle each stated assumption]

3. COUNTER-ARGUMENTS
[Your own reasoning for why an opposing view holds]

4. WHAT WOULD MAKE THIS VALID?
[Exactly what evidence or conditions would force you to accept their position]""",

    "judge": """ROLE: Judge & Synthesizer

TASK: Evaluate the full debate and produce the most epistemically accurate conclusion possible.

RULES:
- Do NOT favor either side — you are not picking a winner
- Combine only the strongest valid points from both sides
- Remove all reasoning exposed as weak or circular during debate
- Be precise; avoid verbose hedging
- Your job is truth-seeking, not diplomacy

OUTPUT FORMAT (follow exactly):
1. FINAL CONCLUSION
[The most defensible position after all arguments and rebuttals]

2. WHAT IS LIKELY TRUE
[Claims well-supported by reasoning from either or both sides]

3. WHAT REMAINS UNCERTAIN
[Claims that the debate left genuinely unresolved]

4. WHAT NEEDS FURTHER EVIDENCE
[Empirical or theoretical work that would settle remaining disputes]""",
}


# ── Request Schema ───────────────────────────────────────────────────────────
class DebateRequest(BaseModel):
    topic:           str
    rounds:          int   = 3
    temp_proposer:   float = 0.70
    temp_challenger: float = 0.30
    temp_judge:      float = 0.10


# ── Core streaming helper ────────────────────────────────────────────────────
async def stream_nvidia(agent_type: str, messages: list, temp: float, top_p: float):
    """Async generator that yields text tokens from NVIDIA API."""
    cfg = AGENTS["challenger"] if agent_type == "challenger" else AGENTS["proposer"]
    headers = {
        "Authorization": f"Bearer {cfg['key']}",
        "Content-Type":  "application/json",
        "Accept":        "text/event-stream",
    }
    payload = {
        "model":       cfg["model"],
        "messages":    messages,
        "temperature": temp,
        "top_p":       top_p,
        "max_tokens":  1400,
        "stream":      True,
    }

    async with httpx.AsyncClient(timeout=180.0) as client:
        async with client.stream("POST", NVIDIA_URL, headers=headers, json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    tok = json.loads(data)["choices"][0]["delta"].get("content", "")
                    if tok:
                        yield tok
                except Exception:
                    pass


# ── Repetition detector ──────────────────────────────────────────────────────
def is_repetitive(texts: list[str], threshold: float = 0.62) -> bool:
    if len(texts) < 2:
        return False
    words = lambda t: set(w for w in t.lower().split() if len(w) > 6)
    a, b  = words(texts[-1]), words(texts[-2])
    if not a:
        return False
    overlap = len(a & b) / len(a)
    return overlap > threshold


# ── Debate orchestrator (SSE generator) ─────────────────────────────────────
async def debate_generator(req: DebateRequest):
    def sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    proposer_history:   list[str] = []
    challenger_history: list[str] = []
    last_proposer    = ""
    last_challenger  = ""

    try:
        for r in range(1, req.rounds + 1):
            yield sse({"type": "round_start", "round": r})

            # ── Proposer ────────────────────────────────────────────────────
            if r == 1:
                p_msgs = [
                    {"role": "system", "content": PROMPTS["proposer_init"]},
                    {"role": "user",   "content": f"TOPIC: {req.topic}\n\nProvide your opening position."},
                ]
            else:
                p_msgs = [
                    {"role": "system", "content": PROMPTS["proposer_refine"]},
                    {"role": "user",   "content": (
                        f"TOPIC: {req.topic}\n\n"
                        f"YOUR PREVIOUS POSITION:\n{last_proposer[:700]}\n\n"
                        f"CHALLENGER CRITIQUE:\n{last_challenger}\n\n"
                        f"Refine your argument."
                    )},
                ]

            full_p = ""
            async for tok in stream_nvidia("proposer", p_msgs, req.temp_proposer, 0.9):
                full_p += tok
                yield sse({"type": "proposer_chunk", "round": r, "content": tok})

            yield sse({"type": "proposer_done", "round": r, "full": full_p})
            last_proposer = full_p
            proposer_history.append(full_p)

            # Repetition check
            if is_repetitive(proposer_history):
                yield sse({"type": "early_stop",
                           "reason": "Argument convergence detected — no new arguments emerging."})
                break

            # ── Challenger ───────────────────────────────────────────────────
            c_msgs = [
                {"role": "system", "content": PROMPTS["challenger"]},
                {"role": "user",   "content": (
                    f"TOPIC: {req.topic}\n\n"
                    f"PROPOSER ARGUMENT:\n{full_p}\n\n"
                    f"Attack this argument."
                )},
            ]

            full_c = ""
            async for tok in stream_nvidia("challenger", c_msgs, req.temp_challenger, 0.8):
                full_c += tok
                yield sse({"type": "challenger_chunk", "round": r, "content": tok})

            yield sse({"type": "challenger_done", "round": r, "full": full_c})
            last_challenger = full_c
            challenger_history.append(full_c)

        # ── Judge synthesis ──────────────────────────────────────────────────
        yield sse({"type": "judge_start"})

        debate_summary = "\n\n".join(
            f"ROUND {i+1} PROPOSER:\n{p[:700]}\n\nROUND {i+1} CHALLENGER:\n{c[:700]}"
            for i, (p, c) in enumerate(zip(proposer_history, challenger_history))
        )

        j_msgs = [
            {"role": "system", "content": PROMPTS["judge"]},
            {"role": "user",   "content": (
                f"TOPIC: {req.topic}\n\n"
                f"FULL DEBATE:\n{debate_summary}\n\n"
                f"Provide your synthesis."
            )},
        ]

        full_j = ""
        async for tok in stream_nvidia("proposer", j_msgs, req.temp_judge, 0.7):  # Nemotron for judge
            full_j += tok
            yield sse({"type": "judge_chunk", "content": tok})

        yield sse({"type": "judge_done", "full": full_j})
        yield sse({"type": "debate_done"})

    except Exception as e:
        yield sse({"type": "error", "message": str(e)})


# ── Routes ───────────────────────────────────────────────────────────────────
@app.post("/debate")
async def start_debate(req: DebateRequest):
    return StreamingResponse(
        debate_generator(req),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",      # disable nginx buffering if behind proxy
        },
    )


@app.get("/", response_class=HTMLResponse)
async def serve_ui():
    html = Path("index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)
