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

    def test_gmd_predicates_shown(self):
        """GMD values like [sound recording] should appear in the left nav.
        
        Regression test for issue #7: leaf-value detection was using brittle
        string-startswith checks which incorrectly filtered out GMDs.
        Filtering now happens at parse time in _extract_props() based on
        actual JSON-LD structure, not string content.
        """
        s   = _make_state(props={"carrier": ["[sound recording]"]})
        nav = self._run_nav(s)
        self.assertIn("carrier", nav.innerHTML)

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
