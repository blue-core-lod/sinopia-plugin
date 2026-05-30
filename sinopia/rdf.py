import pyshacl
import rdflib

_CONTENT_TYPE_TO_FORMAT: dict[str, str] = {
    "text/turtle":            "turtle",
    "application/rdf+xml":   "xml",
    "application/ld+json":   "json-ld",
    "text/n3":               "n3",
    "application/n-triples": "nt",
    "application/n-quads":   "nquads",
    "application/trig":      "trig",
}

_EXT_TO_FORMAT: dict[str, str] = {
    ".ttl":    "turtle",
    ".rdf":    "xml",
    ".jsonld": "json-ld",
    ".json":   "json-ld",
    ".n3":     "n3",
    ".nt":     "nt",
    ".nq":     "nquads",
    ".trig":   "trig",
}


def _detect_format(content_type: str, url: str) -> str:
    """Guess rdflib format string from Content-Type header or URL extension."""
    for ct, fmt in _CONTENT_TYPE_TO_FORMAT.items():
        if ct in content_type:
            return fmt
    path = url.split("?")[0]
    for ext, fmt in _EXT_TO_FORMAT.items():
        if path.endswith(ext):
            return fmt
    return "turtle"


def _parse_rdf(text: str, base_uri: str, fmt: str) -> tuple[rdflib.Graph, str | None]:
    """Parse RDF text into a Graph; return (graph, error_message)."""
    g = rdflib.Graph()
    try:
        g.parse(data=text, format=fmt, publicID=base_uri or None)
        return g, None
    except Exception as exc:
        return g, str(exc)


_SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")


def _shacl_violations(data_graph: rdflib.Graph, shapes_url: str) -> dict:
    """Validate data_graph against shapes loaded from shapes_url.

    Returns a dict with keys: conforms (bool|None), violations (list), error (str|None).
    """
    shapes_graph = rdflib.Graph()
    try:
        shapes_graph.parse(shapes_url)
    except Exception as exc:
        return {"conforms": None, "violations": [], "error": f"Could not load shapes: {exc}"}
    try:
        conforms, results_graph, _ = pyshacl.validate(
            data_graph,
            shacl_graph=shapes_graph,
            inference="rdfs",
            abort_on_first=False,
        )
        violations = []
        for report in results_graph.subjects(rdflib.RDF.type, _SH.ValidationResult):
            severity = results_graph.value(report, _SH.resultSeverity)
            focus    = results_graph.value(report, _SH.focusNode)
            path     = results_graph.value(report, _SH.resultPath)
            message  = results_graph.value(report, _SH.resultMessage)
            violations.append({
                "severity":    str(severity).split("#")[-1] if severity else "",
                "focus_node":  str(focus) if focus else "",
                "result_path": str(path).split("#")[-1] if path else "",
                "message":     str(message) if message else "",
            })
        return {"conforms": conforms, "violations": violations, "error": None}
    except Exception as exc:
        return {"conforms": None, "violations": [], "error": f"Validation error: {exc}"}
