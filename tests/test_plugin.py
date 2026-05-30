"""Tests for sinopia_plugin helper functions."""
import unittest

from sinopia_plugin import (
    _detect_format,
    _format_date,
    _get_label,
    _get_types,
    _loc_types_from_uri,
    _page_range,
    _parse_loc_entry,
    _parse_loc_feed,
    _parse_rdf,
    _process_results,
)

BF = "http://id.loc.gov/ontologies/bibframe/"


class TestFormatDate(unittest.TestCase):
    def test_iso_formats_correctly(self):
        self.assertEqual(_format_date("2025-07-10T19:30:19.094198"), "Jul 10, 2025")

    def test_no_leading_zero_on_day(self):
        self.assertEqual(_format_date("2025-07-04T00:00:00"), "Jul 4, 2025")

    def test_empty_string_returns_empty(self):
        self.assertEqual(_format_date(""), "")

    def test_invalid_iso_returns_original(self):
        self.assertEqual(_format_date("not-a-date"), "not-a-date")


class TestGetLabel(unittest.TestCase):
    def _r(self, data):
        return {"data": data, "uri": "https://example.com/works/abc"}

    def test_prefers_rdfs_label(self):
        r = self._r({"http://www.w3.org/2000/01/rdf-schema#label": "My Label"})
        self.assertEqual(_get_label(r), "My Label")

    def test_falls_back_to_title_mainTitle(self):
        r = self._r({"title": [{"@type": "Title", "mainTitle": "Work Title"}]})
        self.assertEqual(_get_label(r), "Work Title")

    def test_falls_back_to_uri_when_no_title(self):
        r = self._r({})
        self.assertEqual(_get_label(r), "https://example.com/works/abc")

    def test_title_not_dict_skipped(self):
        r = self._r({"title": ["string title"]})
        self.assertEqual(_get_label(r), "https://example.com/works/abc")

    def test_title_as_dict_not_list(self):
        r = self._r({"title": {"@type": "Title", "mainTitle": "Single Title"}})
        self.assertEqual(_get_label(r), "Single Title")


class TestGetTypes(unittest.TestCase):
    def _r(self, data):
        return {"data": data}

    def test_short_type_gets_bf_prefix(self):
        result = _get_types(self._r({"@type": "Work"}))
        self.assertEqual(result, [BF + "Work"])

    def test_full_uri_preserved(self):
        result = _get_types(self._r({"@type": BF + "Work"}))
        self.assertEqual(result, [BF + "Work"])

    def test_list_of_types(self):
        result = _get_types(self._r({"@type": ["Work", "Text"]}))
        self.assertEqual(result, [BF + "Work", BF + "Text"])

    def test_empty_type_returns_empty(self):
        result = _get_types(self._r({}))
        self.assertEqual(result, [])


class TestProcessResults(unittest.TestCase):
    def _raw(self):
        return {
            "data": {
                "@type": "Work",
                "http://www.w3.org/2000/01/rdf-schema#label": "Test Work",
            },
            "uri": "https://dev.bcld.info/works/abc-123",
            "uuid": "abc-123",
            "updated_at": "2025-07-10T12:00:00",
        }

    def test_returns_list(self):
        result = _process_results([self._raw()])
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)

    def test_fields_present(self):
        item = _process_results([self._raw()])[0]
        self.assertIn("label", item)
        self.assertIn("uri", item)
        self.assertIn("uuid", item)
        self.assertIn("types", item)
        self.assertIn("modified", item)

    def test_label_extracted(self):
        item = _process_results([self._raw()])[0]
        self.assertEqual(item["label"], "Test Work")

    def test_uuid_extracted(self):
        item = _process_results([self._raw()])[0]
        self.assertEqual(item["uuid"], "abc-123")

    def test_modified_formatted(self):
        item = _process_results([self._raw()])[0]
        self.assertEqual(item["modified"], "Jul 10, 2025")

    def test_empty_list(self):
        self.assertEqual(_process_results([]), [])


class TestLocTypesFromUri(unittest.TestCase):
    def test_work_uri(self):
        self.assertEqual(_loc_types_from_uri("http://id.loc.gov/resources/works/123"), [BF + "Work"])

    def test_instance_uri(self):
        self.assertEqual(_loc_types_from_uri("http://id.loc.gov/resources/instances/123"), [BF + "Instance"])

    def test_name_authority(self):
        result = _loc_types_from_uri("http://id.loc.gov/authorities/names/n123")
        self.assertIn("http://www.loc.gov/mads/rdf/v1#Authority", result)

    def test_unknown_uri_returns_empty(self):
        self.assertEqual(_loc_types_from_uri("http://id.loc.gov/vocabulary/relators/aut"), [])


class TestParseLocEntry(unittest.TestCase):
    def _entry(self, title, href, updated="2022-06-03T00:00:00-04:00"):
        return [
            "atom:entry",
            {"xmlns:atom": "http://www.w3.org/2005/Atom"},
            ["atom:title", {"xmlns:atom": "http://www.w3.org/2005/Atom"}, title],
            ["atom:link", {"xmlns:atom": "http://www.w3.org/2005/Atom",
                           "rel": "alternate", "href": href}],
            ["atom:updated", {"xmlns:atom": "http://www.w3.org/2005/Atom"}, updated],
        ]

    def test_label_extracted(self):
        result = _parse_loc_entry(self._entry("Star Wars", "http://id.loc.gov/resources/works/1"))
        self.assertEqual(result["label"], "Star Wars")

    def test_uri_extracted(self):
        result = _parse_loc_entry(self._entry("Star Wars", "http://id.loc.gov/resources/works/1"))
        self.assertEqual(result["uri"], "http://id.loc.gov/resources/works/1")

    def test_group_is_loc(self):
        result = _parse_loc_entry(self._entry("Star Wars", "http://id.loc.gov/resources/works/1"))
        self.assertEqual(result["group"], "Library of Congress")

    def test_uuid_is_empty(self):
        result = _parse_loc_entry(self._entry("Star Wars", "http://id.loc.gov/resources/works/1"))
        self.assertEqual(result["uuid"], "")

    def test_modified_formatted(self):
        result = _parse_loc_entry(self._entry("X", "http://id.loc.gov/resources/works/1",
                                              "2022-06-03T00:00:00-04:00"))
        self.assertEqual(result["modified"], "Jun 3, 2022")

    def test_no_uri_returns_none(self):
        entry = ["atom:entry", {}, ["atom:title", {}, "Title"]]
        self.assertIsNone(_parse_loc_entry(entry))

    def test_typed_link_skipped(self):
        entry = [
            "atom:entry", {},
            ["atom:title", {}, "Title"],
            ["atom:link", {"rel": "alternate", "type": "application/rdf+xml",
                           "href": "http://id.loc.gov/resources/works/1.rdf"}],
        ]
        self.assertIsNone(_parse_loc_entry(entry))


class TestParseLocFeed(unittest.TestCase):
    def _feed(self, total, entries):
        return [
            "atom:feed",
            {"xmlns:atom": "http://www.w3.org/2005/Atom"},
            ["opensearch:totalResults", {"xmlns:opensearch": "http://a9.com/-/spec/opensearch/1.1/"}, str(total)],
        ] + entries

    def _entry(self, title, href):
        return [
            "atom:entry", {},
            ["atom:title", {}, title],
            ["atom:link", {"rel": "alternate", "href": href}],
            ["atom:updated", {}, "2022-01-01T00:00:00-04:00"],
        ]

    def test_total_extracted(self):
        _, total = _parse_loc_feed(self._feed(354, []))
        self.assertEqual(total, 354)

    def test_entries_parsed(self):
        feed = self._feed(1, [self._entry("Star Wars", "http://id.loc.gov/resources/works/1")])
        results, _ = _parse_loc_feed(feed)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["label"], "Star Wars")

    def test_empty_feed(self):
        results, total = _parse_loc_feed(self._feed(0, []))
        self.assertEqual(results, [])
        self.assertEqual(total, 0)


class TestProcessResultsGroup(unittest.TestCase):
    def test_bluecore_group(self):
        raw = [{"data": {"@type": "Work"}, "uri": "https://example.com/1",
                "uuid": "abc", "updated_at": ""}]
        item = _process_results(raw)[0]
        self.assertEqual(item["group"], "Blue Core")


class TestPageRange(unittest.TestCase):
    def test_few_pages_returns_all(self):
        self.assertEqual(_page_range(1, 5), [1, 2, 3, 4, 5])

    def test_eight_pages_no_ellipsis(self):
        self.assertEqual(_page_range(1, 8), [1, 2, 3, 4, 5, 6, 7, 8])

    def test_first_page_of_many(self):
        pages = _page_range(1, 50)
        self.assertEqual(pages[0], 1)
        self.assertIn("...", pages)
        self.assertEqual(pages[-1], 50)
        self.assertNotEqual(pages[1], "...")  # no leading ellipsis when starting at page 1

    def test_last_page_of_many(self):
        pages = _page_range(50, 50)
        self.assertEqual(pages[0], 1)
        self.assertIn("...", pages)
        self.assertEqual(pages[-1], 50)

    def test_middle_page_has_both_ellipses(self):
        pages = _page_range(25, 50)
        self.assertEqual(pages[0], 1)
        self.assertEqual(pages[1], "...")
        self.assertEqual(pages[-1], 50)
        self.assertEqual(pages[-2], "...")

    def test_current_page_in_range(self):
        pages = _page_range(25, 50)
        self.assertIn(25, pages)

    def test_single_page_returns_one(self):
        self.assertEqual(_page_range(1, 1), [1])

    def test_zero_pages_returns_empty(self):
        self.assertEqual(_page_range(1, 0), [])


class TestDetectFormat(unittest.TestCase):

    def test_turtle_content_type(self):
        self.assertEqual(_detect_format("text/turtle; charset=utf-8", ""), "turtle")

    def test_jsonld_content_type(self):
        self.assertEqual(_detect_format("application/ld+json", ""), "json-ld")

    def test_rdfxml_content_type(self):
        self.assertEqual(_detect_format("application/rdf+xml", ""), "xml")

    def test_ttl_extension_fallback(self):
        self.assertEqual(_detect_format("", "https://example.com/shapes.ttl"), "turtle")

    def test_jsonld_extension_fallback(self):
        self.assertEqual(_detect_format("", "https://example.com/data.jsonld"), "json-ld")

    def test_unknown_defaults_to_turtle(self):
        self.assertEqual(_detect_format("", "https://example.com/data"), "turtle")

    def test_url_query_string_ignored(self):
        self.assertEqual(_detect_format("", "https://example.com/data.ttl?v=1"), "turtle")

    def test_content_type_takes_priority_over_extension(self):
        self.assertEqual(
            _detect_format("application/ld+json", "https://example.com/data.ttl"),
            "json-ld",
        )


_TURTLE_SIMPLE = """
@prefix bf: <http://id.loc.gov/ontologies/bibframe/> .
<https://example.com/works/1>
    a bf:Work ;
    bf:mainTitle "Star Wars" .
"""

_TURTLE_BAD = "this is not valid turtle @@@@"


class TestParseRdf(unittest.TestCase):

    def test_valid_turtle_returns_graph(self):
        g, err = _parse_rdf(_TURTLE_SIMPLE, "", "turtle")
        self.assertIsNone(err)
        self.assertGreater(len(g), 0)

    def test_triple_count_correct(self):
        g, _ = _parse_rdf(_TURTLE_SIMPLE, "", "turtle")
        self.assertEqual(len(g), 2)

    def test_invalid_turtle_returns_error(self):
        g, err = _parse_rdf(_TURTLE_BAD, "", "turtle")
        self.assertIsNotNone(err)
        self.assertEqual(len(g), 0)

    def test_base_uri_applied(self):
        ttl = "<> a <http://id.loc.gov/ontologies/bibframe/Work> ."
        g, err = _parse_rdf(ttl, "https://example.com/works/1", "turtle")
        self.assertIsNone(err)
        self.assertEqual(len(g), 1)

    def test_empty_string_returns_empty_graph(self):
        g, err = _parse_rdf("", "", "turtle")
        self.assertIsNone(err)
        self.assertEqual(len(g), 0)

    def test_jsonld_parsed(self):
        jsonld = '{"@id":"https://example.com/1","@type":"http://id.loc.gov/ontologies/bibframe/Work"}'
        g, err = _parse_rdf(jsonld, "", "json-ld")
        self.assertIsNone(err)
        self.assertEqual(len(g), 1)


if __name__ == "__main__":
    unittest.main()
