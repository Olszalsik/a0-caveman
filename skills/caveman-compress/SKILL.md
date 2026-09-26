---
name: caveman-compress
description: >
  Compress a natural-language Markdown file (memory notes, todos, preferences,
  runbooks) into caveman-speak. Preserves code blocks, inline code, URLs, paths
  and heading structure, and validates that it did before writing. Writes a
  .original.md backup. Trigger: /caveman-compress FILEPATH, or "compress this
  memory file".
---

# Caveman Compress

## Purpose

Compress a prose-heavy Markdown file to reduce the tokens it costs on every
turn. Code, links, paths and document structure must survive untouched.

## Trigger

`/caveman-compress <filepath>`, or when the user asks to compress a memory file.

## Process

**Prefer the script.** It is the only path with validation:

```bash
# report only, writes nothing
python <plugin>/skills/caveman-compress/scripts/compress.py --check FILE

# compress in place (writes FILE.original.md first)
python <plugin>/skills/caveman-compress/scripts/compress.py FILE

# preview without touching the file
python <plugin>/skills/caveman-compress/scripts/compress.py --stdout FILE
```

The script compresses, validates, and refuses to write on a failed validation.
Exit codes: `0` ok, `1` validation failed (file untouched), `2` usage or IO
error. `--force` overrides a failed validation; the original is still on disk
until then, so it is a deliberate choice.

**Fallback** only if the script cannot run: compress inline by following the
rules below, then state explicitly which of the six checks you verified by
hand. Say that you did, because an unvalidated overwrite is the one outcome
that loses the user's content.

## What validation checks

`helpers/markdown.py` compares the compressed text against the original and
fails on any of:

1. Fenced code block count changed, or any block's content modified.
2. Inline code span lost or invented.
3. URL lost or rewritten.
4. Definite path lost or rewritten.
5. Heading sequence changed or a heading lost.
6. Text containing CJK did not round-trip byte-identically.

Only *unambiguous* paths count as hard losses: a leading `./`, `../`, `/` or
drive letter, or a dotted filename in the final component. Prose pairs like
`pros/cons` and `Node/browser` are ignored, because caveman prose adds those
freely and flagging them would train you to ignore the check.

## Compression rules (for the inline fallback)

### Remove
- Articles: a, an, the
- Filler: just, really, basically, actually, simply, essentially, generally
- Pleasantries: "sure", "certainly", "of course", "happy to", "I'd recommend"
- Hedging: "it might be worth", "you could consider", "it would be good to"
- Redundant phrasing: "in order to" -> "to", "the reason is because" -> "because"
- Connective fluff: "however", "furthermore", "additionally", "in addition"

> Do **not** rewrite `make sure to` -> `ensure`. "Make sure the file exists" is
> a check; "ensure" is still a check, but `make sure` deleted outright turns it
> into something that reads like a create. The script protects this and will
> report a loss. If you are compressing by hand, leave the collocation alone.

### Preserve EXACTLY (never modify)
- Code blocks (fenced ``` and ~~~)
- Inline code (`backtick content`)
- URLs and links (full URLs, markdown links)
- File paths, including a leading `./` or `../`
- Commands (`npm install`, `git commit`, `docker build`)
- Technical terms (library names, API names, protocols, algorithms)
- Proper nouns (project names, people, companies)
- Dates, version numbers, numeric values
- Environment variables (`$HOME`, `NODE_ENV`)

### Preserve structure
- All markdown headings, in the same order and level
- Bullet point hierarchy and numbering
- Tables (compress cell text, keep the table structure)
- Frontmatter / YAML headers

### Compress
- Short synonyms: "big" not "extensive", "fix" not "implement a solution for"
- Fragments are fine: "Run tests before commit" not "You should always run
  tests before committing"
- Drop "you should", "remember to" - just state the action
- Merge bullets that say the same thing differently
- Keep one example where several show the same pattern

### Text with CJK
If the file contains any CJK character, the script returns it unchanged and
says so. English deletion rules are not valid for mixed CJK prose, because an
English article or intent phrase may qualify an embedded technical term.
Compress Chinese prose on a sentence basis instead, or leave the file alone.

## Pattern

Original:
> You should always make sure to run the test suite before pushing any changes to the main branch. This is important because it helps catch bugs early and prevents broken builds from being deployed to production.

Compressed:
> Run tests before push to main. Catch bugs early, prevent broken prod deploys.

Original:
> The application uses a microservices architecture with the following components. The API gateway handles all incoming requests and routes them to the appropriate service. The authentication service is responsible for managing user sessions and JWT tokens.

Compressed:
> Microservices architecture. API gateway route all requests to services. Auth service manage user sessions + JWT tokens.

## Boundaries

- Only compress prose: `.md`, `.txt`, `.typ`, `.tex`, and extensionless files.
- The script refuses `.json`, `.yaml`, `.yml`, `.toml`, `.ini`, `.cfg`, `.lock`.
- Never compress a `.original.md` backup, or any file twice in a row (the
  script reports `no change` on a second pass, which is the check).
- If you cannot tell whether a region is code or prose, leave it unchanged.
