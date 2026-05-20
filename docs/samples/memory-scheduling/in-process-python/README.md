# In-process Python scheduler

When chunkshop runs **inside** your agent server (FastAPI / Quart /
Starlette / whatever), you can skip the external scheduler and drive
the two cells from the same event loop. Trade-off: easier ops (one
process, no cron), but a crash in your agent now also stops your
memory consolidation. Make sure your supervisor restarts cleanly.

See [`run.py`](run.py) for a complete working example.

```bash
export CHUNKSHOP_MEMORY_DSN="postgresql://app:secret@localhost:5432/agent_memory"
python run.py
```

The script:

1. Ensures `chunkshop_staging` exists on startup.
2. Spawns two `asyncio` tasks — one for realtime (60s interval), one
   for consolidate (3600s interval).
3. Provides a `stage_turn(...)` function your agent calls after every
   message exchange.
4. Demonstrates the full loop with a fake agent that stages 5 turns,
   runs the realtime cell, then runs consolidate.

Both cells run via `asyncio.to_thread(run_cell, cfg)` — `run_cell`
is synchronous inside chunkshop (the embedder + sqlx work blocks),
so wrapping in a thread keeps the event loop responsive while the
cell runs.

### Embedding in a real FastAPI app

```python
from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start the memory scheduler when the server boots.
    scheduler_task = asyncio.create_task(memory_scheduler_loop())
    yield
    scheduler_task.cancel()

app = FastAPI(lifespan=lifespan)

@app.post("/chat")
async def chat(req: ChatRequest):
    # ... call your model ...
    stage_turn(req.session_id, "user", req.message, seq=req.seq)
    response = await get_assistant_response(req)
    stage_turn(req.session_id, "assistant", response, seq=req.seq + 1)
    return {"reply": response}
```
