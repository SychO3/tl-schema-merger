#!/usr/bin/env python3
"""Merge TL schema files from tdlib and tdesktop into a unified superset."""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from dataclasses import dataclass

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
    is_preamble: bool = False


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


def parse_tl_schema(
    text: str, *, detect_preamble: bool = False
) -> list[TLEntry]:
    """Parse a TL schema file into combinator entries.

    When *detect_preamble* is True (tdlib), every entry that appears before the
    first ``---types---`` divider is tagged ``is_preamble=True`` so the merger
    can exclude it from additions while still using it for cross-reference.
    """
    entries: list[TLEntry] = []
    current_section = "types"
    in_preamble = detect_preamble
    preamble_ended = False

    for line in text.splitlines():
        stripped = line.strip()

        if stripped == "---types---":
            current_section = "types"
            if in_preamble:
                in_preamble = False
                preamble_ended = True
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
                    is_preamble=in_preamble,
                )
            )

    if detect_preamble and not preamble_ended:
        for e in entries:
            e.is_preamble = False

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
        if entry.is_preamble:
            continue

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


def build_merged_output(tdesktop_text: str, result: MergeResult) -> str:
    """Reconstruct merged schema using tdesktop text as the skeleton."""
    lines = tdesktop_text.splitlines()

    replacements: dict[str, str] = {}
    for td_entry, _, winner in result.conflicts:
        if winner.full_line != td_entry.full_line:
            replacements[td_entry.name] = winner.full_line

    add_types = [e for e in result.only_tdlib if e.section == "types"]
    add_funcs = [e for e in result.only_tdlib if e.section == "functions"]
    types_done = False
    funcs_done = False

    output: list[str] = []

    for line in lines:
        stripped = line.strip()

        m = COMBINATOR_RE.match(stripped)
        if m and m.group(1) in replacements:
            output.append(replacements[m.group(1)])
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

    return "\n".join(output)


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
    tdlib_entries = parse_tl_schema(tdlib_text, detect_preamble=True)
    tdesktop_entries = parse_tl_schema(tdesktop_text)

    preamble_n = sum(1 for e in tdlib_entries if e.is_preamble)
    log(
        f"  tdlib:    {len(tdlib_entries) - preamble_n} main entries "
        f"({preamble_n} preamble skipped)"
    )
    log(f"  tdesktop: {len(tdesktop_entries)} entries")

    log("Merging...")
    result = merge_schemas(tdesktop_entries, tdlib_entries)
    print_report(result)

    if args.diff_only:
        return

    merged = build_merged_output(tdesktop_text, result)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(merged)
    log(f"Written to: {args.output}")


if __name__ == "__main__":
    main()
