"""
Editor State Management for Resources
"""
import rdflib
from pyodide.http import pyfetch


class EditorState(object):
    """All client-side editor state for a single resource."""

    def __init__(self, resource_id: str):
        self.resource_id = resource_id
        self.resource_uri = ""
        self.resource_types: list = []
        self.resource_label = ""
        self.raw_data: dict = {}
        self.triples: list = []    # [(subject, predicate, object_str)]
        self.props: dict = {}      # predicate -> [value_str, ...]
        self.labels: dict = {}     # URI -> rdfs:label string (inline labels only)
        self.field_edits: dict = {}
        self.expanded_sections: set = set()

    async def load(self) -> None:
        """Fetch JSON-LD from the BFF proxy and parse it."""
        response = await pyfetch(
            f"/sinopia/api/resource/{self.resource_id}?expand=true",
            headers={"Accept": "application/ld+json, application/json;q=0.9",
                     "User-Agent": "Sinopia"},
        )
        if not response.ok:
            raise RuntimeError(f"HTTP {response.status}: {response.status_text}")
        data = await response.json()
        if isinstance(data, dict):
            self.resource_uri = data.get("@id", "")
        self.raw_data = data
        self._parse(data)

    def _parse(self, data) -> None:
        """Parse a JSON-LD object (compacted or expanded) into internal state."""
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and str(item.get("@id", "")).startswith("http"):
                    data = item
                    break
            else:
                data = data[0] if data else {}

        if not self.resource_uri:
            self.resource_uri = data.get(
                "@id", f"https://dev.bcld.info/works/{self.resource_id}"
            )

        types = data.get("@type", [])
        self.resource_types = [types] if isinstance(types, str) else list(types)

        for key in [f"{rdflib.RDFS}label", "rdfs:label"]:
            if key in data:
                self.resource_label = self._literal(data[key])
                break

        self._extract_props(data, self.resource_uri, {"@id", "@type", "@context"})

    def _extract_props(self, node: dict, subject: str, skip: set) -> None:
        """Walk a JSON-LD node, storing triples and recursing into blank nodes."""
        for pred, raw_val in node.items():
            if pred in skip or pred.startswith("@"):
                continue
            values = raw_val if isinstance(raw_val, list) else [raw_val]
            for v in values:
                # Only add leaf values (literals and URI refs) to props/triples
                # Blank nodes are recursed into but not stored as leaf values
                if self._is_leaf_value(v):
                    obj_str = self._literal(v)
                    self.triples.append((subject, pred, obj_str))
                    self.props.setdefault(pred, []).append(obj_str)
                
                # Recurse into blank nodes (no @value, no real HTTP @id)
                if isinstance(v, dict) and "@value" not in v:
                    node_id = v.get("@id", "")
                    if not node_id or node_id.startswith("_:"):
                        self._extract_props(v, subject, skip)
                    elif node_id:
                        # Real URI node — capture its inline rdfs:label if present.
                        for lk in (f"{rdflib.RDFS}label", "rdfs:label", "label"):
                            if lk in v:
                                lv = v[lk]
                                if isinstance(lv, list):
                                    lv = lv[0]
                                self.labels[node_id] = self._literal(lv)
                                break

    @staticmethod
    def _is_leaf_value(val) -> bool:
        """Determine if a JSON-LD value is a leaf (literal) vs. a nested node.
        
        Leaf values include:
        - Plain strings, numbers, booleans
        - Dicts with @value (typed literals)
        - Dicts with real HTTP @id (URI references)
        
        Non-leaf values (nested nodes):
        - Dicts without @value and without real HTTP @id (blank nodes)
        """
        if isinstance(val, dict):
            # Has @value → it's a literal
            if "@value" in val:
                return True
            # Has real HTTP URI → it's a URI reference (leaf for our purposes)
            node_id = val.get("@id", "")
            if node_id and not node_id.startswith("_:"):
                return True
            # No @value, no real ID → blank node to recurse into
            return False
        # Plain values (str, int, float, bool, None) are leaves
        return True

    @staticmethod
    def _literal(val) -> str:
        """Extract a string value from a JSON-LD value node or plain string."""
        if isinstance(val, dict):
            return str(val.get("@value", val.get("@id", val)))
        return str(val)

    def type_short(self) -> str:
        """Return the most specific type name (non-Work class, or 'Work')."""
        for t in self.resource_types:
            name = str(t).split("/")[-1].split("#")[-1]
            if name.lower() != "work":
                return name
        return "Work"

    def resource_name(self) -> str:
        return f"_Work ({self.type_short()})"

    def has_prop(self, frag: str) -> bool:
        """True if any predicate key contains frag."""
        return any(frag in p for p in self.props)