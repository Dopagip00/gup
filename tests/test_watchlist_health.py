import copy
import datetime as dt
import io
import json
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

import watchlist_health as health
from engine.market_data import get_calendar


class WatchlistHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = dt.datetime(2026, 10, 20, 9, 20, tzinfo=health.TZ)
        item = {"symbol": "sh600001", "name": "测试证券", "asof": "2026-10-19", "score": 73.2,
                "model_validated": False, "eligible": False, "actionable": False,
                "components": {"technical": 72, "financial": 75},
                **dict.fromkeys(health.PRICE_FIELDS)}
        self.values = {
            "manifest": {"repository": "Dopagip00/gup", "privacy": "public-market-research-only",
                "snapshot_schema_version": 1, "app_version": "2.5.0",
                "generated_at": "2026-10-20T09:12:00+08:00"},
            "calendar": {str(y): get_calendar(y) for y in (2025, 2026)},
            "daily-watchlist": {"protocol": health.PROTOCOL, "date": "2026-10-20",
                "data_date": "2026-10-19", "signal_date": "2026-10-19",
                "generated_at": "2026-10-20T09:10:00+08:00", "release_at": "2026-10-20T09:00:00+08:00",
                "published_at": "2026-10-20T09:10:00+08:00", "publication_timing": "on_time", "late": False,
                "status": "watch_only", "model_validated": False, "eligible": False, "actionable": False,
                "items": [item], "top_pick": copy.deepcopy(item), "alerts": [], "withdrawn_symbols": []},
        }
        self.calls = []

    def loader(self, url):
        self.calls.append(url)
        if "/data/watchlists/" in url:
            return copy.deepcopy(self.values["daily-watchlist"])
        return copy.deepcopy(self.values[next(k for k, v in health.RESOURCE_URLS.items() if v == url)])

    def check(self, **kwargs):
        return health.check_watchlist(**{"now": self.now, "loader": self.loader,
            "expected_version": "2.5.0", **kwargs})

    def codes(self, result):
        return {e["code"] for e in result["errors"]}

    def prepared(self):
        self.values["manifest"]["generated_at"] = "2026-10-19T18:10:00+08:00"
        self.values["daily-watchlist"].update(generated_at="2026-10-19T18:00:00+08:00",
            prepared_at="2026-10-19T18:00:00+08:00", prepared_data_date="2026-10-19",
            published_at=None, publication_timing="prepared", late=False)

    def test_current_watchlist_is_ready_without_becoming_formal_buy(self):
        before = copy.deepcopy(self.values)
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertTrue(result["watchlist_ready"])
        self.assertTrue(result["has_top_pick"])
        self.assertTrue(result["released"])
        self.assertEqual(result["expected_data_date"], "2026-10-19")
        self.assertEqual(result["status"], "ready")
        self.assertNotIn("morning_ready", result)
        self.assertEqual(self.values, before)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(self.calls[0], self.calls[-1])
        self.assertIn(health.BASE_URL + "data/watchlists/2026-10-20.json", self.calls)

    def test_prepared_before_nine_passes_but_stays_hidden(self):
        self.prepared()
        result = self.check(now=self.now.replace(hour=8, minute=59, second=59))
        self.assertTrue(result["ok"])
        self.assertFalse(result["watchlist_ready"])
        self.assertFalse(result["released"])
        self.assertEqual(result["status"], "prepared")
        self.assertIsNone(result["published_at"])

    def test_nine_clock_releases_prepared_file_without_fabricating_server_publication(self):
        self.prepared()
        for moment in (self.now.replace(hour=9, minute=0), self.now.replace(hour=15), self.now.replace(hour=17)):
            with self.subTest(now=moment):
                result = self.check(now=moment)
                self.assertTrue(result["ok"])
                self.assertTrue(result["watchlist_ready"])
                self.assertTrue(result["released"])
                self.assertIsNone(result["published_at"])
                self.assertEqual(result["publication_timing"], "prepared")
                self.assertEqual(result["date"], "2026-10-20")

    def test_server_publication_cannot_be_early_future_or_falsely_on_time(self):
        original = copy.deepcopy(self.values["daily-watchlist"])
        for changes in ({"published_at": "2026-10-20T08:59:00+08:00"},
                        {"published_at": "2026-10-20T09:11:00+08:00"},
                        {"publication_timing": "late"}, {"late": True},
                        {"published_at": None}, {"published_at": "2026-10-20T09:10:00"}):
            with self.subTest(changes=changes):
                self.values["daily-watchlist"] = {**original, **changes}
                self.assertFalse(self.check()["ok"])

    def test_real_late_publication_is_accepted_with_honest_timing(self):
        self.values["manifest"]["generated_at"] = "2026-10-20T10:12:00+08:00"
        self.values["daily-watchlist"].update(generated_at="2026-10-20T10:10:00+08:00",
            published_at="2026-10-20T10:10:00+08:00", publication_timing="late", late=True)
        result = self.check(now=self.now.replace(hour=11))
        self.assertTrue(result["ok"])
        self.assertEqual(result["publication_timing"], "late")

    def archived_release(self):
        self.prepared()
        self.values["manifest"]["generated_at"] = "2026-10-20T09:12:00+08:00"
        self.values["daily-watchlist"].update(published_at="2026-10-20T09:10:00+08:00",
            publication_timing="on_time", release_basis="archived_preparation_not_recomputed",
            review_at="2026-10-20T09:10:00+08:00",
            current_risk_verification={"evidence_cutoff": "2026-10-20T09:10:00+08:00"})

    def test_archived_preparation_release_preserves_generation_and_actual_publication(self):
        self.archived_release()
        before = copy.deepcopy(self.values["daily-watchlist"])
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertTrue(result["watchlist_ready"])
        self.assertEqual(result["generated_at"], "2026-10-19T18:00:00+08:00")
        self.assertEqual(result["published_at"], "2026-10-20T09:10:00+08:00")
        self.assertEqual(result["release_basis"], "archived_preparation_not_recomputed")
        self.assertEqual(self.values["daily-watchlist"], before)

    def test_prepared_cohort_freeze_uses_same_strict_publication_evidence(self):
        self.archived_release()
        watch = self.values["daily-watchlist"]
        watch["release_basis"] = "prepared_cohort_frozen_at_release"
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertTrue(result["watchlist_ready"])
        self.assertEqual(result["generated_at"], "2026-10-19T18:00:00+08:00")
        self.assertEqual(result["release_basis"], "prepared_cohort_frozen_at_release")
        watch["review_at"] = "2026-10-20T09:11:00+08:00"
        watch["current_risk_verification"]["evidence_cutoff"] = watch["review_at"]
        self.assertTrue(self.check()["ok"])
        watch["current_risk_verification"]["evidence_cutoff"] = "2026-10-20T09:12:00+08:00"
        self.assertIn("watchlist_archive_review", self.codes(self.check()))

    def test_legacy_archived_release_can_receive_later_real_risk_review(self):
        self.archived_release()
        watch = self.values["daily-watchlist"]
        watch["review_at"] = "2026-10-20T09:11:00+08:00"
        watch["current_risk_verification"]["evidence_cutoff"] = watch["review_at"]
        result = self.check()
        self.assertTrue(result["ok"])
        self.assertEqual(result["published_at"], "2026-10-20T09:10:00+08:00")
        self.assertEqual(result["generated_at"], "2026-10-19T18:00:00+08:00")

    def test_archived_release_rejects_wrong_basis_fake_future_or_inconsistent_review(self):
        self.archived_release()
        original = copy.deepcopy(self.values["daily-watchlist"])
        for changes in ({"release_basis": "freshly_recomputed"}, {"release_basis": None},
                        {"generated_at": "2026-10-20T09:10:00+08:00"},
                        {"published_at": "2026-10-20T09:13:00+08:00"},
                        {"published_at": None}, {"prepared_at": None},
                        {"review_at": "2026-10-20T09:13:00+08:00"},
                        {"review_at": "2026-10-20T09:09:00+08:00"},
                        {"current_risk_verification": None},
                        {"current_risk_verification": {"evidence_cutoff": "2026-10-20T09:11:00+08:00"}},
                        {"current_risk_verification": {"evidence_cutoff": "2026-10-20T09:10:00"}}):
            with self.subTest(changes=changes):
                self.values["daily-watchlist"] = {**original, **changes}
                self.assertFalse(self.check()["ok"])

    def test_preparation_and_generated_times_cannot_use_future_data(self):
        self.prepared()
        original = copy.deepcopy(self.values["daily-watchlist"])
        for changes in ({"generated_at": "2026-10-19T18:11:00+08:00"},
                        {"prepared_at": "2026-10-19T18:01:00+08:00"},
                        {"prepared_at": "2026-10-19T14:00:00+08:00"},
                        {"prepared_data_date": "2026-10-16"},
                        {"generated_at": "2026-10-19T14:00:00+08:00", "prepared_at": "2026-10-19T14:00:00+08:00"}):
            with self.subTest(changes=changes):
                self.values["daily-watchlist"] = {**original, **changes}
                self.assertFalse(self.check()["ok"])

    def test_wrong_day_protocol_signal_and_release_fail_closed(self):
        original = copy.deepcopy(self.values["daily-watchlist"])
        for changes in ({"protocol": "old"}, {"date": "2026-10-19"}, {"date": "2026-10-21"},
                        {"data_date": "2026-10-16"}, {"signal_date": "2026-10-20"},
                        {"release_at": "2026-10-20T08:00:00+08:00"}):
            with self.subTest(changes=changes):
                self.values["daily-watchlist"] = {**original, **changes}
                self.assertFalse(self.check()["ok"])

    def test_insufficient_data_never_passes_after_nine(self):
        self.values["daily-watchlist"].update(status="insufficient_data", items=[], top_pick=None,
            published_at=None, publication_timing="on_time", data_date=None)
        result = self.check()
        self.assertFalse(result["ok"])
        self.assertFalse(result["watchlist_ready"])
        self.assertFalse(result["has_top_pick"])
        self.assertIn("watchlist_insufficient", self.codes(result))

    def test_flags_and_price_fields_cannot_turn_observation_into_buy(self):
        original = copy.deepcopy(self.values["daily-watchlist"])
        for flag in ("model_validated", "eligible", "actionable"):
            for value in (True, 0, None):
                with self.subTest(flag=flag, value=value):
                    self.values["daily-watchlist"] = copy.deepcopy(original)
                    self.values["daily-watchlist"][flag] = value
                    self.assertFalse(self.check()["ok"])
                    self.values["daily-watchlist"] = copy.deepcopy(original)
                    self.values["daily-watchlist"]["items"][0][flag] = value
                    self.assertFalse(self.check()["ok"])
        for field in health.PRICE_FIELDS:
            self.values["daily-watchlist"] = copy.deepcopy(original)
            self.values["daily-watchlist"]["items"][0][field] = 12.5
            self.assertIn("watchlist_price_fields", self.codes(self.check()))
            self.values["daily-watchlist"]["items"][0].pop(field)
            self.assertIn("watchlist_price_fields", self.codes(self.check()))

    def test_finite_bounded_scores_unique_count_and_symbols(self):
        original = copy.deepcopy(self.values["daily-watchlist"])
        for score in (-.01, 100.01, True, "73", None, float("inf"), float("nan")):
            with self.subTest(score=score):
                self.values["daily-watchlist"] = copy.deepcopy(original)
                self.values["daily-watchlist"]["items"][0]["score"] = score
                self.assertFalse(self.check()["ok"])
        self.values["daily-watchlist"] = copy.deepcopy(original)
        self.values["daily-watchlist"]["items"] *= 2
        self.assertIn("watchlist_symbols", self.codes(self.check()))
        self.values["daily-watchlist"]["items"] *= 6
        self.assertIn("watchlist_items", self.codes(self.check()))

    def test_withdrawn_original_first_is_not_replaced_by_next_survivor(self):
        watch = self.values["daily-watchlist"]
        second = {**copy.deepcopy(watch["items"][0]), "symbol": "sh600002"}
        watch["items"].append(second)
        watch["withdrawn_symbols"] = [watch["items"][0]["symbol"]]
        result = self.check()
        self.assertFalse(result["ok"])
        self.assertFalse(result["has_top_pick"])
        self.assertIn("watchlist_top_withdrawn", self.codes(result))
        watch["top_pick"] = None
        self.assertIn("watchlist_top_withdrawn", self.codes(self.check()))
        watch["top_pick"] = second
        self.assertIn("watchlist_top_pick", self.codes(self.check()))

    def test_alert_alone_can_revoke_first_and_malformed_withdrawal_fails(self):
        watch = self.values["daily-watchlist"]
        watch["alerts"] = [{"symbol": "sh600001", "withdrawn": True}]
        self.assertIn("watchlist_top_withdrawn", self.codes(self.check()))
        for withdrawn in ([{}], ["sh600999"], ["sh600001", "sh600001"]):
            watch["withdrawn_symbols"] = withdrawn
            self.assertFalse(self.check()["ok"])

    def test_holiday_skips_ranking_requirement_but_not_official_calendar(self):
        self.values["manifest"]["generated_at"] = "2026-10-01T08:00:00+08:00"
        self.values["daily-watchlist"] = {}
        now = self.now.replace(day=1)
        result = self.check(now=now)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "skipped")
        self.assertFalse(result["released"])
        self.assertEqual(len(self.calls), 3)
        self.assertFalse(any("/data/watchlists/" in url for url in self.calls))
        self.values["calendar"]["2026"]["source"] = "https://example.com/"
        self.assertIn("calendar_unverified", self.codes(self.check(now=now)))

    def test_missing_calendar_future_or_wrong_manifest_version_is_rejected(self):
        self.values["calendar"].pop("2026")
        self.assertFalse(self.check()["ok"])
        self.values["calendar"]["2026"] = get_calendar(2026)
        self.values["manifest"]["app_version"] = "2.4.2"
        self.assertIn("version_mismatch", self.codes(self.check()))
        self.values["manifest"]["app_version"] = "2.5.0"
        self.values["manifest"]["generated_at"] = "2026-10-20T09:21:00+08:00"
        self.assertIn("future_snapshot", self.codes(self.check()))

    def test_mixed_deployment_has_bounded_retry(self):
        def mixed_once(url):
            value = self.loader(url)
            if len(self.calls) == 4:
                value["generated_at"] = "2026-10-20T09:13:00+08:00"
            return value
        result = self.check(loader=mixed_once)
        self.assertTrue(result["ok"])
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(self.calls), 8)
        self.assertEqual(result["previous_attempt_errors"][0]["code"], "mixed_deployment")

    def test_invalid_oversized_unavailable_json_and_invalid_options_fail(self):
        for payload in (b"[]", b"<html>404</html>", b'{"x":1e309}', b'{"x":NaN}', b" " * (128 * 1024 + 1)):
            self.assertFalse(self.check(loader=lambda url: payload)["ok"])
        loader = Mock(side_effect=urllib.error.URLError("unavailable"))
        self.assertFalse(self.check(loader=loader)["ok"])
        self.assertEqual(loader.call_count, 2)
        for options in ({"attempts": 3}, {"attempts": True}, {"now": self.now.replace(tzinfo=None)}):
            self.assertEqual(self.check(**options)["attempts"], 0)

    def test_network_scope_rejects_external_credentials_queries_and_paths(self):
        for url in ("http://dopagip00.github.io/gup/data/daily-watchlist.json",
                    "https://evil.example/daily-watchlist.json",
                    "https://user@dopagip00.github.io/gup/data/daily-watchlist.json",
                    "https://dopagip00.github.io/gup/data/watchlists/../../private.json",
                    "https://dopagip00.github.io/gup/data/watchlists/2026-10-20.json?token=x"):
            with self.subTest(url=url), self.assertRaises(health.HealthError):
                health._download(url)
        request = urllib.request.Request(health.RESOURCE_URLS["manifest"])
        with self.assertRaises(health.HealthError):
            health._SameOriginRedirect().redirect_request(request, None, 302, "redirect", {}, "https://example.com/")

    def test_network_size_and_timeout_are_bounded(self):
        url = health.RESOURCE_URLS["daily-watchlist"]
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.geturl.return_value = url
        response.status = 200
        response.headers = {"Content-Length": str(128 * 1024 + 1)}
        opener = Mock()
        opener.open.return_value = response
        with patch.object(health.urllib.request, "build_opener", return_value=opener), self.assertRaises(health.HealthError):
            health._download(url)
        response.read.assert_not_called()
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 5)

    def test_cli_exit_tracks_observation_health(self):
        for ok in (True, False):
            with patch.object(health, "check_watchlist", return_value={"ok": ok}) as check, patch("sys.stdout", new_callable=io.StringIO) as output:
                result = health.main(["--expected-version", "2.5.0"])
            self.assertEqual(result, 0 if ok else 1)
            self.assertEqual(json.loads(output.getvalue()), {"ok": ok})
            self.assertEqual(check.call_args.kwargs["expected_version"], "2.5.0")


if __name__ == "__main__":
    unittest.main()
