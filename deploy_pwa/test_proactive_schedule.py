import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from deploy_pwa.proactive_schedule import ProactiveSchedule, Topic


SH = ZoneInfo("Asia/Shanghai")
DAY = datetime(2026, 10, 14, 8, 0, tzinfo=SH)


class ProactiveScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / "proactive.sqlite3")
        self.scheduler = ProactiveSchedule(self.path, randbelow=lambda n: n // 2)
        self.sent = []
        self.good_topic = lambda owner: Topic(owner, "event-nicole-1", "最近读的那本书")

    def consent(self, owner="nicole", now=DAY):
        self.scheduler.set_consent(owner, True, now=now)

    def test_disabled_by_default_and_owner_isolated(self):
        self.assertIsNone(self.scheduler.schedule_day("nicole", now=DAY))
        self.consent()
        self.assertIsNone(self.scheduler.settings("other"))
        self.assertIsNone(self.scheduler.schedule_day("other", now=DAY))
        self.assertEqual(self.scheduler.settings("nicole")["awake_start_hour"], 9)

    def test_random_slot_is_inside_shanghai_awake_window_and_stable(self):
        self.consent()
        slot = self.scheduler.schedule_day("nicole", now=DAY)
        self.assertEqual(slot["local_day"], "2026-10-14")
        due = datetime.fromtimestamp(slot["due_at"], SH)
        self.assertEqual((due.hour, due.minute), (15, 0))
        self.assertEqual(self.scheduler.schedule_day("nicole", now=DAY)["due_at"], slot["due_at"])

    def test_late_consent_never_backdates_or_sends_in_quiet_hours(self):
        late = DAY.replace(hour=20, minute=30)
        self.consent(now=late)
        slot = self.scheduler.schedule_day("nicole", now=late)
        due = datetime.fromtimestamp(slot["due_at"], SH)
        self.assertGreaterEqual(due, late)
        self.assertLess(due, DAY.replace(hour=21))
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=DAY.replace(hour=22)),
                         "skipped")
        self.assertFalse(self.sent)

    def test_due_delivery_once_with_source_and_generic_lock_screen(self):
        self.consent()
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=DAY), "scheduled")
        when = DAY.replace(hour=16)
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=when), "sent")
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=when), "sent")
        self.assertEqual(len(self.sent), 1)
        item = self.sent[0]
        self.assertEqual(item.source_event_id, "event-nicole-1")
        self.assertEqual(item.idempotency_key, "proactive:nicole:2026-10-14")
        self.assertIn("最近读的那本书", item.in_app_text)
        self.assertNotIn("最近读的那本书", item.push_text)
        self.assertNotIn("在哪", item.in_app_text)

    def test_other_account_delivery_never_addresses_nicole(self):
        self.consent("WAITIN")
        topic = lambda owner: Topic(owner, "event-waitin-1", "最近的设计")
        self.assertEqual(self.scheduler.run_due("WAITIN", topic, self.sent.append, now=DAY),
                         "scheduled")
        self.assertEqual(self.scheduler.run_due("WAITIN", topic, self.sent.append,
                                                now=DAY.replace(hour=16)), "sent")
        self.assertEqual(self.sent[0].owner_id, "WAITIN")
        self.assertIn("最近的设计", self.sent[0].in_app_text)
        self.assertNotIn("Nicole", self.sent[0].in_app_text + self.sent[0].push_text)

    def test_no_verified_topic_skips_day_without_delivery(self):
        self.consent()
        self.scheduler.schedule_day("nicole", now=DAY)
        when = DAY.replace(hour=16)
        self.assertEqual(self.scheduler.run_due("nicole", lambda _: None,
                                               self.sent.append, now=when), "skipped")
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=when), "skipped")
        self.assertFalse(self.sent)

    def test_cross_owner_topic_is_rejected(self):
        self.consent()
        self.scheduler.schedule_day("nicole", now=DAY)
        when = DAY.replace(hour=16)
        other = lambda _: Topic("other", "event-other", "私密内容")
        self.assertEqual(self.scheduler.run_due("nicole", other, self.sent.append,
                                               now=when), "skipped")
        self.assertFalse(self.sent)

    def test_opt_out_cancels_future_slot_and_blocks_reenable_same_day(self):
        self.consent()
        self.scheduler.schedule_day("nicole", now=DAY)
        self.scheduler.set_consent("nicole", False, now=DAY.replace(hour=10))
        self.assertFalse(self.scheduler.settings("nicole")["enabled"])
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=DAY.replace(hour=16)),
                         "disabled_or_outside_window")
        self.scheduler.set_consent("nicole", True, now=DAY.replace(hour=16))
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=DAY.replace(hour=16)),
                         "cancelled")
        self.assertFalse(self.sent)

    def test_uncertain_delivery_failure_is_never_retried(self):
        self.consent()
        self.scheduler.schedule_day("nicole", now=DAY)
        when = DAY.replace(hour=16)
        calls = []
        def failing(item):
            calls.append(item)
            raise TimeoutError("secret token should not be logged")
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               failing, now=when), "failed_unknown")
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               failing, now=when), "failed_unknown")
        self.assertEqual(len(calls), 1)

    def test_rolling_24_hours_between_delivery_attempts(self):
        self.consent()
        self.scheduler.schedule_day("nicole", now=DAY)
        first = DAY.replace(hour=20, minute=50)
        self.assertEqual(self.scheduler.run_due("nicole", self.good_topic,
                                               self.sent.append, now=first), "sent")
        next_morning = DAY + timedelta(days=1, hours=1)
        next_slot = self.scheduler.schedule_day("nicole", now=next_morning)
        self.assertGreaterEqual(next_slot["due_at"],
                                (first + timedelta(hours=24)).timestamp())
        self.assertLess(next_slot["due_at"],
                        (DAY + timedelta(days=1)).replace(hour=21).timestamp())

    def test_two_workers_claim_only_one_delivery(self):
        self.consent()
        self.scheduler.schedule_day("nicole", now=DAY)
        when = DAY.replace(hour=16)
        barrier = threading.Barrier(2)
        results = []
        lock = threading.Lock()
        def topic(owner):
            barrier.wait(timeout=3)
            return self.good_topic(owner)
        def worker():
            result = ProactiveSchedule(self.path).run_due(
                "nicole", topic, self.sent.append, now=when)
            with lock:
                results.append(result)
        workers = [threading.Thread(target=worker) for _ in range(2)]
        for worker_thread in workers:
            worker_thread.start()
        for worker_thread in workers:
            worker_thread.join(timeout=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("sent", results)

    def test_invalid_configuration_fails_closed(self):
        with self.assertRaises(ValueError):
            self.scheduler.set_consent("nicole", True, awake_start_hour=21,
                                       awake_end_hour=9, now=DAY)
        with self.assertRaises(ValueError):
            self.scheduler.set_consent("nicole", True, timezone_name="Not/A_Zone", now=DAY)
        with self.assertRaises(ValueError):
            self.scheduler.schedule_day("nicole", now=datetime(2026, 10, 14))


if __name__ == "__main__":
    unittest.main()
