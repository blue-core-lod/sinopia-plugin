from sinopia.bluecore import _format_date
from sinopia.config import _BF_VOCAB

_LOC_URI_TYPES: dict[str, str] = {
    "/resources/works/":      _BF_VOCAB + "Work",
    "/resources/instances/":  _BF_VOCAB + "Instance",
    "/authorities/names/":    "http://www.loc.gov/mads/rdf/v1#Authority",
    "/authorities/subjects/": "http://www.loc.gov/mads/rdf/v1#Topic",
}


def _loc_types_from_uri(uri: str) -> list[str]:
    return [t for path, t in _LOC_URI_TYPES.items() if path in uri]


def _parse_loc_entry(entry: list) -> dict | None:
    label = uri = modified = ""
    for child in entry[2:]:
        if not isinstance(child, list):
            continue
        attrs = child[1] if len(child) > 1 and isinstance(child[1], dict) else {}
        match child[0]:
            case "atom:title":
                label = child[-1] if isinstance(child[-1], str) else ""
            case "atom:link" if attrs.get("rel") == "alternate" and "type" not in attrs:
                uri = attrs.get("href", "")
            case "atom:updated":
                raw = child[-1] if isinstance(child[-1], str) else ""
                modified = _format_date(raw[:10]) if raw else ""
    if not uri:
        return None
    return {
        "label": label,
        "uri": uri,
        "uuid": "",
        "types": _loc_types_from_uri(uri),
        "modified": modified,
        "group": "Library of Congress",
    }


def _parse_loc_feed(data: list) -> tuple[list[dict], int]:
    total = 0
    results: list[dict] = []
    for child in data[2:]:
        if not isinstance(child, list):
            continue
        match child[0]:
            case "opensearch:totalResults":
                try:
                    total = int(child[-1])
                except (ValueError, TypeError):
                    pass
            case "atom:entry":
                entry = _parse_loc_entry(child)
                if entry:
                    results.append(entry)
    return results, total
