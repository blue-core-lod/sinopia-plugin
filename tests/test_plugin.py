"""Tests for sinopia_plugin helper functions."""
import unittest

from sinopia_plugin import _format_date, _get_label, _get_types, _page_range, _process_results

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


if __name__ == "__main__":
    unittest.main()
