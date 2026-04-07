from datetime import datetime, timezone
import unittest

from loom.cron import next_run, parse_cron


class CronParsingTests(unittest.TestCase):
    def test_parse_cron_supports_steps_ranges_and_named_fields(self):
        minutes, hours, doms, months, dows = parse_cron(
            "*/15 9-17 * jan,mar mon-fri"
        )

        self.assertEqual(minutes, {0, 15, 30, 45})
        self.assertEqual(hours, set(range(9, 18)))
        self.assertEqual(months, {1, 3})
        self.assertEqual(dows, {1, 2, 3, 4, 5})
        self.assertIn(1, doms)
        self.assertIn(31, doms)

    def test_parse_cron_rejects_invalid_values(self):
        with self.assertRaises(ValueError):
            parse_cron("61 * * * *")

        with self.assertRaises(ValueError):
            parse_cron("* * *")

    def test_next_run_respects_target_timezone(self):
        after = datetime(2026, 4, 6, 11, 58, tzinfo=timezone.utc)

        nxt = next_run("0 8 * * mon", after, "America/New_York")

        self.assertEqual(
            nxt,
            datetime(2026, 4, 6, 12, 0, tzinfo=timezone.utc),
        )

    def test_next_run_raises_for_impossible_expression(self):
        with self.assertRaises(ValueError):
            next_run(
                "0 0 31 2 *",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
