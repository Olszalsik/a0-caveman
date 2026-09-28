// headroom-store.js - Alpine store for the Headroom plugin settings UI.
//
// Implements the Store Gate pattern required by Agent Zero:
//   - registered as `$store.headroomStore`
//   - exposes `onOpen()` / `cleanup()` for the x-init / x-destroy hooks
//   - uses A0 toast notifications (toastFrontendError / Success) only,
//     never inline error boxes
//
// IMPORTANT FIX (v0.1.1): use callJsonApi from /js/api.js instead of raw
// fetch(). The framework helper handles CSRF, auth, and normalises the URL
// (it expects the path WITHOUT the `/api/` prefix - the helper adds it).
// Raw fetch() was hitting a 404 on the install button because:
//   1. the endpoint URL was wrong (used /api/... instead of /plugins/...)
//   2. no CSRF token was attached so the request was rejected with 403
//
// The settings modal (headroom-config.html) is opened standalone via
// openModal, so the framework's pluginSettingsPrototype wrapper (which
// provides `config.*` on its own scope) is NOT present. The store owns the
// settings: it loads the effective headroom section through
// /plugins/caveman/headroom_config {action:"get"} into `config`, the modal
// binds against it via an x-data getter, and Save posts it back with
// {action:"set", section}.

import { createStore } from "/js/AlpineStore.js";
import { callJsonApi } from "/js/api.js";
import {
  toastFrontendError,
  toastFrontendSuccess,
  toastFrontendInfo,
  toastFrontendWarning,
} from "/components/notifications/notification-store.js";

const DEBUG = false; // set true to log to console

function _log(...args) {
  if (DEBUG) console.info("[headroom-store]", ...args);
}
function _warn(...args) {
  console.warn("[headroom-store]", ...args);
}

export const store = createStore("headroomStore", {
  busy: false,
  headroomAvailable: null, // null=unknown, true/false after detection
  headroomVersion: null,
  toggleState: null, // null=unknown, "on" | "off"
  scope: null, // null=global, "project" | "agent"
  lastError: "", // last error message, shown in a dismissible inline banner

  // ----- Proxy mode state (Phase 4) -----
  proxyBusy: false,
  proxyStatus: null, // { running, host, port, url, mode, pid, ... } or null until first fetch

  // ----- Standalone-plugin coexistence (roadmap P0.4) -----
  // Read-only report from /headroom_config. This store never toggles the
  // other plugin; it only tells the user what is true so they can decide.
  coexistence: null,

  // ----- Settings (self-contained modal) -----
  // headroom-config.html is opened standalone via openModal (banner CTAs,
  // config.html button), outside the framework's plugin-settings wrapper,
  // so nothing provides a `config` scope there and there is no Save footer.
  // The modal binds against `config` below (exposed through an x-data
  // getter) and persists through the /headroom_config get/set actions.
  config: null,
  configSnapshot: "", // JSON of the section as last loaded/saved
  savingConfig: false,

  get configDirty() {
    if (!this.config || !this.config.headroom) return false;
    try {
      return JSON.stringify(this.config.headroom) !== this.configSnapshot;
    } catch (err) {
      return false;
    }
  },

  // ----- Settings migration from the standalone plugin (roadmap P3.1) -----
  migrationPlan: null,
  migrationBusy: false,
  migrationOutcome: null,


  // -----------------------------------------------------------------
  // Computed
  // -----------------------------------------------------------------
  get toggleLabel() {
    if (this.toggleState === "on") return "plugin: on";
    if (this.toggleState === "off") return "plugin: off";
    return "plugin: ?";
  },
  get toggleClass() {
    return this.toggleState === "on" ? "on" : "off";
  },

  // v0.4.2: the autoCompressToolOutputs getter/setter was removed -- it read
  // `this.config`, which this standalone store never receives (the framework
  // settings modal exposes `config` only on its own scope), so the checkbox
  // always showed checked and toggling did nothing. config.html now binds the
  // checkbox directly to config.auto_compress_tool_outputs_min_tokens.

  // -----------------------------------------------------------------
  // Lifecycle
  // -----------------------------------------------------------------
  onOpen() {
    _log("onOpen");
    this._refreshAll();
  },

  cleanup() {
    _log("cleanup");
    this.busy = false;
    this.savingConfig = false;
    this.lastError = "";
  },

  async _refreshAll() {
    // Best-effort: load the effective settings, and probe the stats endpoint
    // to detect whether the api routes are wired up. Never throws.
    await Promise.all([
      this.loadConfig(),
      this._refreshStatus(),
    ]);
  },

  // -----------------------------------------------------------------
  // Settings load / save (self-contained modal)
  // -----------------------------------------------------------------
  async loadConfig() {
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_config",
        { action: "get" }
      );
      if (data && data.ok && data.config) {
        this.config = data.config;
        this.configSnapshot = JSON.stringify(data.config.headroom || {});
        this.scope = data.config?.__scope__ || "global";
      }
      this.coexistence = data?.coexistence || this.coexistence;
    } catch (err) {
      _warn("load config failed (this is OK if the api isn't wired yet):", err);
    }
  },

  async saveConfig() {
    if (this.savingConfig || !this.config) return;
    this.savingConfig = true;
    this.lastError = "";
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_config",
        { action: "set", section: this.config.headroom || {} }
      );
      if (data && data.ok) {
        this.config = data.config || this.config;
        this.configSnapshot = JSON.stringify(this.config.headroom || {});
        const n = (data.written_keys || []).length;
        toastFrontendSuccess(
          n
            ? `Headroom settings saved (${n} key${n === 1 ? "" : "s"} changed).`
            : "Headroom settings saved (no changes).",
          "Headroom"
        );
      } else {
        this.lastError = `Save failed: ${data?.error || "unknown"}`;
        toastFrontendError(this.lastError, "Headroom");
      }
    } catch (err) {
      this.lastError = `Save failed: ${err?.message || err}`;
      toastFrontendError(this.lastError, "Headroom");
    } finally {
      this.savingConfig = false;
    }
  },

  async _refreshStatus() {
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_stats",
        { window_hours: 1, limit: 1 }
      );
      if (data && data.ok) {
        this.headroomAvailable = Boolean(data.config?.headroom?.available);
        this.headroomVersion = data.config?.headroom?.version || null;
        this.toggleState = data.config?.enabled ? "on" : "off";
      }
    } catch (err) {
      this.headroomAvailable = false;
      this.toggleState = null;
      _warn("refresh status failed (api not yet wired?):", err?.message || err);
    }
  },

  // -----------------------------------------------------------------
  // Actions
  // -----------------------------------------------------------------
  async installPackage() {
    if (this.busy) return;
    this.busy = true;
    this.lastError = "";
    _log("installPackage: starting pip install");
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_execute",
        { args: ["--upgrade"] }
      );
      _log("installPackage: response", data);
      if (data && data.ok) {
        toastFrontendSuccess(
          "headroom-ai installed / upgraded. The plugin is now active.",
          "Headroom"
        );
        this.headroomAvailable = true;
      } else {
        const tail = (data?.stderr || data?.stdout || data?.error || "unknown error")
          .toString()
          .slice(-600);
        this.lastError = `Install failed: ${tail}`;
        toastFrontendError(this.lastError, "Headroom");
      }
    } catch (err) {
      _warn("installPackage: request failed", err);
      this.lastError = `Install request failed: ${err?.message || err}`;
      toastFrontendError(this.lastError, "Headroom");
    } finally {
      this.busy = false;
      this._refreshStatus();
    }
  },

  openDashboard() {
    if (typeof globalThis.openModal === "function") {
      globalThis.openModal("/plugins/caveman/webui/headroom-dashboard.html");
    } else {
      toastFrontendError("openModal is not available in this context", "Headroom");
    }
  },

  async testCompress() {
    if (this.busy) return;
    this.busy = true;
    this.lastError = "";
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_execute",
        { args: ["--no-install"] } // probe-only: just verifies the import
      );
      if (data && data.ok) {
        this.headroomAvailable = true;
        toastFrontendSuccess(
          "headroom-ai is importable. Try the compress_text tool from a chat.",
          "Headroom"
        );
      } else {
        this.headroomAvailable = false;
        const tail = (data?.stderr || data?.stdout || data?.error || "")
          .toString()
          .slice(-400);
        this.lastError = `headroom-ai import check failed: ${tail || "unknown"}`;
        toastFrontendError(this.lastError, "Headroom");
      }
    } catch (err) {
      this.lastError = `Test request failed: ${err?.message || err}`;
      toastFrontendError(this.lastError, "Headroom");
    } finally {
      this.busy = false;
    }
  },

  dismissError() {
    this.lastError = "";
  },

  // -----------------------------------------------------------------
  // Settings migration from the standalone plugin (roadmap P3.1)
  // -----------------------------------------------------------------

  // Preview is read-only and is deliberately a separate call from apply, so
  // the user always sees what would change before anything is written.
  async previewMigration() {
    if (this.migrationBusy) return;
    this.migrationBusy = true;
    this.lastError = "";
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_migration",
        { action: "preview" }
      );
      if (data && data.ok) {
        this.migrationPlan = data.plan;
        this.migrationOutcome = null;
      } else {
        this.lastError = `migration preview failed: ${data?.error || "unknown"}`;
        toastFrontendError(this.lastError, "Headroom");
      }
    } catch (err) {
      this.lastError = `migration preview failed: ${err?.message || err}`;
      toastFrontendError(this.lastError, "Headroom");
    } finally {
      this.migrationBusy = false;
    }
  },

  // Apply requires the explicit confirm flag the backend enforces. It only
  // fills in keys this plugin does not already have, and backs the
  // destination file up first.
  async applyMigration() {
    if (this.migrationBusy) return;
    this.migrationBusy = true;
    this.lastError = "";
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_migration",
        { action: "apply", confirm: true }
      );
      if (data && data.ok) {
        this.migrationOutcome = data.outcome;
        this.migrationPlan = data.plan;
        if (data.outcome?.changed) {
          toastFrontendSuccess(
            `Imported ${data.outcome.written_keys.length} setting(s). ` +
              `Backup: ${data.outcome.backup || "none"}`,
            "Headroom"
          );
        } else {
          toastFrontendInfo(
            "Nothing to import - settings already match.",
            "Headroom"
          );
        }
      } else {
        this.lastError = `migration apply failed: ${data?.error || "unknown"}`;
        toastFrontendError(this.lastError, "Headroom");
      }
    } catch (err) {
      this.lastError = `migration apply failed: ${err?.message || err}`;
      toastFrontendError(this.lastError, "Headroom");
    } finally {
      this.migrationBusy = false;
    }
  },

  // -----------------------------------------------------------------
  // Proxy mode (Phase 4)
  // -----------------------------------------------------------------
  async refreshProxyStatus() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_proxy",
        { action: "status" }
      );
      if (data && data.ok) {
        this.proxyStatus = data;
      } else {
        this.proxyStatus = { running: false, error: data?.error || "unknown" };
      }
    } catch (err) {
      this.proxyStatus = { running: false, error: err?.message || String(err) };
    } finally {
      this.proxyBusy = false;
    }
  },

  async startProxy() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_proxy",
        { action: "start" }
      );
      if (data?.ok) {
        this.proxyStatus = data;
        toastFrontendSuccess(
          `Headroom proxy started at ${data.url || "http://127.0.0.1:8787"} (pid ${data.pid || "?"})`,
          "Headroom"
        );
      } else {
        toastFrontendError(
          `Proxy start failed: ${data?.error || "unknown"}`,
          "Headroom"
        );
      }
    } catch (err) {
      toastFrontendError(`Proxy start error: ${err?.message || err}`, "Headroom");
    } finally {
      this.proxyBusy = false;
    }
  },

  async stopProxy() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_proxy",
        { action: "stop" }
      );
      if (data?.ok) {
        this.proxyStatus = data;
        toastFrontendInfo(
          data?.force_killed
            ? "Headroom proxy was force-killed after refusing to stop."
            : "Headroom proxy stopped.",
          "Headroom"
        );
      } else {
        toastFrontendError(
          `Proxy stop failed: ${data?.error || "unknown"}`,
          "Headroom"
        );
      }
    } catch (err) {
      toastFrontendError(`Proxy stop error: ${err?.message || err}`, "Headroom");
    } finally {
      this.proxyBusy = false;
    }
  },

  async restartProxy() {
    if (this.proxyBusy) return;
    this.proxyBusy = true;
    try {
      const data = await callJsonApi(
        "/plugins/caveman/headroom_proxy",
        { action: "restart" }
      );
      if (data?.ok) {
        this.proxyStatus = data;
        toastFrontendSuccess("Headroom proxy restarted.", "Headroom");
      } else {
        toastFrontendError(
          `Proxy restart failed: ${data?.error || "unknown"}`,
          "Headroom"
        );
      }
    } catch (err) {
      toastFrontendError(`Proxy restart error: ${err?.message || err}`, "Headroom");
    } finally {
      this.proxyBusy = false;
    }
  },
});
