"""A background-job link that just failed rests before it is tried first again (0.222.0).

    python tests/run.py aux_cooldown

No network. Without this every background job paid for the same dead model again (a 504 can take
15 seconds to arrive). A resting link moves to the END of the chain rather than being skipped, so
a job still runs when every other link fails too.
"""
from _paths import ROOT, SERVER  # noqa: F401
import asyncio
import os
import tempfile
import unittest

os.environ.setdefault("JAVIS_STATE_DIR", tempfile.mkdtemp(prefix="javis-auxcool-"))

import aux_engine  # noqa: E402


class Link:
    def __init__(self, provider, model="m", events=None):
        self.provider, self.model = provider, model
        self.events = events or []
        self.ran = 0

    def is_available(self):
        return True

    def reset_session(self):
        pass

    async def query(self, prompt):
        self.ran += 1
        for ev in self.events:
            yield ev


def run(chain):
    async def go():
        return [ev async for ev in chain.query("job")]
    return asyncio.run(go())


FINAL = {"type": "final", "content": "ok"}


def err(text):
    return {"type": "error", "content": text}


class ClassifyTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(aux_engine.cooldown_for("HTTP 429 Too Many Requests"), aux_engine.COOLDOWN_QUOTA_S)
        self.assertEqual(aux_engine.cooldown_for("You've hit your usage limit"), aux_engine.COOLDOWN_QUOTA_S)
        self.assertEqual(aux_engine.cooldown_for("503 UNAVAILABLE"), aux_engine.COOLDOWN_DOWN_S)
        self.assertEqual(aux_engine.cooldown_for("ReadTimeout: timed out"), aux_engine.COOLDOWN_DOWN_S)
        self.assertEqual(aux_engine.cooldown_for("404 model not found"), aux_engine.COOLDOWN_ACCESS_S)
        self.assertEqual(aux_engine.cooldown_for("401 invalid api key"), aux_engine.COOLDOWN_ACCESS_S)
        self.assertEqual(aux_engine.cooldown_for("tool javis_read_file failed: no such file"), 0.0)

    def test_retry_after_wins(self):
        self.assertEqual(aux_engine.cooldown_for("429, Retry-After: 42"), 42.0)
        self.assertEqual(aux_engine.cooldown_for("Rate limit reached. Please try again in 2m"), 120.0)
        self.assertEqual(aux_engine.cooldown_for("try again in 900ms"), 1.0)


class ChainTests(unittest.TestCase):
    def setUp(self):
        aux_engine._COOLDOWN.clear()

    def test_failed_link_rests_and_goes_last(self):
        bad = Link("gemini", events=[err("503 UNAVAILABLE")])
        good = Link("openrouter", events=[FINAL])
        run(aux_engine._FallbackChain([bad, good]))
        self.assertEqual((bad.ran, good.ran), (1, 1))
        bad2 = Link("gemini", events=[err("503 UNAVAILABLE")])   # next job: fresh objects, same model
        good2 = Link("openrouter", events=[FINAL])
        out = run(aux_engine._FallbackChain([bad2, good2]))
        self.assertEqual(out[-1], FINAL)
        self.assertEqual(bad2.ran, 0, "the resting model is not tried first again")
        self.assertEqual(good2.ran, 1)

    def test_everyone_resting_still_runs_soonest_first(self):
        a = Link("gemini", events=[err("429 quota")])
        b = Link("groq", events=[err("503 unavailable")])
        run(aux_engine._FallbackChain([a, b]))
        a2 = Link("gemini", events=[FINAL])
        b2 = Link("groq", events=[FINAL])
        out = run(aux_engine._FallbackChain([b2, a2]))
        self.assertEqual(out[-1], FINAL)
        self.assertEqual((a2.ran, b2.ran), (1, 0), "gemini wakes in 5 min, groq in 30: gemini goes first")

    def test_success_clears_the_rest(self):
        run(aux_engine._FallbackChain([Link("gemini", events=[err("429")]), Link("openrouter", events=[FINAL])]))
        self.assertTrue(aux_engine._COOLDOWN)
        run(aux_engine._FallbackChain([Link("openrouter", events=[FINAL]), Link("gemini", events=[FINAL])]))
        only = Link("gemini", events=[FINAL])
        run(aux_engine._FallbackChain([only]))
        self.assertNotIn("gemini:m", aux_engine._COOLDOWN, "a link that works again stops resting")

    def test_unrelated_error_does_not_rest(self):
        run(aux_engine._FallbackChain([Link("gemini", events=[err("tool failed")]), Link("openrouter", events=[FINAL])]))
        self.assertNotIn("gemini:m", aux_engine._COOLDOWN)

    def test_links_without_identity_never_rest(self):
        class Anon(Link):
            pass
        x = Anon(None, events=[err("503")])
        x.provider = None
        run(aux_engine._FallbackChain([x, Link("openrouter", events=[FINAL])]))
        self.assertEqual(list(aux_engine._COOLDOWN), [])


if __name__ == "__main__":
    unittest.main()
