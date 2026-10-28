import threading

import pytest

from runtime import budget, db, runs, service


def submit(owner="ana", **kw):
    return service.submit(owner, "procurement", {"item": "laptops", "quantity": 2, "max_unit_price": 9000}, **kw)


def test_idempotency_key_returns_same_run():
    first, created = submit(idempotency_key="k-1")
    again, created_again = submit(idempotency_key="k-1")
    assert created and not created_again and first["id"] == again["id"]


def test_idempotency_key_with_other_body_conflicts():
    submit(idempotency_key="k-1")
    with pytest.raises(runs.IdempotencyConflict):
        service.submit("ana", "procurement", {"item": "monitors", "quantity": 1, "max_unit_price": 1}, idempotency_key="k-1")


def test_input_is_validated_before_enqueue():
    with pytest.raises(service.InvalidInput) as exc:
        service.submit("ana", "procurement", {"item": "laptops"})
    assert "quantity" in str(exc.value)


def test_run_is_pinned_to_prompt_version():
    from runtime import prompts
    run, _ = submit()
    prompts.publish("procurement.system", "new text {org_name}", author="ops")
    assert runs.get(run["id"])["prompt_version"] == 1
    later, _ = submit()
    assert later["prompt_version"] == 2


def test_concurrent_claims_never_double_claim():
    budget.set_budget("ana", 10**9, max_concurrent=1000)
    ids = {str(submit()[0]["id"]) for _ in range(40)}
    claimed: list[str] = []
    lock = threading.Lock()

    def grab(n: int) -> None:
        while (c := runs.claim_next(f"w{n}")) is not None:
            with lock:
                claimed.append(c.id)

    threads = [threading.Thread(target=grab, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == sorted(ids)          # every run claimed exactly once
    assert len(set(claimed)) == len(claimed)


def test_per_owner_concurrency_cap():
    budget.set_budget("ana", 10**9, max_concurrent=2)
    for _ in range(4):
        submit()
    submit(owner="bob")
    got = [runs.claim_next("w") for _ in range(5)]
    owners = [c.owner for c in got if c]
    assert owners.count("ana") == 2 and owners.count("bob") == 1


def test_stale_runs_are_requeued_then_dead():
    run, _ = submit()
    for attempt in range(1, 4):
        c = runs.claim_next("w-crashy")
        assert c and c.attempts == attempt
        with db.tx() as cur:
            cur.execute("UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (c.id,))
        report = runs.recover_stale(stale_after_s=1, max_attempts=3)
    assert report["dead"] == [str(run["id"])]
    assert runs.get(run["id"])["status"] == "dead"


def test_writes_after_losing_ownership_fail():
    submit()
    c = runs.claim_next("w-old")
    with db.tx() as cur:
        cur.execute("UPDATE runs SET heartbeat_at = now() - interval '1 hour' WHERE id = %s", (c.id,))
    runs.recover_stale(1, 3)
    runs.claim_next("w-new")
    with pytest.raises(runs.LostOwnership):
        runs.checkpoint(c.id, "w-old", [])


def test_cancel_queued_and_running():
    queued, _ = submit()
    assert runs.request_cancel(str(queued["id"])) == "cancelled"
    submit()
    c = runs.claim_next("w")
    assert runs.request_cancel(c.id) == "cancel_requested"
    assert runs.checkpoint(c.id, "w", []) is True
