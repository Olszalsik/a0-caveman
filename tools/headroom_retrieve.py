"""headroom_retrieve: look up the full original for a compressed CCR key.

The compressor injects a CCR key (a 32-char hex prefix) into the compressed
text it returns, e.g.:

    [compressed by headroom — original saved as CCR key a1b2c3d4...;
     retrieve with headroom_retrieve(ccr_key="a1b2c3d4...")]

(The exact wording differs slightly between call sites - some use a plain
hyphen instead of an em-dash - but the marker always contains
"original saved as CCR key" followed by the hex key.)

If the LLM (or the user) needs the full original that the compressed text was
derived from, it can call this tool with that key. Without this, lossy
compression would be irreversible - the LLM could only see the smaller
version.

Actions:
  - get       : return the original string for a CCR key (or a not-found msg).
                Optional offset (default 0) and max_chars (default 4000, cap
                100000) page through large originals; the response states
                when more text exists and which offset fetches the next page.
  - meta      : return rich metadata for a CCR key (no full blob)
  - stats     : return aggregate counters for the CCR cache
  - list      : list recent CCR entries (metadata only, not the blobs)
  - prune     : delete entries older than the configured TTL
"""

from __future__ import annotations

import json

from helpers.tool import Tool, Response
from usr.plugins.caveman.helpers.headroom import compressor
from usr.plugins.caveman.helpers.headroom.ccr_cache import CcrCache
from usr.plugins.caveman.helpers.headroom import config as _config


class HeadroomRetrieve(Tool):
    async def execute(self, **kwargs) -> Response:
        action = (self.args.get("action") or "get").strip().lower()
        cfg = _config.get_config(agent=self.agent)

        if not cfg.get("ccr_enabled", True):
            return Response(
                message="CCR is disabled in plugin config. "
                "Enable it in Settings -> Headroom to use headroom_retrieve.",
                break_loop=False,
            )

        if action == "meta":
            ccr = CcrCache(cfg)
            ccr_key = (self.args.get("ccr_key") or "").strip()
            if not ccr_key:
                return Response(
                    message="headroom_retrieve: 'ccr_key' argument is required for action=meta.",
                    break_loop=False,
                )
            meta = ccr.get_meta(ccr_key)
            if meta is None:
                return Response(
                    message=f"CCR key '{ccr_key}' not found (or expired).",
                    break_loop=False,
                )
            from usr.plugins.caveman.helpers.headroom.ccr_cache import _format_age
            meta["age_human"] = _format_age(float(meta.get("age_seconds") or 0))
            return Response(
                message="Headroom CCR metadata:\n\n```json\n"
                + json.dumps(meta, indent=2, default=str)
                + "\n```",
                break_loop=False,
            )

        if action == "stats":
            ccr = CcrCache(cfg)
            data = ccr.stats()
            return Response(
                message="Headroom CCR stats:\n\n```json\n"
                + json.dumps(data, indent=2)
                + "\n```",
                break_loop=False,
            )

        if action == "list":
            try:
                limit = int(self.args.get("limit", 20) or 20)
            except (TypeError, ValueError):
                limit = 20
            limit = max(1, min(limit, 200))
            ccr = CcrCache(cfg)
            entries = ccr.list_keys(limit=limit)
            return Response(
                message=f"Recent CCR entries (limit {limit}):\n\n```json\n"
                + json.dumps(entries, indent=2, default=str)
                + "\n```",
                break_loop=False,
            )

        if action == "prune":
            ccr = CcrCache(cfg)
            removed = ccr.prune_expired()
            return Response(
                message=f"Pruned {removed} expired CCR entries (TTL={cfg.get('ccr_ttl_days')} days).",
                break_loop=False,
            )

        if action == "get":
            ccr_key = (self.args.get("ccr_key") or "").strip()
            if not ccr_key:
                return Response(
                    message="headroom_retrieve: 'ccr_key' argument is required for action=get.",
                    break_loop=False,
                )
            try:
                offset = int(self.args.get("offset", 0) or 0)
            except (TypeError, ValueError):
                offset = 0
            offset = max(0, offset)
            try:
                max_chars = int(self.args.get("max_chars", 4000) or 4000)
            except (TypeError, ValueError):
                max_chars = 4000
            max_chars = max(1, min(max_chars, 100000))

            original = compressor.retrieve_original(ccr_key, agent=self.agent)
            if original is None:
                return Response(
                    message=f"CCR key '{ccr_key}' not found (or expired).",
                    break_loop=False,
                )

            # Page the blob so one huge original cannot flood the context
            # that compression just tried to save.
            chunk = original[offset : offset + max_chars]
            if not chunk:
                return Response(
                    message=(
                        f"CCR key '{ccr_key}' original is {len(original)} chars; "
                        f"offset {offset} is past the end. "
                        f"Call with offset=0 (or omit offset) to read from the start."
                    ),
                    break_loop=False,
                )
            has_more = offset + max_chars < len(original)
            header = (
                f"CCR key '{ccr_key}' original "
                f"({len(original)} chars, ~{compressor._cheap_token_count(original)} tokens)"
            )
            if offset:
                header += f" - showing chars {offset}-{offset + len(chunk) - 1}"
            if has_more:
                header += (
                    f" [TRUNCATED - {len(original) - offset - max_chars} more chars; "
                    f"call again with offset={offset + max_chars} to continue]"
                )
            return Response(
                message=f"{header}:\n\n{chunk}",
                break_loop=False,
            )

        return Response(
            message="Unknown action. Valid: get, meta, stats, list, prune.",
            break_loop=False,
        )
