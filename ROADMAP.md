# Caveman Plugin - Roadmap & Recommendations

**Current state:** v0.4.0, 38+ files, ~2200 LoC, health check PASSED, v2.2 compatible.

---

## Phase 1: Testing & Hardening (v0.4.1) - ~1 day

### 1.1 Real-world smoke test
- Open a real Agent Zero chat, type /caveman ultra, verify the level switches
- Send 5+ messages, verify the stats HUD updates in the right canvas
- Switch to /caveman off, verify the style reverts
- Test all 6 intensity levels with a real LLM
- Test the WebUI selector button (click each level, verify the badge updates)
- Test the response sanitizer (send a prompt that would normally produce filler, verify it gets stripped at ultra level)
- Test the tool shrinker (enable caveman, verify tool descriptions are shorter in the LLM payload)

### 1.2 Unit tests
- Add tests/helpers/test_state.py: roundtrip, clear, invalid-level rejection, concurrent access
- Add tests/test_slash_commands.py: all 6 slash patterns + natural phrases + edge cases
- Add tests/test_validator.py: banned filler detection, strip mode, warning emission
- Add tests/test_stats.py: HWM tracking, rolling window, risk floor breach, risk floor tuning

### 1.3 Benchmark with a real model
- Run benchmarks/run.py with a real litellm model (not --dry-run)
- Compare the 63.5% dry-run estimate against real output token counts
- Publish results in benchmarks/results/ for reproducibility

---

## Phase 2: Community Publishing (v0.5.0) - ~2 days

### 2.1 Standalone GitHub repo
- Create a new repo (e.g. agent0ai/caveman-a0 or yourname/caveman-a0)
- Copy the plugin contents to the repo root (not in a subfolder)
- Ensure plugin.yaml has the name field matching the index folder name
- Add a proper LICENSE (MIT, already done)
- Add a CHANGELOG.md documenting v0.1.0 through v0.4.0
- Add CONTRIBUTING.md for community contributors
- Add 3-5 screenshots for the Plugin Hub (topbar selector, stats HUD, dropdown, icon)

### 2.2 Plugin Index submission
- Fork agent0ai/a0-plugins
- Create plugins/caveman/ folder
- Add index.yaml with title, description, github URL, tags, screenshots
- Add a square thumbnail (thumbnail.png, max 20KB)
- Open a PR, wait for CI validation, address any feedback

### 2.3 Documentation site
- Port the upstream HONEST-NUMBERS.md to the a0 context
- Write a quick-start guide for new users (3 steps: install, enable, use)
- Document the HTTP API endpoints (caveman_state + caveman_stats)
- Document the extension points used and why

---

## Phase 3: Polish & UX (v0.5.1) - ~1 day

### 3.1 Settings UI page
- Create webui/config.html for Settings -> Developer -> Caveman
- Fields: enabled (toggle), level (dropdown), auto_clarity (toggle), risk_floor_pct (slider)
- Use the a0 plugin-settings-store pattern
- This replaces the need to edit default_config.yaml manually

### 3.2 Keyboard shortcut
- Add Ctrl+Shift+C to toggle caveman on/off for the current chat
- Implement via a small JS listener in the page-head extension

### 3.3 /caveman-stats slash command
- Add a monologue_start pattern for /caveman-stats
- When matched, query the stats API and format a one-line summary in chat
- Format: [caveman-stats] turns=12 tokens_saved=342 hwm=45 risk=ok

### 3.4 Statusline badge in chat input
- Add a small badge near the chat input showing the current caveman level
- Use the set_messages_before_loop webui extension point
- Shows: a small colored dot + level name (e.g. a brown dot + ultra)

### 3.5 Icon improvements
- Create a PNG fallback (thumbnail.png, 128x128, for the Plugin Hub)
- Consider an animated SVG (caveman blinking or stick tapping) for the topbar
- Add a dark-mode variant if the current icon does not read well on dark themes

---

## Phase 4: Advanced Features (v0.6.0) - ~3-4 days

### 4.1 Auto-level selection
- Monitor response length and complexity over the first 5 turns
- If responses are consistently short (< 200 chars), suggest downgrading to lite
- If responses are consistently long (> 1000 chars), suggest upgrading to ultra
- Implement as an advisory chat_extras message, not an automatic switch
- Add a config flag auto_level_suggest: true/false

### 4.2 Per-project defaults
- Store a default caveman level per project (not just per chat)
- When a new chat starts in a project, inherit the project default
- Add a project-level config UI in the project settings modal
- Falls back to the global default_config.yaml level

### 4.3 Memory integration
- When the user consistently picks the same level across 10+ chats, memorize it
- Use memory_save to store: user prefers caveman level X
- On new chat start, suggest the memorized level via chat_extras

### 4.4 MCP server wrapper
- Port the upstream caveman-shrink MCP middleware to a0
- Wrap external MCP tool descriptions before they reach the LLM
- Use the message_loop_prompts_before extension (already wired for tool shrinking)
- Add a config flag mcp_shrink: true/false

### 4.5 Multi-language detection
- Auto-detect the user dominant language from the first 3 messages
- If non-CJK language, skip wenyan-* levels in the dropdown (or grey them out)
- If CJK language, highlight wenyan-* levels as recommended
- Store the detected language in the per-chat state

### 4.6 Custom level editor
- Add a 7th option in the dropdown: Custom...
- Opens a modal where the user can define their own compression rules
- Rules: drop articles (yes/no), drop filler (yes/no), max sentence length, etc.
- Stored as a custom level in the per-chat state

---

## Phase 5: Enterprise & Integration (v0.7.0) - ~5+ days

### 5.1 Per-agent-profile overrides
- Allow specific agent profiles to have different caveman defaults
- Store in the plugin config with a per_profile section
- Example: researcher profile defaults to lite, coder defaults to ultra

### 5.2 Audit logging
- Log every level change with timestamp, chat_id, old_level, new_level
- Store in /a0/usr/workdir/.caveman/audit.log
- Add an API endpoint to query the audit log
- Add a WebUI panel to view recent level changes

### 5.3 External monitoring integration
- Export stats as Prometheus metrics via a new API endpoint
- Add a Grafana dashboard template
- Support webhook notifications on risk floor breach
- Add a Slack/Discord notification option for breach events

### 5.4 Scheduler integration
- Allow scheduled caveman on/off times (e.g. caveman on during work hours, off during meetings)
- Use the a0 scheduler to create a recurring task that toggles the level
- Add a UI in the settings page to configure the schedule

### 5.5 Multi-chat sync
- Add a global level option that syncs across all chats
- When the user picks a level in one chat, it applies to all chats
- Config flag: global_sync: true/false (default false, per-chat is the default)

---

## Recommendation: What to do FIRST

**Do Phase 1 (Testing & Hardening) first.** The plugin is built and the health check passes, but it has not been tested in a real Agent Zero chat yet. The slash command detector uses best-effort attribute guessing to find the user message, and the stats tracker reads loop_data attributes that might not exist in v2.2. A 30-minute real-world smoke test will catch any integration issues before you invest in community publishing.

**Then Phase 2 (Community Publishing).** Once the smoke test passes, publish to the a0-plugins index. This gives you discoverability and lets other users test the plugin on different setups, which catches edge cases you would not find alone.

**Then Phase 3 (Polish & UX).** The Settings UI page is the highest-impact piece here - without it, users have to edit YAML to enable the plugin, which is a barrier to adoption.

**Phase 4 and 5 are optional.** They add real value but are not needed for a v1.0 release. Pick the ones that match your use case:
- If you use caveman daily: 4.2 (per-project defaults) + 4.3 (memory integration)
- If you manage a team: 5.1 (per-profile overrides) + 5.2 (audit logging)
- If you monitor costs: 5.3 (external monitoring)
- If you want max savings: 4.1 (auto-level) + 4.4 (MCP wrapper)

---

## Quick-win checklist (if you have 30 minutes right now)

1. Reload the WebUI, open a chat, click the Caveman selector, pick ultra
2. Send 3 messages, verify the responses are compressed
3. Open the right canvas, verify the stats HUD shows turns + tokens saved
4. Click the selector again, pick off, verify the next response is normal
5. Run: python /a0/usr/plugins/caveman/benchmarks/run.py --dry-run
6. If all 5 pass, the plugin is ready for Phase 2 (publishing)
