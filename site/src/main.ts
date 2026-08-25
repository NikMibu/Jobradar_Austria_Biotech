import maplibregl, { Map as MlMap, Marker } from "maplibre-gl";
import type { GeoJSONSource, MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

// ---------- Typen (Spiegel des Python-Exports) ----------
interface Job {
  id: number;
  title: string;
  company: string | null;
  source: string;
  url: string | null;
  alt_urls: string[];
  first_seen: string;
  last_seen: string;
  location_text: string | null;
  lat: number | null;
  lon: number | null;
  site_label: string | null;
  extraction: Record<string, unknown>;
  hard_pass: boolean | null;
  hard_reasons: { reasons: string[]; flags: string[] } | null;
  fit_score: number | null;
  fit_reasons: string[] | null;
  gaps: string[] | null;
  angle: string | null;
  travel: Record<string, { minutes: number | null; transfers: number | null }>;
}
interface Company {
  name: string;
  website: string | null;
  career_url: string | null;
  initiative_score: number;
  summary: string;
  sites: { label: string; lat: number | null; lon: number | null }[];
}
interface Meta {
  generated_at: string;
  anchors: { id: string; label: string; max_minutes: number }[];
  counts: Record<string, number>;
}

const ROLE_FAMILIES = [
  "bioinformatics", "data_science", "csv_qa_validation", "lab_analytics",
  "downstream_process", "mass_spec", "data_steward", "scientific_software",
  "wet_lab_rnd", "other",
];

// ---------- Daten laden (Fallback: Demo-Modus) ----------
async function loadData(): Promise<{ jobs: Job[]; companies: Company[]; meta: Meta; prefix: string; demo: boolean }> {
  for (const [prefix, demo] of [["data", false], ["data/demo", true]] as const) {
    try {
      const [jobs, companies, meta] = await Promise.all(
        ["jobs.json", "companies.json", "meta.json"].map((f) =>
          fetch(`./${prefix}/${f}`).then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        )
      );
      return { jobs, companies, meta, prefix, demo };
    } catch {
      /* nächster Prefix */
    }
  }
  throw new Error("Keine Daten gefunden — Pipeline oder Demo-Daten fehlen.");
}

// ---------- URL-State, Merkliste, Overrides ----------
type Filters = {
  segment: string; sort: string; role: string; source: string; contract: string;
  score: string; days: string; anchor: string; minutes: string;
  initiative: boolean; saved: boolean; job: string;
};
function readFilters(): Filters {
  const p = new URLSearchParams(location.search);
  return {
    segment: p.get("seg") ?? "treffer", sort: p.get("sort") ?? "score",
    role: p.get("role") ?? "", source: p.get("source") ?? "", contract: p.get("contract") ?? "",
    score: p.get("score") ?? "", days: p.get("days") ?? "", anchor: p.get("anchor") ?? "",
    minutes: p.get("minutes") ?? "", initiative: p.get("init") === "1", saved: p.get("saved") === "1",
    job: p.get("job") ?? "",
  };
}
function writeFilters(f: Filters) {
  const p = new URLSearchParams();
  if (f.segment !== "treffer") p.set("seg", f.segment);
  if (f.sort !== "score") p.set("sort", f.sort);
  if (f.role) p.set("role", f.role);
  if (f.source) p.set("source", f.source);
  if (f.contract) p.set("contract", f.contract);
  if (f.score) p.set("score", f.score);
  if (f.days) p.set("days", f.days);
  if (f.anchor) p.set("anchor", f.anchor);
  if (f.minutes) p.set("minutes", f.minutes);
  if (f.initiative) p.set("init", "1");
  if (f.saved) p.set("saved", "1");
  if (f.job) p.set("job", f.job);
  history.replaceState(null, "", p.size ? `?${p}` : location.pathname);
}

const SAVED_KEY = "heimspiel.saved";
const OVERRIDE_KEY = "heimspiel.roleOverrides"; // {jobId: korrigierte role_family}
function lsGet<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? "") as T; } catch { return fallback; }
}
function lsSet(key: string, value: unknown) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
}
const getSaved = () => new Set<number>(lsGet<number[]>(SAVED_KEY, []));
function toggleSaved(id: number) {
  const s = getSaved();
  s.has(id) ? s.delete(id) : s.add(id);
  lsSet(SAVED_KEY, [...s]);
}
const getOverrides = () => lsGet<Record<string, string>>(OVERRIDE_KEY, {});
function setOverride(id: number, family: string | null) {
  const o = getOverrides();
  if (family) o[String(id)] = family; else delete o[String(id)];
  lsSet(OVERRIDE_KEY, o);
}

// ---------- Farben & Helfer ----------
function scoreColor(job: Job): string {
  if (effectiveSegment(job) === "raus") return "#9ca3af";
  const s = job.fit_score;
  if (s == null) return "#60a5fa";
  if (s >= 80) return "#16a34a";
  if (s >= 60) return "#84cc16";
  if (s >= 40) return "#f59e0b";
  return "#ef4444";
}
function travelColor(minutes: number | null): string {
  if (minutes == null) return "#9ca3af";
  if (minutes <= 45) return "#16a34a";
  if (minutes <= 60) return "#84cc16";
  if (minutes <= 90) return "#f59e0b";
  return "#ef4444";
}
function bestTravel(job: Job, anchor: string): number | null {
  const entries = anchor ? [job.travel[anchor]] : Object.values(job.travel);
  const mins = entries.filter((t) => t && t.minutes != null).map((t) => t!.minutes!) as number[];
  return mins.length ? Math.min(...mins) : null;
}
// "treffer" | "grenzfall" | "raus" — Overrides holen role_family-Ablehnungen zurück
function effectiveSegment(job: Job): string {
  const overridden = getOverrides()[String(job.id)] != null;
  if (job.hard_pass) return job.hard_reasons?.flags.length ? "grenzfall" : "treffer";
  if (overridden && job.hard_reasons?.reasons.every((r) => r.startsWith("Rollenfamilie")))
    return "treffer";
  return "raus";
}
const daysSince = (iso: string) => Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 864e5));
const fmt = (v: unknown): string =>
  v == null || v === "" ? "–" : Array.isArray(v) ? (v.length ? v.join(", ") : "–") : String(v);
const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const hostOf = (u: string): string => {
  try { return new URL(u, location.href).hostname; } catch { return u.slice(0, 40); }
};
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

// ---------- App ----------
let coordFilter: { lat: number; lon: number } | null = null;
let noLocationFilter = false;

async function main() {
  const { jobs, companies, meta, prefix, demo } = await loadData();
  if (demo) $("demo-badge").hidden = false;
  const jobById = new Map(jobs.map((j) => [j.id, j]));

  const map = new maplibregl.Map({
    container: "map",
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: [14.5, 47.8],
    zoom: 6.3,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl());

  // Filter-Optionen befüllen
  const fill = (id: string, values: (string | null)[]) => {
    const sel = $<HTMLSelectElement>(id);
    [...new Set(values.filter(Boolean) as string[])].sort().forEach((v) => {
      const o = document.createElement("option");
      o.value = o.textContent = v;
      sel.appendChild(o);
    });
  };
  fill("f-role", jobs.map((j) => String(j.extraction.role_family ?? "")));
  fill("f-source", jobs.map((j) => j.source));
  fill("f-contract", jobs.map((j) => String(j.extraction.contract_type ?? "")));
  const anchorSel = $<HTMLSelectElement>("f-anchor");
  meta.anchors.forEach((a) => {
    const o = document.createElement("option");
    o.value = a.id;
    o.textContent = `Anker: ${a.label}`;
    anchorSel.appendChild(o);
  });

  const f0 = readFilters();
  $<HTMLSelectElement>("f-segment").value = f0.segment;
  $<HTMLSelectElement>("f-sort").value = f0.sort;
  $<HTMLSelectElement>("f-role").value = f0.role;
  $<HTMLSelectElement>("f-source").value = f0.source;
  $<HTMLSelectElement>("f-contract").value = f0.contract;
  $<HTMLInputElement>("f-score").value = f0.score;
  $<HTMLInputElement>("f-days").value = f0.days;
  anchorSel.value = f0.anchor;
  $<HTMLInputElement>("f-minutes").value = f0.minutes;
  $<HTMLInputElement>("f-initiative").checked = f0.initiative;
  $<HTMLInputElement>("f-saved").checked = f0.saved;

  function currentFilters(): Filters {
    return {
      segment: $<HTMLSelectElement>("f-segment").value,
      sort: $<HTMLSelectElement>("f-sort").value,
      role: $<HTMLSelectElement>("f-role").value,
      source: $<HTMLSelectElement>("f-source").value,
      contract: $<HTMLSelectElement>("f-contract").value,
      score: $<HTMLInputElement>("f-score").value,
      days: $<HTMLInputElement>("f-days").value,
      anchor: anchorSel.value,
      minutes: $<HTMLInputElement>("f-minutes").value,
      initiative: $<HTMLInputElement>("f-initiative").checked,
      saved: $<HTMLInputElement>("f-saved").checked,
      job: new URLSearchParams(location.search).get("job") ?? "",
    };
  }

  function applyFilters(f: Filters): Job[] {
    const saved = getSaved();
    const cutoff = f.days ? Date.now() - Number(f.days) * 864e5 : null;
    let out = jobs.filter((j) => {
      if (f.segment === "treffer" && effectiveSegment(j) === "raus") return false;
      if (f.segment === "grenzfall" && effectiveSegment(j) !== "grenzfall") return false;
      if (f.saved && !saved.has(j.id)) return false;
      if (f.role && j.extraction.role_family !== f.role) return false;
      if (f.source && j.source !== f.source) return false;
      if (f.contract && j.extraction.contract_type !== f.contract) return false;
      if (f.score && (j.fit_score == null || j.fit_score < Number(f.score))) return false;
      if (cutoff && Date.parse(j.first_seen) < cutoff) return false;
      if (f.minutes) {
        const t = bestTravel(j, f.anchor);
        if (t == null || t > Number(f.minutes)) return false;
      }
      if (coordFilter && (j.lat !== coordFilter.lat || j.lon !== coordFilter.lon)) return false;
      if (noLocationFilter && j.lat != null) return false;
      return true;
    });
    const sorters: Record<string, (a: Job, b: Job) => number> = {
      score: (a, b) => (b.fit_score ?? -1) - (a.fit_score ?? -1),
      travel: (a, b) => (bestTravel(a, f.anchor) ?? 9e9) - (bestTravel(b, f.anchor) ?? 9e9),
      new: (a, b) => Date.parse(b.first_seen) - Date.parse(a.first_seen),
    };
    out = out.sort(sorters[f.sort] ?? sorters.score);
    return out;
  }

  // ---------- Karte: GeoJSON-Cluster statt gestapelter Marker ----------
  let initiativeMarkers: Marker[] = [];
  let colorMode: "score" | "travel" = "score";

  function jobsGeojson(list: Job[], f: Filters): GeoJSON.FeatureCollection {
    return {
      type: "FeatureCollection",
      features: list
        .filter((j) => j.lat != null && j.lon != null)
        .map((j) => ({
          type: "Feature",
          geometry: { type: "Point", coordinates: [j.lon!, j.lat!] },
          properties: {
            id: j.id,
            color: colorMode === "score" ? scoreColor(j) : travelColor(bestTravel(j, f.anchor)),
          },
        })),
    };
  }

  map.on("load", () => {
    map.addSource("jobs", {
      type: "geojson",
      data: jobsGeojson([], readFilters()),
      cluster: true,
      clusterMaxZoom: 13,
      clusterRadius: 42,
    });
    map.addLayer({
      id: "clusters", type: "circle", source: "jobs", filter: ["has", "point_count"],
      paint: {
        "circle-color": "#3b82f6", "circle-opacity": 0.85,
        "circle-radius": ["step", ["get", "point_count"], 14, 10, 18, 50, 24],
      },
    });
    map.addLayer({
      id: "cluster-count", type: "symbol", source: "jobs", filter: ["has", "point_count"],
      layout: { "text-field": "{point_count_abbreviated}", "text-size": 12 },
      paint: { "text-color": "#ffffff" },
    });
    map.addLayer({
      id: "job-points", type: "circle", source: "jobs", filter: ["!", ["has", "point_count"]],
      paint: {
        "circle-color": ["get", "color"], "circle-radius": 8,
        "circle-stroke-width": 1.5, "circle-stroke-color": "#ffffff",
      },
    });
    map.on("click", "clusters", async (e: MapLayerMouseEvent) => {
      const feat = map.queryRenderedFeatures(e.point, { layers: ["clusters"] })[0];
      const src = map.getSource("jobs") as GeoJSONSource;
      const clusterId = feat.properties!.cluster_id as number;
      const zoom = await src.getClusterExpansionZoom(clusterId);
      const [lon, lat] = (feat.geometry as GeoJSON.Point).coordinates;
      if (zoom > map.getMaxZoom() - 0.5 || zoom > 15) {
        // gleiche Koordinate (z. B. 78× "Wien") — Liste auf diesen Punkt filtern
        coordFilter = { lat, lon };
        render();
      } else {
        map.easeTo({ center: [lon, lat], zoom });
      }
    });
    map.on("click", "job-points", (e: MapLayerMouseEvent) => {
      const feat = e.features?.[0];
      if (!feat) return;
      const [lon, lat] = (feat.geometry as GeoJSON.Point).coordinates;
      const siblings = applyFilters(currentFilters()).filter((j) => j.lat === lat && j.lon === lon);
      if (siblings.length > 1) {
        coordFilter = { lat, lon };
        render();
      } else {
        openDrawer(jobById.get(feat.properties!.id as number)!, meta);
      }
    });
    for (const id of ["clusters", "job-points"]) {
      map.on("mouseenter", id, () => (map.getCanvas().style.cursor = "pointer"));
      map.on("mouseleave", id, () => (map.getCanvas().style.cursor = ""));
    }
    addIsochroneLayers(map, meta, prefix);
    render();
  });

  // ---------- Karten (Liste) ----------
  function cardBadges(job: Job, f: Filters): string {
    const ex = job.extraction;
    const badges: string[] = [];
    const role = getOverrides()[String(job.id)] ?? String(ex.role_family ?? "?");
    badges.push(`<span class="chip chip-role" data-id="${job.id}" title="Klick: Rollenfamilie korrigieren">${esc(role)}${getOverrides()[String(job.id)] ? " ✎" : ""}</span>`);
    for (const a of meta.anchors) {
      const t = job.travel[a.id];
      if (t?.minutes != null && (!f.anchor || f.anchor === a.id)) {
        const ok = t.minutes <= a.max_minutes;
        badges.push(`<span class="chip" style="color:${ok ? "var(--ok)" : "var(--bad)"}">${esc(a.id)} ${t.minutes}′</span>`);
      }
    }
    const wm = String(ex.workplace_mode ?? "");
    if (wm === "hybrid" || wm === "remote") badges.push(`<span class="chip chip-alt">${wm}</span>`);
    if (ex.salary_min_eur_month) badges.push(`<span class="chip">≥ ${Number(ex.salary_min_eur_month).toLocaleString("de-AT")} €</span>`);
    const ct = String(ex.contract_type ?? "");
    if (ct && ct !== "permanent" && ct !== "unknown") badges.push(`<span class="chip chip-warn">${ct}</span>`);
    if (ex.application_deadline) badges.push(`<span class="chip chip-warn">bis ${esc(String(ex.application_deadline))}</span>`);
    const open = daysSince(job.first_seen);
    if (open > 0) badges.push(`<span class="chip chip-dim">${open} d offen</span>`);
    return badges.join("");
  }

  function render() {
    const f = currentFilters();
    writeFilters(f);
    const list = $("list");
    list.innerHTML = "";

    // Aktive Punkt-Filter als lösbare Chips
    const chipbar = $("chipbar");
    chipbar.innerHTML = "";
    if (coordFilter) {
      const sample = jobs.find((j) => j.lat === coordFilter!.lat && j.lon === coordFilter!.lon);
      const label = sample?.site_label ?? sample?.location_text ?? "Punkt";
      const chip = document.createElement("button");
      chip.className = "chip chip-filter";
      chip.textContent = `📍 ${label} ×`;
      chip.onclick = () => { coordFilter = null; render(); };
      chipbar.appendChild(chip);
    }
    const noLoc = document.createElement("button");
    const noLocCount = jobs.filter((j) => j.lat == null).length;
    noLoc.className = "chip chip-filter" + (noLocationFilter ? " on" : "");
    noLoc.textContent = `ohne Standort (${noLocCount})`;
    noLoc.onclick = () => { noLocationFilter = !noLocationFilter; render(); };
    chipbar.appendChild(noLoc);

    if (f.initiative) {
      $("meta-line").textContent = `${companies.length} Initiativ-Kandidaten · Stand ${new Date(meta.generated_at).toLocaleDateString("de-AT")}`;
      renderInitiative(list, map, companies);
      return;
    }
    for (const m of initiativeMarkers) m.remove();
    initiativeMarkers = [];

    const filtered = applyFilters(f);
    const treffer = jobs.filter((j) => effectiveSegment(j) !== "raus").length;
    $("meta-line").textContent =
      `${filtered.length} angezeigt · ${treffer} Treffer von ${jobs.length} · Stand ${new Date(meta.generated_at).toLocaleDateString("de-AT")}`;

    const saved = getSaved();
    for (const job of filtered) {
      const seg = effectiveSegment(job);
      const card = document.createElement("div");
      card.className = "card" + (seg === "raus" ? " muted" : "");
      card.innerHTML = `
        <div class="card-head">
          <span class="score" style="background:${scoreColor(job)}">${job.fit_score ?? "–"}</span>
          <strong>${esc(job.title)}</strong>
          <button class="star${saved.has(job.id) ? " on" : ""}" title="merken">★</button>
        </div>
        <div class="card-sub">${esc(job.company ?? "?")} · ${esc(job.location_text ?? "?")} · <em>${esc(job.source)}</em></div>
        <div class="card-badges">${cardBadges(job, f)}</div>`;
      card.querySelector(".star")!.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleSaved(job.id);
        (e.target as HTMLElement).classList.toggle("on"); // in-place, kein Full-Rerender
      });
      card.querySelector(".chip-role")!.addEventListener("click", (e) => {
        e.stopPropagation();
        openRolePicker(e.target as HTMLElement, job, () => render());
      });
      card.addEventListener("click", () => openDrawer(job, meta));
      list.appendChild(card);
    }
    if (!filtered.length) list.innerHTML += "<p class='empty'>Keine Treffer mit diesen Filtern.</p>";

    const src = map.getSource("jobs") as GeoJSONSource | undefined;
    src?.setData(jobsGeojson(applyFilters({ ...f, segment: f.segment }), f) as never);
  }

  function renderInitiative(list: HTMLElement, map: MlMap, companies: Company[]) {
    (map.getSource("jobs") as GeoJSONSource | undefined)?.setData(
      { type: "FeatureCollection", features: [] } as never
    );
    for (const c of companies) {
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML = `
        <div class="card-head"><span class="score" style="background:#7c3aed">${c.initiative_score}</span>
        <strong>${esc(c.name)}</strong></div>
        <div class="card-sub">${esc(c.summary)}</div>
        ${c.career_url ? `<a href="${esc(c.career_url)}" target="_blank" rel="noopener">Karriereseite ↗</a>` : ""}`;
      list.appendChild(card);
      for (const s of c.sites) {
        if (s.lat != null && s.lon != null) {
          initiativeMarkers.push(
            new maplibregl.Marker({ color: "#7c3aed" }).setLngLat([s.lon, s.lat]).addTo(map)
          );
        }
      }
    }
    if (!companies.length)
      list.innerHTML = "<p class='empty'>Noch keine Initiativ-Kandidaten — Historie wächst mit jedem Tag Laufzeit.</p>";
  }

  // Farbmodus-Umschalter
  $<HTMLSelectElement>("f-color").addEventListener("change", (e) => {
    colorMode = (e.target as HTMLSelectElement).value as "score" | "travel";
    render();
  });
  document.querySelectorAll("#filters select, #filters input").forEach((el) =>
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", render)
  );
  $("drawer-close").addEventListener("click", () => closeDrawer());

  // Deeplink ?job=<id>
  const deepJob = readFilters().job;
  if (deepJob && jobById.has(Number(deepJob))) openDrawer(jobById.get(Number(deepJob))!, meta);
  render();
}

// ---------- Rollen-Korrektur (Override → localStorage, Few-Shot-Material) ----------
function openRolePicker(anchorEl: HTMLElement, job: Job, onDone: () => void) {
  document.querySelector(".role-picker")?.remove();
  const sel = document.createElement("select");
  sel.className = "role-picker";
  sel.innerHTML =
    `<option value="">– Original: ${esc(String(job.extraction.role_family))} –</option>` +
    ROLE_FAMILIES.map((r) => `<option value="${r}">${r}</option>`).join("");
  sel.value = getOverrides()[String(job.id)] ?? "";
  sel.addEventListener("change", () => {
    setOverride(job.id, sel.value || null);
    sel.remove();
    onDone();
  });
  sel.addEventListener("blur", () => sel.remove());
  anchorEl.after(sel);
  sel.focus();
}

// ---------- Isochronen (M5) ----------
async function addIsochroneLayers(map: MlMap, meta: Meta, prefix: string) {
  for (const a of meta.anchors) {
    try {
      const resp = await fetch(`./${prefix}/isochrones/${a.id}.geojson`);
      if (!resp.ok) continue;
      const gj = await resp.json();
      map.addSource(`iso-${a.id}`, { type: "geojson", data: gj });
      map.addLayer({
        id: `iso-${a.id}`, type: "fill", source: `iso-${a.id}`,
        paint: {
          "fill-color": ["step", ["get", "class"], "#16a34a", 46, "#84cc16", 61, "#f59e0b", 91, "#ef4444"],
          "fill-opacity": 0.18,
        },
      }, "clusters");
      const label = document.createElement("label");
      label.className = "toggle iso-toggle";
      label.innerHTML = `<input type="checkbox" checked /> Iso: ${esc(a.label)}`;
      label.querySelector("input")!.addEventListener("change", (e) => {
        map.setLayoutProperty(`iso-${a.id}`, "visibility",
          (e.target as HTMLInputElement).checked ? "visible" : "none");
      });
      $("filters").appendChild(label);
    } catch { /* keine Isochronen vorhanden */ }
  }
}

// ---------- Drawer ----------
function openDrawer(job: Job, meta: Meta) {
  const ex = job.extraction;
  const travel = meta.anchors
    .map((a) => {
      const t = job.travel[a.id];
      return t?.minutes != null ? `${a.label}: ${t.minutes} min (${t.transfers ?? "?"}×)` : null;
    })
    .filter(Boolean)
    .join(" · ");
  const links = [job.url, ...job.alt_urls].filter(Boolean)
    .map((u) => `<a href="${esc(u!)}" target="_blank" rel="noopener">${esc(hostOf(u!))} ↗</a>`)
    .join(" ");
  const hard = job.hard_reasons;
  // Nur belegte Felder zeigen — leere Zeilen sind Rauschen
  const exRows = [
    "role_family", "seniority", "education_min", "german_required", "years_experience_min",
    "salary_min_eur_month", "workplace_mode", "contract_type", "contract_end",
    "application_deadline", "must_skills", "nice_skills", "domain_keywords",
  ]
    .filter((k) => { const v = ex[k]; return v != null && v !== "" && !(Array.isArray(v) && !v.length); })
    .map((k) => `<tr><td>${k}</td><td>${esc(fmt(ex[k]))}</td></tr>`)
    .join("");
  $("drawer-content").innerHTML = `
    <h2>${esc(job.title)}</h2>
    <p class="card-sub">${esc(job.company ?? "?")} · ${esc(job.location_text ?? "?")} · seit ${daysSince(job.first_seen)} d · zuletzt gesehen ${new Date(job.last_seen).toLocaleDateString("de-AT")}</p>
    <p>${esc(String(ex.summary_2_lines ?? ""))}</p>
    ${travel ? `<p>🚆 ${travel}</p>` : ""}
    ${job.fit_score != null ? `<h3>Score: ${job.fit_score}/100</h3><ul>${(job.fit_reasons ?? []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    ${job.gaps?.length ? `<h3>Lücken</h3><ul>${job.gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>` : ""}
    ${job.angle ? `<h3>Angle</h3><p><em>${esc(job.angle)}</em></p>` : ""}
    ${hard && (hard.reasons.length || hard.flags.length)
      ? `<h3>Filter</h3><ul>${[...hard.reasons.map((r) => `❌ ${r}`), ...hard.flags.map((f) => `⚠️ ${f}`)]
          .map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`
      : ""}
    <h3>Extraktion</h3>
    <table class="ex-table">${exRows}</table>
    <p>${links}</p>`;
  $("drawer").hidden = false;
  const p = new URLSearchParams(location.search);
  p.set("job", String(job.id));
  history.replaceState(null, "", `?${p}`);
}
function closeDrawer() {
  $("drawer").hidden = true;
  const p = new URLSearchParams(location.search);
  p.delete("job");
  history.replaceState(null, "", p.size ? `?${p}` : location.pathname);
}

main().catch((e) => {
  document.getElementById("list")!.innerHTML = `<p class='empty'>${esc(String(e))}</p>`;
});
