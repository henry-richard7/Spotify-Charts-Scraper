import asyncio
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from app import chart_delivery
import spotify_charts
from spotify_charts import SpotifyCharts


class ChartDeliveryTests(unittest.TestCase):
    def test_delivery_metadata_preserves_saved_time_and_reports_staleness(self):
        fetched_at = datetime.now(timezone.utc) - timedelta(minutes=7)
        raw = {"entries": [{"trackMetadata": {"trackName": "Example"}}]}

        response = chart_delivery(raw, fetched_at, "pending")

        self.assertTrue(response["delivery"]["isStale"])
        self.assertEqual(response["delivery"]["refreshStatus"], "pending")
        self.assertEqual(response["delivery"]["fetchedAt"], fetched_at.isoformat())
        self.assertNotIn("delivery", raw)
        self.assertEqual(response["entries"], raw["entries"])

    def test_delivery_marks_recent_row_fresh(self):
        fetched_at = datetime.now(timezone.utc)
        response = chart_delivery({"entries": []}, fetched_at, "fresh")
        self.assertFalse(response["delivery"]["isStale"])
        self.assertLess(response["delivery"]["ageSeconds"], 300)

    def test_concurrent_identical_chart_requests_share_one_fetch(self):
        async def run():
            spotify_charts._chart_inflight.clear()
            scraper = SpotifyCharts(access_token="test")
            calls = 0

            async def fetch(_url):
                nonlocal calls
                calls += 1
                await asyncio.sleep(0.01)
                return {"entries": [{"id": "one"}]}

            with patch.object(scraper, "_fetch_chart_uncached", side_effect=fetch):
                first, second = await asyncio.gather(
                    scraper._fetch_chart("https://example.test/chart"),
                    scraper._fetch_chart("https://example.test/chart"),
                )
            self.assertEqual(calls, 1)
            first["entries"].append({"id": "local"})
            self.assertEqual(second["entries"], [{"id": "one"}])
            spotify_charts._chart_inflight.clear()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
