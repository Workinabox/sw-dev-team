from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from wiab_team.checkpoint import SCHEMA, with_schema

DSN = "postgresql://user:pw@localhost:5432/wiab"


def test_search_path_is_pinned_to_our_schema() -> None:
    """LangGraph's tables must not land in the backend's schema."""
    options = parse_qs(urlsplit(with_schema(DSN)).query)["options"]
    assert options == [f"-csearch_path={SCHEMA}"]


def test_existing_query_parameters_survive() -> None:
    result = with_schema(f"{DSN}?sslmode=require")
    query = parse_qs(urlsplit(result).query)
    assert query["sslmode"] == ["require"]
    assert query["options"] == [f"-csearch_path={SCHEMA}"]


def test_an_operator_supplied_options_wins() -> None:
    """Someone who set `options` by hand knows something we don't."""
    dsn = f"{DSN}?options=-csearch_path%3Dcustom"
    assert with_schema(dsn) == dsn


def test_the_rest_of_the_dsn_is_untouched() -> None:
    parts = urlsplit(with_schema(DSN))
    assert parts.scheme == "postgresql"
    assert parts.netloc == "user:pw@localhost:5432"
    assert parts.path == "/wiab"
