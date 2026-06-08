"""
Unit tests for src/editor_state.py (the EditorState class).

Browser-specific modules (pyscript, pyodide, js) are mocked before import
so the test suite runs in a standard Python environment without Pyodide.
"""
import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Mock browser modules before importing editor_state ─────────────────────────
_mock_document = MagicMock()
_mock_when     = MagicMock(side_effect=lambda *a, **kw: (lambda f: f))
_mock_pyfetch  = AsyncMock()

sys.modules["pyscript"]      = MagicMock(document=_mock_document, when=_mock_when)
sys.modules["pyodide"]       = MagicMock()
sys.modules["pyodide.http"]  = MagicMock(pyfetch=_mock_pyfetch)
sys.modules["js"]            = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import editor_state  # noqa: E402  (must come after sys.modules patches)
from editor_state import EditorState  # noqa: E402

BF = "http://id.loc.gov/ontologies/bibframe/"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_state(**kwargs) -> EditorState:
    s = EditorState("test-uuid")
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ── EditorState.__init__ ───────────────────────────────────────────────────────

class TestEditorStateInit(unittest.TestCase):

    def test_defaults(self):
        s = EditorState("abc-123")
        self.assertEqual(s.resource_id, "abc-123")
        self.assertEqual(s.resource_uri, "")
        self.assertEqual(s.resource_types, [])
        self.assertEqual(s.resource_label, "")
        self.assertEqual(s.raw_data, {})
        self.assertEqual(s.triples, [])
        self.assertEqual(s.props, {})
        self.assertEqual(s.labels, {})
        self.assertEqual(s.field_edits, {})


# ── EditorState._is_leaf_value ────────────────────────────────────────────────

class TestIsLeafValue(unittest.TestCase):

    def test_plain_string_is_leaf(self):
        self.assertTrue(EditorState._is_leaf_value("hello"))

    def test_gmd_with_brackets_is_leaf(self):
        """GMDs like [sound recording] are legitimate literals, not nested structures."""
        self.assertTrue(EditorState._is_leaf_value("[sound recording]"))
        self.assertTrue(EditorState._is_leaf_value("[electronic resource]"))

    def test_dict_with_atvalue_is_leaf(self):
        self.assertTrue(EditorState._is_leaf_value({"@value": "Star wars"}))

    def test_dict_with_http_id_is_leaf(self):
        self.assertTrue(EditorState._is_leaf_value({"@id": "http://example.com/foo"}))

    def test_dict_without_atvalue_or_id_is_not_leaf(self):
        """Blank nodes without @value or real HTTP @id are not leaves."""
        self.assertFalse(EditorState._is_leaf_value({"@type": ["Title"]}))
        self.assertFalse(EditorState._is_leaf_value({"mainTitle": {"@value": "Test"}}))

    def test_dict_with_bnode_id_is_not_leaf(self):
        """Blank node IDs starting with _: are not leaves."""
        self.assertFalse(EditorState._is_leaf_value({"@id": "_:b1"}))

    def test_integer_is_leaf(self):
        self.assertTrue(EditorState._is_leaf_value(42))

    def test_boolean_is_leaf(self):
        self.assertTrue(EditorState._is_leaf_value(True))


# ── EditorState._literal ───────────────────────────────────────────────────────

class TestLiteral(unittest.TestCase):

    def test_plain_string(self):
        self.assertEqual(EditorState._literal("hello"), "hello")

    def test_dict_with_value(self):
        self.assertEqual(EditorState._literal({"@value": "Star wars"}), "Star wars")

    def test_dict_with_id(self):
        self.assertEqual(EditorState._literal({"@id": "http://example.com/foo"}),
                         "http://example.com/foo")

    def test_dict_value_takes_priority_over_id(self):
        self.assertEqual(EditorState._literal({"@value": "lit", "@id": "http://x"}), "lit")

    def test_integer_coerced(self):
        self.assertEqual(EditorState._literal(42), "42")


# ── EditorState._parse ─────────────────────────────────────────────────────────

class TestParse(unittest.TestCase):

    SAMPLE = {
        "@id":   "https://dev.bcld.info/works/ed1213b5",
        "@type": [BF + "Work", BF + "Monograph"],
        "http://www.w3.org/2000/01/rdf-schema#label": {"@value": "Star wars"},
        BF + "mainTitle": {"@value": "Star wars"},
    }

    def _parsed(self, data=None):
        s = EditorState("ed1213b5")
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
        s = EditorState("xyz")
        s._parse(data)
        self.assertEqual(s.resource_uri, "https://dev.bcld.info/works/xyz")

    def test_fallback_uri_when_missing(self):
        s = EditorState("fallback-id")
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
        s = EditorState("w1")
        s._parse(data)
        self.assertIn(BF + "mainTitle", s.props)
        self.assertEqual(s.props[BF + "mainTitle"], ["Star Wars"])

    def test_real_uri_node_not_recursed(self):
        data = {
            "@id":   "https://dev.bcld.info/works/w3",
            "@type": [BF + "Work"],
            BF + "language": [{"@id": "http://id.loc.gov/vocabulary/languages/eng"}],
        }
        s = EditorState("w3")
        s._parse(data)
        self.assertNotIn("http://www.w3.org/2000/01/rdf-schema#label", s.props)

    def test_gmd_literal_preserved(self):
        """GMD values like [sound recording] should be preserved as leaf values."""
        data = {
            "@id":   "https://dev.bcld.info/works/w4",
            "@type": [BF + "Work"],
            BF + "carrier": [{"@value": "[sound recording]"}],
        }
        s = EditorState("w4")
        s._parse(data)
        self.assertIn(BF + "carrier", s.props)
        self.assertEqual(s.props[BF + "carrier"], ["[sound recording]"])

    def test_gmd_literal_in_triples(self):
        """GMD values should appear in triples."""
        data = {
            "@id":   "https://dev.bcld.info/works/w5",
            "@type": [BF + "Work"],
            BF + "carrier": [{"@value": "[electronic resource]"}],
        }
        s = EditorState("w5")
        s._parse(data)
        carrier_triples = [t for t in s.triples if t[1] == BF + "carrier"]
        self.assertEqual(len(carrier_triples), 1)
        self.assertEqual(carrier_triples[0][2], "[electronic resource]")

    def test_blank_node_not_in_props(self):
        """Blank nodes should not appear as values in props."""
        data = {
            "@id":   "https://dev.bcld.info/works/w6",
            "@type": [BF + "Work"],
            BF + "title": [{"@type": [BF + "Title"], BF + "mainTitle": {"@value": "Test"}}],
        }
        s = EditorState("w6")
        s._parse(data)
        # The blank node itself should not be in props as a stringified dict
        title_vals = s.props.get(BF + "title", [])
        for val in title_vals:
            self.assertFalse(val.startswith("{"))
            self.assertFalse(val.startswith("["))


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
        s = EditorState("w1")
        s._parse(data)
        self.assertEqual(s.labels.get("http://id.loc.gov/vocabulary/languages/doi"), "Dogri")

    def test_no_label_no_entry(self):
        data = {
            "@id":   "https://dev.bcld.info/works/w2",
            "@type": [BF + "Work"],
            BF + "language": [{"@id": "http://id.loc.gov/vocabulary/languages/eng"}],
        }
        s = EditorState("w2")
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
        s = EditorState("w3")
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
        s = EditorState("test-uuid")
        with patch("editor_state.pyfetch", return_value=self._mock_response(data)):
            _run(s.load())
        self.assertEqual(s.resource_uri, "https://dev.bcld.info/works/test-uuid")

    def test_http_error_raises(self):
        s = EditorState("bad-uuid")
        with patch("editor_state.pyfetch", return_value=self._mock_response({}, ok=False, status=404)):
            with self.assertRaises(RuntimeError) as ctx:
                _run(s.load())
        self.assertIn("404", str(ctx.exception))

    def test_fetch_url_includes_resource_id(self):
        data = {"@id": "https://dev.bcld.info/works/my-id", "@type": []}
        s = EditorState("my-id")
        with patch("editor_state.pyfetch", return_value=self._mock_response(data)) as mock_fetch:
            _run(s.load())
        self.assertIn("my-id", mock_fetch.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
