"use strict";

const $ = (id) => document.getElementById(id);
let lang = "fr";
let profiles = [];
let controls = [];

function storedLanguage() {
  try { return localStorage.getItem("flowxav.langue"); } catch { return null; }
}
function t(key, values = {}) {
  const table = window.FLOWXAV_I18N[lang] || window.FLOWXAV_I18N.en;
  let text = table[key] ?? window.FLOWXAV_I18N.en[key] ?? key;
  for (const [name, value] of Object.entries(values)) text = text.replace(`{${name}}`, value);
  return text;
}
function translate() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
  fillProcedures();
}
function pick(entry) {
  return typeof entry === "object" && entry ? (entry[lang] || entry.en || entry.fr || "") : (entry || "");
}

async function api(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function fillProfiles() {
  $("profil").innerHTML = profiles.map((p) => `<option value="${p.id}">${p.constructeur} — ${p.nom}</option>`).join("");
  fillProcedures();
  loadControls();
}
function currentProfile() { return profiles.find((p) => p.id === $("profil").value); }
function fillProcedures() {
  const profile = currentProfile();
  $("procedure").innerHTML = (profile?.procedures || [])
    .map((p) => `<option value="${p.id}">${pick(p.titre)} (${p.etapes} ${t("steps")})</option>`).join("");
}
async function loadControls() {
  const profile = currentProfile();
  controls = profile ? await api(`/api/profils/${profile.id}/commandes`) : [];
  renderControls();
}
function renderControls() {
  const filter = $("filtre").value.trim().toLowerCase();
  const shown = controls.filter((c) => !filter || c.libelle.toLowerCase().includes(filter) || c.id.toLowerCase().includes(filter));
  $("commandes").innerHTML = shown.slice(0, 200)
    .map((c) => `<li data-id="${c.id}"><span>${c.libelle}</span><small>${c.vue.replaceAll("_", " ")}</small></li>`).join("");
}

function render(state) {
  const sim = state.simulateur;
  const pillSim = $("pill-sim");
  pillSim.textContent = sim.connecte ? t("sim_on") : t("sim_off");
  pillSim.className = "pill " + (sim.connecte ? "ok" : "");
  $("pill-avion").textContent = sim.connecte ? (sim.profil ? sim.avion : `${sim.avion || ""} — ${t("no_profile")}`) : "";
  $("pill-avion").className = "pill " + (sim.profil ? "ok" : "warn");
  $("pill-etat").textContent = sim.profil ? (sim.etat_disponible ? t("state_ok") : t("state_missing")) : "";
  $("pill-etat").className = "pill " + (sim.etat_disponible ? "ok" : "warn");
  $("message-etat").textContent = sim.message_etat || "";
  if (sim.profil && $("profil").value !== sim.profil && !state.guide.actif) {
    $("profil").value = sim.profil;
    fillProcedures();
    loadControls();
  }

  const guide = state.guide;
  $("etape").hidden = !guide.actif;
  $("choix").hidden = guide.actif;
  if (!guide.actif) return;
  const step = guide.etape;
  $("progression").style.width = `${((guide.index + (step.validee ? 1 : 0)) / guide.total) * 100}%`;
  $("compteur").textContent = `${guide.titre_procedure} — ${t("step_of", { n: guide.index + 1, total: guide.total })}`;
  $("etape-titre").textContent = step.titre;
  $("etape-commande").textContent = step.commande;
  $("etape-explication").textContent = step.explication;
  const status = $("etape-statut");
  status.textContent = guide.termine ? t("finished") : step.validee ? t("validated") : step.manuelle ? t("manual") : t("waiting");
  status.className = "status " + (guide.termine || step.validee ? "ok" : "");
  $("precedente").disabled = guide.index === 0;
  $("suivante").disabled = guide.index + 1 >= guide.total;
  $("fait").disabled = guide.termine || step.validee;
}

async function poll() {
  try { render(await api("/api/etat")); } catch { /* service en cours d'arrêt */ }
  setTimeout(poll, 400);
}

async function init() {
  lang = storedLanguage() || (navigator.language || "fr").slice(0, 2);
  if (!window.FLOWXAV_I18N[lang]) lang = "en";
  $("langue").value = lang;
  $("langue").addEventListener("change", (event) => {
    lang = event.target.value;
    try { localStorage.setItem("flowxav.langue", lang); } catch { /* stockage indisponible */ }
    translate();
  });
  profiles = await api("/api/profils");
  fillProfiles();
  translate();
  $("profil").addEventListener("change", () => { fillProcedures(); loadControls(); });
  $("filtre").addEventListener("input", renderControls);
  $("commandes").addEventListener("click", (event) => {
    const item = event.target.closest("li");
    if (item) api("/api/montrer", { profil: $("profil").value, commande: item.dataset.id });
  });
  $("demarrer").addEventListener("click", () =>
    api("/api/guide/demarrer", { profil: $("profil").value, procedure: $("procedure").value, langue: lang }));
  $("fait").addEventListener("click", () => api("/api/guide/fait", {}));
  $("suivante").addEventListener("click", () => api("/api/guide/suivante", {}));
  $("precedente").addEventListener("click", () => api("/api/guide/precedente", {}));
  $("arreter").addEventListener("click", () => api("/api/guide/arreter", {}));
  poll();
}

init();
