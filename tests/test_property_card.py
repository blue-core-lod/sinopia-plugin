"""
Unit tests for src/factories/property_card.py.

Covers PropCardFactory and the supporting helpers extracted from main.py
(PropShape, _sid, _add_link, _values_for_path).

Browser-specific modules (pyscript, pyodide, js) are mocked before import
so the test suite runs in a standard Python environment without Pyodide.
"""
import os
import sys
import unittest

from unittest.mock import AsyncMock, MagicMock

# ── Mock browser modules before importing property_card ────────────────────────
_mock_document = MagicMock()
_mock_when     = MagicMock(side_effect=lambda *a, **kw: (lambda f: f))
_mock_pyfetch  = AsyncMock()

sys.modules["pyscript"]      = MagicMock(document=_mock_document, when=_mock_when)
sys.modules["pyodide"]       = MagicMock()
sys.modules["pyodide.http"]  = MagicMock(pyfetch=_mock_pyfetch)
sys.modules["js"]            = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from factories import property_card  # noqa: E402  (must come after sys.modules patches)
from factories.property_card import PropCardFactory, PropShape  # noqa: E402
from editor_state import EditorState  # noqa: E402

BF = "http://id.loc.gov/ontologies/bibframe/"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_state(**kwargs) -> EditorState:
    s = EditorState("test-uuid")
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _ps(path, name, required=False, value_class="", datatype="", description="", order=1):
    return PropShape(path, name, required, value_class, datatype, description, order)


# ── _sid ───────────────────────────────────────────────────────────────────────

class TestSid(unittest.TestCase):

    def test_spaces_to_dashes(self):
        self.assertEqual(property_card.sid("Work Title"), "work-title")

    def test_slashes_become_dashes(self):
        self.assertEqual(property_card.sid("Variant and/or Parallel"), "variant-and-or-parallel")

    def test_parens_removed(self):
        self.assertEqual(property_card.sid("Other (creator)"), "other-creator")


# ── _add_link ──────────────────────────────────────────────────────────────────

class TestAddLink(unittest.TestCase):

    def test_label_present(self):
        self.assertIn("+ Add Contributor", property_card._add_link("Contributor"))

    def test_no_external_icon_by_default(self):
        self.assertNotIn("box-arrow-up-right", property_card._add_link("Note"))

    def test_external_icon_added(self):
        self.assertIn("box-arrow-up-right", property_card._add_link("Part", external=True))


# ── _values_for_path ──────────────────────────────────────────────────────────

class TestValuesForPath(unittest.TestCase):

    def test_exact_uri_match(self):
        s = _make_state(props={BF + "title": ["x"]})
        self.assertEqual(property_card.values_for_path(s, BF + "title"), ["x"])

    def test_fragment_fallback(self):
        s = _make_state(props={BF + "title": ["X"]})
        self.assertEqual(property_card.values_for_path(s, "http://other.org/onto/title"), ["X"])

    def test_compact_key_match(self):
        s = _make_state(props={"title": ["Star Wars"]})
        self.assertEqual(property_card.values_for_path(s, BF + "title"), ["Star Wars"])

    def test_no_match_returns_empty(self):
        self.assertEqual(property_card.values_for_path(_make_state(props={}), BF + "title"), [])

    def test_gmd_value_preserved(self):
        """GMD values like [sound recording] should be returned, not filtered out.

        Filtering now happens at parse time in _extract_props(), not here.
        This function returns all values from state.props as-is.
        """
        s = _make_state(props={"carrier": ["[sound recording]", "[electronic resource]"]})
        self.assertEqual(property_card.values_for_path(s, "carrier"),
                         ["[sound recording]", "[electronic resource]"])

    def test_returns_all_values_no_filtering(self):
        """_values_for_path no longer filters - that happens at parse time.

        This is a regression test for issue #7: leaf-value detection was using
        brittle string-startswith checks which incorrectly filtered out GMDs.
        Now filtering happens in _extract_props() based on actual JSON-LD structure.
        """
        s = _make_state(props={"items": ["value1", "value2"]})
        self.assertEqual(property_card.values_for_path(s, "items"), ["value1", "value2"])


# ── PropCardFactory ───────────────────────────────────────────────────────────

class TestPropCardFactory(unittest.TestCase):

    def _factory(self, **props):
        return PropCardFactory(_make_state(props=props,
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

    def test_fallback_card_shows_gmd_values(self):
        """GMD values like [sound recording] should be shown, not filtered out.

        Regression test for issue #7: leaf-value detection was using brittle
        string-startswith checks which incorrectly filtered out GMDs.
        Filtering now happens at parse time in _extract_props() based on
        actual JSON-LD structure, not string content.
        """
        factory = self._factory(**{"carrier": ["[sound recording]"],
                                   BF + "language": ["eng"]})
        html = factory.build_fallback_card()
        self.assertIn("eng", html)
        self.assertIn("[sound recording]", html)

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
        return PropCardFactory(state)

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
        factory = PropCardFactory(state)
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
        factory = PropCardFactory(state)
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


if __name__ == "__main__":
    unittest.main()
