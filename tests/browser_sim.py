"""Test helper: plays the role of the user's browser against the local test Moodle.

Logs in with the normal Moodle login form and returns the moodlemobile://token=... link that
admin/tool/mobile/launch.php hands to the app (works with and without confirmed=1).
"""

from __future__ import annotations

import html
import http.cookiejar
import re
import urllib.error
import urllib.parse
import urllib.request


class _NoAppRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith(("http://", "https://")):
            raise _AppLink(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _AppLink(Exception):
    def __init__(self, url: str) -> None:
        super().__init__(url)
        self.url = url


def browser_login(launch_url: str, username: str, password: str) -> str:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                         urllib.request.HTTPCookieProcessor(jar), _NoAppRedirect())
    try:
        resp = opener.open(launch_url)
        page = resp.read().decode("utf-8", "replace")
        url = resp.geturl()
        if "logintoken" in page:
            token = re.search(r'name="logintoken" value="([^"]+)"', page).group(1)
            form = re.search(r"<form[^>]*id=\"login\"[^>]*>", page).group(0)
            action = html.unescape(re.search(r'action="([^"]+)"', form).group(1))
            data = urllib.parse.urlencode({"username": username, "password": password, "logintoken": token}).encode()
            resp = opener.open(urllib.parse.urljoin(url, action), data)
            page = resp.read().decode("utf-8", "replace")
    except _AppLink as link:
        return link.url
    m = re.search(r'href="(moodlemobile://token=[^"]+)"', page)
    if m:
        return html.unescape(m.group(1))
    raise RuntimeError("No app link found after login")
