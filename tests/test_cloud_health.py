import copy
import datetime as dt
import io
import json
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

import cloud_health as health
from engine.market_data import get_calendar


class CloudHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 19, 9, 20, tzinfo=health.TZ)
        self.values = {
            "manifest": {"generated_at": "2026-10-19T09:12:00+08:00", "data_date": "2026-10-16",
                         "snapshot_schema_version": 1, "data_schema_version": 1,
                         "app_version": "2.3.0", "stock_files": 2321, "reports": 1,
                         "privacy": "public-market-research-only", "repository": "Dopagip00/gup"},
            "daily-plan": {"date": "2026-10-19", "status": "published", "data_date": "2026-10-16",
                           "published_at": "2026-10-19T09:10:00+08:00", "morning_ready": True,
                           "late": False, "buy": None, "sell": [],
                           "timezone": "Asia/Shanghai", "mode": "manual_research"},
            "calendar": {str(year): get_calendar(year) for year in (2025, 2026)},
            "system": {"hosted": True, "version": {"version": "2.3.0", "data_schema_version": 1,
                                                     "project_id": "huyan-ashare"}},
        }
        self.calls = []

    def loader(self, url):
        self.calls.append(url)
        name = next(name for name, known in health.RESOURCE_URLS.items() if url == known)
        return copy.deepcopy(self.values[name])

    def check(self, **kwargs):
        defaults = dict(now=self.now, loader=self.loader, require_morning=True,
                        expected_version="2.3.0")
        defaults.update(kwargs)
        return health.check_site(**defaults)

    def codes(self, result):
        return {item["code"] for item in result["errors"]}

    def test_complete_morning_with_zero_recommendations_is_healthy(self):
        before = copy.deepcopy(self.values)
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "healthy")
        self.assertTrue(result["morning"]["ready"])
        self.assertEqual(result["expected_data_date"], "2026-10-16")
        self.assertTrue(result["snapshot_readable"])
        self.assertTrue(result["data_ready"])
        self.assertEqual(result["data_status"], "ready")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.calls[0], self.calls[-1])
        self.assertEqual(self.values, before)

    def test_afternoon_morning_acceptance_uses_morning_knowledge_boundary(self):
        result = self.check(now=self.now.replace(hour=16, minute=0))
        self.assertTrue(result["ok"])
        self.assertEqual(result["expected_data_date"], "2026-10-16")
        self.assertEqual(result["latest_completed_session"], "2026-10-19")
        self.assertEqual(result["expected_morning_data_date"], "2026-10-16")
        self.assertEqual(result["data_status"], "await_close_publication")

    def test_stale_close_data_fails_at_deadline_even_when_every_json_is_readable(self):
        for hour, minute in ((16, 20), (17, 10), (21, 0)):
            with self.subTest(hour=hour, minute=minute):
                result = self.check(now=self.now.replace(hour=hour, minute=minute), require_morning=False)
                self.assertFalse(result["ok"])
                self.assertEqual(result["status"], "stale")
                self.assertTrue(result["snapshot_readable"])
                self.assertFalse(result["data_ready"])
                self.assertEqual(result["data_status"], "stale")
                self.assertEqual(result["data_date"], "2026-10-16")
                self.assertEqual(result["expected_data_date"], "2026-10-19")
                self.assertEqual(self.codes(result), {"stale_market_data"})
                self.assertEqual(result["attempts"], 2)
        just_before = self.check(now=self.now.replace(hour=16, minute=19, second=59))
        self.assertTrue(just_before["ok"])
        self.assertEqual(just_before["data_status"], "await_close_publication")

    def test_fresh_close_snapshot_does_not_force_immutable_morning_plan_to_change(self):
        before = copy.deepcopy(self.values["daily-plan"])
        self.values["manifest"].update(generated_at="2026-10-19T16:00:00+08:00", data_date="2026-10-19")
        result = self.check(now=self.now.replace(hour=21, minute=0))
        self.assertTrue(result["ok"])
        self.assertTrue(result["data_ready"])
        self.assertEqual(result["data_status"], "ready")
        self.assertEqual(result["expected_data_date"], "2026-10-19")
        self.assertEqual(result["expected_morning_data_date"], "2026-10-16")
        self.assertTrue(result["morning"]["ready"])
        self.assertFalse(result["data_fresh_for_morning"])
        self.assertEqual(self.values["daily-plan"], before)

    def test_genuine_new_close_data_is_accepted_before_publication_deadline(self):
        self.values["manifest"].update(generated_at="2026-10-19T16:00:00+08:00", data_date="2026-10-19")
        result = self.check(now=self.now.replace(hour=16, minute=10))
        self.assertTrue(result["ok"])
        self.assertTrue(result["data_ready"])
        self.assertEqual(result["expected_data_date"], "2026-10-16")
        self.assertEqual(result["data_status"], "ready")

    def test_stale_previous_session_is_not_accepted_as_current_morning_data(self):
        self.values["manifest"]["data_date"] = "2026-10-15"
        result = self.check(require_morning=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["data_ready"])
        self.assertEqual(result["expected_data_date"], "2026-10-16")
        self.assertEqual(self.codes(result), {"stale_market_data"})

    def test_weekend_requires_friday_data_and_does_not_require_a_new_weekend_plan(self):
        self.values["manifest"].update(generated_at="2026-10-23T17:00:00+08:00", data_date="2026-10-23")
        result = self.check(now=self.now.replace(day=24))
        self.assertTrue(result["ok"])
        self.assertFalse(result["trading_day"])
        self.assertEqual(result["expected_data_date"], "2026-10-23")
        self.assertEqual(result["morning"]["status"], "closed")
        self.values["manifest"]["data_date"] = "2026-10-22"
        stale = self.check(now=self.now.replace(day=24))
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["status"], "stale")
        self.assertEqual(self.codes(stale), {"stale_market_data"})

    def test_ordinary_resource_check_does_not_require_morning(self):
        self.values["daily-plan"].update(status="pending", morning_ready=False, late=True, published_at=None)
        ordinary = self.check(require_morning=False)
        self.assertTrue(ordinary["ok"])
        self.assertFalse(ordinary["morning"]["ready"])
        self.assertFalse(ordinary["morning"]["required"])
        strict = self.check()
        self.assertFalse(strict["ok"])
        self.assertIn("morning_unready", self.codes(strict))
        self.assertEqual(strict["attempts"], 2)

    def test_holiday_skips_only_morning_requirement(self):
        self.values["manifest"].update(generated_at="2026-10-01T09:12:00+08:00", data_date="2026-09-30")
        self.values["daily-plan"].update(date="2026-09-30", status="pending", morning_ready=False,
                                         late=True, data_date="2026-09-29", published_at=None)
        result = self.check(now=dt.datetime(2026, 10, 1, 9, 20, tzinfo=health.TZ))
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["trading_day"])
        self.assertEqual(result["morning"]["status"], "closed")
        self.values["system"]["hosted"] = False
        self.assertFalse(self.check(now=dt.datetime(2026, 10, 1, 9, 20, tzinfo=health.TZ))["ok"])

    def test_calendar_requires_valid_official_annual_coverage_even_on_weekend(self):
        for mutate in (lambda c: c["2026"].update(source="https://example.com/calendar"),
                       lambda c: c["2026"].update(holidays=["2026-10-01"]),
                       lambda c: c.pop("2026")):
            with self.subTest(mutate=mutate):
                original = copy.deepcopy(self.values["calendar"])
                mutate(self.values["calendar"])
                result = self.check(now=self.now.replace(day=24))
                self.assertFalse(result["ok"])
                self.assertIn("calendar_unverified", self.codes(result))
                self.values["calendar"] = original

    def test_morning_flags_date_and_data_day_are_strict(self):
        original = copy.deepcopy(self.values["daily-plan"])
        for changes in ({"date": "2026-10-16"}, {"status": "pending"}, {"morning_ready": 1},
                        {"late": None}, {"late": True}, {"data_date": "2026-10-15"}):
            with self.subTest(changes=changes):
                self.values["daily-plan"] = {**original, **changes}
                self.assertIn("morning_unready", self.codes(self.check()))

    def test_morning_timestamp_must_be_actual_local_window_and_not_future(self):
        for value in ("2026-10-19T08:59:59+08:00", "2026-10-19T09:30:00+08:00",
                      "2026-10-19T09:21:00+08:00", "2026-10-18T09:10:00+08:00",
                      "2026-10-19T09:10:00", None):
            with self.subTest(value=value):
                self.values["daily-plan"]["published_at"] = value
                self.assertFalse(self.check()["ok"])
        self.values["daily-plan"]["published_at"] = "2026-10-19T01:10:00Z"
        self.assertTrue(self.check()["ok"])

    def test_future_snapshot_or_noncompleted_or_holiday_data_is_rejected(self):
        original = copy.deepcopy(self.values["manifest"])
        for changes in ({"generated_at": "2026-10-19T09:21:00+08:00"},
                        {"generated_at": "2026-10-19T09:12:00"},
                        {"data_date": "2026-10-19"}, {"data_date": "2026-10-17"},
                        {"data_date": "2026-10-01"}):
            with self.subTest(changes=changes):
                self.values["manifest"] = {**original, **changes}
                self.assertFalse(self.check(require_morning=False)["ok"])

    def test_static_schema_and_versions_cannot_silently_accept_old_deployment(self):
        for name, key, value in (("manifest", "snapshot_schema_version", None),
                                 ("manifest", "data_schema_version", True),
                                 ("manifest", "app_version", "2.2.0"),
                                 ("manifest", "stock_files", 0),
                                 ("manifest", "repository", "someone/else")):
            with self.subTest(name=name, key=key):
                original = copy.deepcopy(self.values[name])
                self.values[name][key] = value
                self.assertFalse(self.check()["ok"])
                self.values[name] = original
        self.values["system"]["version"]["version"] = "2.2.0"
        self.assertIn("version_mismatch", self.codes(self.check()))

    def test_missing_buy_field_is_bad_but_null_is_valid(self):
        self.values["daily-plan"].pop("buy")
        self.assertIn("plan_buy_shape", self.codes(self.check()))
        self.values["daily-plan"]["buy"] = "unknown"
        self.assertIn("plan_buy_shape", self.codes(self.check()))

    def test_manifest_mixed_deployment_retries_once_then_accepts_stable(self):
        def mixed_once(url):
            result = self.loader(url)
            if len(self.calls) == 5:
                result["generated_at"] = "2026-10-19T09:13:00+08:00"
            return result
        result = self.check(loader=mixed_once)
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(self.calls), 10)
        self.assertEqual(result["previous_attempt_errors"][0]["code"], "mixed_deployment")

    def test_repeated_mixed_deployment_never_exceeds_two_rounds(self):
        def always_mixed(url):
            result = self.loader(url)
            if len(self.calls) % 5 == 0:
                result["generated_at"] = "2026-10-19T09:13:00+08:00"
            return result
        result = self.check(loader=always_mixed)
        self.assertFalse(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(self.calls), 10)
        self.assertEqual(self.codes(result), {"mixed_deployment"})

    def test_missing_public_resource_is_failure_after_bounded_retry(self):
        loader = Mock(side_effect=urllib.error.URLError("unavailable"))
        result = self.check(loader=loader)
        self.assertFalse(result["ok"])
        self.assertEqual(loader.call_count, 2)

    def test_malformed_nonobject_and_oversized_json_fail(self):
        for value in (b"<html>404</html>", b"[]", b'{"a":NaN}', b" " * (health.MAX_BYTES["manifest"] + 1)):
            with self.subTest(value=value[:30]):
                self.assertFalse(self.check(loader=lambda url: value)["ok"])

    def test_attempts_and_now_cannot_be_unbounded_or_naive(self):
        loader = Mock()
        for options in ({"attempts": 3}, {"attempts": True}, {"require_morning": "true"},
                        {"now": self.now.replace(tzinfo=None)}):
            result = self.check(loader=loader, **options)
            self.assertFalse(result["ok"])
            self.assertEqual(result["attempts"], 0)
        loader.assert_not_called()

    def test_fixed_origin_rejects_external_queries_credentials_and_redirects(self):
        for url in ("http://dopagip00.github.io/gup/data/manifest.json",
                    "https://example.com/gup/data/manifest.json",
                    "https://dopagip00.github.io/gup/data/manifest.json?token=secret",
                    "https://dopagip00.github.io/gup/data/../manifest.json",
                    "https://user@dopagip00.github.io/gup/data/manifest.json"):
            with self.subTest(url=url), self.assertRaises(health.HealthError):
                health._download(url)
        handler = health._SameOriginRedirect()
        request = urllib.request.Request(health.RESOURCE_URLS["manifest"])
        with self.assertRaises(health.HealthError):
            handler.redirect_request(request, None, 302, "redirect", {}, "https://example.com/")

    def test_network_reader_limits_header_bytes_and_timeout(self):
        url = health.RESOURCE_URLS["manifest"]
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = url
        response.status = 200
        response.headers = {"Content-Length": str(health.MAX_BYTES["manifest"] + 1)}
        opener = Mock()
        opener.open.return_value = response
        with patch.object(health.urllib.request, "build_opener", return_value=opener), self.assertRaises(health.HealthError):
            health._download(url)
        response.read.assert_not_called()
        self.assertEqual(opener.open.call_args.kwargs["timeout"], health.TIMEOUT_SECONDS)
        response.headers = {}
        response.read.return_value = b"x" * (health.MAX_BYTES["manifest"] + 1)
        with patch.object(health.urllib.request, "build_opener", return_value=opener), self.assertRaises(health.HealthError):
            health._download(url)
        response.read.assert_called_once_with(health.MAX_BYTES["manifest"] + 1)

    def test_cli_exit_code_tracks_real_health(self):
        for ok in (True, False):
            with patch.object(health, "check_site", return_value={"ok": ok}) as check, patch("sys.stdout", new_callable=io.StringIO) as output:
                code = health.main(["--require-morning", "--expected-version", "2.3.0"])
            self.assertEqual(code, 0 if ok else 1)
            self.assertEqual(json.loads(output.getvalue()), {"ok": ok})
            self.assertTrue(check.call_args.kwargs["require_morning"])


if __name__ == "__main__":
    unittest.main()
