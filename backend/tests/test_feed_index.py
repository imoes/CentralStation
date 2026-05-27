"""Tests for feed_index — CheckMK metadata filters + OpenSearch query logic.

Three test groups:
  1. Pure helper functions  (_apply_metadata_filters, _normalise_query_string, …)
     → no mocking required, pure Python logic
  2. search()              → query construction verified against a mocked OS client
  3. search_by_query() +   → query construction + exclusion-clause DB integration
     get_exclusion_must_not_clauses()

Run inside the backend container:
    docker compose exec backend pytest tests/ -v
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.feed_index import (
    _apply_metadata_filters,
    _normalise_query_string,
    _terms_filter,
    _to_list,
    get_exclusion_must_not_clauses,
    search,
    search_by_query,
)


# ── Test-data helpers ────────────────────────────────────────────────────────

def _item(source: str, idx: int = 0, **meta) -> dict:
    return {"source": source, "metadata": meta or {}, "id": f"{source}-{idx}"}

def _cmk(idx: int = 0, **meta) -> dict:  return _item("checkmk", idx, **meta)
def _gl(idx: int = 0, **meta) -> dict:   return _item("graylog",  idx, **meta)
def _wz(idx: int = 0, **meta) -> dict:   return _item("wazuh",    idx, **meta)


def _os_client_stub(hits: list[dict] | None = None) -> AsyncMock:
    """Return a minimal AsyncMock that behaves like an OpenSearch client."""
    client = AsyncMock()
    client.search.return_value = {
        "hits": {
            "hits": [{"_source": h} for h in (hits or [])],
            "total": {"value": len(hits or []), "relation": "eq"},
        }
    }
    return client


def _db_stub(feed_searches: list) -> AsyncMock:
    """Return a mocked async DB session that yields the given FeedSearch rows."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = feed_searches
    db = AsyncMock()
    db.execute.return_value = mock_result
    return db


def _feed_search(query_string: str) -> MagicMock:
    s = MagicMock()
    s.query_string = query_string
    return s


# ═══════════════════════════════════════════════════════════════════════════
# Part 1 — Pure helper functions
# ═══════════════════════════════════════════════════════════════════════════

class TestToList:
    def test_none_returns_none(self):
        assert _to_list(None) is None

    def test_empty_string_returns_none(self):
        assert _to_list("") is None

    def test_empty_list_returns_none(self):
        assert _to_list([]) is None

    def test_list_of_empty_strings_returns_none(self):
        assert _to_list(["", ""]) is None

    def test_single_string_wraps_in_list(self):
        assert _to_list("Linux") == ["Linux"]

    def test_list_returned_unchanged(self):
        assert _to_list(["Linux", "Windows"]) == ["Linux", "Windows"]

    def test_empty_strings_filtered_from_list(self):
        assert _to_list(["Linux", "", "Windows"]) == ["Linux", "Windows"]


class TestTermsFilter:
    def test_none_returns_none(self):
        assert _terms_filter("severity", None) is None

    def test_empty_list_returns_none(self):
        assert _terms_filter("severity", []) is None

    def test_single_string_returns_term_dict(self):
        assert _terms_filter("severity", "critical") == {"term": {"severity": "critical"}}

    def test_single_item_list_returns_term_dict(self):
        assert _terms_filter("severity", ["critical"]) == {"term": {"severity": "critical"}}

    def test_multi_value_list_returns_terms_dict(self):
        assert _terms_filter("severity", ["critical", "high"]) == {
            "terms": {"severity": ["critical", "high"]}
        }


class TestNormaliseQueryString:
    def test_empty_string_unchanged(self):
        assert _normalise_query_string("") == ""

    def test_bare_host_expands_to_or_expression(self):
        result = _normalise_query_string("host:docker086")
        assert "metadata.host:docker086" in result
        assert "metadata.agent:docker086" in result
        assert " OR " in result

    def test_bare_host_with_wildcard_expands(self):
        result = _normalise_query_string("host:docker*")
        assert "metadata.host:docker*" in result
        assert "metadata.agent:docker*" in result

    def test_already_prefixed_metadata_host_not_re_expanded(self):
        q = "metadata.host:docker086"
        result = _normalise_query_string(q)
        # must appear exactly once, no OR introduced
        assert result.count("metadata.host:docker086") == 1
        assert "OR" not in result

    def test_unrelated_fields_unchanged(self):
        q = "severity:high AND metadata.rule_level:>=7"
        assert _normalise_query_string(q) == q

    def test_mixed_query_only_bare_host_expanded(self):
        q = "host:docker086 AND severity:critical"
        result = _normalise_query_string(q)
        assert "metadata.host:docker086" in result
        assert "metadata.agent:docker086" in result
        assert "severity:critical" in result


class TestApplyMetadataFilters:
    """
    Signature: _apply_metadata_filters(items, os_filter, location, ve, criticality, hostgroup=None)

    Rules:
    - CheckMK items are filtered by all criteria.
    - Non-CheckMK items (Graylog, Wazuh) always pass regardless of filters.
    - An item WITHOUT a metadata field always passes (unknown ≠ excluded).
    - An item with an empty string in a metadata field always passes.
    """

    # ── No filters ──────────────────────────────────────────────────────────

    def test_no_filters_returns_all_items(self):
        items = [_cmk(location="MUE-0"), _gl(), _wz()]
        assert _apply_metadata_filters(items, None, None, None, None) == items

    # ── Location ─────────────────────────────────────────────────────────────

    def test_checkmk_matching_location_passes(self):
        assert len(_apply_metadata_filters([_cmk(location="MUE-0")], None, ["MUE-0"], None, None)) == 1

    def test_checkmk_wrong_location_excluded(self):
        assert len(_apply_metadata_filters([_cmk(location="WOL-0")], None, ["MUE-0"], None, None)) == 0

    def test_checkmk_missing_location_field_passes(self):
        assert len(_apply_metadata_filters([_cmk()], None, ["MUE-0"], None, None)) == 1

    def test_checkmk_empty_location_field_passes(self):
        assert len(_apply_metadata_filters([_cmk(location="")], None, ["MUE-0"], None, None)) == 1

    def test_graylog_bypasses_location_filter(self):
        assert len(_apply_metadata_filters([_gl(location="WOL-0")], None, ["MUE-0"], None, None)) == 1

    def test_wazuh_bypasses_location_filter(self):
        assert len(_apply_metadata_filters([_wz(location="WOL-0")], None, ["MUE-0"], None, None)) == 1

    # ── OS ───────────────────────────────────────────────────────────────────

    def test_checkmk_matching_os_passes(self):
        assert len(_apply_metadata_filters([_cmk(os="Linux")], ["Linux"], None, None, None)) == 1

    def test_checkmk_wrong_os_excluded(self):
        assert len(_apply_metadata_filters([_cmk(os="Windows")], ["Linux"], None, None, None)) == 0

    def test_checkmk_missing_os_passes(self):
        assert len(_apply_metadata_filters([_cmk()], ["Linux"], None, None, None)) == 1

    def test_graylog_bypasses_os_filter(self):
        assert len(_apply_metadata_filters([_gl(os="Cisco")], ["Linux"], None, None, None)) == 1

    # ── VE ───────────────────────────────────────────────────────────────────

    def test_checkmk_matching_ve_passes(self):
        assert len(_apply_metadata_filters([_cmk(ve="IMG")], None, None, ["IMG"], None)) == 1

    def test_checkmk_wrong_ve_excluded(self):
        assert len(_apply_metadata_filters([_cmk(ve="SDZ")], None, None, ["IMG"], None)) == 0

    def test_checkmk_missing_ve_passes(self):
        assert len(_apply_metadata_filters([_cmk()], None, None, ["IMG"], None)) == 1

    # ── Criticality ──────────────────────────────────────────────────────────

    def test_checkmk_matching_criticality_passes(self):
        assert len(_apply_metadata_filters([_cmk(criticality="prod")], None, None, None, ["prod"])) == 1

    def test_checkmk_wrong_criticality_excluded(self):
        assert len(_apply_metadata_filters([_cmk(criticality="test")], None, None, None, ["prod"])) == 0

    def test_checkmk_missing_criticality_passes(self):
        assert len(_apply_metadata_filters([_cmk()], None, None, None, ["prod"])) == 1

    # ── Hostgroup ────────────────────────────────────────────────────────────

    def test_checkmk_matching_hostgroup_passes(self):
        items = [_cmk(hostgroups=["linux-docker", "prod"])]
        assert len(_apply_metadata_filters(items, None, None, None, None, ["linux-docker"])) == 1

    def test_checkmk_no_overlap_excluded(self):
        items = [_cmk(hostgroups=["windows-vms"])]
        assert len(_apply_metadata_filters(items, None, None, None, None, ["linux-docker"])) == 0

    def test_checkmk_empty_hostgroups_passes(self):
        items = [_cmk(hostgroups=[])]
        assert len(_apply_metadata_filters(items, None, None, None, None, ["linux-docker"])) == 1

    def test_checkmk_missing_hostgroups_key_passes(self):
        assert len(_apply_metadata_filters([_cmk()], None, None, None, None, ["linux-docker"])) == 1

    # ── Multiple filters combined ────────────────────────────────────────────

    def test_all_filters_match_passes(self):
        items = [_cmk(os="Linux", location="MUE-0", ve="IMG", criticality="prod")]
        assert len(_apply_metadata_filters(items, ["Linux"], ["MUE-0"], ["IMG"], ["prod"])) == 1

    def test_one_mismatching_filter_excludes(self):
        # location is wrong, rest matches
        items = [_cmk(os="Linux", location="WOL-0", ve="IMG", criticality="prod")]
        assert len(_apply_metadata_filters(items, ["Linux"], ["MUE-0"], ["IMG"], ["prod"])) == 0

    # ── Mixed sources ────────────────────────────────────────────────────────

    def test_only_checkmk_items_are_filtered(self):
        items = [
            _cmk(0, location="MUE-0"),   # passes
            _cmk(1, location="WOL-0"),   # excluded
            _gl(0, location="WOL-0"),    # passes — not CheckMK
            _wz(0),                       # passes — not CheckMK
        ]
        result = _apply_metadata_filters(items, None, ["MUE-0"], None, None)
        assert len(result) == 3
        assert all(i["id"] != "checkmk-1" for i in result)

    def test_result_order_preserved(self):
        items = [_cmk(0, location="MUE-0"), _gl(0), _wz(0)]
        result = _apply_metadata_filters(items, None, ["MUE-0"], None, None)
        assert [i["source"] for i in result] == ["checkmk", "graylog", "wazuh"]

    def test_multiple_allowed_locations(self):
        items = [_cmk(0, location="MUE-0"), _cmk(1, location="MUE-5"), _cmk(2, location="WOL-0")]
        result = _apply_metadata_filters(items, None, ["MUE-0", "MUE-5"], None, None)
        assert len(result) == 2

    def test_multiple_allowed_os_values(self):
        items = [_cmk(0, os="Linux"), _cmk(1, os="Windows"), _cmk(2, os="Cisco")]
        result = _apply_metadata_filters(items, ["Linux", "Windows"], None, None, None)
        assert len(result) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Part 2 — search(): query construction with mocked OpenSearch
# ═══════════════════════════════════════════════════════════════════════════

class TestSearch:
    async def test_no_filters_uses_match_all(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search()
        body = client.search.call_args[1]["body"]
        assert body["query"] == {"match_all": {}}

    async def test_queries_all_sources_by_default(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search()
        index_arg = client.search.call_args[1]["index"]
        for src in ["checkmk", "graylog", "wazuh", "o365", "teams"]:
            assert f"cs-feed-{src}" in index_arg

    async def test_sources_parameter_limits_indices(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search(sources=["graylog", "wazuh"])
        index_arg = client.search.call_args[1]["index"]
        assert "cs-feed-graylog" in index_arg
        assert "cs-feed-wazuh" in index_arg
        assert "cs-feed-checkmk" not in index_arg

    async def test_severity_filter_adds_term_clause(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search(severity="critical")
        body = client.search.call_args[1]["body"]
        filters = body["query"]["bool"]["filter"]
        assert {"term": {"severity": "critical"}} in filters

    async def test_metadata_filter_fetches_oversized_batch(self):
        """When CheckMK metadata filters are active, OpenSearch must return more items
        than the requested page size so Python post-filtering has enough to work with."""
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search(location=["MUE-0"], size=50)
        body = client.search.call_args[1]["body"]
        assert body["size"] >= 50 * 4

    async def test_no_metadata_filter_uses_exact_size(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search(size=30)
        body = client.search.call_args[1]["body"]
        assert body["size"] == 30

    async def test_post_filter_removes_non_matching_checkmk(self):
        """Items that don't match the location filter must be stripped from results."""
        hits = [_cmk(i, location="WOL-0") for i in range(10)]
        client = _os_client_stub(hits)
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            result = await search(location=["MUE-0"], size=50)
        assert result == []

    async def test_post_filter_keeps_graylog_items(self):
        """Non-CheckMK items must survive CheckMK-specific metadata filters."""
        hits = [_gl(0, location="WOL-0"), _cmk(0, location="WOL-0")]
        client = _os_client_stub(hits)
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            result = await search(location=["MUE-0"], size=50)
        assert len(result) == 1
        assert result[0]["source"] == "graylog"

    async def test_checkmk_cutoff_adds_range_clause(self):
        cutoff = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search(checkmk_cutoff=cutoff)
        body = client.search.call_args[1]["body"]
        assert "lte" in str(body)
        assert "created_at" in str(body)

    async def test_opensearch_error_returns_empty_list(self):
        client = AsyncMock()
        client.search.side_effect = Exception("Connection refused")
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            result = await search()
        assert result == []

    async def test_sorted_by_created_at_desc(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search()
        body = client.search.call_args[1]["body"]
        assert body["sort"] == [{"created_at": {"order": "desc"}}]

    async def test_db_exclusion_searches_go_into_must_not(self):
        """Exclusion FeedSearches loaded from DB must appear as must_not clauses."""
        client = _os_client_stub()
        db = _db_stub([_feed_search("body:DOT1X*"), _feed_search("body:\"cue.zipline.audit\"")])
        with patch("app.services.feed_index.get_opensearch", return_value=client), \
             patch("sqlalchemy.select", MagicMock()):
            await search(db=db)
        body = client.search.call_args[1]["body"]
        must_not = body["query"]["bool"].get("must_not", [])
        qs_queries = [c["query_string"]["query"] for c in must_not if "query_string" in c]
        assert "body:DOT1X*" in qs_queries
        assert "body:\"cue.zipline.audit\"" in qs_queries

    async def test_no_db_produces_no_must_not(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search(severity="high")   # no db= argument
        body = client.search.call_args[1]["body"]
        # must_not should be absent or empty when no db provided
        must_not = body["query"].get("bool", {}).get("must_not", [])
        assert must_not == []

    async def test_user_id_adds_personal_source_scope(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search(user_id="user-abc")
        body = client.search.call_args[1]["body"]
        assert "user-abc" in str(body)


# ═══════════════════════════════════════════════════════════════════════════
# Part 3 — search_by_query() + get_exclusion_must_not_clauses()
# ═══════════════════════════════════════════════════════════════════════════

class TestSearchByQuery:
    async def test_empty_query_uses_match_all(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-graylog", "")
        body = client.search.call_args[1]["body"]
        assert body["query"] == {"match_all": {}}

    async def test_non_empty_query_uses_query_string(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-graylog", "severity:high")
        body = client.search.call_args[1]["body"]
        assert "query_string" in body["query"]

    async def test_query_string_content_forwarded(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-wazuh", "metadata.rule_level:>=7")
        body = client.search.call_args[1]["body"]
        assert "metadata.rule_level:>=7" in body["query"]["query_string"]["query"]

    async def test_bare_host_in_query_is_normalised(self):
        """host:X must be expanded to (metadata.host:X OR metadata.agent:X)."""
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-graylog", "host:docker086")
        body = client.search.call_args[1]["body"]
        qs = body["query"]["query_string"]["query"]
        assert "metadata.host:docker086" in qs
        assert "metadata.agent:docker086" in qs

    async def test_index_pattern_forwarded_to_opensearch(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-checkmk", "severity:critical")
        assert client.search.call_args[1]["index"] == "cs-feed-checkmk"

    async def test_wildcard_index_pattern_forwarded(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-*", "body:error")
        assert client.search.call_args[1]["index"] == "cs-feed-*"

    async def test_user_id_wraps_query_in_bool_with_source_filter(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-*", "severity:high", user_id="user-xyz")
        body = client.search.call_args[1]["body"]
        assert "bool" in body["query"]
        assert "user-xyz" in str(body["query"])

    async def test_results_extracted_from_hits(self):
        docs = [{"id": "a", "title": "OOM killed"}, {"id": "b", "title": "segfault"}]
        client = _os_client_stub(docs)
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            result = await search_by_query("cs-feed-graylog", "body:oom")
        assert result == docs

    async def test_opensearch_error_returns_empty_list(self):
        client = AsyncMock()
        client.search.side_effect = Exception("Timeout")
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            result = await search_by_query("cs-feed-*", "body:error")
        assert result == []

    async def test_size_and_offset_forwarded(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-graylog", "body:error", size=25, from_=50)
        body = client.search.call_args[1]["body"]
        assert body["size"] == 25
        assert body["from"] == 50

    async def test_sorted_by_created_at_desc(self):
        client = _os_client_stub()
        with patch("app.services.feed_index.get_opensearch", return_value=client):
            await search_by_query("cs-feed-*", "")
        body = client.search.call_args[1]["body"]
        assert body["sort"] == [{"created_at": {"order": "desc"}}]


class TestGetExclusionMustNotClauses:
    async def test_no_exclusion_searches_returns_empty(self):
        db = _db_stub([])
        with patch("sqlalchemy.select", MagicMock()):
            result = await get_exclusion_must_not_clauses(db)
        assert result == []

    async def test_single_exclusion_search_returns_one_clause(self):
        db = _db_stub([_feed_search("body:DOT1X*")])
        with patch("sqlalchemy.select", MagicMock()):
            result = await get_exclusion_must_not_clauses(db)
        assert len(result) == 1
        assert result[0]["query_string"]["query"] == "body:DOT1X*"

    async def test_multiple_exclusion_searches_all_returned(self):
        searches = [
            _feed_search("body:DOT1X*"),
            _feed_search("body:\"cue.zipline.audit\""),
            _feed_search("body:\"cci:ccitext\""),
        ]
        db = _db_stub(searches)
        with patch("sqlalchemy.select", MagicMock()):
            result = await get_exclusion_must_not_clauses(db)
        assert len(result) == 3
        queries = [c["query_string"]["query"] for c in result]
        assert "body:DOT1X*" in queries
        assert "body:\"cue.zipline.audit\"" in queries
        assert "body:\"cci:ccitext\"" in queries

    async def test_each_clause_has_lenient_true(self):
        db = _db_stub([_feed_search("body:error")])
        with patch("sqlalchemy.select", MagicMock()):
            result = await get_exclusion_must_not_clauses(db)
        assert result[0]["query_string"]["lenient"] is True

    async def test_each_clause_uses_and_default_operator(self):
        db = _db_stub([_feed_search("body:error AND severity:high")])
        with patch("sqlalchemy.select", MagicMock()):
            result = await get_exclusion_must_not_clauses(db)
        assert result[0]["query_string"]["default_operator"] == "AND"

    async def test_db_error_returns_empty_list(self):
        db = AsyncMock()
        db.execute.side_effect = Exception("DB connection lost")
        with patch("sqlalchemy.select", MagicMock()):
            result = await get_exclusion_must_not_clauses(db)
        assert result == []

    async def test_db_called_once(self):
        db = _db_stub([])
        with patch("sqlalchemy.select", MagicMock()):
            await get_exclusion_must_not_clauses(db)
        db.execute.assert_awaited_once()
