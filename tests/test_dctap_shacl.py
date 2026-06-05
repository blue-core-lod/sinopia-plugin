"""Tests for src/dctap_shacl.py and sinopia/dctap.py."""

import asyncio
import csv
import io
import os
import sys
import unittest
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

# ── Mock browser modules before importing dctap_shacl ────────────────────────
_mock_js        = MagicMock()
_mock_pyfetch   = AsyncMock()

sys.modules["js"]           = _mock_js
sys.modules["pyodide"]      = MagicMock()
sys.modules["pyodide.http"] = MagicMock(pyfetch=_mock_pyfetch)
sys.modules["pyscript"]     = MagicMock()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import dctap_shacl  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


_SAMPLE_TSV = (
    "shapeID\tshapeLabel\ttarget\tpropertyID\tpropertyLabel\t"
    "valueShape\tmandatory\tseverity\tvalueNodeType\trepeatable\tnote\n"
    "big:Work\tWork\tbf:Work\tbf:title\tTitle\t\ttrue\tViolation\tIRI\ttrue\t\n"
)

_SAMPLE_SHACL_FRAGMENT = "@prefix sh:"


def _make_zip(files: dict[str, str]) -> bytes:
    """Build an in-memory zip with the given {path: content} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ── dctap_shacl._key ──────────────────────────────────────────────────────────

class TestKey(unittest.TestCase):

    def test_includes_version_and_filename(self):
        key = dctap_shacl._key("v0.3.0", "Monograph_Work_Text.tsv")
        self.assertIn("v0.3.0", key)
        self.assertIn("Monograph_Work_Text.tsv", key)

    def test_different_versions_produce_different_keys(self):
        self.assertNotEqual(
            dctap_shacl._key("v0.2.0", "file.tsv"),
            dctap_shacl._key("v0.3.0", "file.tsv"),
        )

    def test_different_files_produce_different_keys(self):
        self.assertNotEqual(
            dctap_shacl._key("v0.3.0", "a.tsv"),
            dctap_shacl._key("v0.3.0", "b.tsv"),
        )


# ── dctap_shacl._read_cache / _write_cache ────────────────────────────────────

class TestCache(unittest.TestCase):

    def setUp(self):
        _mock_js.localStorage.getItem.reset_mock()
        _mock_js.localStorage.setItem.reset_mock()

    def test_read_cache_returns_stored_value(self):
        _mock_js.localStorage.getItem.return_value = "@prefix sh: <...>"
        result = dctap_shacl._read_cache("v0.3.0", "Work.tsv")
        self.assertEqual(result, "@prefix sh: <...>")

    def test_read_cache_returns_none_when_missing(self):
        _mock_js.localStorage.getItem.return_value = None
        self.assertIsNone(dctap_shacl._read_cache("v0.3.0", "Work.tsv"))

    def test_write_cache_calls_set_item(self):
        dctap_shacl._write_cache("v0.3.0", "Work.tsv", "shacl turtle")
        _mock_js.localStorage.setItem.assert_called_once_with(
            dctap_shacl._key("v0.3.0", "Work.tsv"), "shacl turtle"
        )


# ── dctap_shacl._convert ──────────────────────────────────────────────────────

class TestConvert(unittest.TestCase):

    def test_returns_turtle_string(self):
        result = dctap_shacl._convert(_SAMPLE_TSV)
        self.assertIsInstance(result, str)
        self.assertIn("@prefix", result)

    def test_output_contains_shacl_prefix(self):
        result = dctap_shacl._convert(_SAMPLE_TSV)
        self.assertIn("sh:", result)

    def test_header_only_tsv_produces_string(self):
        header = "shapeID\tshapeLabel\ttarget\tpropertyID\tpropertyLabel\n"
        result = dctap_shacl._convert(header)
        self.assertIsInstance(result, str)


# ── dctap_shacl.get_shacl ─────────────────────────────────────────────────────

class TestGetShacl(unittest.TestCase):

    def setUp(self):
        _mock_js.localStorage.getItem.reset_mock()
        _mock_js.localStorage.setItem.reset_mock()

    def test_returns_cached_value_without_fetch(self):
        cached = "@prefix sh: <cached>"
        _mock_js.localStorage.getItem.return_value = cached
        with patch("dctap_shacl.pyfetch") as mock_fetch:
            result = _run(dctap_shacl.get_shacl("v0.3.0", "Work.tsv"))
        mock_fetch.assert_not_awaited()
        self.assertEqual(result, cached)

    def test_fetches_and_converts_on_cache_miss(self):
        _mock_js.localStorage.getItem.return_value = None
        mock_resp = AsyncMock()
        mock_resp.ok = True
        mock_resp.string = AsyncMock(return_value=_SAMPLE_TSV)
        with patch("dctap_shacl.pyfetch", return_value=mock_resp) as mock_fetch:
            result = _run(dctap_shacl.get_shacl("v0.3.0", "Work.tsv"))
        mock_fetch.assert_awaited_once()
        self.assertIn("@prefix", result)

    def test_saves_to_storage_on_cache_miss(self):
        _mock_js.localStorage.getItem.return_value = None
        mock_resp = AsyncMock()
        mock_resp.ok = True
        mock_resp.string = AsyncMock(return_value=_SAMPLE_TSV)
        with patch("dctap_shacl.pyfetch", return_value=mock_resp):
            _run(dctap_shacl.get_shacl("v0.3.0", "Work.tsv"))
        _mock_js.localStorage.setItem.assert_called_once()
        key_used = _mock_js.localStorage.setItem.call_args[0][0]
        self.assertIn("v0.3.0", key_used)
        self.assertIn("Work.tsv", key_used)

    def test_raises_on_http_error(self):
        _mock_js.localStorage.getItem.return_value = None
        mock_resp = AsyncMock()
        mock_resp.ok = False
        mock_resp.status = 404
        with patch("dctap_shacl.pyfetch", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                _run(dctap_shacl.get_shacl("v0.3.0", "Missing.tsv"))
        self.assertIn("404", str(ctx.exception))


# ── dctap_shacl.is_in_template_graph ─────────────────────────────────────────

class TestIsInTemplateGraph(unittest.TestCase):

    def setUp(self):
        _mock_js.localStorage.getItem.reset_mock()
        _mock_js.localStorage.getItem.side_effect = None

    def _setup_storage(self, template_items: list, cached_shacl: str | None):
        import json
        def _getitem(key):
            if key == "template":
                return json.dumps(template_items) if template_items is not None else None
            return cached_shacl
        _mock_js.localStorage.getItem.side_effect = _getitem

    def test_returns_true_when_shacl_in_template(self):
        self._setup_storage(["@prefix sh: <x> ."], "@prefix sh: <x> .")
        self.assertTrue(dctap_shacl.is_in_template_graph("v0.3.0", "Work.tsv"))

    def test_returns_false_when_template_empty(self):
        _mock_js.localStorage.getItem.return_value = None
        self.assertFalse(dctap_shacl.is_in_template_graph("v0.3.0", "Work.tsv"))

    def test_returns_false_when_shacl_not_cached(self):
        import json
        def _getitem(key):
            if key == "template":
                return json.dumps(["some shacl"])
            return None  # no cached shacl for this file
        _mock_js.localStorage.getItem.side_effect = _getitem
        self.assertFalse(dctap_shacl.is_in_template_graph("v0.3.0", "Work.tsv"))

    def test_returns_false_when_shacl_not_in_template(self):
        self._setup_storage(["different shacl"], "@prefix sh: <x> .")
        self.assertFalse(dctap_shacl.is_in_template_graph("v0.3.0", "Work.tsv"))


# ── dctap_shacl.add_to_template_graph ────────────────────────────────────────

class TestAddToTemplateGraph(unittest.TestCase):

    def setUp(self):
        _mock_js.localStorage.getItem.reset_mock()
        _mock_js.localStorage.getItem.side_effect = None
        _mock_js.localStorage.setItem.reset_mock()

    def test_adds_first_item_returns_length_one(self):
        _mock_js.localStorage.getItem.return_value = None
        count = dctap_shacl.add_to_template_graph("@prefix sh: <x> .")
        self.assertEqual(count, 1)

    def test_saves_json_list_to_storage(self):
        _mock_js.localStorage.getItem.return_value = None
        dctap_shacl.add_to_template_graph("turtle content")
        key, value = _mock_js.localStorage.setItem.call_args[0]
        self.assertEqual(key, "template")
        import json
        self.assertEqual(json.loads(value), ["turtle content"])

    def test_appends_to_existing_list(self):
        import json
        existing = json.dumps(["first"])
        _mock_js.localStorage.getItem.return_value = existing
        count = dctap_shacl.add_to_template_graph("second")
        self.assertEqual(count, 2)
        _, saved = _mock_js.localStorage.setItem.call_args[0]
        self.assertEqual(json.loads(saved), ["first", "second"])

    def test_does_not_add_duplicate(self):
        import json
        existing = json.dumps(["already here"])
        _mock_js.localStorage.getItem.return_value = existing
        count = dctap_shacl.add_to_template_graph("already here")
        self.assertEqual(count, 1)
        _mock_js.localStorage.setItem.assert_not_called()

    def test_returns_correct_count_after_multiple_adds(self):
        import json
        _mock_js.localStorage.getItem.side_effect = [
            None,
            json.dumps(["a"]),
            json.dumps(["a", "b"]),
        ]
        dctap_shacl.add_to_template_graph("a")
        dctap_shacl.add_to_template_graph("b")
        count = dctap_shacl.add_to_template_graph("c")
        self.assertEqual(count, 3)


# ── dctap_shacl.view_as_html ─────────────────────────────────────────────────

class TestViewAsHtml(unittest.TestCase):

    def test_returns_html_table(self):
        mock_resp = AsyncMock()
        mock_resp.ok = True
        mock_resp.string = AsyncMock(return_value=_SAMPLE_TSV)
        with patch("dctap_shacl.pyfetch", return_value=mock_resp):
            result = _run(dctap_shacl.view_as_html("v0.3.0", "Work.tsv"))
        self.assertIn("<table", result)
        self.assertIn("</table>", result)

    def test_html_contains_column_headers(self):
        mock_resp = AsyncMock()
        mock_resp.ok = True
        mock_resp.string = AsyncMock(return_value=_SAMPLE_TSV)
        with patch("dctap_shacl.pyfetch", return_value=mock_resp):
            result = _run(dctap_shacl.view_as_html("v0.3.0", "Work.tsv"))
        self.assertIn("shapeID", result)
        self.assertIn("shapeLabel", result)

    def test_html_contains_bootstrap_classes(self):
        mock_resp = AsyncMock()
        mock_resp.ok = True
        mock_resp.string = AsyncMock(return_value=_SAMPLE_TSV)
        with patch("dctap_shacl.pyfetch", return_value=mock_resp):
            result = _run(dctap_shacl.view_as_html("v0.3.0", "Work.tsv"))
        self.assertIn("table-striped", result)
        self.assertIn("table-bordered", result)

    def test_raises_on_http_error(self):
        mock_resp = AsyncMock()
        mock_resp.ok = False
        mock_resp.status = 404
        with patch("dctap_shacl.pyfetch", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as ctx:
                _run(dctap_shacl.view_as_html("v0.3.0", "Missing.tsv"))
        self.assertIn("404", str(ctx.exception))


# ── sinopia.dctap (server-side) ───────────────────────────────────────────────

from sinopia.dctap import (  # noqa: E402
    MARVA_SOURCE,
    _marva_shape_tsv,
    _marva_templates,
    _parse_marva_csv,
    _parse_zip,
    fetch_templates,
    fetch_tsv_content,
)


_PREFIXES_TSV = "Vocabulary\tPrefix\tNamespace\nBIBFRAME\tbf:\thttp://id.loc.gov/ontologies/bibframe/\n"

_SHAPES_TSV = (
    "shapeID\tshapeLabel\ttarget\tpropertyID\n"
    "big:Work\tWork\tbf:Work\tbf:title\n"
)


def _make_test_zip() -> bytes:
    return _make_zip({
        "prefix-bc07b25/Monograph DCTAP/Monograph_Prefixes.tsv": _PREFIXES_TSV,
        "prefix-bc07b25/Monograph DCTAP/Monograph_Work_Text.tsv": _SHAPES_TSV,
        "prefix-bc07b25/Serials DCTAP/Serial_Prefixes.tsv": _PREFIXES_TSV,
        "prefix-bc07b25/Serials DCTAP/Serial_Work_Text.tsv": _SHAPES_TSV,
    })


class TestParseZip(unittest.TestCase):

    def setUp(self):
        self.entries = _parse_zip(_make_test_zip())

    def test_returns_non_prefixes_tsv_files(self):
        filenames = [e["filename"] for e in self.entries]
        self.assertIn("Monograph_Work_Text.tsv", filenames)
        self.assertIn("Serial_Work_Text.tsv", filenames)

    def test_excludes_prefixes_files(self):
        filenames = [e["filename"] for e in self.entries]
        self.assertNotIn("Monograph_Prefixes.tsv", filenames)

    def test_type_derived_from_folder(self):
        monograph = next(e for e in self.entries if "Monograph" in e["filename"])
        self.assertEqual(monograph["type"], "Monograph")

    def test_entry_has_filename_and_type_keys(self):
        for entry in self.entries:
            self.assertIn("filename", entry)
            self.assertIn("type", entry)


class TestFetchTsvContent(unittest.TestCase):

    def setUp(self):
        # Clear the zip cache so each test starts fresh
        import sinopia.dctap as dctap_mod
        dctap_mod._zip_cache.clear()
        dctap_mod._template_cache.clear()
        self.zip_bytes = _make_test_zip()

    def _mock_fetch(self):
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = self.zip_bytes
        return mock_resp

    def test_returns_tsv_text_for_known_file(self):
        with patch("sinopia.dctap.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=self._mock_fetch())
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = _run(fetch_tsv_content("v0.3.0", "Monograph_Work_Text.tsv"))
        self.assertIsNotNone(result)
        self.assertIn("shapeID", result)

    def test_returns_none_for_unknown_file(self):
        with patch("sinopia.dctap.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=self._mock_fetch())
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            result = _run(fetch_tsv_content("v0.3.0", "nonexistent.tsv"))
        self.assertIsNone(result)

    def test_uses_zip_cache_on_second_call(self):
        with patch("sinopia.dctap.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=self._mock_fetch())
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            _run(fetch_tsv_content("v0.3.0", "Monograph_Work_Text.tsv"))
            _run(fetch_tsv_content("v0.3.0", "Serial_Work_Text.tsv"))
        self.assertEqual(mock_client.get.await_count, 1)


# ── sinopia.dctap marva-profiles CSV source ──────────────────────────────────

_MARVA_CSV = (
    "shapeID,shapeLabel,resourceURI,propertyID,propertyLabel,mandatory,repeatable,"
    "valueNodeType,valueDataType,valueShape,valueConstraint,valueConstraintType,note\n"
    "lc:RT:bf2:Item,Digital Item,bf:Item,bf:heldBy,Held by,false,false,IRI,,,,,\n"
    ",,,bf:note,\"Notes, etc.\",false,true,bnode,,lc:RT:bf2:Note,,,\n"
    "lc:RT:bf2:Work,BIBFRAME Work,bf:Work,bf:title,Title,true,true,bnode,,lc:RT:bf2:Title,,,\n"
)


class TestParseMarvaCsv(unittest.TestCase):

    def setUp(self):
        self.rows = _parse_marva_csv(_MARVA_CSV)

    def test_forward_fills_shape_columns(self):
        # The second row (bf:note) is a continuation of the first shape.
        note_row = next(r for r in self.rows if r["propertyID"] == "bf:note")
        self.assertEqual(note_row["shapeID"], "lc:RT:bf2:Item")
        self.assertEqual(note_row["shapeLabel"], "Digital Item")

    def test_renames_resource_uri_to_target(self):
        first = self.rows[0]
        self.assertEqual(first["target"], "bf:Item")
        self.assertNotIn("resourceURI", first)

    def test_target_forward_filled(self):
        note_row = next(r for r in self.rows if r["propertyID"] == "bf:note")
        self.assertEqual(note_row["target"], "bf:Item")

    def test_quoted_comma_preserved(self):
        note_row = next(r for r in self.rows if r["propertyID"] == "bf:note")
        self.assertEqual(note_row["propertyLabel"], "Notes, etc.")

    def test_all_rows_returned(self):
        self.assertEqual(len(self.rows), 3)


class TestMarvaTemplates(unittest.TestCase):

    def setUp(self):
        self.entries = _marva_templates(_MARVA_CSV)

    def test_one_entry_per_shape(self):
        filenames = [e["filename"] for e in self.entries]
        self.assertEqual(filenames, ["lc:RT:bf2:Item", "lc:RT:bf2:Work"])

    def test_type_is_shape_label(self):
        item = next(e for e in self.entries if e["filename"] == "lc:RT:bf2:Item")
        self.assertEqual(item["type"], "Digital Item")

    def test_entry_has_filename_and_type_keys(self):
        for entry in self.entries:
            self.assertIn("filename", entry)
            self.assertIn("type", entry)


class TestMarvaShapeTsv(unittest.TestCase):

    def test_returns_tab_separated_rows_for_shape(self):
        tsv = _marva_shape_tsv(_MARVA_CSV, "lc:RT:bf2:Item")
        self.assertIsNotNone(tsv)
        header = tsv.splitlines()[0]
        self.assertIn("\t", header)
        self.assertIn("target", header.split("\t"))

    def test_only_includes_rows_for_requested_shape(self):
        tsv = _marva_shape_tsv(_MARVA_CSV, "lc:RT:bf2:Item")
        rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t"))
        self.assertEqual({r["shapeID"] for r in rows}, {"lc:RT:bf2:Item"})
        self.assertEqual(len(rows), 2)  # bf:heldBy + continuation bf:note

    def test_output_round_trips_through_convert(self):
        tsv = _marva_shape_tsv(_MARVA_CSV, "lc:RT:bf2:Work")
        shacl = dctap_shacl._convert(tsv)
        self.assertIn("targetClass", shacl)

    def test_unknown_shape_returns_none(self):
        self.assertIsNone(_marva_shape_tsv(_MARVA_CSV, "lc:RT:bf2:Nope"))


class TestFetchMarvaSource(unittest.TestCase):

    def setUp(self):
        import sinopia.dctap as dctap_mod
        dctap_mod._csv_cache.clear()
        dctap_mod._template_cache.clear()

    def _patch_csv(self):
        return patch("sinopia.dctap._fetch_csv", AsyncMock(return_value=_MARVA_CSV))

    def test_fetch_templates_lists_marva_shapes(self):
        with self._patch_csv():
            entries = _run(fetch_templates(MARVA_SOURCE))
        self.assertEqual([e["filename"] for e in entries],
                         ["lc:RT:bf2:Item", "lc:RT:bf2:Work"])

    def test_fetch_tsv_content_returns_shape_tsv(self):
        with self._patch_csv():
            content = _run(fetch_tsv_content(MARVA_SOURCE, "lc:RT:bf2:Work"))
        self.assertIsNotNone(content)
        self.assertIn("bf:title", content)

    def test_fetch_tsv_content_unknown_shape_returns_none(self):
        with self._patch_csv():
            content = _run(fetch_tsv_content(MARVA_SOURCE, "missing"))
        self.assertIsNone(content)


if __name__ == "__main__":
    unittest.main()
