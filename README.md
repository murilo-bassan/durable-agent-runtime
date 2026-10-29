# runloop

**English** · [Português](README.pt-BR.md)

Runs AI agents on long tasks without losing work. If the server goes down halfway,
the agent continues where it stopped and nothing is done twice.

![runloop](docs/screenshots/run-full.png)

## The problem

In a demo, an agent is a loop in a single process. In production it will run into a deploy halfway
through a task, a server that crashes, a provider that returns errors, a person who takes hours to
approve and a user who clicks "send" twice. runloop is built so none of that breaks the task or
causes a duplicate action.

## What it guarantees

- **Nothing is lost when a worker dies.** The agent saves every step to Postgres. If the process dies, another worker resumes from the last saved step.
- **Nothing happens twice.** Actions like "create purchase order" are recorded under a unique key; if the task is retried, the action isn't repeated.
- **A person approves important actions first.** The agent pauses and waits. The approval only covers those exact arguments: if the agent changes the order amount, it needs a new approval.
- **Cost has a ceiling.** Each user has a token budget, reserved before the model is called, so many tasks at once can't overspend it.
- **A double click creates one task**, thanks to the `Idempotency-Key` header.
- **You can cancel at any time**, even mid-run.

## The chaos test

`scripts/chaos.py` queues 24 tasks, kills a worker with `SIGKILL` mid-work and checks the outcome:

```
enqueued 24 runs
worker A killed with 4 runs in flight
succeeded 24/24 · runs that needed a retry: 4 · purchase orders: 24
chaos drill passed ✓
```

All 24 tasks finished, 4 had to be resumed and no order was duplicated.

## Running it

```bash
git clone https://github.com/murilo-bassan/durable-agent-runtime.git
cd durable-agent-runtime
pip install -r requirements-dev.txt
docker compose up -d db                       # Postgres 16 on port 55432

python -m pytest                              # tests against a real Postgres
python scripts/chaos.py                       # the chaos test

RUNTIME_EMBEDDED_WORKER=true python -m uvicorn runtime.api:app --port 8700
python scripts/demo.py                        # creates sample tasks
```

Console at http://localhost:8700 and API docs at http://localhost:8700/docs.
To start everything with two workers competing for the queue: `docker compose up --build`.

Without configuration, the agent uses a deterministic planner, which keeps tests reproducible.
To use an LLM: `LLM_PROVIDER=openai`, `LLM_API_KEY` and optionally `LLM_BASE_URL` (any OpenAI-compatible API).

## How it works

The queue is a Postgres table. Each worker grabs the next task with `SELECT ... FOR UPDATE SKIP LOCKED`,
so two workers never take the same one. While working, the worker sends heartbeats;
if they stop, the task goes back to the queue.

```
queued ──► running ──► succeeded | failed | cancelled
  ▲           │ └── worker gone ──► queued (retry) … ──► dead
  └── decision ◄── waiting_approval
```

Design decisions are in [`docs/adr`](docs/adr).

| Rejected twice, the agent adapts | Console |
|---|---|
| ![](docs/screenshots/rejected-full.png) | ![](docs/screenshots/console.png) |

**Stack:** Python, PostgreSQL, FastAPI, Docker

MIT license.
