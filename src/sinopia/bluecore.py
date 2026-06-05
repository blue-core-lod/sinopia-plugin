from datetime import datetime

from sinopia.config import _BF_VOCAB


def _format_date(iso_str: str) -> str:
    match iso_str:
        case "":
            return ""
        case _:
            try:
                dt = datetime.fromisoformat(iso_str)
                return dt.strftime(f"%b {dt.day}, %Y")
            except ValueError:
                return iso_str


def _get_label(result: dict) -> str:
    data = result.get("data", {})
    match data.get("http://www.w3.org/2000/01/rdf-schema#label"):
        case str(label) if label:
            return label
    match data.get("title"):
        case {"mainTitle": str(t)} if t:
            return t
        case [{"mainTitle": str(t)}, *_] if t:
            return t
    return result.get("uri", "")


def _get_types(result: dict) -> list[str]:
    data = result.get("data", {})
    match data.get("@type", ""):
        case str(raw) if raw:
            raw_list = [raw]
        case [*items]:
            raw_list = items
        case _:
            raw_list = []
    return [t if t.startswith("http") else _BF_VOCAB + t for t in raw_list if t]


def _process_results(results: list[dict]) -> list[dict]:
    return [
        {
            "label": _get_label(r),
            "uri": r.get("uri", ""),
            "uuid": r.get("uuid", ""),
            "types": _get_types(r),
            "modified": _format_date(r.get("updated_at", "")),
            "group": "Blue Core",
        }
        for r in results
    ]


def _page_range(current: int, total_pages: int) -> list:
    """Return page numbers and '...' sentinels for the paginator."""
    if total_pages <= 8:
        return list(range(1, total_pages + 1))
    window_start = max(1, min(current - 2, total_pages - 5))
    window_end = min(window_start + 5, total_pages)
    pages: list = []
    if window_start > 1:
        pages.append(1)
        if window_start > 2:
            pages.append("...")
    pages.extend(range(window_start, window_end + 1))
    if window_end < total_pages:
        if window_end < total_pages - 1:
            pages.append("...")
        pages.append(total_pages)
    return pages
