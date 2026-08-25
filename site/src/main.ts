import maplibregl, { Map as MlMap, Marker } from "maplibre-gl";
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

// ---------- Daten laden (Fallback: Demo-Modus) ----------
async function loadData(): Promise<{ jobs: Job[]; companies: Company[]; meta: Meta; demo: boolean }> {
  for (const [prefix, demo] of [["data", false], ["data/demo", true]] as const) {
    try {
      const [jobs, companies, meta] = await Promise.all(
        ["jobs.json", "companies.json", "meta.json"].map((f) =>
          fetch(`./${prefix}/${f}`).then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
        )
      );
      return { jobs, companies, meta, demo };
    } catch {
      /* nächster Prefix */
    }
  }
  throw new Error("Keine Daten gefunden — Pipeline oder Demo-Daten fehlen.");
}

// ---------- URL-State + Merkliste ----------
type Filters = {
  role: string; source: string; contract: string; score: string; days: string;
  anchor: string; minutes: string; initiative: boolean; saved: boolean;
};
function readFilters(): Filters {
  const p = new URLSearchParams(location.search);
  return {
    role: p.get("role") ?? "", source: p.get("source") ?? "", contract: p.get("contract") ?? "",
    score: p.get("score") ?? "", days: p.get("days") ?? "", anchor: p.get("anchor") ?? "",
    minutes: p.get("minutes") ?? "", initiative: p.get("init") === "1", saved: p.get("saved") === "1",
  };
}
function writeFilters(f: Filters) {
  const p = new URLSearchParams();
  if (f.role) p.set("role", f.role);
  if (f.source) p.set("source", f.source);
  if (f.contract) p.set("contract", f.contract);
  if (f.score) p.set("score", f.score);
  if (f.days) p.set("days", f.days);
  if (f.anchor) p.set("anchor", f.anchor);
  if (f.minutes) p.set("minutes", f.minutes);
  if (f.initiative) p.set("init", "1");
  if (f.saved) p.set("saved", "1");
  history.replaceState(null, "", p.size ? `?${p}` : location.pathname);
}
const SAVED_KEY = "heimspiel.saved";
function getSaved(): Set<number> {
  try { return new Set(JSON.parse(localStorage.getItem(SAVED_KEY) ?? "[]")); } catch { return new Set(); }
}
function toggleSaved(id: number) {
  const s = getSaved();
  s.has(id) ? s.delete(id) : s.add(id);
  try { localStorage.setItem(SAVED_KEY, JSON.stringify([...s])); } catch { /* private mode */ }
}

// ---------- Score-Farben ----------
function scoreColor(job: Job): string {
  if (job.hard_pass === false) return "#9ca3af";
  const s = job.fit_score;
  if (s == null) return "#60a5fa";
  if (s >= 80) return "#16a34a";
  if (s >= 60) return "#84cc16";
  if (s >= 40) return "#f59e0b";
  return "#ef4444";
}
function bestTravel(job: Job, anchor: string): number | null {
  const entries = anchor ? [job.travel[anchor]] : Object.values(job.travel);
  const mins = entries.filter((t) => t && t.minutes != null).map((t) => t!.minutes!) as number[];
  return mins.length ? Math.min(...mins) : null;
}

// ---------- App ----------
const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

async function main() {
  const { jobs, companies, meta, demo } = await loadData();
  if (demo) $("demo-badge").hidden = false;
  $("meta-line").textContent =
    `${meta.counts.jobs ?? jobs.length} Inserate · Stand ${new Date(meta.generated_at).toLocaleDateString("de-AT")}`;

  const map = new maplibregl.Map({
    container: "map",
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: [14.5, 47.8],
    zoom: 6.3,
    attributionControl: { compact: true },
  });
  map.addControl(new maplibregl.NavigationControl());
  map.on("load", () => addIsochroneLayers(map, meta));

  // Filter-Optionen aus Daten befüllen
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

  // Filter-State in die Controls
  const f = readFilters();
  $<HTMLSelectElement>("f-role").value = f.role;
  $<HTMLSelectElement>("f-source").value = f.source;
  $<HTMLSelectElement>("f-contract").value = f.contract;
  $<HTMLInputElement>("f-score").value = f.score;
  $<HTMLInputElement>("f-days").value = f.days;
  anchorSel.value = f.anchor;
  $<HTMLInputElement>("f-minutes").value = f.minutes;
  $<HTMLInputElement>("f-initiative").checked = f.initiative;
  $<HTMLInputElement>("f-saved").checked = f.saved;

  let markers: Marker[] = [];

  function currentFilters(): Filters {
    return {
      role: $<HTMLSelectElement>("f-role").value,
      source: $<HTMLSelectElement>("f-source").value,
      contract: $<HTMLSelectElement>("f-contract").value,
      score: $<HTMLInputElement>("f-score").value,
      days: $<HTMLInputElement>("f-days").value,
      anchor: anchorSel.value,
      minutes: $<HTMLInputElement>("f-minutes").value,
      initiative: $<HTMLInputElement>("f-initiative").checked,
      saved: $<HTMLInputElement>("f-saved").checked,
    };
  }

  function applyFilters(): Job[] {
    const f = currentFilters();
    writeFilters(f);
    const saved = getSaved();
    const cutoff = f.days ? Date.now() - Number(f.days) * 864e5 : null;
    return jobs.filter((j) => {
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
      return true;
    });
  }

  function render() {
    const f = currentFilters();
    const list = $("list");
    list.innerHTML = "";
    markers.forEach((m) => m.remove());
    markers = [];

    if (f.initiative) {
      renderInitiative(list, map, markers, companies);
      return;
    }
    const filtered = applyFilters();
    const saved = getSaved();
    for (const job of filtered) {
      const card = document.createElement("div");
      card.className = "card" + (job.hard_pass === false ? " muted" : "");
      const t = bestTravel(job, f.anchor);
      card.innerHTML = `
        <div class="card-head">
          <span class="score" style="background:${scoreColor(job)}">${job.fit_score ?? "–"}</span>
          <strong>${esc(job.title)}</strong>
          <button class="star${saved.has(job.id) ? " on" : ""}" title="merken">★</button>
        </div>
        <div class="card-sub">${esc(job.company ?? "?")} · ${esc(job.location_text ?? "?")}${t != null ? ` · ${t} min` : ""} · <em>${esc(job.source)}</em></div>`;
      card.querySelector(".star")!.addEventListener("click", (e) => {
        e.stopPropagation();
        toggleSaved(job.id);
        render();
      });
      card.addEventListener("click", () => openDrawer(job, meta));
      list.appendChild(card);

      if (job.lat != null && job.lon != null) {
        const marker = new maplibregl.Marker({ color: scoreColor(job) })
          .setLngLat([job.lon, job.lat])
          .addTo(map);
        marker.getElement().addEventListener("click", () => openDrawer(job, meta));
        markers.push(marker);
      }
    }
    if (!filtered.length) list.innerHTML = "<p class='empty'>Keine Treffer mit diesen Filtern.</p>";
  }

  document.querySelectorAll("#filters select, #filters input").forEach((el) =>
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", render)
  );
  $("drawer-close").addEventListener("click", () => ($("drawer").hidden = true));
  render();
}

function renderInitiative(list: HTMLElement, map: MlMap, markers: Marker[], companies: Company[]) {
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
        markers.push(new maplibregl.Marker({ color: "#7c3aed" }).setLngLat([s.lon, s.lat]).addTo(map));
      }
    }
  }
  if (!companies.length) list.innerHTML = "<p class='empty'>Noch keine Initiativ-Kandidaten — Historie wächst mit jedem Tag Laufzeit.</p>";
}

async function addIsochroneLayers(map: MlMap, meta: Meta) {
  // Hex-Isochronen je Anker (M5) — Layer nur anlegen, wenn die Datei existiert
  for (const a of meta.anchors) {
    try {
      const resp = await fetch(`./data/isochrones/${a.id}.geojson`);
      if (!resp.ok) continue;
      const gj = await resp.json();
      map.addSource(`iso-${a.id}`, { type: "geojson", data: gj });
      map.addLayer({
        id: `iso-${a.id}`,
        type: "fill",
        source: `iso-${a.id}`,
        paint: {
          "fill-color": ["step", ["get", "class"], "#16a34a", 46, "#84cc16", 61, "#f59e0b", 91, "#ef4444"],
          "fill-opacity": 0.18,
        },
      });
      const label = document.createElement("label");
      label.className = "toggle iso-toggle";
      label.innerHTML = `<input type="checkbox" checked /> Iso: ${esc(a.label)}`;
      label.querySelector("input")!.addEventListener("change", (e) => {
        map.setLayoutProperty(`iso-${a.id}`, "visibility",
          (e.target as HTMLInputElement).checked ? "visible" : "none");
      });
      document.getElementById("filters")!.appendChild(label);
    } catch { /* keine Isochronen vorhanden */ }
  }
}

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
    .map((u) => `<a href="${esc(u!)}" target="_blank" rel="noopener">${esc(new URL(u!).hostname)} ↗</a>`)
    .join(" ");
  const hard = job.hard_reasons;
  $("drawer-content").innerHTML = `
    <h2>${esc(job.title)}</h2>
    <p class="card-sub">${esc(job.company ?? "?")} · ${esc(job.location_text ?? "?")} · seit ${new Date(job.first_seen).toLocaleDateString("de-AT")}</p>
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
    <table class="ex-table">${["role_family", "seniority", "education_min", "german_required", "salary_min_eur_month", "workplace_mode", "contract_type", "must_skills", "nice_skills"]
      .map((k) => `<tr><td>${k}</td><td>${esc(fmt(ex[k]))}</td></tr>`).join("")}</table>
    <p>${links}</p>`;
  $("drawer").hidden = false;
}

const fmt = (v: unknown): string =>
  v == null ? "–" : Array.isArray(v) ? v.join(", ") : String(v);
const esc = (s: string): string =>
  s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);

main().catch((e) => {
  document.getElementById("list")!.innerHTML = `<p class='empty'>${esc(String(e))}</p>`;
});
