# TL Schema Merger

Merges the Telegram API [TL](https://core.telegram.org/mtproto/TL) schema files from **[tdlib](https://github.com/tdlib/td)** and **[tdesktop](https://github.com/telegramdesktop/tdesktop)** into a single unified superset.

## Why

Both tdlib and tdesktop maintain their own copy of the Telegram API schema. They are nearly identical but can differ in edge cases — one may add new constructors before the other, or carry internal-only definitions. This tool produces a merged schema that includes everything from both sources.

## How it works

1. Downloads (or reads) both `.tl` files
2. Parses every combinator definition, keyed by `name#crc`
3. Skips the tdlib preamble (built-in type aliases, internal types, `invokeWith*Prefix` variants, test functions)
4. Merges using CRC32 as the ground truth:
   - **Same name + same CRC** — identical definition, keep one
   - **Same name + different CRC** — conflict, auto-select the version with more parameters
   - **Name only in one source** — include it
   - **Same CRC + different name** — alias (e.g. Prefix variant), skip
5. Outputs the merged schema using tdesktop's structure as the skeleton, preserving the `// LAYER` marker

## Usage

No external dependencies — pure Python standard library.

```bash
# Download from GitHub and merge (default)
python main.py

# Specify output file
python main.py -o output.tl

# Use local files
python main.py --tdlib path/to/telegram_api.tl --tdesktop path/to/api.tl

# Only show differences, don't write output
python main.py --diff-only
```

## Automated releases

A [GitHub Actions workflow](.github/workflows/daily-merge.yml) runs daily at 08:00 CST (00:00 UTC). It downloads the latest schemas, runs the merge, and — if the result differs from the previous version — commits the updated `merged.tl` and creates a GitHub release tagged `layer-{N}`.

## License

[Unlicense](LICENSE) — public domain.
