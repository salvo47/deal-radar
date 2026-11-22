from dealradar.http import parse_robots, robots_allowed

MYDEALZ_LIKE = """
User-agent: *
Allow: /
Disallow: /search
Disallow: /*?sortBy=*
Disallow: /*&groups=*

User-agent: ClaudeBot
Disallow: /
"""


def test_wildcards_and_group():
    r = parse_robots(MYDEALZ_LIKE)
    assert robots_allowed(r, "/rss/gruppe/konto-kreditkarten")
    assert not robots_allowed(r, "/search")
    assert not robots_allowed(r, "/deals?sortBy=new")
    assert not robots_allowed(r, "/deals?a=1&groups=2")


def test_specific_group_wins():
    r = parse_robots("User-agent: *\nAllow: /\n\nUser-agent: DealRadar\nDisallow: /privat/\n")
    assert not robots_allowed(r, "/privat/x")
    assert robots_allowed(r, "/oeffentlich")


def test_dollar_anchor_and_longest_match():
    r = parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /a/\nAllow: /a/b/\n")
    assert not robots_allowed(r, "/x/bedingungen.pdf")
    assert robots_allowed(r, "/x/bedingungen.pdf?v=2")
    assert not robots_allowed(r, "/a/c")
    assert robots_allowed(r, "/a/b/c")


def test_empty_disallow_allows_all():
    assert robots_allowed(parse_robots("User-agent: *\nDisallow:\n"), "/alles")
