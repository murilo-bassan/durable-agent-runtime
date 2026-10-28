"""Worker process: N slots claiming runs, one housekeeping thread.

    python -m runtime.worker

Each slot loops: claim (SKIP LOCKED) → execute → repeat, sleeping when the
queue is empty. Housekeeping beats for the runs this process holds, requeues
runs of dead workers, and expires overdue approvals. SIGTERM/SIGINT stop
claiming new work and let in-flight runs reach their next checkpoint.

The slot loop is the process border: an unexpected exception requeues the run
(if it has attempts left) or fails it — the slot itself never dies.
"""
from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
import uuid

from runtime import agent_loop, approvals, db, runs
from runtime.config import get_settings
from runtime.llm import LLM, from_settings


def log(msg: str) -> None:
    sys.stderr.write(f"[worker {time.strftime('%H:%M:%S')}] {msg}\n")


class Worker:
    def __init__(self, llm: LLM | None = None, slots: int | None = None) -> None:
        self.settings = get_settings()
        self.llm = llm or from_settings()
        self.slots = slots or self.settings.worker_slots
        self.process_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self.stop = threading.Event()
        self._held: dict[str, str] = {}   # run_id -> slot id
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------ one unit of work
    def run_one(self, slot_id: str) -> bool:
        claimed = runs.claim_next(slot_id)
        if claimed is None:
            return False
        with self._lock:
            self._held[claimed.id] = slot_id
        try:
            outcome = agent_loop.execute(claimed, slot_id, self.llm)
            log(f"{claimed.id[:8]} {claimed.agent} → {outcome} (attempt {claimed.attempts})")
        except runs.LostOwnership:
            log(f"{claimed.id[:8]} lost ownership; another worker has it now")
        except Exception as error:  # the border
            log(f"{claimed.id[:8]} crashed: {type(error).__name__}: {error}")
            self._retry_or_fail(claimed, slot_id, error)
        finally:
            with self._lock:
                self._held.pop(claimed.id, None)
        return True

    def _retry_or_fail(self, claimed: runs.Claimed, slot_id: str, error: Exception) -> None:
        detail = {"reason": "error", "type": type(error).__name__, "message": str(error)[:300]}
        with db.tx() as cur:
            if claimed.attempts < self.settings.max_attempts:
                cur.execute("UPDATE runs SET status = 'queued', worker_id = NULL, error = %s "
                            "WHERE id = %s AND worker_id = %s AND status = 'running'",
                            (db.json(detail), claimed.id, slot_id))
            else:
                cur.execute("UPDATE runs SET status = 'failed', worker_id = NULL, finished_at = now(), error = %s "
                            "WHERE id = %s AND worker_id = %s AND status = 'running'",
                            (db.json(detail), claimed.id, slot_id))

    # ------------------------------------------------------------ loops
    def _slot(self, n: int) -> None:
        slot_id = f"{self.process_id}/{n}"
        while not self.stop.is_set():
            try:
                if not self.run_one(slot_id):
                    self.stop.wait(self.settings.poll_interval_s)
            except Exception as error:  # e.g. database briefly down
                log(f"slot {n}: {type(error).__name__}: {error}")
                self.stop.wait(2)

    def housekeeping_once(self) -> None:
        with self._lock:
            by_slot: dict[str, list[str]] = {}
            for run_id, slot in self._held.items():
                by_slot.setdefault(slot, []).append(run_id)
        for slot, ids in by_slot.items():
            runs.heartbeat(slot, ids)
        recovered = runs.recover_stale(self.settings.stale_after_s, self.settings.max_attempts)
        if recovered["requeued"] or recovered["dead"]:
            log(f"recovered {recovered}")
        approvals.expire_due()

    def _housekeeping(self) -> None:
        while not self.stop.is_set():
            try:
                self.housekeeping_once()
            except Exception as error:
                log(f"housekeeping: {type(error).__name__}: {error}")
            self.stop.wait(self.settings.heartbeat_interval_s)

    def start(self) -> None:
        self._threads = [threading.Thread(target=self._slot, args=(i,), daemon=True, name=f"slot-{i}")
                         for i in range(self.slots)]
        self._threads.append(threading.Thread(target=self._housekeeping, daemon=True, name="housekeeping"))
        for t in self._threads:
            t.start()
        log(f"{self.process_id} started with {self.slots} slots")

    def join(self, timeout: float | None = None) -> None:
        for t in self._threads:
            t.join(timeout)


def main() -> None:
    from runtime import agents
    db.migrate()
    agents.seed_prompts()
    worker = Worker()

    def shutdown(signum, _frame) -> None:
        log(f"signal {signum}: finishing in-flight steps")
        worker.stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    worker.start()
    while not worker.stop.is_set():
        time.sleep(0.5)
    worker.join(timeout=30)


if __name__ == "__main__":
    main()
