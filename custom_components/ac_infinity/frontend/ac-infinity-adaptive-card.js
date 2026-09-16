class ACInfinityAdaptiveCard extends HTMLElement {
  setConfig(config) {
    if (!config.fan_entity) {
      throw new Error("fan_entity is required");
    }
    this.config = config;
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  getCardSize() {
    return 8;
  }

  _entitiesForDevice() {
    const registry = this._hass.entities || {};
    const fan = registry[this.config.fan_entity];
    if (!fan?.device_id) return [];
    return Object.entries(registry)
      .filter(([, entity]) => entity.device_id === fan.device_id)
      .map(([entityId, entity]) => ({ entityId, ...entity }));
  }

  _entity(name, domain) {
    const configured = this.config.entities?.[name];
    if (configured) return configured;
    const wanted = name.toLowerCase();
    const match = this._entitiesForDevice().find((entity) => {
      const original = (entity.original_name || entity.name || "").toLowerCase();
      return entity.entityId.startsWith(`${domain}.`) && original === wanted;
    });
    return match?.entityId;
  }

  _state(entityId) {
    return entityId ? this._hass.states[entityId] : undefined;
  }

  _value(entityId, fallback = "—") {
    const state = this._state(entityId);
    if (!state) return fallback;
    const unit = state.attributes.unit_of_measurement || "";
    return `${state.state}${unit ? ` ${unit}` : ""}`;
  }

  _toggle(entityId) {
    const state = this._state(entityId);
    if (!state) return;
    this._hass.callService("switch", state.state === "on" ? "turn_off" : "turn_on", {
      entity_id: entityId,
    });
  }

  _setNumber(entityId, value) {
    this._hass.callService("number", "set_value", {
      entity_id: entityId,
      value: Number(value),
    });
  }

  _render() {
    if (!this.config || !this._hass) return;
    const fan = this._state(this.config.fan_entity);
    const adaptive = this._entity("Adaptive control", "switch");
    const manual = this._entity("Manual override", "switch");
    const leafVpd = this._entity("Adaptive leaf VPD", "sensor");
    const current = this._entity("Adaptive current fan level", "sensor");
    const requested = this._entity("Adaptive requested fan level", "sensor");
    const reason = this._entity("Adaptive control reason", "sensor");
    const period = this._entity("Adaptive active period", "sensor");
    const healthy = this._entity("Adaptive climate sensors healthy", "binary_sensor");

    const targets = [
      ["Day temperature target", "number"],
      ["Day maximum humidity", "number"],
      ["Day leaf VPD target", "number"],
      ["Night temperature target", "number"],
      ["Night maximum humidity", "number"],
      ["Night leaf VPD target", "number"],
      ["Manual fan level", "number"],
      ["Minimum fan level", "number"],
      ["Maximum fan level", "number"],
    ].map(([name, domain]) => [name, this._entity(name, domain)]);

    const numberRows = targets.map(([name, entityId]) => {
      const state = this._state(entityId);
      if (!state) return "";
      return `<label class="number"><span>${name}</span><input data-number="${entityId}" type="number" value="${state.state}" min="${state.attributes.min}" max="${state.attributes.max}" step="${state.attributes.step}"><small>${state.attributes.unit_of_measurement || ""}</small></label>`;
    }).join("");

    this.innerHTML = `
      <ha-card>
        <style>
          .wrap{padding:20px;color:var(--primary-text-color)}
          h2{margin:0 0 16px;font-size:1.35rem}
          .fan,.toggle,.metric,.status,.number{background:var(--ha-card-background,var(--card-background-color));border:1px solid var(--divider-color);border-radius:14px;padding:12px}
          .fan{display:flex;align-items:center;gap:12px;margin-bottom:10px}.fan ha-icon{color:var(--success-color);--mdc-icon-size:34px}
          .speed{margin-left:auto;font-size:1.35rem;font-weight:700}
          .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:10px 0}
          .metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.metric b,.metric small{display:block}.metric small{color:var(--secondary-text-color)}
          .toggle{cursor:pointer;text-align:left;color:inherit}.toggle.on{background:color-mix(in srgb,var(--success-color) 18%,var(--card-background-color))}
          .status{display:grid;grid-template-columns:1fr auto;gap:8px 16px;margin:10px 0}.status span:nth-child(even){font-weight:600;text-align:right}
          h3{margin:20px 0 8px}.number{display:grid;grid-template-columns:1fr 88px 38px;align-items:center;gap:8px}.number input{width:100%;box-sizing:border-box;background:var(--secondary-background-color);color:inherit;border:1px solid var(--divider-color);border-radius:8px;padding:8px}
          @media(max-width:600px){.metrics{grid-template-columns:1fr}.number{grid-template-columns:1fr 82px 34px}}
        </style>
        <div class="wrap">
          <h2>${this.config.name || "AC Infinity Adaptive Speed Control"}</h2>
          <div class="fan"><ha-icon icon="mdi:fan"></ha-icon><div><b>${fan?.attributes.friendly_name || this.config.fan_entity}</b><small>${fan?.state || "unavailable"}</small></div><span class="speed">${fan?.attributes.percentage ?? 0}%</span></div>
          <div class="grid">
            <button class="toggle ${this._state(adaptive)?.state === "on" ? "on" : ""}" data-toggle="${adaptive || ""}">Adaptive control<br><b>${this._state(adaptive)?.state || "unavailable"}</b></button>
            <button class="toggle ${this._state(manual)?.state === "on" ? "on" : ""}" data-toggle="${manual || ""}">Manual override<br><b>${this._state(manual)?.state || "unavailable"}</b></button>
          </div>
          <div class="grid metrics">
            <div class="metric"><small>Temperature</small><b>${this._value(this._entity("Temperature", "sensor"))}</b></div>
            <div class="metric"><small>Humidity</small><b>${this._value(this._entity("Humidity", "sensor"))}</b></div>
            <div class="metric"><small>Leaf VPD</small><b>${this._value(leafVpd)}</b></div>
          </div>
          <h3>Control status</h3>
          <div class="status"><span>Reason</span><span>${this._value(reason)}</span><span>Current / requested</span><span>${this._value(current)} / ${this._value(requested)}</span><span>Sensor health</span><span>${this._state(healthy)?.state || "unavailable"}</span><span>Active period</span><span>${this._value(period)}</span></div>
          <h3>Targets and limits</h3>
          <div class="grid">${numberRows}</div>
        </div>
      </ha-card>`;

    this.querySelectorAll("[data-toggle]").forEach((button) => button.addEventListener("click", () => this._toggle(button.dataset.toggle)));
    this.querySelectorAll("[data-number]").forEach((input) => input.addEventListener("change", () => this._setNumber(input.dataset.number, input.value)));
  }
}

if (!customElements.get("ac-infinity-adaptive-card")) {
  customElements.define("ac-infinity-adaptive-card", ACInfinityAdaptiveCard);
}
window.customCards = window.customCards || [];
if (!window.customCards.some((card) => card.type === "ac-infinity-adaptive-card")) {
  window.customCards.push({
    type: "ac-infinity-adaptive-card",
    name: "AC Infinity Adaptive Control",
    description: "Adaptive environmental fan control for local AC Infinity Bluetooth controllers",
  });
}
