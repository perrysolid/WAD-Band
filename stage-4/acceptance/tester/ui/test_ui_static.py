"""S2-U1 / D26 HTML-or-JSON routing over plain HTTP, and S2-U13 self-contained assets:
no external URL in served HTML/CSS/JS, a Content-Security-Policy on every page."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import httpx
import pytest

import pf_model as m
from pf_client import expect, expect_error

HTML = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
SHELLS = ["/", "/split", "/signup", "/login", "/requests", "/authorizations"]


@pytest.fixture
def seeded(reset):
    reset(m.fixture())


@pytest.fixture
def raw(base_url):
    c = httpx.Client(base_url=base_url, timeout=5.0)
    yield c
    c.close()


@pytest.mark.parametrize("path", SHELLS)
def test_browser_navigation_gets_html_without_a_token(seeded, raw, path):
    resp = raw.get(path, headers={"Accept": HTML})
    assert resp.status_code == 200, (path, resp.status_code)
    assert resp.headers["content-type"].startswith("text/html"), resp.headers["content-type"]


@pytest.mark.parametrize("path", ["/", "/split", "/signup", "/login"])
def test_shell_routes_are_html_whatever_the_accept_header(seeded, raw, path):
    """[D26] the four UI-only routes always return the HTML shell."""
    for accept in ("application/json", "*/*", None):
        resp = raw.get(path, headers={"Accept": accept} if accept else {})
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path,field", [("/requests", "requests"),
                                        ("/authorizations", "authorizations")])
@pytest.mark.parametrize("accept", [None, "application/json", "*/*", "text/html;q=0",
                                    "application/json, text/plain"])
def test_shared_routes_are_the_json_api_without_text_html(seeded, api, path, field, accept):
    """[S2, D26] no text/html (or q=0) -> the authenticated JSON API, unchanged errors."""
    hdrs = {"Accept": accept} if accept else {}
    ada = api().authenticate("ada@example.com")
    body = expect(ada.get(path, headers=hdrs), 200)
    assert body.headers["content-type"].startswith("application/json")
    assert field in body.json() and "has_more" in body.json()
    expect_error(ada.get(path, headers=hdrs, token=None), 401, "unauthenticated")
    expect_error(ada.get(path, headers=hdrs, params={"limit": "0"}), 422, "validation_failed")


@pytest.mark.parametrize("path", ["/requests", "/authorizations"])
def test_shared_routes_serve_html_to_a_browser_even_with_a_token(seeded, api, path):
    ada = api().authenticate("ada@example.com")
    resp = ada.get(path, headers={"Accept": HTML})
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")


def test_writes_on_shared_routes_stay_json_even_with_text_html(seeded, api):
    ada = api().authenticate("ada@example.com")
    r = ada.post("/requests", json={"payer_handle": "bob", "amount": 5}, key="k1",
                 headers={"Accept": HTML})
    assert expect(r, 201).headers["content-type"].startswith("application/json")
    a = ada.post("/authorizations", json={"to_handle": "bob", "amount": 5}, key="k2",
                 headers={"Accept": HTML})
    assert expect(a, 201).json()["status"] == "open"


@pytest.mark.parametrize("path", ["/nope", "/requests/x", "/assets/does-not-exist.js",
                                  "/payments/p_1", "/refunds", "/me/html"])
def test_unknown_routes_are_404_json_even_for_a_browser(seeded, raw, path):
    resp = raw.get(path, headers={"Accept": HTML})
    assert resp.status_code == 404, (path, resp.status_code)
    assert resp.headers["content-type"].startswith("application/json")
    assert resp.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("path", SHELLS)
def test_csp_header_on_every_page(seeded, raw, path):
    resp = raw.get(path, headers={"Accept": HTML})
    csp = resp.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp, f"{path}: CSP is {csp!r}"
    for bad in ("http:", "https:", "*"):
        assert bad not in csp.replace("'self'", ""), f"{path}: CSP allows {bad}: {csp!r}"


# namespace identifiers are not fetched; everything else absolute is an external URL
_ALLOWED = re.compile(r"https?://www\.w3\.org/(2000/svg|1999/xlink|1999/xhtml|XML/1998/namespace)")
_URL = re.compile(r"""(?:https?:)?//[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}[^\s"'()<>]*""")


def _external(text: str) -> list[str]:
    return [u for u in _URL.findall(_ALLOWED.sub("", text))]


def _assets(html: str, base: str) -> list[str]:
    refs = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html)
    return [urljoin(base, r) for r in refs if not r.startswith(("#", "mailto:", "data:"))]


def test_served_html_css_and_js_reference_no_external_url(seeded, raw, base_url):
    """[S2-U13] crawl every page, script, stylesheet, @import and url() reachable from them."""
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(base_url))
    todo = [urljoin(base_url + "/", p.lstrip("/")) for p in SHELLS]
    seen, found = set(), {}
    while todo:
        url = todo.pop()
        if url in seen or not url.startswith(origin):
            continue
        seen.add(url)
        resp = raw.get(url, headers={"Accept": HTML})
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or not any(t in ctype for t in ("html", "css", "javascript")):
            continue
        body = resp.text
        ext = _external(body)
        if ext:
            found[url] = ext[:5]
        if "html" in ctype:
            todo += _assets(body, url)
        if "css" in ctype:
            todo += [urljoin(url, u.strip("'\" ")) for u in re.findall(r"url\(([^)]+)\)", body)
                     if not u.strip("'\" ").startswith("data:")]
            todo += [urljoin(url, u) for u in re.findall(r"""@import\s+["']([^"']+)""", body)]
        if "javascript" in ctype:
            todo += [urljoin(url, u) for u in
                     re.findall(r"""(?:import|from)\s*\(?\s*["'](\.{0,2}/[^"']+)["']""", body)]
    assert len(seen) >= len(SHELLS)
    assert not found, f"external URLs in served files: {found}"


def test_assets_are_served_with_real_content_types(seeded, raw, base_url):
    html = raw.get("/", headers={"Accept": HTML}).text
    for url in _assets(html, base_url + "/"):
        if not url.startswith(base_url):
            continue
        resp = raw.get(url)
        assert resp.status_code == 200, url
        path = urlsplit(url).path
        if path.endswith(".js") or path.endswith(".mjs"):
            assert "javascript" in resp.headers["content-type"], (url, resp.headers)
        if path.endswith(".css"):
            assert "text/css" in resp.headers["content-type"], (url, resp.headers)
        if path.endswith(".woff2"):
            assert "font/woff2" in resp.headers["content-type"], (url, resp.headers)
