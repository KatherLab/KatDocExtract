from __future__ import annotations

import ast
import html as _html
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

TABLE_RE = re.compile(r"<table.*?>.*?</table>", re.IGNORECASE | re.DOTALL)
ROW_RE = re.compile(r"<tr.*?>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
CELL_RE = re.compile(r"<t[dh].*?>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)

REFDET_RE = re.compile(r"(<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>)", re.DOTALL)

URL_RE = re.compile(r"(https?://[^\s\]\)\"\'<>]+)")
MD_LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^\s\)]+)\)")


def clean_cell_text(cell_html: str) -> str:
    text = re.sub(r"<.*?>", "", cell_html, flags=re.DOTALL)
    text = _html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def html_table_to_markdown(html_table: str) -> str:
    rows: List[List[str]] = []
    for row_match in ROW_RE.finditer(html_table):
        cells_raw = CELL_RE.findall(row_match.group(1))
        cells = [clean_cell_text(c) for c in cells_raw]
        if cells:
            rows.append(cells)

    if not rows:
        return html_table

    max_cols = max(len(r) for r in rows)
    for r in rows:
        if len(r) < max_cols:
            r.extend([""] * (max_cols - len(r)))

    header = rows[0]
    sep = ["---"] * max_cols

    md_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(sep) + " |",
    ]
    for r in rows[1:]:
        md_lines.append("| " + " | ".join(r) + " |")

    return "\n".join(md_lines)


def extract_tables(text: str) -> List[str]:
    return TABLE_RE.findall(text)


def replace_tables_inline_markdown(text: str) -> str:
    def repl(m: re.Match) -> str:
        return html_table_to_markdown(m.group(0))
    return TABLE_RE.sub(repl, text)


def extract_hyperlinks(text: str) -> List[str]:
    found = set()

    for m in URL_RE.finditer(text):
        found.add(m.group(1))

    for m in MD_LINK_RE.finditer(text):
        found.add(m.group(1))

    return sorted(found)


def re_match_refdet(text: str):
    matches = REFDET_RE.findall(text)
    # matches: list of tuples (full, label_type, coords_str)
    return matches


def extract_coordinates_and_label(ref_tuple) -> Optional[Tuple[str, List[List[float]]]]:
    try:
        full, label_type, coords_str = ref_tuple
        cor_list = ast.literal_eval(coords_str)

        if isinstance(cor_list, (list, tuple)):
            if not cor_list:
                return None
            first = cor_list[0]
            if isinstance(first, (int, float)):
                cor_list = [cor_list]
        else:
            return None
    except Exception:
        return None

    # normalize to list[list[float]]
    out: List[List[float]] = []
    for p in cor_list:
        if not isinstance(p, (list, tuple)) or len(p) != 4:
            continue
        out.append([float(p[0]), float(p[1]), float(p[2]), float(p[3])])

    if not out:
        return None
    return label_type, out


@dataclass
class Region:
    bbox: Tuple[int, int, int, int]  # x1,y1,x2,y2
    full_tag: str
