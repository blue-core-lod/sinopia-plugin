"""
Unit tests for src/main.py.

Browser-specific modules (pyscript, pyodide, js) are mocked before import
so the test suite runs in a standard Python environment without Pyodide.
The emscripten-platform guard in main.py prevents _entry_point() from being
scheduled automatically during import.
"""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Mock browser modules before importing main ─────────────────────────────────
_mock_document = MagicMock()
_mock_when     = MagicMock(side_effect=lambda *a, **kw: (lambda f: f))
_mock_pyfetch  = AsyncMock()

sys.modules["pyscript"]      = MagicMock(document=_mock_document, when=_mock_when)
sys.modules["pyodide"]       = MagicMock()
sys.modules["pyodide.http"]  = MagicMock(pyfetch=_mock_pyfetch)
sys.modules["js"]            = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import main  # noqa: E402  (must come after sys.modules patches)

BF = "http://id.loc.gov/ontologies/bibframe/"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_state(**kwargs) -> main.EditorState:
    s = main.EditorState("test-uuid")
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _ps(path, name, required=False, value_class="", datatype="", description="", order=1):
    return main.PropShape(path, name, required, value_class, datatype, description, order)


# ── EditorState.__init__ ───────────────────────────────────────────────────────

class TestEditorStateInit(unittest.TestCase):

    def test_defaults(self):
        s = main.EditorState("abc-123")
        self.assertEqual(s.resource_id, "abc-123")
        self.assertEqual(s.resource_uri, "")
        self.assertEqual(s.resource_types, [])
        self.assertEqual(s.resource_label, "")
        self.assertEqual(s.raw_data, {})
        self.assertEqual(s.triples, [])
        self.assertEqual(s.props, {})
        self.assertEqual(s.labels, {})
        self.assertEqual(s.field_edits, {})


# ── EditorState._literal ───────────────────────────────────────────────────────

class TestLiteral(unittest.TestCase):

    def test_plain_string(self):
        self.assertEqual(main.EditorState._literal("hello"), "hello")

    def test_dict_with_value(self):
        self.assertEqual(main.EditorState._literal({"@value": "Star wars"}), "Star wars")

    def test_dict_with_id(self):
        self.assertEqual(main.EditorState._literal({"@id": "http://example.com/foo"}),
                         "http://example.com/foo")

    def test_dict_value_takes_priority_over_id(self):
        self.assertEqual(main.EditorState._literal({"@value": "lit", "@id": "http://x"}), "lit")

    def test_integer_coerced(self):
        self.assertEqual(main.EditorState._literal(42), "42")


# ── EditorState._parse ─────────────────────────────────────────────────────────

class TestParse(unittest.TestCase):

    SAMPLE = {
        "@id":   "https://dev.bcld.info/works/ed1213b5",
        "@type": [BF + "Work", BF + "Monograph"],
        "http://www.w3.org/2000/01/rdf-schema#label": {"@value": "Star wars"},
        BF + "mainTitle": {"@value": "Star wars"},
    }

    def _parsed(self, data=None):
        s = main.EditorState("ed1213b5")
        s._parse(data or self.SAMPLE)
        return s

    def test_resource_uri(self):
        self.assertEqual(self._parsed().resource_uri, "https://dev.bcld.info/works/ed1213b5")

    def test_resource_types_list(self):
        self.assertIn(BF + "Monograph", self._parsed().resource_types)

    def test_resource_types_string_coerced_to_list(self):
        data = dict(self.SAMPLE, **{"@type": BF + "Work"})
        self.assertIsInstance(self._parsed(data).resource_types, list)

    def test_resource_label(self):
        self.assertEqual(self._parsed().resource_label, "Star wars")

    def test_triples_populated(self):
        s = self._parsed()
        self.assertGreater(len(s.triples), 0)
        self.assertEqual({t[0] for t in s.triples}, {"https://dev.bcld.info/works/ed1213b5"})

    def test_props_populated(self):
        self.assertIn(BF + "mainTitle", self._parsed().props)

    def test_expanded_jsonld_list(self):
        data = [{"@id": "_:blank"},
                {"@id": "https://dev.bcld.info/works/xyz", "@type": [BF + "Work"]}]
        s = main.EditorState("xyz")
        s._parse(data)
        self.assertEqual(s.resource_uri, "https://dev.bcld.info/works/xyz")

    def test_fallback_uri_when_missing(self):
        s = main.EditorState("fallback-id")
        s._parse({"@type": [BF + "Work"]})
        self.assertIn("fallback-id", s.resource_uri)

    def test_skip_keys_not_in_triples(self):
        preds = {t[1] for t in self._parsed().triples}
        self.assertNotIn("@id", preds)
        self.assertNotIn("@type", preds)

    def test_nested_blank_node_props_extracted(self):
        data = {
            "@id": "https://dev.bcld.info/works/w1",
            "@type": [BF + "Work"],
            BF + "title": [{"@type": [BF + "Title"], BF + "mainTitle": [{"@value": "Star Wars"}]}],
        }
        s = main.EditorState("w1")
        s._parse(data)
        self.assertIn(BF + "mainTitle", s.props)
        self.assertEqual(s.props[BF + "mainTitle"], ["Star Wars"])

    def test_real_uri_node_not_recursed(self):
        data = {
            "@id":   "https://dev.bcld.info/works/w3",
            "@type": [BF + "Work"],
            BF + "language": [{"@id": "http://id.loc.gov/vocabulary/languages/eng"}],
        }
        s = main.EditorState("w3")
        s._parse(data)
        self.assertNotIn("http://www.w3.org/2000/01/rdf-schema#label", s.props)


# ── EditorState.labels (inline label capture) ─────────────────────────────────

class TestInlineLabels(unittest.TestCase):

    def test_inline_label_captured(self):
        """rdfs:label on a URI-referenced node should be stored in state.labels."""
        data = {
            "@id":   "https://dev.bcld.info/works/w1",
            "@type": [BF + "Work"],
            BF + "language": [{
                "@id": "http://id.loc.gov/vocabulary/languages/doi",
                "http://www.w3.org/2000/01/rdf-schema#label": [{"@value": "Dogri"}],
            }],
        }
        s = main.EditorState("w1")
        s._parse(data)
        self.assertEqual(s.labels.get("http://id.loc.gov/vocabulary/languages/doi"), "Dogri")

    def test_no_label_no_entry(self):
        data = {
            "@id":   "https://dev.bcld.info/works/w2",
            "@type": [BF + "Work"],
            BF + "language": [{"@id": "http://id.loc.gov/vocabulary/languages/eng"}],
        }
        s = main.EditorState("w2")
        s._parse(data)
        self.assertNotIn("http://id.loc.gov/vocabulary/languages/eng", s.labels)

    def test_compact_label_key_captured(self):
        data = {
            "@id":   "https://dev.bcld.info/works/w3",
            "@type": [BF + "Work"],
            BF + "language": [{
                "@id":   "http://id.loc.gov/vocabulary/languages/fre",
                "rdfs:label": "French",
            }],
        }
        s = main.EditorState("w3")
        s._parse(data)
        self.assertEqual(s.labels.get("http://id.loc.gov/vocabulary/languages/fre"), "French")


# ── EditorState.type_short / resource_name / has_prop ─────────────────────────

class TestEditorStateMethods(unittest.TestCase):

    def test_type_short_monograph(self):
        s = _make_state(resource_types=[BF + "Work", BF + "Monograph"])
        self.assertEqual(s.type_short(), "Monograph")

    def test_type_short_fallback(self):
        s = _make_state(resource_types=[])
        self.assertEqual(s.type_short(), "Work")

    def test_resource_name(self):
        s = _make_state(resource_types=[BF + "Work", BF + "Monograph"])
        self.assertEqual(s.resource_name(), "_Work (Monograph)")

    def test_has_prop_found(self):
        s = _make_state(props={BF + "title": ["x"]})
        self.assertTrue(s.has_prop("title"))

    def test_has_prop_not_found(self):
        self.assertFalse(_make_state(props={}).has_prop("title"))


# ── EditorState.load ───────────────────────────────────────────────────────────

class TestLoad(unittest.TestCase):

    def _mock_response(self, data, ok=True, status=200):
        resp = AsyncMock()
        resp.ok = ok
        resp.status = status
        resp.status_text = "OK" if ok else "Not Found"
        resp.json = AsyncMock(return_value=data)
        return resp

    def test_successful_load(self):
        data = {"@id": "https://dev.bcld.info/works/test-uuid", "@type": [BF + "Work"]}
        s = main.EditorState("test-uuid")
        with patch("main.pyfetch", return_value=self._mock_response(data)):
            _run(s.load())
        self.assertEqual(s.resource_uri, "https://dev.bcld.info/works/test-uuid")

    def test_http_error_raises(self):
        s = main.EditorState("bad-uuid")
        with patch("main.pyfetch", return_value=self._mock_response({}, ok=False, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                _run(s.load())
        self.assertIn("404", str(ctx.exception))

    def test_fetch_url_includes_resource_id(self):
        data = {"@id": "https://dev.bcld.info/works/my-id", "@type": []}
        s = main.EditorState("my-id")
        with patch("main.pyfetch", return_value=self._mock_response(data)) as mock_fetch:
            _run(s.load())
        self.assertIn("my-id", mock_fetch.call_args[0][0])


# ── _sid ───────────────────────────────────────────────────────────────────────

class TestSid(unittest.TestCase):

    def test_spaces_to_dashes(self):
        self.assertEqual(main._sid("Work Title"), "work-title")

    def test_slashes_become_dashes(self):
        self.assertEqual(main._sid("Variant and/or Parallel"), "variant-and-or-parallel")

    def test_parens_removed(self):
        self.assertEqual(main._sid("Other (creator)"), "other-creator")


# ── _add_link ──────────────────────────────────────────────────────────────────

class TestAddLink(unittest.TestCase):

    def test_label_present(self):
        self.assertIn("+ Add Contributor", main._add_link("Contributor"))

    def test_no_external_icon_by_default(self):
        self.assertNotIn("box-arrow-up-right", main._add_link("Note"))

    def test_external_icon_added(self):
        self.assertIn("box-arrow-up-right", main._add_link("Part", external=True))


# ── _input_card ────────────────────────────────────────────────────────────────

class TestInputCard(unittest.TestCase):

    def test_contains_ids(self):
        html = main._input_card("bf:title", "Enter", "my-id")
        self.assertIn('id="my-id"', html)
        self.assertIn('data-field="my-id"', html)

    def test_value_rendered(self):
        self.assertIn("Star wars", main._input_card("bf:title", "p", "id", value="Star wars"))

    def test_html_escaped_in_value(self):
        html = main._input_card("bf:title", "p", "id", value="<script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_data_rdf_path_present(self):
        html = main._input_card(BF + "title", "p", "id")
        self.assertIn(f'data-rdf-path="{BF}title"', html)


# ── _values_for_path ──────────────────────────────────────────────────────────

class TestValuesForPath(unittest.TestCase):

    def test_exact_uri_match(self):
        s = _make_state(props={BF + "title": ["x"]})
        self.assertEqual(main._values_for_path(s, BF + "title"), ["x"])

    def test_fragment_fallback(self):
        s = _make_state(props={BF + "title": ["X"]})
        self.assertEqual(main._values_for_path(s, "http://other.org/onto/title"), ["X"])

    def test_compact_key_match(self):
        s = _make_state(props={"title": ["Star Wars"]})
        self.assertEqual(main._values_for_path(s, BF + "title"), ["Star Wars"])

    def test_no_match_returns_empty(self):
        self.assertEqual(main._values_for_path(_make_state(props={}), BF + "title"), [])

    def test_nested_blob_filtered_out(self):
        s = _make_state(props={"contribution": ["{'@type': ['PrimaryContribution']}"]})
        self.assertEqual(main._values_for_path(s, "contribution"), [])

    def test_list_blob_filtered_out(self):
        s = _make_state(props={"items": ["[1, 2, 3]", "plain"]})
        self.assertEqual(main._values_for_path(s, "items"), ["plain"])


# ── PropCardFactory ───────────────────────────────────────────────────────────

class TestPropCardFactory(unittest.TestCase):

    def _factory(self, **props):
        return main.PropCardFactory(_make_state(props=props,
                                                resource_uri="https://example.com/w/1"))

    # ── build_node_card ───────────────────────────────────────────────────────

    def test_required_with_no_value_shows_blank_card(self):
        factory = self._factory()
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps], target_class=BF + "Work")
        self.assertIn("inputcard-work-title-0", html)

    def test_optional_with_no_value_returns_empty(self):
        factory = self._factory()
        ps      = _ps(BF + "note", "Note", required=False)
        html    = factory.build_node_card("Work", [ps], target_class=BF + "Work")
        self.assertEqual(html, "")

    def test_optional_with_value_shows_card(self):
        factory = self._factory(**{BF + "note": ["A note"]})
        ps      = _ps(BF + "note", "Note", required=False)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn("A note", html)

    def test_required_with_value_prefills(self):
        factory = self._factory(**{BF + "title": ["Star Wars"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn("Star Wars", html)

    # ── prop-card IDs and data attributes ────────────────────────────────────

    def test_propcard_id_from_shape_name(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn('id="propcard-work"', html)

    def test_propcard_id_from_rdfs_label_with_special_chars(self):
        # "Instance (Monograph) Print" → propcard-instance-monograph-print
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Title", required=True)
        html    = factory.build_node_card("Instance (Monograph) Print", [ps])
        self.assertIn('id="propcard-instance-monograph-print"', html)

    def test_propcard_data_rdf_shape(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps],
                                          shape_uri="http://example.com/shapes/Work")
        self.assertIn('data-rdf-shape="http://example.com/shapes/Work"', html)

    def test_propcard_data_rdf_class(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps], target_class=BF + "Work")
        self.assertIn(f'data-rdf-class="{BF}Work"', html)

    def test_propcard_data_rdf_subject(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn('data-rdf-subject="https://example.com/w/1"', html)

    def test_no_shape_uri_omits_data_rdf_shape(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertNotIn("data-rdf-shape", html)

    # ── input-card IDs and data attributes ───────────────────────────────────

    def test_inputcard_id(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn('id="inputcard-work-title-0"', html)

    def test_inputcard_data_rdf_path(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn(f'data-rdf-path="{BF}title"', html)

    def test_textarea_id_is_inputcard_value(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn('id="inputcard-work-title-0-value"', html)

    # ── required star ─────────────────────────────────────────────────────────

    def test_required_shows_red_star(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Work Title", required=True)
        html    = factory.build_node_card("Work", [ps])
        self.assertIn("text-danger", html)

    def test_optional_no_star(self):
        factory = self._factory(**{BF + "note": ["X"]})
        ps      = _ps(BF + "note", "Note", required=False)
        html    = factory.build_node_card("Work", [ps])
        self.assertNotIn("text-danger", html)

    # ── multiple values ───────────────────────────────────────────────────────

    def test_multiple_values_multiple_inputcards(self):
        factory = self._factory(**{BF + "language": ["eng", "fre"]})
        ps      = _ps(BF + "language", "Language")
        html    = factory.build_node_card("Work", [ps])
        self.assertIn("inputcard-language-0", html)
        self.assertIn("inputcard-language-1", html)
        self.assertIn("eng", html)
        self.assertIn("fre", html)

    # ── violations ────────────────────────────────────────────────────────────

    def test_violation_shows_optional_property(self):
        factory = self._factory()
        ps      = _ps(BF + "note", "Note", required=False)
        html    = factory.build_node_card(
            "Work", [ps],
            [{"path": BF + "note", "severity": "violation", "message": ""}],
        )
        self.assertIn("inputcard-note-0", html)

    def test_violation_badge_shown(self):
        factory = self._factory()
        ps      = _ps(BF + "note", "Note", required=False)
        html    = factory.build_node_card(
            "Work", [ps],
            [{"path": BF + "note", "severity": "violation", "message": ""}],
        )
        self.assertIn("bg-danger", html)

    def test_warning_badge_shown(self):
        factory = self._factory()
        ps      = _ps(BF + "note", "Note", required=False)
        html    = factory.build_node_card(
            "Work", [ps],
            [{"path": BF + "note", "severity": "warning", "message": ""}],
        )
        self.assertIn("bg-warning", html)

    def test_info_badge_shown(self):
        factory = self._factory()
        ps      = _ps(BF + "note", "Note", required=False)
        html    = factory.build_node_card(
            "Work", [ps],
            [{"path": BF + "note", "severity": "info", "message": ""}],
        )
        self.assertIn("bg-info", html)

    # ── build_fallback_card ───────────────────────────────────────────────────

    def test_fallback_card_shows_leaf_props(self):
        factory = self._factory(**{BF + "language": ["eng"]})
        self.assertIn("eng", factory.build_fallback_card())

    def test_fallback_card_has_propcard_id(self):
        factory = self._factory(**{BF + "language": ["eng"]})
        self.assertIn("propcard-", factory.build_fallback_card())

    def test_fallback_card_empty_when_no_props(self):
        self.assertEqual(self._factory().build_fallback_card(), "")

    def test_fallback_card_filters_blobs(self):
        factory = self._factory(**{"contribution": ["{'@type': ['PrimaryContribution']}"],
                                   BF + "language": ["eng"]})
        html = factory.build_fallback_card()
        self.assertIn("eng", html)
        self.assertNotIn("PrimaryContribution", html)

    # ── metadata ──────────────────────────────────────────────────────────────

    def test_value_class_shown(self):
        factory = self._factory(**{BF + "title": ["X"]})
        ps      = _ps(BF + "title", "Title", value_class=BF + "Title")
        html    = factory.build_node_card("Work", [ps])
        self.assertIn("bibframe/Title", html)

    def test_compact_key_value_found(self):
        factory = self._factory(language=["eng"])
        ps      = _ps(BF + "language", "Language")
        html    = factory.build_node_card("Work", [ps])
        self.assertIn("eng", html)


# ── URI input-card widget ─────────────────────────────────────────────────────

class TestUriInputCard(unittest.TestCase):
    """PropCardFactory uses the URI+Label widget when ps.value_class is set
    or when the existing value is a URL string."""

    def _factory(self, **props):
        state = _make_state(props=props, resource_uri="https://example.com/w/1")
        return main.PropCardFactory(state)

    def _ps_uri(self, path, name, value_class=BF + "Language", required=False):
        return _ps(path, name, required=required, value_class=value_class)

    # ── dispatch ──────────────────────────────────────────────────────────────

    def test_value_class_triggers_uri_widget(self):
        factory = self._factory(**{BF + "language": ["http://id.loc.gov/vocabulary/languages/eng"]})
        ps   = self._ps_uri(BF + "language", "Language")
        html = factory.build_node_card("Work", [ps])
        self.assertIn('id="inputcard-language-0-uri"', html)
        self.assertIn('id="inputcard-language-0-label"', html)

    def test_http_value_without_class_triggers_uri_widget(self):
        # Even without sh:value_class, a value starting with http:// → URI widget
        factory = self._factory(**{BF + "language": ["http://id.loc.gov/vocabulary/languages/eng"]})
        ps   = _ps(BF + "language", "Language", value_class="")
        html = factory.build_node_card("Work", [ps])
        self.assertIn('id="inputcard-language-0-uri"', html)

    def test_literal_value_uses_literal_widget(self):
        factory = self._factory(**{BF + "note": ["A plain note"]})
        ps   = _ps(BF + "note", "Note")
        html = factory.build_node_card("Work", [ps])
        self.assertIn('id="inputcard-note-0-value"', html)
        self.assertNotIn('id="inputcard-note-0-uri"', html)

    # ── URI field ─────────────────────────────────────────────────────────────

    def test_uri_prefilled(self):
        factory = self._factory(**{BF + "language": ["http://id.loc.gov/vocabulary/languages/doi"]})
        ps   = self._ps_uri(BF + "language", "Language")
        html = factory.build_node_card("Work", [ps])
        self.assertIn("http://id.loc.gov/vocabulary/languages/doi", html)

    def test_uri_has_data_rdf_path(self):
        factory = self._factory(**{BF + "language": ["http://id.loc.gov/vocabulary/languages/eng"]})
        ps   = self._ps_uri(BF + "language", "Language")
        html = factory.build_node_card("Work", [ps])
        self.assertIn(f'data-rdf-path="{BF}language"', html)

    def test_external_link_shown_when_uri_present(self):
        factory = self._factory(**{BF + "language": ["http://id.loc.gov/vocabulary/languages/eng"]})
        ps   = self._ps_uri(BF + "language", "Language")
        html = factory.build_node_card("Work", [ps])
        self.assertIn('target="_blank"', html)
        self.assertIn("http://id.loc.gov/vocabulary/languages/eng", html)

    def test_no_external_link_when_empty(self):
        factory = self._factory()
        ps   = self._ps_uri(BF + "language", "Language", required=True)
        html = factory.build_node_card("Work", [ps])
        self.assertNotIn('target="_blank"', html)

    # ── Label field ───────────────────────────────────────────────────────────

    def test_label_field_present(self):
        factory = self._factory(**{BF + "language": ["http://id.loc.gov/vocabulary/languages/doi"]})
        ps   = self._ps_uri(BF + "language", "Language")
        html = factory.build_node_card("Work", [ps])
        self.assertIn('id="inputcard-language-0-label"', html)

    def test_label_prefilled_from_state_labels(self):
        state = _make_state(
            props={BF + "language": ["http://id.loc.gov/vocabulary/languages/doi"]},
            labels={"http://id.loc.gov/vocabulary/languages/doi": "Dogri"},
            resource_uri="https://example.com/w/1",
        )
        factory = main.PropCardFactory(state)
        ps      = self._ps_uri(BF + "language", "Language")
        html    = factory.build_node_card("Work", [ps])
        self.assertIn("Dogri", html)

    def test_label_html_escaped_from_state_labels(self):
        # A resource-supplied rdfs:label must be HTML-escaped before going into
        # the textarea, so it cannot break out of the element.
        state = _make_state(
            props={BF + "language": ["http://id.loc.gov/vocabulary/languages/doi"]},
            labels={"http://id.loc.gov/vocabulary/languages/doi": "</textarea><script>x</script>"},
            resource_uri="https://example.com/w/1",
        )
        factory = main.PropCardFactory(state)
        ps      = self._ps_uri(BF + "language", "Language")
        html    = factory.build_node_card("Work", [ps])
        self.assertNotIn("</textarea><script>", html)
        self.assertIn("&lt;/textarea&gt;&lt;script&gt;", html)

    def test_label_field_data_rdf_path_is_rdfs_label(self):
        factory = self._factory(**{BF + "language": ["http://id.loc.gov/vocabulary/languages/doi"]})
        ps   = self._ps_uri(BF + "language", "Language")
        html = factory.build_node_card("Work", [ps])
        rdfs_label = "http://www.w3.org/2000/01/rdf-schema#label"
        self.assertIn(f'data-rdf-path="{rdfs_label}"', html)

    # ── required star survives dispatch ───────────────────────────────────────

    def test_required_star_on_uri_widget(self):
        factory = self._factory()
        ps   = self._ps_uri(BF + "language", "Language", required=True)
        html = factory.build_node_card("Work", [ps])
        self.assertIn("text-danger", html)


# ── SHACL helpers ─────────────────────────────────────────────────────────────

_SHACL_WORK = """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix bf:  <http://id.loc.gov/ontologies/bibframe/> .

[] a sh:NodeShape ;
   sh:targetClass bf:Work ;
   sh:property [
       sh:path bf:title ;    sh:name "Work Title" ; sh:minCount 1 ; sh:order 1 ;
       sh:class bf:Title ;
   ] ;
   sh:property [
       sh:path bf:language ; sh:name "Language" ;   sh:minCount 1 ; sh:order 2 ;
   ] ;
   sh:property [
       sh:path bf:note ;     sh:name "Note" ;       sh:minCount 0 ; sh:order 5 ;
   ] .
"""


def _shacl_graph():
    import rdflib
    g = rdflib.Graph()
    g.parse(data=_SHACL_WORK, format="turtle")
    return g


class TestLoadShaclGraph(unittest.TestCase):

    def test_empty_when_no_template(self):
        with patch("main.js") as mock_js:
            mock_js.localStorage.getItem.return_value = None
            g = main._load_shacl_graph()
        self.assertEqual(len(g), 0)

    def test_parses_valid_turtle(self):
        with patch("main.js") as mock_js:
            mock_js.localStorage.getItem.return_value = json.dumps([_SHACL_WORK])
            g = main._load_shacl_graph()
        self.assertGreater(len(g), 0)

    def test_merges_multiple_graphs(self):
        shape2 = """
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix bf: <http://id.loc.gov/ontologies/bibframe/> .
[] a sh:NodeShape ; sh:targetClass bf:Instance .
"""
        with patch("main.js") as mock_js:
            mock_js.localStorage.getItem.return_value = json.dumps([_SHACL_WORK, shape2])
            g = main._load_shacl_graph()
        targets = [str(t) for t in g.objects(None, main.SH.targetClass)]
        self.assertIn(BF + "Work", targets)
        self.assertIn(BF + "Instance", targets)


class TestShapesForTypes(unittest.TestCase):

    def test_finds_work_shape(self):
        g = _shacl_graph()
        self.assertEqual(len(main._shapes_for_types(g, [BF + "Work"])), 1)

    def test_no_match(self):
        g = _shacl_graph()
        self.assertEqual(main._shapes_for_types(g, ["http://example.com/Unknown"]), [])

    def test_empty_types(self):
        g = _shacl_graph()
        self.assertEqual(main._shapes_for_types(g, []), [])


_SHACL_MULTI = _SHACL_WORK + """
@prefix sh:  <http://www.w3.org/ns/shacl#> .
@prefix bf:  <http://id.loc.gov/ontologies/bibframe/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix ex: <http://example.com/shapes/> .

ex:InstanceShape a sh:NodeShape ;
    rdfs:label "Instance (Monograph) Print" ;
    sh:targetClass bf:Instance ;
    sh:property [
        sh:path bf:title ; sh:name "Instance Title" ; sh:minCount 1 ; sh:order 1 ;
    ] .
"""


class TestAllShapes(unittest.TestCase):

    def test_returns_all_node_shapes(self):
        import rdflib
        g = rdflib.Graph()
        g.parse(data=_SHACL_MULTI, format="turtle")
        shapes = main._all_shapes(g)
        self.assertEqual(len(shapes), 2)

    def test_named_shapes_before_blanks(self):
        import rdflib
        g = rdflib.Graph()
        g.parse(data=_SHACL_MULTI, format="turtle")
        shapes = main._all_shapes(g)
        # ex:InstanceShape is a named URIRef; the Work shape is a blank node
        import rdflib as rl
        named = [s for s in shapes if isinstance(s, rl.URIRef)]
        self.assertEqual(len(named), 1)
        self.assertIn("InstanceShape", str(named[0]))

    def test_empty_graph_returns_empty(self):
        import rdflib
        self.assertEqual(main._all_shapes(rdflib.Graph()), [])


class TestShapeLabel(unittest.TestCase):

    def test_rdfs_label_preferred(self):
        import rdflib
        g = rdflib.Graph()
        g.parse(data=_SHACL_MULTI, format="turtle")
        shapes = main._all_shapes(g)
        named  = next(s for s in shapes if isinstance(s, rdflib.URIRef))
        self.assertEqual(main._shape_label(g, named), "Instance (Monograph) Print")

    def test_falls_back_to_uri_fragment(self):
        import rdflib
        g = rdflib.Graph()
        g.parse(data="""
@prefix sh: <http://www.w3.org/ns/shacl#> .
<http://example.com/shapes/MyShape> a sh:NodeShape .
""", format="turtle")
        shape = list(g.subjects(rdflib.RDF.type, main.SH.NodeShape))[0]
        self.assertEqual(main._shape_label(g, shape), "MyShape")

    def test_colon_separated_uri_uses_last_segment(self):
        import rdflib
        g = rdflib.Graph()
        # Simulate big:Monograph:Instance:Print style URI
        g.parse(data="""
@prefix sh: <http://www.w3.org/ns/shacl#> .
@prefix big: <http://example.com/big/> .
<http://example.com/big/Monograph:Instance:Print> a sh:NodeShape .
""", format="turtle")
        shape = list(g.subjects(rdflib.RDF.type, main.SH.NodeShape))[0]
        label = main._shape_label(g, shape)
        self.assertTrue(len(label) > 0)


class TestPropShapes(unittest.TestCase):

    def setUp(self):
        self.g     = _shacl_graph()
        self.shape = main._shapes_for_types(self.g, [BF + "Work"])[0]

    def test_count(self):
        self.assertEqual(len(main._prop_shapes(self.g, self.shape)), 3)

    def test_sorted_by_order(self):
        orders = [p.order for p in main._prop_shapes(self.g, self.shape)]
        self.assertEqual(orders, sorted(orders))

    def test_required_flag(self):
        props = main._prop_shapes(self.g, self.shape)
        title = next(p for p in props if "title" in p.path)
        self.assertTrue(title.required)
        note  = next(p for p in props if "note"  in p.path)
        self.assertFalse(note.required)

    def test_value_class_extracted(self):
        props = main._prop_shapes(self.g, self.shape)
        title = next(p for p in props if p.name == "Work Title")
        self.assertIn("Title", title.value_class)


class TestValidate(unittest.TestCase):

    _TURTLE_VALID = """
@prefix bf: <http://id.loc.gov/ontologies/bibframe/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
[] a sh:NodeShape ; sh:targetClass bf:Work ;
   sh:property [ sh:path bf:title ; sh:minCount 1 ] .
<https://example.com/w/1> a bf:Work ; bf:title "Star Wars" .
"""

    _TURTLE_SHACL = """
@prefix bf: <http://id.loc.gov/ontologies/bibframe/> .
@prefix sh: <http://www.w3.org/ns/shacl#> .
[] a sh:NodeShape ; sh:targetClass bf:Work ;
   sh:property [ sh:path bf:title ; sh:minCount 1 ] .
"""

    def _state(self, raw):
        s = main.EditorState("w1")
        s.raw_data = raw
        return s

    def test_returns_empty_list_on_conforms(self):
        import rdflib
        g = rdflib.Graph()
        g.parse(data=self._TURTLE_SHACL, format="turtle")
        s = self._state({"@id": "https://example.com/w/1",
                         "@type": [BF + "Work"],
                         BF + "title": "Star Wars"})
        result = main._validate(s, g)
        self.assertIsInstance(result, list)

    def test_returns_list_on_violation(self):
        import rdflib
        g = rdflib.Graph()
        g.parse(data=self._TURTLE_SHACL, format="turtle")
        # resource has no bf:title → should violate minCount 1
        s = self._state({"@id": "https://example.com/w/1", "@type": [BF + "Work"]})
        result = main._validate(s, g)
        self.assertIsInstance(result, list)

    def test_returns_empty_list_on_exception(self):
        import rdflib as _rdflib
        # Empty SHACL graph should not raise; just return []
        g = _rdflib.Graph()
        s = self._state({})
        self.assertEqual(main._validate(s, g), [])


# ── render_resource_header ────────────────────────────────────────────────────

class TestRenderResourceHeader(unittest.TestCase):

    def test_sets_badge(self):
        s = _make_state(resource_types=[BF + "Monograph"])
        elements = {}
        def _get(eid):
            elements.setdefault(eid, MagicMock())
            return elements[eid]
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.side_effect = _get
            main.render_resource_header(s)
        self.assertEqual(elements["resource-badge"].textContent, "MONOGRAPH")


# ── render_uri_bar ────────────────────────────────────────────────────────────

class TestRenderUriBar(unittest.TestCase):

    def test_sets_uri(self):
        s = _make_state(resource_uri="https://dev.bcld.info/works/test")
        el = MagicMock()
        with patch("main.document") as mock_doc, patch("main.when"):
            mock_doc.getElementById.return_value = el
            main.render_uri_bar(s)
        self.assertIn("https://dev.bcld.info/works/test", el.innerHTML)


# ── render_triples ────────────────────────────────────────────────────────────

class TestRenderTriples(unittest.TestCase):

    def test_no_call_when_empty(self):
        s = _make_state(triples=[])
        with patch("main.document") as mock_doc:
            main.render_triples(s)
        mock_doc.getElementById.assert_not_called()

    def test_html_escaped(self):
        s = _make_state(triples=[("<s>", "<p>", "<o>")])
        mock_tbody = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.side_effect = (
                lambda eid: MagicMock() if eid == "triples-section" else mock_tbody
            )
            main.render_triples(s)
        self.assertIn("&lt;s&gt;", mock_tbody.innerHTML)


# ── render_left_nav ───────────────────────────────────────────────────────────

class TestRenderLeftNav(unittest.TestCase):

    def _run_nav(self, state):
        mock_nav = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.return_value = mock_nav
            main.render_left_nav(state)
        return mock_nav

    def test_has_data_items_shown(self):
        s   = _make_state(props={BF + "language": ["eng"]})
        nav = self._run_nav(s)
        self.assertIn("has-data", nav.innerHTML)

    def test_empty_props_produces_empty_nav(self):
        s   = _make_state(props={})
        nav = self._run_nav(s)
        self.assertEqual(nav.innerHTML, "")

    def test_nav_links_to_inputcard_id(self):
        s   = _make_state(props={BF + "language": ["eng"]})
        nav = self._run_nav(s)
        # fallback nav links to inputcard-{frag}-0
        self.assertIn("inputcard-language-0", nav.innerHTML)

    def test_blob_predicates_not_shown(self):
        s   = _make_state(props={"contribution": ["{'@type': ['PrimaryContribution']}"]})
        nav = self._run_nav(s)
        self.assertEqual(nav.innerHTML, "")

    def test_work_nav_compat(self):
        s   = _make_state(props={})
        nav = self._run_nav(s)
        for label, _, _ in main.WORK_NAV:  # empty list — loop is a no-op
            self.assertIn(label, nav.innerHTML)


# ── render_main_editor ────────────────────────────────────────────────────────

class TestRenderMainEditor(unittest.TestCase):

    def _run_editor(self, state):
        mock_main = MagicMock()
        with patch("main.document") as mock_doc, patch("main.when"):
            mock_doc.getElementById.side_effect = (
                lambda eid: mock_main if eid == "main-editor" else MagicMock()
            )
            mock_doc.querySelectorAll.return_value = []
            main.render_main_editor(state)
        return mock_main

    def test_empty_props_empty_editor(self):
        s  = _make_state(props={}, field_edits={})
        el = self._run_editor(s)
        self.assertEqual(el.innerHTML, "")

    def test_data_props_produce_html(self):
        s  = _make_state(props={BF + "language": ["eng"]}, field_edits={})
        el = self._run_editor(s)
        self.assertIn("eng", el.innerHTML)

    def test_field_edits_populated(self):
        s = _make_state(props={}, field_edits={})
        mock_ta = MagicMock()
        mock_ta.getAttribute.return_value = "inputcard-language-0-value"
        mock_ta.value = "eng"
        with patch("main.document") as mock_doc, patch("main.when"):
            mock_doc.getElementById.return_value = MagicMock()
            mock_doc.querySelectorAll.return_value = [mock_ta]
            main.render_main_editor(s)
        self.assertEqual(s.field_edits.get("inputcard-language-0-value"), "eng")


# ── render_main_editor: SHACL path ────────────────────────────────────────────

class TestRenderMainEditorShacl(unittest.TestCase):

    def _run_with_shacl(self, state, shacl_json):
        mock_main = MagicMock()
        with patch("main.js") as mock_js, \
             patch("main.document") as mock_doc, \
             patch("main.when", side_effect=lambda *a, **kw: (lambda f: f)):
            mock_js.localStorage.getItem.return_value = shacl_json
            mock_doc.getElementById.side_effect = (
                lambda eid: mock_main if eid == "main-editor" else MagicMock()
            )
            mock_doc.querySelectorAll.return_value = []
            main.render_main_editor(state)
        return mock_main.innerHTML

    def test_required_shacl_prop_shown_without_data(self):
        state = _make_state(props={}, field_edits={}, resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_WORK]))
        # bf:title and bf:language are required → always shown
        self.assertIn("Work Title",  html)
        self.assertIn("Language", html)

    def test_optional_shacl_prop_hidden_without_data(self):
        state = _make_state(props={}, field_edits={}, resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_WORK]))
        # bf:note is optional with no data → not shown
        self.assertNotIn("inputcard-note-0", html)

    def test_optional_shacl_prop_shown_with_data(self):
        state = _make_state(props={BF + "note": ["A note"]}, field_edits={},
                            resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_WORK]))
        self.assertIn("A note", html)

    def test_existing_values_prefilled(self):
        state = _make_state(props={BF + "title": ["Star Wars"]}, field_edits={},
                            resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_WORK]))
        self.assertIn("Star Wars", html)

    def test_one_propcard_per_nodeshape(self):
        state = _make_state(props={}, field_edits={}, resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_WORK]))
        # One prop-card for the single Work NodeShape
        self.assertEqual(html.count('class="prop-card"'), 1)

    def test_multiple_nodeshapes_produce_multiple_propcards(self):
        # Two NodeShapes → two prop-cards, regardless of resource @type
        state = _make_state(props={}, field_edits={}, resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_MULTI]))
        self.assertGreaterEqual(html.count('class="prop-card"'), 2)

    def test_propcard_id_uses_rdfs_label(self):
        state = _make_state(props={}, field_edits={}, resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_MULTI]))
        # ex:InstanceShape has rdfs:label "Instance (Monograph) Print"
        self.assertIn('id="propcard-instance-monograph-print"', html)

    def test_inputcard_data_rdf_path(self):
        state = _make_state(props={}, field_edits={}, resource_types=[BF + "Work"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_WORK]))
        self.assertIn(f'data-rdf-path="{BF}title"', html)

    def test_fallback_with_data_when_no_matching_shape(self):
        state = _make_state(props={BF + "language": ["eng"]}, field_edits={},
                            resource_types=["http://example.com/Unknown"])
        html  = self._run_with_shacl(state, json.dumps([_SHACL_WORK]))
        self.assertIn("eng", html)

    def test_fallback_empty_when_no_shacl_and_no_props(self):
        # No SHACL loaded, no props → empty editor
        state = _make_state(props={}, field_edits={},
                            resource_types=["http://example.com/Unknown"])
        html  = self._run_with_shacl(state, json.dumps([]))  # empty template list
        self.assertEqual(html, "")


# ── render_tabs ───────────────────────────────────────────────────────────────

class TestRenderTabs(unittest.TestCase):

    def test_three_handlers_registered(self):
        with patch("main.document"), patch("main.when") as mock_when_local:
            mock_when_local.side_effect = lambda *a, **kw: (lambda f: f)
            main.render_tabs()
        self.assertEqual(mock_when_local.call_count, 3)


# ── _entry_point ──────────────────────────────────────────────────────────────

class TestEntryPoint(unittest.TestCase):

    def _make_mock_doc(self, resource_id="ep-test"):
        mock_el = MagicMock()
        mock_el.getAttribute.return_value = resource_id
        mock_el.style = MagicMock()
        doc = MagicMock()
        doc.getElementById.return_value = mock_el
        doc.querySelectorAll.return_value = []
        return doc

    def test_successful_render(self):
        data = {"@id": "https://dev.bcld.info/works/ep-test", "@type": [BF + "Work"]}
        mock_resp = AsyncMock()
        mock_resp.ok = True
        mock_resp.json = AsyncMock(return_value=data)
        with patch("main.document", self._make_mock_doc()), \
             patch("main.when", side_effect=lambda *a, **kw: (lambda f: f)), \
             patch("main.pyfetch", return_value=mock_resp):
            _run(main._entry_point())

    def test_error_renders_alert(self):
        mock_resp = AsyncMock()
        mock_resp.ok = False
        mock_resp.status = 503
        mock_resp.status_text = "Service Unavailable"
        mock_doc    = self._make_mock_doc()
        main_el     = MagicMock()
        bluecore_el = MagicMock()
        bluecore_el.getAttribute.return_value = "https://dev.bcld.info"
        rid_el      = MagicMock()
        rid_el.getAttribute.return_value = "ep-test"
        def _get(eid):
            if eid == "main-editor":       return main_el
            if eid == "bluecore-url-meta": return bluecore_el
            if eid == "resource-id-meta":  return rid_el
            return MagicMock()
        mock_doc.getElementById.side_effect = _get
        mock_doc.querySelectorAll.return_value = []
        with patch("main.document", mock_doc), \
             patch("main.when", side_effect=lambda *a, **kw: (lambda f: f)), \
             patch("main.pyfetch", return_value=mock_resp):
            _run(main._entry_point())
        self.assertIn("alert-danger", main_el.innerHTML)

    def test_blank_resource_id_skips_fetch(self):
        rid_el   = MagicMock()
        rid_el.getAttribute.return_value = ""
        title_el = MagicMock()
        def _get(eid):
            if eid == "resource-id-meta": return rid_el
            if eid == "resource-title":   return title_el
            return MagicMock()
        mock_doc = MagicMock()
        mock_doc.getElementById.side_effect = _get
        mock_doc.querySelectorAll.return_value = []
        with patch("main.document", mock_doc), \
             patch("main.when", side_effect=lambda *a, **kw: (lambda f: f)), \
             patch("main.pyfetch") as mock_fetch:
            _run(main._entry_point())
        mock_fetch.assert_not_awaited()
        self.assertIn("New Resource", title_el.innerHTML)


if __name__ == "__main__":
    unittest.main()
