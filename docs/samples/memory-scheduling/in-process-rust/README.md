# In-process Rust scheduler

Rust mirror of [`../in-process-python/`](../in-process-python/). When
your agent runtime is a Rust binary (axum, actix, custom tokio
runtime), you can drive the two memory cells from the same tokio
runtime instead of running an external scheduler.

[`main.rs`](main.rs) is a minimal complete example. Drop it into a
binary crate that depends on `chunkshop-rs` with the `memory` feature
(included by default via `full`).

```toml
# Cargo.toml
[dependencies]
anyhow = "1"
chunkshop-rs = { version = "0.4", features = ["full"] }
sqlx = { version = "0.8", features = ["runtime-tokio", "postgres"] }
tokio = { version = "1", features = ["full"] }
tracing = "0.1"
tracing-subscriber = "0.3"
```

```bash
export CHUNKSHOP_MEMORY_DSN="postgresql://app:secret@localhost:5432/agent_memory"
cargo run --bin memory-scheduler-demo
```

The example:

1. Bootstraps the staging table via `ensure_staging_table` on start.
2. Spawns two tokio tasks via `tokio::spawn` — one realtime (60s),
   one consolidate (3600s).
3. Provides an `on_agent_turn` helper your axum/actix handler calls
   after every user/assistant exchange.
4. Demonstrates a 5-turn session round-trip and shows the resulting
   `agent_memory.memory` state.

### Embedding in a real axum app

```rust
use axum::{routing::post, Router, Json};
use chunkshop::memory::stage_event;
use sqlx::postgres::PgPoolOptions;
use std::sync::Arc;

#[derive(Clone)]
struct AppState { pool: sqlx::PgPool }

async fn chat(state: axum::extract::State<Arc<AppState>>,
              Json(req): Json<ChatRequest>) -> Json<ChatResponse> {
    // stage user turn synchronously — fast (~ms)
    stage_event(&state.pool, "public", "chunkshop_staging",
                &chunkshop::memory::StagedEvent {
                    session_id: req.session_id.clone(),
                    seq: Some(req.seq),
                    role: Some("user".into()),
                    content: req.message.clone(),
                    ..Default::default()
                }).await.ok();
    let reply = call_model(&req.message).await;
    // stage assistant turn
    stage_event(&state.pool, "public", "chunkshop_staging",
                &chunkshop::memory::StagedEvent {
                    session_id: req.session_id,
                    seq: Some(req.seq + 1),
                    role: Some("assistant".into()),
                    content: reply.clone(),
                    ..Default::default()
                }).await.ok();
    Json(ChatResponse { reply })
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt::init();
    let dsn = std::env::var("CHUNKSHOP_MEMORY_DSN")?;
    let pool = PgPoolOptions::new().max_connections(8).connect(&dsn).await?;
    let state = Arc::new(AppState { pool: pool.clone() });
    memory_scheduler::start(&pool).await?;       // see main.rs
    let app = Router::new().route("/chat", post(chat)).with_state(state);
    axum::serve(tokio::net::TcpListener::bind("0.0.0.0:8080").await?, app).await?;
    Ok(())
}
```

The scheduler is `tokio::spawn`'d once at startup and runs for the life
of the process. tokio cancels the tasks on drop, so a `Ctrl-C` shutdown
is clean.
