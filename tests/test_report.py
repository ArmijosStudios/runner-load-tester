import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import report  # noqa: E402


def job(i, conclusion="success", runner="r1", created="2026-01-01T00:00:00Z", started="2026-01-01T00:00:10Z",
        completed="2026-01-01T00:01:10Z"):
    return {
        "id": 100 + i, "name": f"load #{i}", "html_url": f"https://x/{i}", "status": "completed",
        "conclusion": conclusion, "runner_name": runner, "runner_group_name": "Default", "labels": ["self-hosted"],
        "created_at": created, "started_at": started if runner else completed, "completed_at": completed,
        "steps": [
            {"name": "Set up job", "conclusion": "success", "started_at": started, "completed_at": "2026-01-01T00:00:12Z"},
            {"name": "Run workload", "conclusion": "failure" if conclusion == "failure" else "success",
             "started_at": "2026-01-01T00:00:12Z", "completed_at": "2026-01-01T00:01:05Z"},
        ] if runner else [],
    }


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "result-2"))
        with open(os.path.join(self.dir, "result-2", "meta.txt"), "w") as f:
            f.write("host=box2\nexit_code=3\nprivate_ips=10.0.0.5,172.17.0.1\npublic_ip=203.0.113.7\n")
        with open(os.path.join(self.dir, "result-2", "output.log"), "w") as f:
            f.write("boom\n")
        self.jobs = [
            job(1),
            job(2, conclusion="failure", runner="r2", started="2026-01-01T00:00:20Z"),
            job(3, conclusion="cancelled", runner=""),
            {"name": "report", "id": 1},
        ]
        self.runs = report.collect(self.jobs, self.dir, lambda j: ["Process completed with exit code 3."])

    def test_collect(self):
        self.assertEqual([r["index"] for r in self.runs], [1, 2, 3])
        r1, r2, r3 = self.runs
        self.assertEqual((r1["queue_seconds"], r1["setup_seconds"], r1["workload_seconds"], r1["duration_seconds"]),
                         (10.0, 2.0, 53.0, 60.0))
        self.assertEqual(r2["host"], "box2")
        self.assertIn("Workload exited with code 3", r2["errors"])
        self.assertIn("Failed step: Run workload", r2["errors"])
        self.assertEqual(r2["output_tail"], "boom\n")
        self.assertFalse(r3["started"])
        self.assertIn("Never got a runner", r3["errors"])
        self.assertIsNone(r3["queue_seconds"])

    def test_summary_and_render(self):
        s = report.summarize(self.runs, 3, "2026-01-01T00:00:00Z", None)
        self.assertEqual((s["succeeded"], s["failed"], s["cancelled"], s["never_started"]), (1, 1, 1, 1))
        self.assertEqual(s["success_rate"], 33.3)
        self.assertEqual(s["distinct_runners"], 2)
        self.assertEqual(s["max_concurrency"], 2)
        self.assertEqual(s["first_start_after_request_seconds"], 10.0)
        self.assertIsNone(s["all_started_after_request_seconds"])
        self.assertEqual(s["wall_clock_seconds"], 70.0)
        self.assertEqual(s["queue_seconds"]["max"], 20.0)
        md = report.render(s, self.runs, "self-hosted", "https://run")
        self.assertIn("FAILED: 1/3 runs succeeded on `self-hosted`", md)
        self.assertIn("Process completed with exit code 3.", md)
        self.assertIn("| [#2](https://x/2) | failure | r2 | box2 |", md)
        self.assertIn("### IP addresses by run", md)
        self.assertIn("| [#2](https://x/2) | r2 | 10.0.0.5, 172.17.0.1 | 203.0.113.7 |", md)
        self.assertIn("| [#1](https://x/1) | r1 | – | – |", md)
        self.assertEqual(s["distinct_public_ips"], 1)
        self.assertNotIn("IP addresses by run", report.render(s, self.runs, "x", "u", include_ips=False))

    def test_fmt_and_stats(self):
        self.assertEqual(report.fmt(5), "5.0s")
        self.assertEqual(report.fmt(125), "2m 05s")
        self.assertEqual(report.fmt(3700), "1h 01m")
        self.assertEqual(report.stats([1, 2, 3, 4])["p50"], 2)
        self.assertIsNone(report.stats([None]))


if __name__ == "__main__":
    unittest.main()
