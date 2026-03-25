#!/usr/bin/env python3
"""Merge TL schema files from tdlib and tdesktop into a unified superset."""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

TDLIB_URL = (
    "https://raw.githubusercontent.com/tdlib/td/refs/heads/master/"
    "td/generate/scheme/telegram_api.tl"
)
TDESKTOP_URL = (
    "https://raw.githubusercontent.com/telegramdesktop/tdesktop/refs/heads/dev/"
    "Telegram/SourceFiles/mtproto/scheme/api.tl"
)

COMBINATOR_RE = re.compile(r"^(\w[\w.]*)#([0-9a-f]+)\b(.+)$")


@dataclass
class TLEntry:
    name: str
    crc: str
    params_raw: str
    full_line: str
    section: str


@dataclass
class MergeResult:
    only_tdlib: list[TLEntry]
    only_tdesktop: list[TLEntry]
    conflicts: list[tuple[TLEntry, TLEntry, TLEntry]]
    common_count: int


def fetch_content(source: str) -> str:
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source) as resp:
            return resp.read().decode("utf-8")
    with open(source, encoding="utf-8") as f:
        return f.read()


def extract_preamble(text: str) -> tuple[list[str], set[str]]:
    """Extract raw preamble lines from tdlib (everything before the first
    ``---types---``) and collect combinator names that appear in them."""
    raw_lines: list[str] = []
    names: set[str] = set()
    for line in text.splitlines():
        if line.strip() == "---types---":
            break
        raw_lines.append(line)
        m = COMBINATOR_RE.match(line.strip())
        if m:
            names.add(m.group(1))
    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()
    return raw_lines, names


def parse_tl_schema(text: str) -> list[TLEntry]:
    """Parse a TL schema file into combinator entries."""
    entries: list[TLEntry] = []
    current_section = "types"

    for line in text.splitlines():
        stripped = line.strip()

        if stripped == "---types---":
            current_section = "types"
            continue
        if stripped == "---functions---":
            current_section = "functions"
            continue

        m = COMBINATOR_RE.match(stripped)
        if m:
            entries.append(
                TLEntry(
                    name=m.group(1),
                    crc=m.group(2),
                    params_raw=m.group(3),
                    full_line=stripped,
                    section=current_section,
                )
            )

    return entries


def count_params(params_raw: str) -> int:
    """Count real data parameters, ignoring flag bitfields and bare ``?true`` flags."""
    head = params_raw.rsplit("=", 1)
    if len(head) < 2:
        return 0
    param_str = head[0]

    count = 0
    in_braces = False
    for token in param_str.split():
        if token.startswith("{"):
            in_braces = True
        if in_braces:
            if token.endswith("}"):
                in_braces = False
            continue
        if ":" not in token:
            continue
        _, type_part = token.split(":", 1)
        if type_part == "#":
            continue
        if type_part.endswith("?true"):
            continue
        count += 1
    return count


def merge_schemas(
    tdesktop_entries: list[TLEntry],
    tdlib_entries: list[TLEntry],
) -> MergeResult:
    td_by_name: dict[str, TLEntry] = {e.name: e for e in tdesktop_entries}
    td_crcs: set[str] = {e.crc for e in tdesktop_entries}

    tdlib_all_by_name: dict[str, TLEntry] = {e.name: e for e in tdlib_entries}
    tdlib_all_crcs: set[str] = {e.crc for e in tdlib_entries}

    only_tdlib: list[TLEntry] = []
    conflicts: list[tuple[TLEntry, TLEntry, TLEntry]] = []
    common_count = 0

    for entry in tdlib_entries:
        if entry.name in td_by_name:
            existing = td_by_name[entry.name]
            if existing.crc == entry.crc:
                common_count += 1
            else:
                td_p = count_params(existing.params_raw)
                tdlib_p = count_params(entry.params_raw)
                winner = entry if tdlib_p > td_p else existing
                conflicts.append((existing, entry, winner))
        elif entry.crc in td_crcs:
            pass
        else:
            only_tdlib.append(entry)

    only_tdesktop = [
        e
        for e in tdesktop_entries
        if e.name not in tdlib_all_by_name and e.crc not in tdlib_all_crcs
    ]

    return MergeResult(
        only_tdlib=only_tdlib,
        only_tdesktop=only_tdesktop,
        conflicts=conflicts,
        common_count=common_count,
    )


def build_merged_output(
    tdesktop_text: str,
    result: MergeResult,
    preamble_lines: list[str],
    preamble_names: set[str],
    *,
    tdlib_source: str,
    tdesktop_source: str,
    layer: str,
) -> str:
    """Reconstruct merged schema: tdlib preamble + tdesktop body (deduped)."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    header = [
        f"// Merged TL schema — layer {layer}",
        f"// Generated: {now}",
        f"// Sources:",
        f"//   tdlib:    {tdlib_source}",
        f"//   tdesktop: {tdesktop_source}",
        "",
    ]

    output: list[str] = header + list(preamble_lines)
    output.append("")
    output.append("---types---")
    output.append("")

    replacements: dict[str, str] = {}
    for td_entry, _, winner in result.conflicts:
        if winner.full_line != td_entry.full_line:
            replacements[td_entry.name] = winner.full_line

    add_types = [
        e for e in result.only_tdlib
        if e.section == "types" and e.name not in preamble_names
    ]
    add_funcs = [
        e for e in result.only_tdlib
        if e.section == "functions" and e.name not in preamble_names
    ]
    types_done = False
    funcs_done = False

    for line in tdesktop_text.splitlines():
        stripped = line.strip()

        m = COMBINATOR_RE.match(stripped)
        if m:
            name = m.group(1)
            if name in preamble_names:
                continue
            if name in replacements:
                output.append(replacements[name])
                continue

        if stripped == "---functions---" and not types_done and add_types:
            output.append("")
            for entry in add_types:
                output.append(entry.full_line)
            types_done = True

        if stripped.startswith("// LAYER") and not funcs_done and add_funcs:
            for entry in add_funcs:
                output.append(entry.full_line)
            output.append("")
            funcs_done = True

        output.append(line)

    if not funcs_done and add_funcs:
        output.append("")
        for entry in add_funcs:
            output.append(entry.full_line)

    cleaned: list[str] = []
    for line in output:
        if not line.strip() and cleaned and not cleaned[-1].strip():
            continue
        cleaned.append(line)

    return "\n".join(cleaned)


def print_report(result: MergeResult) -> None:
    def log(msg: str = "") -> None:
        print(msg, file=sys.stderr)

    log(f"\n{'=' * 60}")
    log("TL Schema Merge Report")
    log(f"{'=' * 60}")
    log(f"  Common (identical):     {result.common_count}")
    log(f"  Only in tdlib (added):  {len(result.only_tdlib)}")
    log(f"  Only in tdesktop:       {len(result.only_tdesktop)}")
    log(f"  Conflicts resolved:     {len(result.conflicts)}")

    if result.only_tdlib:
        log()
        log("--- Only in tdlib (added to merged output) ---")
        for e in result.only_tdlib:
            log(f"  [{e.section:9s}] {e.name}#{e.crc}")

    if result.only_tdesktop:
        log()
        log("--- Only in tdesktop ---")
        for e in result.only_tdesktop:
            log(f"  [{e.section:9s}] {e.name}#{e.crc}")

    if result.conflicts:
        log()
        log("--- Conflicts (auto-resolved: picked version with more params) ---")
        for td, tdlib, winner in result.conflicts:
            td_p = count_params(td.params_raw)
            tdlib_p = count_params(tdlib.params_raw)
            src = "tdlib" if winner is tdlib else "tdesktop"
            log(f"  {td.name}:")
            log(f"    tdesktop #{td.crc} ({td_p} params)")
            log(f"    tdlib    #{tdlib.crc} ({tdlib_p} params)")
            log(f"    -> selected {src}")

    log(f"{'=' * 60}\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Merge TL schema files from tdlib and tdesktop.",
    )
    parser.add_argument(
        "--tdlib",
        default=TDLIB_URL,
        help="Path or URL to tdlib schema (default: GitHub master)",
    )
    parser.add_argument(
        "--tdesktop",
        default=TDESKTOP_URL,
        help="Path or URL to tdesktop schema (default: GitHub dev)",
    )
    parser.add_argument(
        "-o", "--output",
        default="merged.tl",
        help="Output file path (default: merged.tl)",
    )
    parser.add_argument(
        "--diff-only",
        action="store_true",
        help="Only show differences, do not write output file",
    )

    args = parser.parse_args()
    log = lambda msg: print(msg, file=sys.stderr)

    log("Fetching tdlib schema...")
    tdlib_text = fetch_content(args.tdlib)
    log("Fetching tdesktop schema...")
    tdesktop_text = fetch_content(args.tdesktop)

    log("Parsing...")
    tdlib_entries = parse_tl_schema(tdlib_text)
    tdesktop_entries = parse_tl_schema(tdesktop_text)
    log(f"  tdlib:    {len(tdlib_entries)} entries")
    log(f"  tdesktop: {len(tdesktop_entries)} entries")

    log("Merging...")
    result = merge_schemas(tdesktop_entries, tdlib_entries)
    print_report(result)

    if args.diff_only:
        return

    preamble_lines, preamble_names = extract_preamble(tdlib_text)
    layer_match = re.search(r"//\s*LAYER\s+(\d+)", tdesktop_text)
    layer = layer_match.group(1) if layer_match else "unknown"
    merged = build_merged_output(
        tdesktop_text, result, preamble_lines, preamble_names,
        tdlib_source=args.tdlib, tdesktop_source=args.tdesktop, layer=layer,
    )
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(merged)
    log(f"Written to: {args.output}")


if __name__ == "__main__":
    main()
