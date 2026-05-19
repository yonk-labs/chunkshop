from __future__ import annotations
from dataclasses import replace

from chunkshop.config import SessionEpisodeFramerConfig as Cfg
from chunkshop.sources.base import Document


class SessionEpisodeFramer:
    """Split one session Document into episode Documents. Stateless, no I/O.

    Boundaries: time gap > max_gap_seconds, OR turn count >= max_turns, OR
    word count >= max_words, OR (boundary_on_tool and a tool event follows a
    non-tool event). Reads metadata['_session_events'] (ordered)."""

    def __init__(self, cfg: Cfg):
        self.cfg = cfg

    def frame(self, raw: Document) -> list[Document]:
        events = list((raw.metadata or {}).get("_session_events") or [])
        if not events:
            return []
        episodes: list[list[dict]] = [[]]
        words = 0
        for i, e in enumerate(events):
            cur = episodes[-1]
            if cur:
                prev = cur[-1]
                gap = (e.get("ts") or 0) - (prev.get("ts") or 0)
                tool_boundary = (self.cfg.boundary_on_tool and e.get("tool")
                                 and not prev.get("tool"))
                if (gap > self.cfg.max_gap_seconds
                        or len(cur) >= self.cfg.max_turns
                        or words >= self.cfg.max_words
                        or tool_boundary):
                    episodes.append([])
                    cur = episodes[-1]
                    words = 0
            cur.append(e)
            words += len((e.get("content") or "").split())
        base_meta = {k: v for k, v in (raw.metadata or {}).items()
                     if k != "_session_events"}
        out: list[Document] = []
        for seq, evs in enumerate(e for e in episodes if e):
            lines = []
            for ev in evs:
                tag = ev.get("role") or "event"
                if ev.get("tool"):
                    tag += f"/{ev['tool']}"
                lines.append(f"[{tag}] {ev.get('content','')}")
            meta = dict(base_meta)
            meta.update({
                "framer": "session_episode",
                "frame_seq": seq,
                "episode_start_ts": evs[0].get("ts"),
                "episode_end_ts": evs[-1].get("ts"),
                "episode_turn_span": len(evs),
                "_episode_events": evs,
            })
            out.append(Document(id=raw.id, content="\n".join(lines),
                                title=raw.title, metadata=meta))
        return out
