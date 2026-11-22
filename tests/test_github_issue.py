from __future__ import annotations

import json

import httpx
import pytest

from dealradar.notify.github_issue import GitHubIssue

ENV = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "me/deal-radar"}


def test_ohne_token_nicht_eingerichtet():
    with pytest.raises(RuntimeError):
        GitHubIssue({})


def test_warnung_als_issue():
    seen = {}

    def h(req: httpx.Request):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(201, json={})

    gh = GitHubIssue(ENV, client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(h)))
    gh.send_warning("Quelle x", "seit 3 Tagen down")
    assert seen["path"] == "/repos/me/deal-radar/issues"
    assert seen["body"]["labels"] == ["warnung"] and "Quelle x" in seen["body"]["title"]


def test_keine_treffer_kein_issue():
    def h(req):
        raise AssertionError("darf nicht aufgerufen werden")
    gh = GitHubIssue(ENV, client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(h)))
    gh.send_alerts([], "2026-11-22")
