"""Pure first-party budget/store regression; no model/provider executed."""

from runtime import budget, service


def run_id():
    run, _ = service.submit("ana", "procurement", {"item": "laptops", "quantity": 1, "max_unit_price": 1})
    return str(run["id"])


def test_duplicate_settle_does_not_double_refund():
    budget.set_budget("ana", 1000)
    reservation = budget.reserve("ana", run_id(), 600)
    budget.settle(reservation, 250)
    budget.settle(reservation, 250)
    assert budget.remaining("ana") == 750


def test_release_after_settle_is_not_another_refund():
    budget.set_budget("ana", 1000)
    reservation = budget.reserve("ana", run_id(), 600)
    budget.settle(reservation, 250)
    budget.release(reservation)
    assert budget.remaining("ana") == 750


def test_two_distinct_reservations_same_run_stay_independent():
    budget.set_budget("ana", 1000)
    rid = run_id()
    first = budget.reserve("ana", rid, 400)
    second = budget.reserve("ana", rid, 400)
    budget.settle(first, 200)
    budget.release(second)
    assert budget.remaining("ana") == 800
