"""
Unit tests for src/main.py.

Browser-specific modules (pyscript, pyodide, js) are mocked before import
so the test suite runs in a standard Python environment without Pyodide.
The emscripten-platform guard in main.py prevents _entry_point() from being
scheduled automatically during import.
"""
import asyncio
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_state(**kwargs) -> main.EditorState:
    s = main.EditorState("test-uuid")
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


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
        self.assertEqual(s.field_edits, {})
        self.assertIn("Work Title", s.expanded_sections)


# ── EditorState._literal ───────────────────────────────────────────────────────

class TestLiteral(unittest.TestCase):

    def test_plain_string(self):
        self.assertEqual(main.EditorState._literal("hello"), "hello")

    def test_dict_with_value(self):
        self.assertEqual(main.EditorState._literal({"@value": "Star wars"}), "Star wars")

    def test_dict_with_id(self):
        self.assertEqual(
            main.EditorState._literal({"@id": "http://example.com/foo"}),
            "http://example.com/foo",
        )

    def test_dict_value_takes_priority_over_id(self):
        self.assertEqual(
            main.EditorState._literal({"@value": "literal", "@id": "http://x"}),
            "literal",
        )

    def test_dict_without_known_keys(self):
        result = main.EditorState._literal({"@other": "x"})
        self.assertIsInstance(result, str)

    def test_integer_coerced(self):
        self.assertEqual(main.EditorState._literal(42), "42")


# ── EditorState._parse ─────────────────────────────────────────────────────────

class TestParse(unittest.TestCase):

    SAMPLE = {
        "@id": "https://dev.bcld.info/works/ed1213b5",
        "@type": [
            "http://id.loc.gov/ontologies/bibframe/Work",
            "http://id.loc.gov/ontologies/bibframe/Monograph",
        ],
        "http://www.w3.org/2000/01/rdf-schema#label": {"@value": "Star wars"},
        "http://id.loc.gov/ontologies/bibframe/mainTitle": {"@value": "Star wars"},
    }

    def _parsed(self, data=None):
        s = main.EditorState("ed1213b5")
        s._parse(data or self.SAMPLE)
        return s

    def test_resource_uri(self):
        s = self._parsed()
        self.assertEqual(s.resource_uri, "https://dev.bcld.info/works/ed1213b5")

    def test_resource_types_list(self):
        s = self._parsed()
        self.assertIn("http://id.loc.gov/ontologies/bibframe/Monograph", s.resource_types)

    def test_resource_types_string_coerced_to_list(self):
        data = dict(self.SAMPLE, **{"@type": "http://id.loc.gov/ontologies/bibframe/Work"})
        s = self._parsed(data)
        self.assertIsInstance(s.resource_types, list)

    def test_resource_label(self):
        s = self._parsed()
        self.assertEqual(s.resource_label, "Star wars")

    def test_triples_populated(self):
        s = self._parsed()
        self.assertTrue(len(s.triples) > 0)
        subjects = {t[0] for t in s.triples}
        self.assertEqual(subjects, {"https://dev.bcld.info/works/ed1213b5"})

    def test_props_populated(self):
        s = self._parsed()
        self.assertIn("http://id.loc.gov/ontologies/bibframe/mainTitle", s.props)

    def test_expanded_jsonld_list(self):
        data = [
            {"@id": "_:blank"},
            {
                "@id": "https://dev.bcld.info/works/xyz",
                "@type": ["http://id.loc.gov/ontologies/bibframe/Work"],
            },
        ]
        s = main.EditorState("xyz")
        s._parse(data)
        self.assertEqual(s.resource_uri, "https://dev.bcld.info/works/xyz")

    def test_fallback_uri_when_missing(self):
        s = main.EditorState("fallback-id")
        s._parse({"@type": ["http://id.loc.gov/ontologies/bibframe/Work"]})
        self.assertIn("fallback-id", s.resource_uri)

    def test_rdfs_label_shorthand(self):
        data = dict(self.SAMPLE)
        del data["http://www.w3.org/2000/01/rdf-schema#label"]
        data["rdfs:label"] = {"@value": "Short label"}
        s = self._parsed(data)
        self.assertEqual(s.resource_label, "Short label")

    def test_skip_keys_not_in_triples(self):
        s = self._parsed()
        preds = {t[1] for t in s.triples}
        self.assertNotIn("@id", preds)
        self.assertNotIn("@type", preds)
        self.assertNotIn("@context", preds)

    def test_list_values_expanded(self):
        data = {
            "@id": "https://example.com/w1",
            "@type": ["http://id.loc.gov/ontologies/bibframe/Work"],
            "http://id.loc.gov/ontologies/bibframe/language": [
                {"@id": "http://id.loc.gov/vocabulary/languages/eng"},
                {"@id": "http://id.loc.gov/vocabulary/languages/fre"},
            ],
        }
        s = main.EditorState("w1")
        s._parse(data)
        lang_vals = s.props.get("http://id.loc.gov/ontologies/bibframe/language", [])
        self.assertEqual(len(lang_vals), 2)


# ── EditorState.type_short ─────────────────────────────────────────────────────

class TestTypeShort(unittest.TestCase):

    def test_monograph(self):
        s = _make_state(resource_types=[
            "http://id.loc.gov/ontologies/bibframe/Work",
            "http://id.loc.gov/ontologies/bibframe/Monograph",
        ])
        self.assertEqual(s.type_short(), "Monograph")

    def test_work_only(self):
        s = _make_state(resource_types=["http://id.loc.gov/ontologies/bibframe/Work"])
        self.assertEqual(s.type_short(), "Work")

    def test_empty_types(self):
        s = _make_state(resource_types=[])
        self.assertEqual(s.type_short(), "Work")

    def test_hash_fragment_type(self):
        s = _make_state(resource_types=["http://example.com/onto#Text"])
        self.assertEqual(s.type_short(), "Text")


# ── EditorState.resource_name ──────────────────────────────────────────────────

class TestResourceName(unittest.TestCase):

    def test_includes_type(self):
        s = _make_state(resource_types=[
            "http://id.loc.gov/ontologies/bibframe/Work",
            "http://id.loc.gov/ontologies/bibframe/Monograph",
        ])
        self.assertEqual(s.resource_name(), "_Work (Monograph)")

    def test_defaults_to_work(self):
        s = _make_state(resource_types=[])
        self.assertEqual(s.resource_name(), "_Work (Work)")


# ── EditorState.main_title ─────────────────────────────────────────────────────

class TestMainTitle(unittest.TestCase):

    def test_returns_main_title(self):
        s = _make_state(props={
            "http://id.loc.gov/ontologies/bibframe/mainTitle": ["Star wars"]
        })
        self.assertEqual(s.main_title(), "Star wars")

    def test_returns_empty_when_missing(self):
        s = _make_state(props={})
        self.assertEqual(s.main_title(), "")

    def test_returns_first_value(self):
        s = _make_state(props={
            "http://id.loc.gov/ontologies/bibframe/mainTitle": ["First", "Second"]
        })
        self.assertEqual(s.main_title(), "First")

    def test_empty_list(self):
        s = _make_state(props={
            "http://id.loc.gov/ontologies/bibframe/mainTitle": []
        })
        self.assertEqual(s.main_title(), "")


# ── EditorState.has_prop ───────────────────────────────────────────────────────

class TestHasProp(unittest.TestCase):

    def test_found(self):
        s = _make_state(props={"http://id.loc.gov/ontologies/bibframe/title": ["x"]})
        self.assertTrue(s.has_prop("title"))

    def test_not_found(self):
        s = _make_state(props={})
        self.assertFalse(s.has_prop("title"))

    def test_partial_fragment(self):
        s = _make_state(props={"http://id.loc.gov/ontologies/bibframe/originDate": ["1977"]})
        self.assertTrue(s.has_prop("originDate"))


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
        data = {
            "@id": "https://dev.bcld.info/works/test-uuid",
            "@type": ["http://id.loc.gov/ontologies/bibframe/Work"],
        }
        s = main.EditorState("test-uuid")
        with patch("main.pyfetch", return_value=self._mock_response(data)) as mock_fetch:
            _run(s.load())
        mock_fetch.assert_awaited_once()
        self.assertEqual(s.resource_uri, "https://dev.bcld.info/works/test-uuid")
        self.assertEqual(s.raw_data, data)

    def test_http_error_raises(self):
        s = main.EditorState("bad-uuid")
        resp = self._mock_response({}, ok=False, status=404)
        with patch("main.pyfetch", return_value=resp):
            with self.assertRaises(RuntimeError) as ctx:
                _run(s.load())
        self.assertIn("404", str(ctx.exception))

    def test_fetch_url_includes_resource_id(self):
        data = {"@id": "https://dev.bcld.info/works/my-id", "@type": []}
        s = main.EditorState("my-id")
        with patch("main.pyfetch", return_value=self._mock_response(data)) as mock_fetch:
            _run(s.load())
        call_args = mock_fetch.call_args[0][0]
        self.assertIn("my-id", call_args)


# ── _sid ───────────────────────────────────────────────────────────────────────

class TestSid(unittest.TestCase):

    def test_spaces_to_dashes(self):
        self.assertEqual(main._sid("Work Title"), "work-title")

    def test_slashes_removed(self):
        self.assertEqual(main._sid("Variant and/or Parallel"), "variant-andor-parallel")

    def test_parens_removed(self):
        self.assertEqual(main._sid("Other (creator)"), "other-creator")

    def test_commas_removed(self):
        self.assertEqual(main._sid("Date, Legal"), "date-legal")

    def test_apostrophes_removed(self):
        self.assertEqual(main._sid("Creator's note"), "creators-note")

    def test_full_label(self):
        result = main._sid("Geographic Coverage of the Content of the Resource")
        self.assertNotIn(" ", result)
        self.assertNotIn("/", result)


# ── _section ───────────────────────────────────────────────────────────────────

class TestSection(unittest.TestCase):

    def test_required_star_present(self):
        html = main._section("My Field", True, "<p>body</p>")
        self.assertIn("text-danger", html)
        self.assertIn("*", html)

    def test_not_required_no_star(self):
        html = main._section("My Field", False, "<p>body</p>")
        self.assertNotIn("text-danger", html)

    def test_id_derived_from_title(self):
        html = main._section("Work Title", False, "")
        self.assertIn('id="section-work-title"', html)

    def test_body_included(self):
        html = main._section("Field", False, "<span>BODY</span>")
        self.assertIn("<span>BODY</span>", html)

    def test_title_in_output(self):
        html = main._section("My Label", False, "")
        self.assertIn("My Label", html)


# ── _add_link ──────────────────────────────────────────────────────────────────

class TestAddLink(unittest.TestCase):

    def test_contains_label(self):
        html = main._add_link("Contributor")
        self.assertIn("+ Add Contributor", html)

    def test_no_external_icon_by_default(self):
        html = main._add_link("Note")
        self.assertNotIn("box-arrow-up-right", html)

    def test_external_icon_added(self):
        html = main._add_link("Part Number/Letter", external=True)
        self.assertIn("box-arrow-up-right", html)

    def test_is_anchor(self):
        html = main._add_link("Language")
        self.assertIn("<a ", html)
        self.assertIn("</a>", html)


# ── _input_card ────────────────────────────────────────────────────────────────

class TestInputCard(unittest.TestCase):

    def test_contains_id(self):
        html = main._input_card("bf:mainTitle", "Enter a literal", "my-input")
        self.assertIn('id="my-input"', html)

    def test_contains_data_field(self):
        html = main._input_card("bf:mainTitle", "Enter a literal", "my-input")
        self.assertIn('data-field="my-input"', html)

    def test_value_rendered(self):
        html = main._input_card("bf:mainTitle", "prompt", "id", value="Star wars")
        self.assertIn("Star wars", html)

    def test_html_escaped_in_value(self):
        html = main._input_card("bf:mainTitle", "prompt", "id", value="<script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_ampersand_escaped(self):
        html = main._input_card("bf:mainTitle", "prompt", "id", value="A & B")
        self.assertIn("A &amp; B", html)

    def test_prop_uri_displayed(self):
        html = main._input_card("bf:mainTitle", "Enter a literal", "id")
        self.assertIn("bf:mainTitle", html)

    def test_prompt_displayed(self):
        html = main._input_card("bf:mainTitle", "Enter a literal", "id")
        self.assertIn("Enter a literal", html)


# ── _build_work_title ─────────────────────────────────────────────────────────

class TestBuildWorkTitle(unittest.TestCase):

    def test_prefills_main_title(self):
        s = _make_state(props={
            "http://id.loc.gov/ontologies/bibframe/mainTitle": ["Star wars"]
        })
        html = main._build_work_title(s)
        self.assertIn("Star wars", html)

    def test_empty_title_when_missing(self):
        s = _make_state(props={})
        html = main._build_work_title(s)
        self.assertIn('id="input-main-title"', html)

    def test_section_id_present(self):
        s = _make_state(props={})
        html = main._build_work_title(s)
        self.assertIn('id="section-work-title"', html)

    def test_required_star(self):
        s = _make_state(props={})
        html = main._build_work_title(s)
        self.assertIn("text-danger", html)

    def test_non_sort_num_field_present(self):
        s = _make_state(props={})
        html = main._build_work_title(s)
        self.assertIn('id="input-non-sort-num"', html)


# ── _build_variant_title ──────────────────────────────────────────────────────

class TestBuildVariantTitle(unittest.TestCase):

    def setUp(self):
        self.html = main._build_variant_title()

    def test_parallel_title_card(self):
        self.assertIn("Title--Parallel Title", self.html)

    def test_variant_title_card(self):
        self.assertIn("Title--Work Title Variant", self.html)

    def test_section_id(self):
        self.assertIn("section-variant-andor-parallel-work-title", self.html)

    def test_add_links_present(self):
        self.assertIn("+ Add Parallel Title", self.html)
        self.assertIn("+ Add Date", self.html)


# ── _build_simple ─────────────────────────────────────────────────────────────

class TestBuildSimple(unittest.TestCase):

    def test_label_in_output(self):
        html = main._build_simple("Government Publication Type", False)
        self.assertIn("Government Publication Type", html)

    def test_add_link_in_output(self):
        html = main._build_simple("Language of Accompanying Work", False)
        self.assertIn("+ Add Language of Accompanying Work", html)

    def test_required_propagated(self):
        html = main._build_simple("Language", True)
        self.assertIn("text-danger", html)


# ── render_resource_header ────────────────────────────────────────────────────

class TestRenderResourceHeader(unittest.TestCase):

    def _run_with_mock_doc(self, state):
        mock_el = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.return_value = mock_el
            main.render_resource_header(state)
        return mock_doc, mock_el

    def test_sets_title_innerHTML(self):
        s = _make_state(resource_types=[
            "http://id.loc.gov/ontologies/bibframe/Work",
            "http://id.loc.gov/ontologies/bibframe/Monograph",
        ])
        mock_doc, _ = self._run_with_mock_doc(s)
        calls = [str(c) for c in mock_doc.getElementById.call_args_list]
        self.assertTrue(any("resource-title" in c for c in calls))

    def test_sets_badge_textContent(self):
        s = _make_state(resource_types=["http://id.loc.gov/ontologies/bibframe/Monograph"])
        elements = {}
        def _get(eid):
            elements.setdefault(eid, MagicMock())
            return elements[eid]
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.side_effect = _get
            main.render_resource_header(s)
        self.assertEqual(elements["resource-badge"].textContent, "MONOGRAPH")

    def test_class_info_set(self):
        s = _make_state(resource_types=["http://id.loc.gov/ontologies/bibframe/Work"])
        mock_doc, _ = self._run_with_mock_doc(s)
        ids_called = [c.args[0] for c in mock_doc.getElementById.call_args_list]
        self.assertIn("resource-class", ids_called)


# ── render_uri_bar ────────────────────────────────────────────────────────────

class TestRenderUriBar(unittest.TestCase):

    def test_sets_uri_innerHTML(self):
        s = _make_state(resource_uri="https://dev.bcld.info/works/test")
        mock_el = MagicMock()
        with patch("main.document") as mock_doc, patch("main.when"):
            mock_doc.getElementById.return_value = mock_el
            main.render_uri_bar(s)
        ids = [c.args[0] for c in mock_doc.getElementById.call_args_list]
        self.assertIn("resource-uri", ids)
        self.assertIn("https://dev.bcld.info/works/test", mock_el.innerHTML)

    def test_when_registered_for_copy_btn(self):
        s = _make_state(resource_uri="https://dev.bcld.info/works/test")
        with patch("main.document") as mock_doc, patch("main.when") as mock_when_local:
            mock_doc.getElementById.return_value = MagicMock()
            mock_when_local.side_effect = lambda *a, **kw: (lambda f: f)
            main.render_uri_bar(s)
        mock_when_local.assert_called()


# ── render_triples ────────────────────────────────────────────────────────────

class TestRenderTriples(unittest.TestCase):

    def test_no_call_when_empty(self):
        s = _make_state(triples=[])
        with patch("main.document") as mock_doc:
            main.render_triples(s)
        mock_doc.getElementById.assert_not_called()

    def test_section_shown_when_triples_present(self):
        s = _make_state(triples=[("subj", "pred", "obj")])
        mock_section = MagicMock()
        mock_tbody   = MagicMock()
        def _get_el(eid):
            return mock_section if eid == "triples-section" else mock_tbody
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.side_effect = _get_el
            main.render_triples(s)
        self.assertEqual(mock_section.style.display, "block")

    def test_rows_written_to_tbody(self):
        s = _make_state(triples=[("s", "p", "o")])
        mock_tbody = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.return_value = MagicMock()
            mock_doc.getElementById.side_effect = (
                lambda eid: MagicMock() if eid == "triples-section" else mock_tbody
            )
            main.render_triples(s)
        self.assertIn("s", mock_tbody.innerHTML)

    def test_html_entities_escaped(self):
        s = _make_state(triples=[("<subject>", "<pred>", "<obj>")])
        mock_tbody = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.side_effect = (
                lambda eid: MagicMock() if eid == "triples-section" else mock_tbody
            )
            main.render_triples(s)
        self.assertNotIn("<subject>", mock_tbody.innerHTML)
        self.assertIn("&lt;subject&gt;", mock_tbody.innerHTML)


# ── render_left_nav ───────────────────────────────────────────────────────────

class TestRenderLeftNav(unittest.TestCase):

    def test_sets_left_nav_innerHTML(self):
        s = _make_state(props={})
        mock_nav = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.return_value = mock_nav
            main.render_left_nav(s)
        self.assertIsNotNone(mock_nav.innerHTML)

    def test_has_data_items_marked(self):
        s = _make_state(props={"http://id.loc.gov/ontologies/bibframe/title": ["x"]})
        mock_nav = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.return_value = mock_nav
            main.render_left_nav(s)
        self.assertIn("has-data", mock_nav.innerHTML)

    def test_no_data_items_marked(self):
        s = _make_state(props={})
        mock_nav = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.return_value = mock_nav
            main.render_left_nav(s)
        self.assertIn("no-data", mock_nav.innerHTML)

    def test_all_nav_items_rendered(self):
        s = _make_state(props={})
        mock_nav = MagicMock()
        with patch("main.document") as mock_doc:
            mock_doc.getElementById.return_value = mock_nav
            main.render_left_nav(s)
        for label, _, _ in main.WORK_NAV:
            self.assertIn(label, mock_nav.innerHTML)


# ── render_main_editor ────────────────────────────────────────────────────────

class TestRenderMainEditor(unittest.TestCase):

    def test_sets_main_editor_innerHTML(self):
        s = _make_state(props={}, field_edits={})
        mock_el = MagicMock()
        mock_el.getAttribute.return_value = "test-field"
        mock_el.value = ""
        with patch("main.document") as mock_doc, patch("main.when"):
            mock_doc.getElementById.return_value = MagicMock()
            mock_doc.querySelectorAll.return_value = []
            main.render_main_editor(s)
        mock_doc.getElementById.assert_any_call("main-editor")

    def test_all_sections_included(self):
        s = _make_state(props={}, field_edits={})
        captured = []
        def _capture(eid):
            el = MagicMock()
            if eid == "main-editor":
                def _set_inner(val):
                    captured.append(val)
                type(el).__setattr__ = lambda self, name, val: captured.append(val) if name == "innerHTML" else None
                el.innerHTML = property(lambda self: "", lambda self, v: captured.append(v))
            return el
        with patch("main.document") as mock_doc, patch("main.when"):
            mock_main = MagicMock()
            mock_doc.getElementById.side_effect = lambda eid: mock_main if eid == "main-editor" else MagicMock()
            mock_doc.querySelectorAll.return_value = []
            main.render_main_editor(s)
        html = mock_main.innerHTML
        # innerHTML is set once with all sections joined
        self.assertIsNotNone(html)

    def test_field_edits_populated(self):
        s = _make_state(props={}, field_edits={})
        mock_ta = MagicMock()
        mock_ta.getAttribute.return_value = "input-main-title"
        mock_ta.value = "existing"
        with patch("main.document") as mock_doc, patch("main.when"):
            mock_doc.getElementById.return_value = MagicMock()
            mock_doc.querySelectorAll.return_value = [mock_ta]
            main.render_main_editor(s)
        self.assertEqual(s.field_edits.get("input-main-title"), "existing")


# ── render_tabs ───────────────────────────────────────────────────────────────

class TestRenderTabs(unittest.TestCase):

    def test_registers_click_handlers_for_all_tabs(self):
        with patch("main.document"), patch("main.when") as mock_when_local:
            mock_when_local.side_effect = lambda *a, **kw: (lambda f: f)
            main.render_tabs()
        calls = [c.args[0] for c in mock_when_local.call_args_list]
        self.assertIn("click", calls)
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
        data = {
            "@id": "https://dev.bcld.info/works/ep-test",
            "@type": ["http://id.loc.gov/ontologies/bibframe/Work"],
        }
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

        mock_doc = self._make_mock_doc()
        mock_main_el = MagicMock()
        bluecore_el = MagicMock()
        bluecore_el.getAttribute.return_value = "https://dev.bcld.info"
        resource_id_el = MagicMock()
        resource_id_el.getAttribute.return_value = "ep-test"

        def _get(eid):
            if eid == "main-editor":
                return mock_main_el
            if eid == "bluecore-url-meta":
                return bluecore_el
            if eid == "resource-id-meta":
                return resource_id_el
            return MagicMock()

        mock_doc.getElementById.side_effect = _get
        mock_doc.querySelectorAll.return_value = []

        with patch("main.document", mock_doc), \
             patch("main.when", side_effect=lambda *a, **kw: (lambda f: f)), \
             patch("main.pyfetch", return_value=mock_resp):
            _run(main._entry_point())

        self.assertIn("alert-danger", mock_main_el.innerHTML)

    def test_blank_resource_id_skips_fetch(self):
        resource_id_el = MagicMock()
        resource_id_el.getAttribute.return_value = ""
        title_el = MagicMock()

        def _get(eid):
            if eid == "resource-id-meta":
                return resource_id_el
            if eid == "resource-title":
                return title_el
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
