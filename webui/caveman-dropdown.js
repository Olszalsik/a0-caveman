/**
 * caveman — topbar level selector.
 *
 * Injected through the plugin's `page-head` WebUI extension point.
 *
 * Notes on correctness (these were all real defects):
 *
 *  - It POSTs `action: "set"`, which the backend actually implements. The
 *    previous revision sent `action: "set"` to a handler that only knew
 *    `get | set_level | set_enabled | list`, got HTTP 200 with
 *    `{"ok": false, "error": "unknown action: 'set'"}`, and still closed the
 *    menu and updated the label. Every click reported success and changed
 *    nothing. The client now checks `ok` and surfaces a real error.
 *
 *  - "off" is sent as `{ level: "off" }`; the backend turns that into
 *    "clear the level and disable for this chat", which is the one round trip
 *    the old two-call approach could not do.
 *
 *  - It uses `fetchApi` from the framework, which attaches the CSRF token.
 *    A bare `fetch` sends no token, and the handler is auth+CSRF protected.
 *
 *  - It no longer polls on a 2s interval forever, and no longer wipes the
 *    host `x-extension` slot's innerHTML.
 */
import { fetchApi } from "/js/api.js";

const LEVELS = [
  { v: "off", l: "Off", d: "Normal replies for this chat" },
  { v: "lite", l: "Lite", d: "No filler, keep articles" },
  { v: "full", l: "Full", d: "Drop articles, fragments OK" },
  { v: "ultra", l: "Ultra", d: "Extreme compression" },
  { v: "wenyan-lite", l: "Wenyan Lite", d: "Semi-classical Chinese" },
  { v: "wenyan-full", l: "Wenyan Full", d: "Full classical Chinese" },
  { v: "wenyan-ultra", l: "Wenyan Ultra", d: "Extreme classical Chinese" },
];

const STATE_URL = "/api/plugins/caveman/caveman_state";

const ICON =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="14" height="14" ' +
  'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" ' +
  'stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="5" r="2"/>' +
  '<path d="M6 8 C 5 10, 5 12, 6 14 L 7 17 L 7 21 L 8 21 L 8 18 L 10 18 L 10 21 ' +
  'L 11 21 L 11 17 L 12 14 L 13 12 L 16 9 L 15 8 L 13 9 L 11 10.5 L 9 10.5 ' +
  'C 8 9.5, 7.5 9, 6 8 Z"/><line x1="15" y1="11" x2="20" y2="5"/>' +
  '<polyline points="17 5, 20 5, 20 8"/></svg>';

const state = {
  chatId: null,
  level: null,
  enabled: null,
  root: null,
  button: null,
  label: null,
  menu: null,
  observer: null,
  pending: false,
};

function currentChatId() {
  try {
    if (globalThis.__context) return String(globalThis.__context);
  } catch (e) {
    /* not available */
  }
  try {
    if (window.Alpine && window.Alpine.store) {
      const store = window.Alpine.store("chats");
      if (store) {
        const selected = store.selected || store.current;
        if (selected) return String(selected.id || selected.context_id || selected);
      }
    }
  } catch (e) {
    /* not available */
  }
  const url = new URL(location.href);
  return url.searchParams.get("ctxid") || url.searchParams.get("chat_id") || null;
}

async function callApi(body) {
  const response = await fetchApi(STATE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!result || result.ok !== true) {
    const message = (result && result.error) || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return result;
}

function displayLevel() {
  if (state.enabled === false) return "off";
  return state.level || "off";
}

function paint() {
  if (state.label) state.label.textContent = `Caveman: ${displayLevel()}`;
  if (state.root && state.enabled === false) {
    state.root.style.opacity = "0.6";
  } else if (state.root) {
    state.root.style.opacity = "";
  }
  renderMenu();
}

function renderMenu() {
  if (!state.menu) return;
  const active = displayLevel();
  state.menu.replaceChildren(
    ...LEVELS.map((level) => {
      const item = document.createElement("button");
      item.type = "button";
      item.style.display = "block";
      item.style.width = "100%";
      item.style.textAlign = "left";
      item.style.padding = "6px 14px";
      item.style.background = "transparent";
      item.style.border = "none";
      item.style.color = "inherit";
      item.style.font = "inherit";
      item.style.fontSize = "0.6rem";
      item.style.cursor = state.pending ? "wait" : "pointer";

      const row = document.createElement("div");
      row.style.fontWeight = "600";
      row.textContent = level.v === active ? `${level.l} ✓` : level.l;

      const desc = document.createElement("div");
      desc.style.fontSize = "0.55rem";
      desc.style.opacity = "0.7";
      desc.textContent = level.d;

      item.append(row, desc);
      item.addEventListener("mouseenter", () => {
        item.style.background = "var(--color-background-hover,#333)";
      });
      item.addEventListener("mouseleave", () => {
        item.style.background = "transparent";
      });
      item.addEventListener("mousedown", (event) => event.stopPropagation());
      item.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        select(level.v);
      });
      return item;
    })
  );
}

async function select(value) {
  const chatId = currentChatId();
  if (!chatId) {
    state.label && (state.label.textContent = "Caveman: no chat");
    return;
  }
  state.chatId = chatId;
  state.pending = true;
  renderMenu();
  try {
    // "off" is handled server-side as "clear level + disable".
    const result = await callApi({ action: "set", chat_id: chatId, level: value });
    state.level = result.level;
    state.enabled = result.enabled;
    state.label && (state.label.textContent = `Caveman: ${displayLevel()}`);
  } catch (error) {
    console.error("[caveman] set level failed", error);
    // Do not claim success. Re-read the real state and show it. This is an
    // inline GET, not refresh(): refresh() early-returns while state.pending
    // is true (finding 15, remediation 2026-09-28), so the old error path
    // silently never re-read anything and left the stale label on screen.
    try {
      const result = await callApi({ action: "get", chat_id: chatId });
      state.level = result.level;
      state.enabled = result.enabled;
      state.label && (state.label.textContent = `Caveman: ${displayLevel()}`);
    } catch (readError) {
      console.error("[caveman] state re-read failed", readError);
      state.label && (state.label.textContent = "Caveman: error");
    }
  } finally {
    state.pending = false;
    renderMenu();
  }
}

async function refresh() {
  const chatId = currentChatId();
  if (!chatId || state.pending) return;
  state.chatId = chatId;
  try {
    const result = await callApi({ action: "get", chat_id: chatId });
    state.level = result.level;
    state.enabled = result.enabled;
  } catch (error) {
    console.error("[caveman] state read failed", error);
    return;
  }
  // Drop the poll if the chat changed while the request was in flight.
  if (currentChatId() === chatId) paint();
}

function setOpen(open) {
  if (!state.menu) return;
  state.menu.style.display = open ? "block" : "none";
  if (open) renderMenu();
}

function build() {
  const existing = document.getElementById("caveman-btn");
  if (existing) {
    // Reuse the already-built control, but make sure it is still attached.
    if (existing.isConnected) {
      state.root = existing.parentElement;
      state.button = existing;
      state.label = existing.querySelector("span:last-child");
      state.menu = document.getElementById("caveman-menu");
      return true;
    }
    existing.remove();
  }

  // Resolve the host lazily; the composer may not be mounted yet.
  const host =
    document.querySelector("x-extension[id='chat-input-bottom-actions-end']") ||
    document.querySelector("x-extension[id*='bottom-actions-end']");
  if (!host) return false;

  // Append a wrapper. Do NOT clear host.innerHTML — that destroys the
  // framework's extension slot and its own registration.
  const root = document.createElement("span");
  root.id = "caveman-root";
  root.style.position = "relative";
  root.style.display = "inline-flex";
  root.style.alignItems = "center";

  const button = document.createElement("button");
  button.type = "button";
  button.className = "text-button";
  button.id = "caveman-btn";
  button.style.fontSize = "0.6rem";
  button.style.display = "inline-flex";
  button.style.alignItems = "center";
  button.style.gap = "4px";
  button.title = "Caveman compression level for this chat";

  const icon = document.createElement("span");
  icon.innerHTML = ICON;
  icon.style.display = "inline-flex";
  icon.style.width = "14px";
  icon.style.height = "14px";

  const label = document.createElement("span");
  label.textContent = "Caveman: …";

  const menu = document.createElement("div");
  menu.id = "caveman-menu";
  menu.style.display = "none";
  menu.style.position = "absolute";
  menu.style.bottom = "calc(100% + 6px)";
  menu.style.right = "0";
  menu.style.background = "var(--color-background,#1e1e1e)";
  menu.style.border = "1px solid var(--color-border,#444)";
  menu.style.borderRadius = "8px";
  menu.style.padding = "6px 0";
  menu.style.minWidth = "220px";
  menu.style.boxShadow = "0 4px 16px rgba(0,0,0,0.4)";
  menu.style.zIndex = "9999";

  button.append(icon, label);
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    setOpen(menu.style.display !== "block");
  });
  button.addEventListener("mousedown", (event) => event.stopPropagation());

  root.append(button, menu);
  host.appendChild(root);

  state.root = root;
  state.button = button;
  state.label = label;
  state.menu = menu;
  return true;
}

function watch() {
  if (state.observer || typeof MutationObserver === "undefined") return;
  // Finding 15 (remediation 2026-09-28): the observer used to run `build()`
  // (a full querySelector pass) and, on success, a POST `refresh()` on EVERY
  // mutation batch anywhere in the document — typing in the composer or a
  // streaming response was enough to flood the backend with state GETs.
  // Now: act only when the control is actually missing or detached, and
  // debounce bursts of mutations into one check.
  let timer = null;
  const controlMissing = () => {
    const btn = document.getElementById("caveman-btn");
    return !btn || !btn.isConnected;
  };
  state.observer = new MutationObserver(() => {
    if (!controlMissing()) return;
    if (timer) return;
    timer = setTimeout(() => {
      timer = null;
      if (controlMissing() && build()) refresh();
    }, 500);
  });
  state.observer.observe(document.body, { childList: true, subtree: true });
}

function start() {
  if (!build()) return false;

  document.addEventListener("click", (event) => {
    if (state.menu && !event.target.closest("#caveman-root")) setOpen(false);
  });

  watch();
  refresh();

  // The previous revision ran `setInterval(tick, 2000)` for the whole page
  // lifetime, POSTing to the API ~1800x/hour per open tab even when the menu
  // was closed. Refresh on real state changes instead: the extension tree
  // mutation above covers mount/unmount, and a light idle tick (15s) covers
  // a level changed from another surface such as a slash command.
  setInterval(() => {
    if (!state.pending && !document.hidden) refresh();
  }, 15000);

  return true;
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", start, { once: true });
} else {
  start();
}

export { state as cavemanUiState };
