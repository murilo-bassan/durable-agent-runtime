import threading

import pytest

from runtime import budget, prompts, service


def run_id():
    return str(service.submit("ana", "procurement", {"item": "laptops", "quantity": 1, "max_unit_price": 1})[0]["id"])


def test_reserve_settle_release():
    budget.set_budget("ana", 1000)
    rid = run_id()
    r = budget.reserve("ana", rid, 600)
    assert budget.remaining("ana") == 400
    budget.settle(r, 250)
    assert budget.remaining("ana") == 750
    r2 = budget.reserve("ana", rid, 700)
    budget.release(r2)
    assert budget.remaining("ana") == 750


def test_over_budget_raises():
    budget.set_budget("ana", 100)
    with pytest.raises(budget.BudgetExceeded) as exc:
        budget.reserve("ana", run_id(), 101)
    assert exc.value.remaining == 100


def test_concurrent_reservations_cannot_overspend():
    budget.set_budget("ana", 1000)
    rid = run_id()
    ok, refused = [], []

    def try_reserve():
        try:
            ok.append(budget.reserve("ana", rid, 300))
        except budget.BudgetExceeded:
            refused.append(1)

    threads = [threading.Thread(target=try_reserve) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 3 and len(refused) == 7      # 3 × 300 ≤ 1000 < 4 × 300


def test_prompt_versions_and_single_active():
    p2 = prompts.publish("procurement.system", "v2 for {org_name}", author="ops")
    p2_again = prompts.publish("procurement.system", "v2 for {org_name}", author="ops")
    assert p2.version == p2_again.version == 2
    prompts.publish("procurement.system", "v3 {org_name}", author="ops", activate=False)
    assert prompts.active("procurement.system").version == 2
    assert [p.version for p in prompts.versions("procurement.system")] == [1, 2, 3]


def test_render_requires_all_variables():
    with pytest.raises(prompts.PromptError):
        prompts.active("procurement.system").render({})
