"""Unit tests for the evidence spine. Run from the repo root with:

    PYTHONPATH=. python tests/test_evidence.py

Uses a temp dir via SCROLL_EVIDENCE_PATH so it never touches ~/.scroll.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Make `server` importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import evidence as ev  # noqa: E402


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["SCROLL_EVIDENCE_PATH"] = str(Path(self._tmp.name) / "evidence.jsonl")

    def tearDown(self):
        os.environ.pop("SCROLL_EVIDENCE_PATH", None)
        self._tmp.cleanup()

    def test_append_and_replay(self):
        tid = ev.new_trace(workflow_id="wf1", agent_id="qwen")
        ev.event(tid, ev.TOOL_CALL, name="read_file", args={})
        ev.event(tid, ev.VERDICT, verdict="LGTM")
        ev.event(tid, ev.HALT, by="operator")

        recs = ev.replay(tid)
        types = [r["type"] for r in recs]
        self.assertEqual(types, ["run.start", "tool.call", "verdict", "halt"])
        # every record carries its trace; the start record carries correlation ids
        self.assertTrue(all(r["trace_id"] == tid for r in recs))
        self.assertEqual(recs[0]["workflow_id"], "wf1")
        self.assertEqual(recs[0]["agent_id"], "qwen")

    def test_filter_by_type(self):
        tid = ev.new_trace()
        ev.event(tid, ev.TOOL_RESULT, name="write_file", error=False)
        ev.event(tid, ev.INTERACTION, depth="review", outcome="accepted")

        got = ev.events(trace_id=tid, type_=ev.INTERACTION)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["outcome"], "accepted")

    def test_append_only_chronological_order(self):
        tid = ev.new_trace()
        for i in range(3):
            ev.event(tid, ev.PHASE, name=f"p{i}")

        recs = ev.replay(tid)
        phases = [r["name"] for r in recs if r["type"] == ev.PHASE]
        self.assertEqual(phases, ["p0", "p1", "p2"])

    def test_traces_are_isolated(self):
        a = ev.new_trace()
        b = ev.new_trace()
        ev.event(a, ev.PHASE, name="in-a")
        ev.event(b, ev.PHASE, name="in-b")

        self.assertEqual([r["name"] for r in ev.replay(a) if r["type"] == ev.PHASE], ["in-a"])
        self.assertEqual([r["name"] for r in ev.replay(b) if r["type"] == ev.PHASE], ["in-b"])

    def test_corrupt_line_is_skipped(self):
        p = Path(os.environ["SCROLL_EVIDENCE_PATH"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("not-json\n", encoding="utf-8")
        tid = ev.new_trace()
        ev.event(tid, ev.RUN_DONE, total_tokens=1)
        recs = ev.replay(tid)
        self.assertTrue(all(r["trace_id"] == tid for r in recs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
