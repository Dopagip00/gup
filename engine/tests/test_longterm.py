import copy
import unittest
from unittest.mock import patch

import longterm


STOCK = {"symbol": "sh600298", "name": "安琪酵母"}
QUOTE = {"symbol": "sh600298", "pe": 20, "pb": 2.5, "market_cap_100m": 300,
         "date": "2026-10-16", "source": "https://qt.gtimg.cn/"}


def reports():
    output = []
    for period, publication, profit, cash, recurring in (
        ("2026-06-30", "2026-08-20", 12e8, 14e8, 11e8),
        ("2025-12-31", "2026-03-20", 20e8, 24e8, 18e8),
        ("2025-06-30", "2025-08-20", 10e8, 12e8, 9e8),
    ):
        output.append({"SECURITY_CODE": "600298", "REPORT_DATE": period, "NOTICE_DATE": publication,
                       "UPDATE_DATE": publication, "PARENTNETPROFIT": profit, "NETCASH_OPERATE_PK": cash,
                       "KCFJCXSYJLR": recurring, "ROEJQ": 15, "TOTALOPERATEREVETZ": 12,
                       "PARENTNETPROFITTZ": 20, "ZCFZL": 40})
    return output


class LongTermTests(unittest.TestCase):
    def run_refresh(self, rows=None, quote=None):
        with patch.object(longterm, "_fetch_reports", return_value=reports() if rows is None else rows):
            return longterm.refresh([STOCK], {STOCK["symbol"]: quote or QUOTE}, "2026-10-16")

    def test_ttm_pe_is_computed_from_three_disclosed_periods(self):
        result = self.run_refresh()["stocks"][0]
        self.assertAlmostEqual(result["pe_ttm"], 300 / (12 + 20 - 10), places=2)
        self.assertNotEqual(result["pe_ttm"], QUOTE["pe"])
        self.assertEqual(len(result["ttm_profit_components"]), 3)
        self.assertEqual(result["report_date"], "2026-06-30")
        self.assertEqual(result["publication_date"], "2026-08-20")
        self.assertEqual(result["roe_report_date"], "2025-12-31")
        self.assertTrue(result["eligible"])

    def test_future_publication_is_never_used(self):
        original = self.run_refresh()["stocks"][0]
        rows = reports()
        later = dict(rows[0], REPORT_DATE="2026-09-30", NOTICE_DATE="2026-10-20", UPDATE_DATE="2026-10-20",
                     PARENTNETPROFIT=100e8, PARENTNETPROFITTZ=999)
        rows.insert(0, later)
        altered = self.run_refresh(rows)["stocks"][0]
        self.assertEqual(original, altered)

    def test_future_restatement_is_excluded(self):
        rows = reports()
        rows[2]["UPDATE_DATE"] = "2026-10-20"
        result = self.run_refresh(rows)["stocks"][0]
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["pe_ttm"])
        self.assertIsNone(result["score"])

    def test_missing_report_or_cash_flow_does_not_become_recommendation(self):
        for rows in ([], reports()[:2]):
            result = self.run_refresh(rows)["stocks"][0]
            self.assertFalse(result["eligible"])
            self.assertIsNone(result["score"])
        rows = reports()
        rows[0]["NETCASH_OPERATE_PK"] = None
        result = self.run_refresh(rows)["stocks"][0]
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["operating_cash_to_profit"])

    def test_future_or_stale_valuation_is_not_backfilled(self):
        for day in ("2026-10-15", "2026-10-17", None):
            quote = dict(QUOTE, date=day)
            result = self.run_refresh(quote=quote)
            self.assertEqual(result["stocks"], [])
            self.assertEqual(result["coverage"]["valuation_missing"], 1)

    def test_st_banks_and_heavyweights_excluded_before_requests(self):
        universe = [{"symbol": "sh600000", "name": "浦发银行"},
                    {"symbol": "sh600309", "name": "万华化学"},
                    {"symbol": "sh600123", "name": "*ST测试"}]
        with patch.object(longterm, "_fetch_reports") as request:
            result = longterm.refresh(universe, {stock["symbol"]: QUOTE for stock in universe}, "2026-10-16")
        request.assert_not_called()
        self.assertEqual(result["stocks"], [])

    def test_financial_failure_is_visible_and_not_eligible(self):
        with patch.object(longterm, "_fetch_reports", side_effect=RuntimeError("unavailable")):
            result = longterm.refresh([STOCK], {STOCK["symbol"]: QUOTE}, "2026-10-16")
        self.assertFalse(result["stocks"][0]["eligible"])
        self.assertIsNone(result["stocks"][0]["score"])
        self.assertIn("unavailable", result["stocks"][0]["risks"][0])

    def test_loss_or_decline_is_not_hidden_by_low_valuation(self):
        rows = reports()
        rows[0]["TOTALOPERATEREVETZ"] = -12
        result = self.run_refresh(rows)["stocks"][0]
        self.assertFalse(result["eligible"])
        self.assertIn("未同时改善", result["thesis"][-1])
        rows = reports()
        for row in rows:
            row["PARENTNETPROFIT"] *= -1
        result = self.run_refresh(rows)["stocks"][0]
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["pe_ttm"])

    def test_missing_publication_date_cannot_be_assumed(self):
        rows = reports()
        for row in rows:
            row.pop("NOTICE_DATE")
        result = self.run_refresh(rows)["stocks"][0]
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["report_date"])


if __name__ == "__main__":
    unittest.main()
