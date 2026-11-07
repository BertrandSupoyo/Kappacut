"""The pure parts of the Admin dashboard: what counts as a failure, what asks for an admin's
attention, how the charts' days are filled in, and how a search box's text is escaped.

No database or network. The routes themselves are exercised against real Postgres in a throwaway
stack before a release.

Run:  python tests/test_admin.py
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.admin_stats import (  # noqa: E402
    QUEUE_WAIT_WARN,
    attention_items,
    fill_days,
    job_outcome,
    like_pattern,
)

HEALTHY = {"redis": True, "disk_ok": True, "disk_free_pct": 92.0}


def _items(**over):
    args = dict(system=HEALTHY, workers=1, oldest_queued_s=None, longest_running_s=None, failed_24h=0, repeat=[],
                legal_reviewed=True, disk_min_free_pct=4.0, job_timeout_s=3600)
    return attention_items(**{**args, **over})


def test_a_healthy_system_asks_for_nothing():
    assert _items() == []


def test_no_worker_is_critical_because_jobs_would_wait_forever():
    item = _items(workers=0)[0]
    assert item["level"] == "critical" and "No worker" in item["text"]


def test_redis_down_doesnt_also_claim_there_are_no_workers():
    items = _items(system={**HEALTHY, "redis": False}, workers=None)
    assert len(items) == 1 and "Redis" in items[0]["text"]


def test_unknown_worker_count_isnt_reported_as_none_running():
    assert _items(workers=None) == []


def test_low_disk_says_how_low_and_what_it_stops():
    text = _items(system={**HEALTHY, "disk_ok": False, "disk_free_pct": 3.5})[0]["text"]
    assert "3.5%" in text and "refused below 4%" in text


def test_a_short_queue_wait_is_normal():
    assert _items(oldest_queued_s=QUEUE_WAIT_WARN - 1) == []
    item = _items(oldest_queued_s=QUEUE_WAIT_WARN)[0]
    assert item["level"] == "warning" and "5 minutes" in item["text"] and item["to"] == "/admin/jobs?status=active"


def test_a_job_past_the_timeout_may_be_stuck():
    assert _items(longest_running_s=3600) == []
    assert "2 hours" in _items(longest_running_s=7200)[0]["text"]


def test_failures_link_to_the_failed_filter_and_count_correctly():
    assert "1 job failed" in _items(failed_24h=1)[0]["text"]
    item = _items(failed_24h=3)[0]
    assert "3 jobs failed" in item["text"] and item["to"] == "/admin/jobs?status=failed"


def test_repeat_infringers_are_named_and_linked_with_a_safe_address():
    item = _items(repeat=[("a+b@example.com", 2)])[0]
    assert "a+b@example.com has had 2" in item["text"]
    assert item["to"] == "/admin/users?q=a%2Bb%40example.com"           # a bare + would read as a space


def test_a_draft_legal_page_is_a_reminder_not_an_alarm():
    assert _items(legal_reviewed=False)[0]["level"] == "info"


def test_the_worst_comes_first():
    levels = [i["level"] for i in _items(legal_reviewed=False, failed_24h=1, workers=0)]
    assert levels == ["critical", "warning", "info"]


def test_only_a_job_with_a_raw_error_counts_as_failed():
    assert job_outcome("error", "RuntimeError: command failed") == "failed"
    assert job_outcome("error", None) == "stopped"                      # a Cancel, a refused video
    assert job_outcome("error", "") == "stopped"
    for status in ("queued", "running", "done"):
        assert job_outcome(status, None) == status


def test_every_day_appears_oldest_first_even_a_quiet_one():
    today = dt.date(2026, 11, 5)
    days = fill_days(today, {today: 3}, {today - dt.timedelta(days=1): 2}, {today: 90.04}, {}, days=3)
    assert [d["date"] for d in days] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    assert days[0] == {"date": "2026-11-03", "done": 0, "failed": 0, "minutes": 0.0, "signups": 0}
    assert days[1]["failed"] == 2 and days[2]["done"] == 3 and days[2]["minutes"] == 90.0


def test_days_cross_a_month_boundary():
    days = fill_days(dt.date(2026, 11, 1), {}, {}, {}, {}, days=2)
    assert [d["date"] for d in days] == ["2026-10-31", "2026-11-01"]


def test_search_text_matches_literally():
    assert like_pattern("  bob ") == "%bob%"
    assert like_pattern("100%") == "%100\\%%"
    assert like_pattern("a_b") == "%a\\_b%"
    assert like_pattern("c:\\x") == "%c:\\\\x%"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
