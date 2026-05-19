"""Unit tests for SessionEpisodeFramer (pure/stateless)."""
from chunkshop.config import SessionEpisodeFramerConfig as Cfg
from chunkshop.framers.session_episode import SessionEpisodeFramer
from chunkshop.sources.base import Document


def _doc(events):
    return Document(id="s1", content="x", title=None,
                    metadata={"session_id": "s1", "_session_events": events})


def test_time_gap_splits_episodes():
    evs = [{"role": "user", "content": "a", "ts": 0.0, "tool": None},
           {"role": "assistant", "content": "b", "ts": 10.0, "tool": None},
           {"role": "user", "content": "c", "ts": 99999.0, "tool": None}]
    fr = SessionEpisodeFramer(Cfg(max_gap_seconds=1800))
    out = fr.frame(_doc(evs))
    assert len(out) == 2
    assert out[0].metadata["frame_seq"] == 0
    assert out[1].metadata["frame_seq"] == 1
    assert out[0].metadata["framer"] == "session_episode"
    assert "a" in out[0].content and "c" in out[1].content
    assert len(out[0].metadata["_episode_events"]) == 2


def test_single_episode_when_contiguous():
    evs = [{"role": "user", "content": "a", "ts": 0.0, "tool": None},
           {"role": "assistant", "content": "b", "ts": 5.0, "tool": None}]
    out = SessionEpisodeFramer(Cfg()).frame(_doc(evs))
    assert len(out) == 1 and out[0].metadata["episode_turn_span"] == 2


def test_empty_session_yields_nothing():
    assert SessionEpisodeFramer(Cfg()).frame(_doc([])) == []
