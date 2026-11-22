"use strict";

// FlowXav — interface : parcours des procédures, étape en cours, briefing (état de départ, SimBrief),
// palette « Montre-moi ». Sans dépendance ; l'état vient de /api/etat toutes les 400 ms.

const $ = (id) => document.getElementById(id);
const STORE_SIMBRIEF = "flowxav.simbrief";
let lang = "fr";
let profiles = [];
let controls = [];
let lastState = null;
let journeyKey = "";
let paletteIndex = 0;
let lastSuggestion;

// ------------------------------------------------------------------ outils
function stored(key) { try { return localStorage.getItem(key); } catch { return null; } }
function store(key, value) { try { localStorage.setItem(key, value); } catch { /* stockage indisponible */ } }
function t(key, values = {}) {
  const table = window.FLOWXAV_I18N[lang] || window.FLOWXAV_I18N.en;
  let text = table[key] ?? window.FLOWXAV_I18N.en[key] ?? key;
  for (const [name, value] of Object.entries(values)) text = text.replace(`{${name}}`, value);
  return text;
}
function pick(entry) {
  return typeof entry === "object" && entry ? (entry[lang] || entry.en || entry.fr || "") : (entry || "");
}
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
async function post(path, body) {
  // Comme api(), mais renvoie aussi les erreurs présentables du service ({detail: {code, detail}}).
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json().catch(() => ({ detail: { code: "interne", detail: `HTTP ${response.status}` } }));
  return { ok: response.ok, data, status: response.status };
}
function errorText(prefix, error, status) {
  const key = `${prefix}_err_${error.code}`;
  return window.FLOWXAV_I18N.fr[key] ? t(key, { detail: error.detail || "" })
    : t(`${prefix}_err_autre`, { detail: error.detail || error.code || status });
}
let toastTimer = 0;
function toast(message) {
  const node = $("toast");
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 1800);
}
function note(id, text, kind) {
  const node = $(id);
  node.hidden = !text;
  node.textContent = text || "";
  node.className = `note ${kind || ""}`;
}
function typing() {
  const active = document.activeElement;
  return active && (active.tagName === "INPUT" || active.tagName === "SELECT" || active.tagName === "TEXTAREA");
}

// ------------------------------------------------------------------ langue
function translate() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((node) => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  document.querySelectorAll("[data-i18n-title]").forEach((node) => { node.title = t(node.dataset.i18nTitle); });
  journeyKey = "";
  renderResetButtons();
  renderSimbrief(savedFlight());
  if (lastState) render(lastState);
}

// ------------------------------------------------------------------ profils et commandes
function currentProfile() { return profiles.find((p) => p.id === $("profil").value); }
function procedures() { return currentProfile()?.procedures || []; }
function mainProcedures() { return procedures().filter((p) => !p.demonstration); }
// Phases du parcours : une phase peut avoir plusieurs variantes (mise sous tension sur APU ou sur GPU).
function phaseGroups() {
  const groups = [];
  const byPhase = new Map();
  mainProcedures().forEach((p, index) => {
    const key = p.phase ?? `#${index}`;
    if (!byPhase.has(key)) { byPhase.set(key, []); groups.push(byPhase.get(key)); }
    byPhase.get(key).push(p);
  });
  return groups;
}
function fillProfiles() {
  $("profil").innerHTML = "";
  profiles.forEach((p) => $("profil").append(new Option(`${p.constructeur} — ${p.nom}`, p.id)));
  loadControls();
}
async function loadControls() {
  const profile = currentProfile();
  controls = profile ? await api(`/api/profils/${profile.id}/commandes`) : [];
  renderPalette();
}

// ------------------------------------------------------------------ rendu général
function render(state) {
  lastState = state;
  const sim = state.simulateur;
  setChip("chip-sim", sim.connecte ? "ok" : "", sim.connecte ? t("sim_on") : t("sim_off"));
  setChip("chip-avion", sim.profil ? "ok" : "warn", sim.connecte ? (sim.profil ? sim.avion : `${sim.avion || ""} — ${t("no_profile")}`) : "", !sim.connecte);
  setChip("chip-etat", sim.etat_disponible ? "ok" : "warn", sim.profil ? (sim.etat_disponible ? t("state_ok") : t("state_missing")) : "", !sim.profil);
  setChip("chip-phase", "ok", sim.phase_vol ? t(`flight_${sim.phase_vol}`) : "", !sim.phase_vol);
  $("message-etat").hidden = !sim.message_etat;
  $("message-etat").textContent = sim.message_etat || "";
  if (sim.profil && $("profil").value !== sim.profil && !state.guide.actif) {
    $("profil").value = sim.profil;
    journeyKey = "";
    loadControls();
  }
  announceSuggestion(state);
  const guide = state.guide;
  $("parcours-vue").hidden = guide.actif;
  $("etape-vue").hidden = !guide.actif;
  if (guide.actif) renderStep(guide);
  else renderJourney(state);
}
function announceSuggestion(state) {
  if (!state.simulateur.connecte) { lastSuggestion = undefined; return; }
  const suggestion = state.suggestion || null;
  if (lastSuggestion === undefined) { lastSuggestion = suggestion; return; }
  if (suggestion && suggestion !== lastSuggestion) {
    const procedure = procedures().find((p) => p.id === suggestion);
    if (procedure) {
      const group = phaseGroups().find((items) => items.some((p) => p.id === suggestion)) || [];
      toast(group.length > 1 ? t("phase_suggest_variants", { name: pick(procedure.titre) })
        : t("phase_suggest", { name: pick(procedure.titre) }));
    }
  }
  lastSuggestion = suggestion;
}
function setChip(id, kind, text, hide = false) {
  const chip = $(id);
  chip.hidden = hide || !text;
  chip.className = `dot-chip ${kind}`;
  chip.querySelector("span").textContent = text;
}

// ------------------------------------------------------------------ parcours
function heroTarget(state) {
  const list = mainProcedures();
  const statuses = state.parcours || {};
  const suggested = list.find((p) => p.id === state.suggestion);
  const allDone = list.length > 0 && list.every((p) => statuses[p.id] === "fait");
  return { procedure: suggested || list[0], allDone, connected: Boolean(state.simulateur.etat_disponible) };
}
function renderJourney(state) {
  const statuses = state.parcours || {};
  const key = JSON.stringify([lang, $("profil").value, statuses, state.suggestion, state.simulateur.etat_disponible]);
  if (key === journeyKey) return;
  journeyKey = key;

  const { procedure, allDone, connected } = heroTarget(state);
  $("hero").hidden = !procedure;
  if (procedure) {
    $("hero-eyebrow").textContent = allDone ? t("hero_all_done") : t("next_up");
    $("hero-titre").textContent = pick(procedure.titre);
    $("hero-description").textContent = connected ? pick(procedure.description) : t("hero_offline_sub");
    $("hero-demarrer").dataset.procedure = procedure.id;
    const other = (phaseGroups().find((g) => g.some((p) => p.id === procedure.id)) || []).find((p) => p.id !== procedure.id);
    $("hero-variante").hidden = !other;
    if (other) {
      $("hero-variante").textContent = t("other_variant", { name: pick(other.variante) });
      $("hero-variante").onclick = () => startProcedure(other.id);
    }
  }

  $("parcours").replaceChildren(...phaseGroups().map((group, index) => journeyItem(group, statuses[group[0].id] || "a_faire", index + 1, state.suggestion)));
  const demos = procedures().filter((p) => p.demonstration);
  $("demo-bloc").hidden = demos.length === 0;
  $("demo-liste").replaceChildren(...demos.map((p) => journeyItem([p], "a_faire", "·")));
}
function journeyItem(group, status, number, suggestion) {
  // Une ligne par phase ; s'il y a des variantes (APU / GPU), un bouton par variante.
  const main = group.find((p) => p.id === suggestion) || group[0];
  const item = el("li", `journey-item ${status}`);
  const node = el("span", "journey-node", status === "fait" ? "✓" : String(number));
  const body = el("div", "journey-body");
  body.append(el("h3", "", pick(main.titre)));
  const names = group.map((p) => pick(p.variante)).filter(Boolean).join(t("variants_or"));
  body.append(el("p", "", group.length > 1 ? t("variants_hint", { names }) : t("steps_count", { n: main.etapes })));
  const side = el("div", "journey-side");
  if (!main.demonstration) side.append(el("span", `tag ${status}`, t(`tag_${status}`)));
  const buttons = el("div", "variants");
  group.forEach((p) => {
    const primary = status === "propose" && p.id === main.id;
    const label = group.length > 1 ? `${pick(p.variante)} · ${t("steps_count", { n: p.etapes })}` : t("start_short");
    const button = el("button", primary ? "btn btn-primary btn-sm" : "btn btn-soft btn-sm", label);
    button.type = "button";
    button.addEventListener("click", () => startProcedure(p.id));
    buttons.append(button);
  });
  side.append(buttons);
  item.append(node, body, side);
  return item;
}
function startProcedure(procedureId) {
  if (!procedureId) return;
  api("/api/guide/demarrer", { profil: $("profil").value, procedure: procedureId, langue: lang }).catch(() => {});
}

// ------------------------------------------------------------------ étape en cours
function nextProcedure(guide) {
  // Procédure suivante = première de la phase suivante (on saute l'autre variante de la même phase).
  const list = (profiles.find((p) => p.id === guide.profil)?.procedures || []).filter((p) => !p.demonstration);
  const index = list.findIndex((p) => p.id === guide.procedure);
  if (index < 0) return undefined;
  const phase = list[index].phase;
  return list.slice(index + 1).find((p) => phase === undefined || phase === null || p.phase !== phase);
}
function renderStep(guide) {
  const step = guide.etape;
  const groups = phaseGroups();
  const phase = groups.findIndex((g) => g.some((p) => p.id === guide.procedure));
  $("compteur").textContent = t("step_of", { n: guide.index + 1, total: guide.total });
  $("phase-titre").textContent = guide.titre_procedure + (phase >= 0 ? ` · ${t("phase_of", { n: phase + 1, total: groups.length })}` : "");

  const segments = $("segments");
  if (segments.childElementCount !== guide.total) {
    segments.replaceChildren(...Array.from({ length: guide.total }, () => el("span")));
  }
  [...segments.children].forEach((bar, i) => {
    const done = i < guide.index || (i === guide.index && (step.validee || guide.termine));
    bar.className = done ? "done" : i === guide.index ? "current" : "";
  });

  $("etape-commande").hidden = !step.commande;
  $("etape-commande").textContent = step.commande;
  $("etape-titre").textContent = step.titre;
  $("etape-cible").hidden = !step.cible;
  $("etape-cible").querySelector("strong").textContent = step.cible || "";
  $("etape-explication").textContent = step.explication;
  const following = nextProcedure(guide);
  const status = $("etape-statut");
  if (guide.termine) status.textContent = following ? t("finished_next", { name: pick(following.titre) }) : t("finished");
  else status.textContent = step.validee ? t("validated") : step.manuelle ? t("manual") : t("waiting");
  status.className = "step-status " + (guide.termine || step.validee ? "ok" : step.manuelle ? "manual" : "");
  $("carte-etape").classList.toggle("validee", Boolean(guide.termine || step.validee));

  $("precedente").disabled = guide.index === 0;
  $("suivante").disabled = guide.index + 1 >= guide.total;
  // « Fait » n'existe que pour les étapes manuelles : les autres attendent l'état de l'avion.
  $("fait").disabled = guide.termine || step.validee || !step.manuelle;
  const chain = guide.termine && following;
  $("enchainer").hidden = !chain;
  $("fait").hidden = $("suivante").hidden = Boolean(chain);
  $("faire").hidden = Boolean(chain) || !step.faisable;
  $("faire").disabled = step.action === "en_cours";
  $("auto").checked = Boolean(guide.auto);
  const note = $("action-note");
  const actionText = { en_cours: t("do_running"), fait: t("do_done"), echec: t("do_failed") }[step.action] || "";
  note.hidden = !actionText;
  note.textContent = actionText;
  note.className = "action-note" + (step.action === "echec" ? " error" : "");

  const alerts = guide.alertes || [];
  $("alertes").hidden = alerts.length === 0;
  $("alertes").replaceChildren(...alerts.map((message) => el("p", "", message)));
  renderWait(alerts.length ? null : guide.attente);

  const upcoming = guide.termine ? [] : (guide.suivantes || []);
  $("a-suivre").hidden = upcoming.length === 0;
  $("suivantes").replaceChildren(...upcoming.map((title) => el("li", "", title)));
}
// Compte à rebours d'une attente (démarrage de l'APU…) : la durée vient de l'avion lui-même une fois apprise.
function renderWait(wait) {
  const box = $("attente");
  box.hidden = !wait;
  if (!wait) return;
  $("attente-libelle").textContent = wait.libelle;
  const pending = wait.ecoule === null;
  box.classList.toggle("pending", pending);
  if (pending) {
    $("attente-temps").textContent = clock(wait.duree);
    $("attente-conseil").textContent = wait.debut || t("wait_pending");
    return;
  }
  const left = wait.duree - wait.ecoule;
  $("attente-temps").textContent = left > 0 ? `≈ ${clock(left)}` : t("wait_almost");
  $("attente-barre").style.width = `${Math.min(100, (wait.ecoule / wait.duree) * 100)}%`;
  $("attente-conseil").textContent = left > 0 ? t("wait_running") : t("wait_overdue");
}
function clock(seconds) {
  const s = Math.max(0, Math.round(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

async function doStep() {
  const { ok, data } = await post("/api/guide/faire", {});
  if (ok && data && data.ok === false) {
    const key = `do_why_${data.raison}`;
    toast(window.FLOWXAV_I18N.fr[key] ? t(key) : t("do_why_au_pilote"));
  }
}

async function chainNext() {
  const guide = (await api("/api/etat")).guide;
  const following = guide.actif && nextProcedure(guide);
  if (following) startProcedure(following.id);
}

// ------------------------------------------------------------------ palette « Montre-moi »
function openPalette() {
  $("palette").hidden = false;
  $("filtre").value = "";
  paletteIndex = 0;
  renderPalette();
  setTimeout(() => $("filtre").focus(), 0);
}
function closePalette() { $("palette").hidden = true; }
function paletteMatches() {
  const words = $("filtre").value.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return controls.filter((c) => {
    const haystack = `${c.libelle} ${c.id} ${c.vue}`.toLowerCase();
    return words.every((word) => haystack.includes(word));
  }).slice(0, 60);
}
function renderPalette() {
  const matches = paletteMatches();
  paletteIndex = Math.min(paletteIndex, Math.max(matches.length - 1, 0));
  const list = $("commandes");
  if (!matches.length) {
    list.replaceChildren(el("li", "empty", t("palette_empty")));
    return;
  }
  list.replaceChildren(...matches.map((c, i) => {
    const item = el("li", i === paletteIndex ? "active" : "");
    item.dataset.id = c.id;
    item.setAttribute("role", "option");
    item.append(el("span", "", c.libelle), el("small", "", c.vue.replaceAll("_", " ")));
    item.addEventListener("click", () => showControl(c.id));
    return item;
  }));
  list.children[paletteIndex]?.scrollIntoView({ block: "nearest" });
}
function showControl(id) {
  closePalette();
  api("/api/montrer", { profil: $("profil").value, commande: id }).catch(() => {});
}

// ------------------------------------------------------------------ état de départ
function renderResetButtons() {
  const states = currentProfile()?.depart_etats || [];
  $("dp-etats").replaceChildren(...states.map((s) => {
    const button = el("button", "btn btn-danger-soft grow dp-reset", t("dp_reset_to", { name: pick(s.libelle) }));
    button.type = "button";
    button.addEventListener("click", () => {
      if (window.confirm(t("dp_confirm_state", { name: pick(s.libelle) }))) departAction("/api/depart/reinitialiser", "dp_loading", s.id);
    });
    return button;
  }));
}

async function departAction(path, busyKey, state) {
  note("dp-statut", t(busyKey), "busy");
  $("dp-liste").hidden = true;
  const buttons = [$("dp-check"), ...document.querySelectorAll(".dp-reset")];
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const { ok, data, status } = await post(path, { profil: $("profil").value, etat: state || null });
    if (!ok) {
      note("dp-statut", errorText("dp", data.detail || {}, status), "error");
      setBadge("");
      return;
    }
    const checks = data.verifications || [];
    const failed = checks.filter((c) => !c.ok);
    note("dp-statut", failed.length ? t("dp_ko", { n: failed.length }) : t("dp_ok"), failed.length ? "error" : "ok");
    setBadge(failed.length ? "ko" : "ok", failed.length);
    $("dp-liste").replaceChildren(...failed.map((c) => {
      const item = el("li", "ko", pick(c.libelle));
      item.append(el("small", "", pick(c.conseil)));
      return item;
    }));
    $("dp-liste").hidden = failed.length === 0;
  } catch {
    note("dp-statut", t("dp_err_autre", { detail: "FlowXav" }), "error");
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}
function setBadge(kind, count = 0) {
  const badge = $("dp-badge");
  badge.hidden = !kind;
  badge.className = `badge ${kind}`;
  badge.textContent = kind === "ok" ? t("dp_badge_ok") : kind === "ko" ? t("dp_badge_ko", { n: count }) : "";
}

// ------------------------------------------------------------------ SimBrief (sur clic uniquement)
function savedFlight() {
  try { return JSON.parse(stored(STORE_SIMBRIEF) || "null"); } catch { return null; }
}
function renderSimbrief(flight, forceInput = false) {
  const hasId = Boolean($("sb-identifiant").value.trim());
  $("sb-vol").hidden = !flight;
  $("sb-saisie").hidden = Boolean(flight && hasId && !forceInput);
  $("sb-modifier").hidden = !$("sb-saisie").hidden;
  if (!flight) return;
  $("sb-orig").textContent = flight.origine;
  $("sb-dest").textContent = flight.destination;
  $("sb-rwy-dep").textContent = flight.piste_depart ? `RWY ${flight.piste_depart}` : "";
  $("sb-rwy-arr").textContent = flight.piste_arrivee ? `RWY ${flight.piste_arrivee}` : "";
  $("sb-meta").textContent = [flight.vol, flight.sid].filter(Boolean).join(" · ");
  const withUnits = (value) => (value ? `${value} ${(flight.unites || "").toLowerCase()}` : "");
  const cells = [
    ["sb_level", flight.niveau, flight.altitude_croisiere], ["sb_ci", flight.cost_index],
    ["sb_zfw", withUnits(flight.zfw), flight.zfw], ["sb_reserve", withUnits(flight.reserve), flight.reserve],
    ["sb_trans", flight.altitude_transition], ["sb_block", withUnits(flight.carburant_bloc), flight.carburant_bloc],
  ].filter(([, shown]) => shown);
  $("sb-ofp").replaceChildren(...cells.map(([key, shown, raw]) => {
    const cell = el("div");
    cell.title = t("sb_copy_hint");
    cell.append(el("dt", "", t(key)), el("dd", "", shown));
    cell.addEventListener("click", () => copy(String(raw ?? shown)));
    return cell;
  }));
  $("sb-route").textContent = flight.route || "";
}
async function copy(value) {
  try { await navigator.clipboard.writeText(value); } catch {
    const area = el("textarea"); area.value = value; document.body.append(area); area.select();
    document.execCommand("copy"); area.remove();
  }
  toast(t("copied", { value }));
}
async function importSimbrief() {
  note("sb-statut", t("sb_loading"), "busy");
  $("sb-importer").disabled = true;
  try {
    const { ok, data, status } = await post("/api/simbrief/importer", { profil: $("profil").value, identifiant: $("sb-identifiant").value });
    if (!ok) {
      note("sb-statut", errorText("sb", data.detail || {}, status), "error");
      return;
    }
    note("sb-statut", t("sb_ok", { file: data.fichier, fmc: data.fmc }) + (data.vents ? " " + t("sb_wind", { file: data.vents }) : ""), "ok");
    store(STORE_SIMBRIEF, JSON.stringify(data));
    renderSimbrief(data);
  } catch {
    note("sb-statut", t("sb_err_reseau"), "error");
  } finally {
    $("sb-importer").disabled = false;
  }
}

// ------------------------------------------------------------------ clavier
function onKey(event) {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    $("palette").hidden ? openPalette() : closePalette();
    return;
  }
  if (!$("palette").hidden) {
    const count = paletteMatches().length;
    if (event.key === "Escape") closePalette();
    else if (event.key === "ArrowDown") { paletteIndex = Math.min(paletteIndex + 1, count - 1); renderPalette(); event.preventDefault(); }
    else if (event.key === "ArrowUp") { paletteIndex = Math.max(paletteIndex - 1, 0); renderPalette(); event.preventDefault(); }
    else if (event.key === "Enter") { const match = paletteMatches()[paletteIndex]; if (match) showControl(match.id); }
    return;
  }
  if (typing() || event.altKey || event.ctrlKey) return;
  // Un bouton qui a le focus gère lui-même Entrée et Espace : pas de double action.
  if ((event.key === " " || event.key === "Enter") && document.activeElement?.tagName === "BUTTON") return;
  const guide = lastState?.guide;
  if (event.key === "/") { event.preventDefault(); openPalette(); return; }
  if (!guide?.actif) {
    if (event.key === "Enter" && $("hero-demarrer").dataset.procedure) startProcedure($("hero-demarrer").dataset.procedure);
    return;
  }
  if (event.key === " " || event.key === "Enter") {
    event.preventDefault();
    if (!$("enchainer").hidden) chainNext();
    else if (!$("fait").disabled) api("/api/guide/fait", {});
  } else if (event.key === "+" || event.key === "=") api("/api/camera/zoom", { pas: 2 });
  else if (event.key === "-") api("/api/camera/zoom", { pas: -2 });
  else if ((event.key === "f" || event.key === "F") && !$("faire").hidden && !$("faire").disabled) doStep();
  else if (event.key === "0") api("/api/camera/recentrer", {});
  else if (event.key === "ArrowRight" && !$("suivante").disabled) api("/api/guide/suivante", {});
  else if (event.key === "ArrowLeft" && !$("precedente").disabled) api("/api/guide/precedente", {});
}

// ------------------------------------------------------------------ démarrage
async function poll() {
  try {
    const response = await fetch("/api/etat");
    if (response.status === 401) showPairing();          // second écran pas encore appairé
    else if (response.ok) {
      const state = await response.json();
      document.body.classList.toggle("distant", Boolean(state.distant));
      render(state);
    }
  } catch { /* service en cours d'arrêt */ }
  setTimeout(poll, 400);
}

// ------------------------------------------------------------------ contrat de licence de l'éditeur
let licenceAsked = "";
async function checkLicence() {
  const profile = currentProfile();
  if (!profile || document.body.classList.contains("distant") || licenceAsked === profile.id) return;
  let status;
  try { status = await api(`/api/licence/${profile.id}`); } catch { return; }
  if (!status.requise || status.acceptee) return;
  licenceAsked = profile.id;
  $("licence-editeur").textContent = status.editeur;
  $("licence-texte").hidden = !status.texte;
  $("licence-texte").textContent = status.texte || "";
  $("licence-absent").hidden = Boolean(status.trouve);
  $("licence-case").checked = false;
  $("licence-accepter").disabled = true;
  $("licence").hidden = false;
}
async function acceptLicence() {
  const { ok } = await post("/api/licence/accepter", { profil: $("profil").value });
  if (ok) { $("licence").hidden = true; toast(t("lic_done")); }
}

// ------------------------------------------------------------------ second écran
function showPairing() {
  if (!$("appairage").hidden) return;
  $("appairage").hidden = false;
  setTimeout(() => $("appairage-code").focus(), 0);
}
async function pair(event) {
  event.preventDefault();
  const { ok } = await post("/api/appairage", { code: $("appairage-code").value.trim() });
  if (ok) window.location.reload();
  else {
    $("appairage-erreur").hidden = false;
    $("appairage-erreur").textContent = t("pair_refused");
    $("appairage-code").value = "";
  }
}
function renderScreen(status) {
  const active = Boolean(status && status.actif);
  $("sc-bouton").textContent = active ? t("sc_off") : t("sc_on");
  $("sc-info").hidden = !active;
  $("sc-badge").hidden = !active;
  if (!active) return;
  $("sc-url").textContent = status.url;
  $("sc-code").textContent = status.code;
  $("sc-badge").textContent = t("sc_devices", { n: status.appareils || 0 });
}
async function refreshScreen() {
  if (document.body.classList.contains("distant")) return;
  try { renderScreen(await api("/api/ecran")); } catch { /* service local indisponible */ }
}
async function toggleScreen() {
  const active = !$("sc-info").hidden;
  $("sc-bouton").disabled = true;
  const { ok, data, status } = await post("/api/ecran", { actif: !active });
  $("sc-bouton").disabled = false;
  if (!ok) {
    const code = (data.detail || {}).code;
    note("sc-statut", window.FLOWXAV_I18N.fr[`sc_err_${code}`] ? t(`sc_err_${code}`) : t("dp_err_autre", { detail: code || status }), "error");
    return;
  }
  note("sc-statut", "");
  renderScreen(data);
}

async function init() {
  lang = stored("flowxav.langue") || (navigator.language || "fr").slice(0, 2);
  if (!window.FLOWXAV_I18N[lang]) lang = "en";
  $("langue").value = lang;
  $("langue").addEventListener("change", (event) => {
    lang = event.target.value;
    store("flowxav.langue", lang);
    translate();
  });
  try {
    profiles = await api("/api/profils");
  } catch {
    // Second écran pas encore appairé : seul l'écran d'appairage est utile.
    translate();
    $("appairage-form").addEventListener("submit", pair);
    showPairing();
    return;
  }
  fillProfiles();
  try { $("sb-identifiant").value = (await api("/api/simbrief")).identifiant || ""; } catch { /* réglages indisponibles */ }
  translate();

  $("profil").addEventListener("change", () => { journeyKey = ""; loadControls(); renderResetButtons(); checkLicence(); if (lastState) render(lastState); });
  $("hero-demarrer").addEventListener("click", () => startProcedure($("hero-demarrer").dataset.procedure));
  $("fait").addEventListener("click", () => api("/api/guide/fait", {}));
  $("suivante").addEventListener("click", () => api("/api/guide/suivante", {}));
  $("precedente").addEventListener("click", () => api("/api/guide/precedente", {}));
  $("arreter").addEventListener("click", () => { journeyKey = ""; api("/api/guide/arreter", {}); });
  $("enchainer").addEventListener("click", chainNext);
  $("faire").addEventListener("click", doStep);
  $("auto").addEventListener("change", (event) => api("/api/guide/auto", { actif: event.target.checked }));
  $("zoom-plus").addEventListener("click", () => api("/api/camera/zoom", { pas: 2 }));
  $("zoom-moins").addEventListener("click", () => api("/api/camera/zoom", { pas: -2 }));
  $("zoom-centre").addEventListener("click", () => api("/api/camera/recentrer", {}));

  $("ouvrir-palette").addEventListener("click", openPalette);
  $("palette").addEventListener("click", (event) => { if (event.target === $("palette")) closePalette(); });
  $("filtre").addEventListener("input", () => { paletteIndex = 0; renderPalette(); });

  $("dp-check").addEventListener("click", () => departAction("/api/depart/verifier", "dp_checking"));
  $("sb-importer").addEventListener("click", importSimbrief);
  $("sb-identifiant").addEventListener("keydown", (event) => { if (event.key === "Enter") importSimbrief(); });
  $("sb-modifier").addEventListener("click", () => { renderSimbrief(savedFlight(), true); $("sb-identifiant").focus(); });

  document.addEventListener("keydown", onKey);
  $("appairage-form").addEventListener("submit", pair);
  $("sc-bouton").addEventListener("click", toggleScreen);
  $("licence-case").addEventListener("change", (event) => { $("licence-accepter").disabled = !event.target.checked; });
  $("licence-accepter").addEventListener("click", acceptLicence);
  $("licence-plus-tard").addEventListener("click", () => { $("licence").hidden = true; toast(t("lic_later_note")); });
  checkLicence();
  refreshScreen();
  setInterval(refreshScreen, 3000);
  poll();
}

init();
