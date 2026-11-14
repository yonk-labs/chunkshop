"""HTTP source: every redirect hop obeys the host / robots / scheme rules (PYS-03)."""
from __future__ import annotations

import httpx

from chunkshop.config import HttpSource as Cfg
from chunkshop.sources.http import HttpSource

HTML = {"Content-Type": "text/html; charset=utf-8"}


def _src(routes, seen, **kw):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        seen.append(url)
        v = routes.get(url)
        if v is None:
            return httpx.Response(404, text="nope")
        return v(request) if callable(v) else v

    cfg = Cfg(
        type="http",
        urls=kw.pop("urls"),
        request_delay_seconds=0.0,
        respect_robots=kw.pop("respect_robots", False),
        **kw,
    )
    return HttpSource(cfg, transport=httpx.MockTransport(handler))


def _redir(to, status=302):
    return httpx.Response(status, headers={"Location": to})


def _page(text):
    return httpx.Response(200, text=f"<html><body>{text}</body></html>", headers=HTML)


def test_redirect_to_other_host_not_followed():
    seen: list[str] = []
    src = _src(
        {
            "http://seed.test/a": _redir("http://127.0.0.1/secret"),
            "http://127.0.0.1/secret": _page("INTERNAL SECRET"),
        },
        seen,
        urls=["http://seed.test/a"],
    )
    docs = list(src.iter_documents())
    assert docs == []
    assert not any("127.0.0.1" in u for u in seen)


def test_redirect_to_non_http_scheme_not_followed():
    seen: list[str] = []
    src = _src(
        {"http://seed.test/a": _redir("file:///etc/passwd")}, seen, urls=["http://seed.test/a"]
    )
    assert list(src.iter_documents()) == []


def test_same_host_redirect_followed_and_keyed_by_requested_url():
    seen: list[str] = []
    src = _src(
        {
            "http://seed.test/a": _redir("https://seed.test/a/", 301),
            "https://seed.test/a/": _page("hello there"),
        },
        seen,
        urls=["http://seed.test/a"],
    )
    docs = list(src.iter_documents())
    assert [d.id for d in docs] == ["http://seed.test/a"]
    assert "hello there" in docs[0].content
    assert docs[0].metadata["url"] == "http://seed.test/a"


def test_redirect_target_disallowed_by_robots_not_followed():
    seen: list[str] = []
    src = _src(
        {
            "http://seed.test/robots.txt": httpx.Response(
                200, text="User-agent: *\nDisallow: /private\n"
            ),
            "http://seed.test/a": _redir("http://seed.test/private/x"),
            "http://seed.test/private/x": _page("PRIVATE"),
        },
        seen,
        urls=["http://seed.test/a"],
        respect_robots=True,
    )
    assert list(src.iter_documents()) == []
    assert "http://seed.test/private/x" not in seen


def test_redirect_loop_terminates():
    seen: list[str] = []
    src = _src(
        {
            "http://seed.test/a": _redir("http://seed.test/b"),
            "http://seed.test/b": _redir("http://seed.test/a"),
        },
        seen,
        urls=["http://seed.test/a"],
    )
    assert list(src.iter_documents()) == []
    assert len(seen) <= 7


def test_more_than_five_hops_stops():
    seen: list[str] = []
    routes = {f"http://seed.test/h{i}": _redir(f"http://seed.test/h{i + 1}") for i in range(10)}
    routes["http://seed.test/h10"] = _page("end")
    src = _src(routes, seen, urls=["http://seed.test/h0"])
    assert list(src.iter_documents()) == []
    assert "http://seed.test/h10" not in seen


def test_apex_to_www_redirect_is_followed_both_ways():
    for a, b in (("example.test", "www.example.test"), ("www.example.test", "example.test")):
        seen: list[str] = []
        routes = {
            f"http://{a}/docs": _redir(f"https://{b}/docs", 301),
            f"https://{b}/docs": _page("docs body"),
        }
        docs = list(_src(routes, seen, urls=[f"http://{a}/docs"]).iter_documents())
        assert [d.id for d in docs] == [f"http://{a}/docs"]
        assert "docs body" in docs[0].content


def test_www_lookalike_host_is_not_treated_as_same_site():
    seen: list[str] = []
    routes = {
        "http://example.test/a": _redir("http://www.example.test.evil.test/a"),
        "http://www.example.test.evil.test/a": _page("EVIL"),
    }
    docs = list(_src(routes, seen, urls=["http://example.test/a"]).iter_documents())
    assert docs == []
    assert "http://www.example.test.evil.test/a" not in seen
