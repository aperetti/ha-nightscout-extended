/**
 * Nightscout Glucose Card: a glanceable bedside/kitchen glucose display for
 * the Nightscout Extended integration. Served and loaded by the integration;
 * use it as `type: custom:nightscout-glucose-card`.
 *
 * It finds the integration's sensors on its own (or from `entity`), colours the
 * screen by the site's Nightscout alarm thresholds, dims to coloured-on-black
 * at night, and draws recent readings plus the AR2 forecast behind the number.
 */

export const CARD_VERSION = "0.3.0";
const PLATFORM = "nightscout_extended";
const MGDL_PER_MMOL = 18.0182;
const DEFAULT_THRESHOLDS = { urgent_low: 55, low: 80, high: 180, urgent_high: 260 };

// Sensor translation keys, matched within the same Nightscout device.
const KEYS = {
  glucose: "glucose",
  delta: "glucose_delta",
  trend: "glucose_trend",
  last: "last_reading",
  iob: "iob",
  cob: "cob",
  forecast: "forecast_30",
};

const ARROWS = {
  double_up: "⇈", single_up: "↑", forty_five_up: "↗", flat: "→",
  forty_five_down: "↘", single_down: "↓", double_down: "⇊",
};

const DAY = {
  stale: { bg: "#5f6368", fg: "#ffffff" },
  urgent_low: { bg: "#c62828", fg: "#ffffff" },
  low: { bg: "#e8615a", fg: "#ffffff" },
  in_range: { bg: "#3d9a5b", fg: "#ffffff" },
  high: { bg: "#f2df74", fg: "#1f1f1f" },
  urgent_high: { bg: "#ef7a78", fg: "#ffffff" },
};
const NIGHT_FG = {
  stale: "#8a8a8a", urgent_low: "#ff5a50", low: "#ff8a7a",
  in_range: "#f3e7a6", high: "#ffd84d", urgent_high: "#ff8a7a",
};

const DEFAULT_CONFIG = {
  hours_to_show: 3,
  night_mode: true,
  night_start: 21,
  night_end: 7,
  stale_minutes: 15,
  show_chart: true,
  show_footer: true,
  show_clock: true,
  fill_screen: false,
};

const CHART_LO = 40;   // mg/dL at the bottom of the chart
const CHART_HI = 300;  // mg/dL at the top
const FORECAST_AHEAD_MIN = 30;

/** Exposed for tests; pure functions with no DOM or hass access. */
export const logic = {
  classify(mgdl, ageMin, staleMin, t) {
    if (!Number.isFinite(mgdl) || !Number.isFinite(ageMin) || ageMin > staleMin) return "stale";
    if (mgdl < t.urgent_low) return "urgent_low";
    if (mgdl < t.low) return "low";
    if (mgdl <= t.high) return "in_range";
    if (mgdl <= t.urgent_high) return "high";
    return "urgent_high";
  },
  isNight(hour, cfg) {
    if (!cfg.night_mode || cfg.night_start === cfg.night_end) return false;
    return cfg.night_start > cfg.night_end
      ? hour >= cfg.night_start || hour < cfg.night_end
      : hour >= cfg.night_start && hour < cfg.night_end;
  },
  colors(level, night) {
    return night ? { bg: "#000000", fg: NIGHT_FG[level] } : DAY[level];
  },
  formatDelta(delta, mmol) {
    if (!Number.isFinite(delta)) return "";
    const v = mmol ? Math.round(delta * 10) / 10 : Math.round(delta);
    if (v === 0) return "±0";
    const text = mmol ? Math.abs(v).toFixed(1) : String(Math.abs(v));
    return (v > 0 ? "+" : "−") + text;
  },
  formatAge(ageMin) {
    if (!Number.isFinite(ageMin)) return "no data";
    const m = Math.floor(ageMin);
    if (m < 1) return "now";
    if (m < 60) return `${m} min ago`;
    return `${Math.floor(m / 60)} h ago`;
  },
  thresholds(attr) {
    const out = { ...DEFAULT_THRESHOLDS };
    for (const k of Object.keys(out)) {
      const v = Number(attr && attr[k]);
      if (Number.isFinite(v) && v > 0) out[k] = v;
    }
    return out;
  },
};

function num(state) {
  if (!state) return NaN;
  const v = Number(state.state);
  return Number.isFinite(v) ? v : NaN;
}

function isMmol(state) {
  return /mmol/i.test((state && state.attributes && state.attributes.unit_of_measurement) || "");
}

class NightscoutGlucoseCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  static getConfigForm() {
    return {
      schema: [
        {
          name: "entity",
          selector: { entity: { filter: { integration: PLATFORM, domain: "sensor", device_class: "blood_glucose_concentration" } } },
        },
        { name: "name", selector: { text: {} } },
        { name: "fill_screen", selector: { boolean: {} } },
        { name: "hours_to_show", selector: { number: { min: 1, max: 24, mode: "box" } } },
        {
          name: "",
          type: "grid",
          schema: [
            { name: "show_chart", selector: { boolean: {} } },
            { name: "show_footer", selector: { boolean: {} } },
            { name: "show_clock", selector: { boolean: {} } },
            { name: "night_mode", selector: { boolean: {} } },
            { name: "night_start", selector: { number: { min: 0, max: 23, mode: "box", unit_of_measurement: "h" } } },
            { name: "night_end", selector: { number: { min: 0, max: 23, mode: "box", unit_of_measurement: "h" } } },
          ],
        },
      ],
      computeLabel: (s) => ({
        entity: "Glucose sensor (optional; found automatically)",
        name: "Name shown at the top",
        fill_screen: "Fill the screen (for a panel view on a kiosk)",
        hours_to_show: "Hours of history in the chart",
        show_chart: "Chart",
        show_footer: "IOB / COB / forecast line",
        show_clock: "Clock",
        night_mode: "Dim at night",
        night_start: "Night starts",
        night_end: "Night ends",
      })[s.name],
    };
  }

  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    const cfg = { ...DEFAULT_CONFIG, ...config };
    for (const key of ["hours_to_show", "night_start", "night_end", "stale_minutes"]) {
      cfg[key] = Number(cfg[key]);
      if (!Number.isFinite(cfg[key])) throw new Error(`${key} must be a number`);
    }
    cfg.hours_to_show = Math.min(48, Math.max(1, cfg.hours_to_show));
    this._config = cfg;
    this._history = { t: 0, key: null, pts: [], busy: false };
    if (this._root) this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._ids = this._findEntities();
    this._fetchHistory();
    this._render();
  }

  connectedCallback() {
    if (!this._root) this._build();
    this._timer = setInterval(() => this._render(), 20000); // clock, age, night switch
    this._onResize = () => this._render();
    window.addEventListener("resize", this._onResize);
    // After layout, so a fill_screen card can measure where it starts.
    requestAnimationFrame(() => this._render());
  }

  disconnectedCallback() {
    clearInterval(this._timer);
    window.removeEventListener("resize", this._onResize);
  }

  getCardSize() {
    return 6;
  }

  getGridOptions() {
    return { columns: 12, rows: 6, min_columns: 6, min_rows: 4 };
  }

  // ---- entities ---------------------------------------------------------

  _findEntities() {
    const hass = this._hass;
    if (!hass) return {};
    const reg = hass.entities || {};
    let glucoseId = this._config.entity;
    if (!glucoseId) {
      glucoseId = Object.keys(reg).find(
        (id) => reg[id].platform === PLATFORM && reg[id].translation_key === KEYS.glucose
      );
    }
    if (!glucoseId) {
      // No registry info: only this integration's glucose sensor carries thresholds_mgdl.
      glucoseId = Object.keys(hass.states).find(
        (id) => id.startsWith("sensor.") && hass.states[id].attributes && hass.states[id].attributes.thresholds_mgdl
      );
    }
    if (!glucoseId) return {};

    const ids = { glucose: glucoseId };
    const device = reg[glucoseId] && reg[glucoseId].device_id;
    const siblings = device
      ? Object.keys(reg).filter((id) => reg[id].device_id === device && reg[id].platform === PLATFORM)
      : [];
    const base = glucoseId.replace(/_glucose$/, "");
    for (const [name, key] of Object.entries(KEYS)) {
      if (name === "glucose") continue;
      const byKey = siblings.find((id) => reg[id].translation_key === key);
      // Fall back to the default entity IDs when the registry has no translation keys.
      const guess = `${base}_${{ delta: "glucose_change", trend: "glucose_trend", last: "last_reading",
        iob: "insulin_on_board", cob: "carbs_on_board", forecast: "forecast_in_30_minutes" }[name]}`;
      ids[name] = byKey || (hass.states[guess] ? guess : undefined);
    }
    return ids;
  }

  _state(name) {
    const id = this._ids && this._ids[name];
    return id && this._hass ? this._hass.states[id] : undefined;
  }

  // ---- history ----------------------------------------------------------

  _fetchHistory() {
    const h = this._history;
    const id = this._ids && this._ids.glucose;
    if (!this._config.show_chart || !id || !this._hass || !this._hass.callWS || h.busy) return;
    const last = this._state("last");
    const key = `${id}|${last ? last.state : ""}|${this._config.hours_to_show}`;
    if (key === h.key && Date.now() - h.t < 60000) return;
    h.busy = true;
    const mmol = isMmol(this._state("glucose"));
    this._hass
      .callWS({
        type: "history/history_during_period",
        start_time: new Date(Date.now() - this._config.hours_to_show * 3600000).toISOString(),
        entity_ids: [id],
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      })
      .then((r) => {
        h.pts = ((r && r[id]) || [])
          .map((p) => [(p.lu || p.last_updated_ts || 0) * 1000, Number(p.s) * (mmol ? MGDL_PER_MMOL : 1)])
          .filter((p) => p[0] > 0 && Number.isFinite(p[1]));
        h.key = key;
      })
      .catch(() => {})
      .finally(() => {
        h.t = Date.now();
        h.busy = false;
        this._render();
      });
  }

  // ---- DOM --------------------------------------------------------------

  _build() {
    this._root = this.attachShadow({ mode: "open" });
    this._root.innerHTML = `
      <style>
        :host { display: block; height: 100%; }
        ha-card { display: block; height: 100%; overflow: hidden; position: relative; border: none; }
        .wrap {
          container-type: size; position: relative; box-sizing: border-box;
          height: 100%; min-height: 200px; aspect-ratio: 16 / 10;
          display: grid; grid-template-rows: auto 1fr auto auto;
          padding: 3cqh 3cqw 2cqh;
          background: var(--ngc-bg); color: var(--ngc-fg);
          font-family: var(--ngc-font, var(--ha-font-family-body, Roboto, system-ui, sans-serif));
          transition: background 0.6s ease, color 0.6s ease;
        }
        svg.chart { position: absolute; left: 0; right: 0; top: 12%; bottom: 14%; width: 100%; height: 74%;
          pointer-events: none; }
        .top, .mid, .bar, .footer { position: relative; z-index: 1; }
        .top { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center;
          font-size: min(5cqh, 3.2cqw); }
        .clock, .age { opacity: 0.85; }
        .age { justify-self: end; }
        .name { font-size: 1.6em; font-weight: 500; text-align: center; white-space: nowrap;
          overflow: hidden; text-overflow: ellipsis; }
        .mid { display: flex; align-items: center; justify-content: center; gap: 2cqw; min-height: 0; }
        .value { font-size: min(56cqh, 36cqw); line-height: 1; letter-spacing: -0.02em;
          font-variant-numeric: tabular-nums; }
        .side { display: flex; flex-direction: column; align-items: flex-start; line-height: 1; }
        .arrow { font-size: min(22cqh, 14cqw); }
        .delta { font-size: min(14cqh, 9cqw); }
        .bar { justify-self: center; width: 70%; height: min(2.4cqh, 14px); margin: 1cqh 0 1.6cqh;
          border-radius: 999px; background: color-mix(in srgb, var(--ngc-fg) 25%, transparent); }
        .fill { height: 100%; border-radius: inherit; background: var(--ngc-fg); transition: width 0.6s ease; }
        .footer { justify-self: center; font-size: min(4cqh, 2.8cqw); opacity: 0.8; white-space: nowrap; }
        .footer span + span::before { content: "·"; margin: 0 0.9em; }
        .missing { padding: 16px; }
        [hidden] { display: none !important; }
      </style>
      <ha-card>
        <div class="wrap" role="img">
          <svg class="chart" viewBox="0 0 1000 400" preserveAspectRatio="none" aria-hidden="true"></svg>
          <div class="top"><span class="clock"></span><span class="name"></span><span class="age"></span></div>
          <div class="mid"><span class="value"></span><div class="side"><span class="arrow"></span><span class="delta"></span></div></div>
          <div class="bar"><div class="fill"></div></div>
          <div class="footer"></div>
        </div>
        <div class="missing" hidden>Nightscout Extended glucose sensor not found. Set <code>entity</code> on the card.</div>
      </ha-card>`;
    const $ = (s) => this._root.querySelector(s);
    this._el = {
      card: $("ha-card"), wrap: $(".wrap"), chart: $("svg.chart"), clock: $(".clock"), name: $(".name"),
      age: $(".age"), value: $(".value"), arrow: $(".arrow"), delta: $(".delta"), bar: $(".bar"),
      fill: $(".fill"), footer: $(".footer"), missing: $(".missing"),
    };
  }

  _render() {
    if (!this._root || !this._config) return;
    const el = this._el;
    const cfg = this._config;
    const g = this._state("glucose");
    el.wrap.hidden = !g;
    el.missing.hidden = !!g;
    if (!g || !this._hass) return;

    const mmol = isMmol(g);
    const value = num(g);
    const mgdl = value * (mmol ? MGDL_PER_MMOL : 1);
    const last = this._state("last");
    const lastMs = last ? new Date(last.state).getTime() : NaN;
    const ageMin = (Date.now() - lastMs) / 60000;
    const t = logic.thresholds(g.attributes && g.attributes.thresholds_mgdl);
    const level = logic.classify(mgdl, ageMin, cfg.stale_minutes, t);
    const stale = level === "stale";
    const night = logic.isNight(new Date().getHours(), cfg);
    const c = logic.colors(level, night);

    el.wrap.style.setProperty("--ngc-bg", c.bg);
    el.wrap.style.setProperty("--ngc-fg", c.fg);
    this._sizeCard();

    el.clock.textContent = cfg.show_clock
      ? new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }).replace(/\s?[AP]M$/i, "")
      : "";
    el.name.textContent = cfg.name || "";
    el.age.textContent = logic.formatAge(ageMin);
    el.value.textContent = Number.isFinite(value) ? (mmol ? value.toFixed(1) : String(Math.round(value))) : "---";
    const trend = this._state("trend");
    el.arrow.textContent = stale ? "" : ARROWS[trend ? trend.state : ""] || "";
    el.delta.textContent = stale ? "" : logic.formatDelta(num(this._state("delta")), mmol);
    const pct = Number.isFinite(ageMin) ? Math.max(0, Math.min(100, 100 - (ageMin / cfg.stale_minutes) * 100)) : 0;
    el.fill.style.width = `${pct}%`;

    el.footer.hidden = !cfg.show_footer;
    if (cfg.show_footer) {
      const parts = [];
      if (stale) parts.push("⚠ No recent reading");
      const iob = num(this._state("iob"));
      const cob = num(this._state("cob"));
      const fc = num(this._state("forecast"));
      if (Number.isFinite(iob)) parts.push(`IOB ${iob.toFixed(2)} U`);
      if (Number.isFinite(cob)) parts.push(`COB ${Math.round(cob)} g`);
      if (Number.isFinite(fc) && !stale) parts.push(`30 min ≈ ${mmol ? fc.toFixed(1) : Math.round(fc)}`);
      el.footer.replaceChildren(...parts.map((p) => Object.assign(document.createElement("span"), { textContent: p })));
    }

    el.wrap.setAttribute(
      "aria-label",
      stale ? `No recent glucose reading (${el.age.textContent})`
        : `Glucose ${el.value.textContent} ${mmol ? "mmol/L" : "mg/dL"}, ${el.delta.textContent}, ${el.age.textContent}`
    );

    el.chart.style.display = cfg.show_chart ? "" : "none";
    if (cfg.show_chart) this._drawChart(c.fg, t, stale ? null : [lastMs, mgdl]);
  }

  _sizeCard() {
    const card = this._el.card;
    if (!this._config.fill_screen) {
      card.style.height = "";
      card.style.borderRadius = "";
      this._el.wrap.style.aspectRatio = "";
      return;
    }
    // Fill from where the card starts to the bottom of the screen, header or not.
    const r = this.getBoundingClientRect();
    const top = r.width || r.height ? Math.max(0, Math.round(r.top + window.scrollY)) : null;
    card.style.height = top === null ? "calc(100vh - var(--header-height, 0px))" : `calc(100dvh - ${top}px)`;
    card.style.borderRadius = "0";
    this._el.wrap.style.aspectRatio = "auto";
  }

  _drawChart(col, t, current) {
    const now = Date.now();
    const t0 = now - this._config.hours_to_show * 3600000;
    const t1 = now + FORECAST_AHEAD_MIN * 60000;
    const X = (ms) => (((ms - t0) / (t1 - t0)) * 1000).toFixed(1);
    const Y = (v) => ((1 - (Math.min(CHART_HI, Math.max(CHART_LO, v)) - CHART_LO) / (CHART_HI - CHART_LO)) * 400).toFixed(1);
    const pts = this._history.pts.filter((p) => p[0] >= t0);
    if (current && Number.isFinite(current[0]) && Number.isFinite(current[1])
        && (!pts.length || current[0] > pts[pts.length - 1][0])) pts.push(current);
    const line = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0])},${Y(p[1])}`).join("");

    let fline = "";
    const f = this._state("forecast");
    const fc = (f && f.attributes && f.attributes.forecast) || [];
    if (current && pts.length && fc.length) {
      const lastPt = pts[pts.length - 1];
      fline = `M${X(lastPt[0])},${Y(lastPt[1])}` +
        fc.map((p) => `L${X(new Date(p.time).getTime())},${Y(Number(p.mgdl))}`).join("");
    }
    const dash = 'vector-effect="non-scaling-stroke"';
    this._el.chart.innerHTML = `
      <rect x="0" y="${Y(t.high)}" width="1000" height="${Y(t.low) - Y(t.high)}" fill="${col}" opacity="0.18"/>
      <line x1="0" x2="1000" y1="${Y(t.high)}" y2="${Y(t.high)}" stroke="${col}" stroke-opacity="0.5" stroke-width="2" stroke-dasharray="10 8" ${dash}/>
      <line x1="0" x2="1000" y1="${Y(t.low)}" y2="${Y(t.low)}" stroke="${col}" stroke-opacity="0.5" stroke-width="2" stroke-dasharray="10 8" ${dash}/>
      <line x1="${X(now)}" x2="${X(now)}" y1="0" y2="400" stroke="${col}" stroke-opacity="0.15" stroke-dasharray="4 6" ${dash}/>
      <path d="${line}" fill="none" stroke="${col}" stroke-opacity="0.35" stroke-width="4" stroke-linejoin="round" stroke-linecap="round" ${dash}/>
      <path d="${fline}" fill="none" stroke="${col}" stroke-opacity="0.3" stroke-width="3" stroke-dasharray="2 8" stroke-linecap="round" ${dash}/>`;
  }
}

if (!customElements.get("nightscout-glucose-card")) {
  customElements.define("nightscout-glucose-card", NightscoutGlucoseCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "nightscout-glucose-card",
    name: "Nightscout Glucose",
    description: "Large glucose display with trend, history chart and night mode (Nightscout Extended).",
    preview: true,
    documentationURL: "https://github.com/aperetti/ha-nightscout-extended#glucose-card",
  });
  console.info(`%c NIGHTSCOUT-GLUCOSE-CARD %c ${CARD_VERSION} `, "color:#fff;background:#3d9a5b", "");
}
