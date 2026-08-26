import "./style.css";

import {
  effectiveRole, effectiveSegment, filterJobs, filtersUrl, groupJobsByLocation,
  isForeign, readFilters, scoreColor,
} from "./state";
import type { MapView } from "./map-view";
import type {
  Company, Filters, JobDetail, JobSummary, Meta, RankingLabel, StoredState, TrafficStatus,
} from "./types";

const ROLE_FAMILIES = [
  "bioinformatics", "data_science", "csv_qa_validation", "lab_analytics",
  "downstream_process", "mass_spec", "data_steward", "scientific_software",
  "wet_lab_rnd", "other",
];
const SAVED_KEY = "heimspiel.saved";
const OVERRIDE_KEY = "heimspiel.roleOverrides";
const LABEL_KEY = "heimspiel.rankingLabels.v1";

const $ = <T extends HTMLElement>(id: string): T => document.getElementById(id) as T;
const esc = (value: string): string => value.replace(/[&<>"']/g, (char) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!);
const fmt = (value: unknown): string =>
  value == null || value === "" ? "–" : Array.isArray(value) ? (value.length ? value.join(", ") : "–") : String(value);
const daysSince = (iso: string): number => Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 86_400_000));
const safeUrl = (value: string | null): string | null => {
  if (!value) return null;
  try {
    const url = new URL(value, location.href);
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : null;
  } catch { return null; }
};
const hostOf = (url: string): string => {
  try { return new URL(url).hostname; } catch { return url.slice(0, 40); }
};

function parseStorage<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? "") as T; } catch { return fallback; }
}

function loadStoredState(): StoredState {
  return {
    saved: new Set(parseStorage<number[]>(SAVED_KEY, [])),
    overrides: parseStorage<Record<string, string>>(OVERRIDE_KEY, {}),
    labels: parseStorage<Record<string, RankingLabel>>(LABEL_KEY, {}),
  };
}

function persistStoredState(state: StoredState) {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify([...state.saved]));
    localStorage.setItem(OVERRIDE_KEY, JSON.stringify(state.overrides));
    localStorage.setItem(LABEL_KEY, JSON.stringify(state.labels));
  } catch { /* private mode */ }
}

function normalizeJob(raw: Record<string, unknown>): JobSummary {
  const extraction = (raw.extraction ?? {}) as Record<string, unknown>;
  return {
    ...(raw as unknown as JobSummary),
    role_family: (raw.role_family ?? extraction.role_family ?? null) as string | null,
    workplace_mode: (raw.workplace_mode ?? extraction.workplace_mode ?? null) as string | null,
    contract_type: (raw.contract_type ?? extraction.contract_type ?? null) as string | null,
    salary_min_eur_month: (raw.salary_min_eur_month ?? extraction.salary_min_eur_month ?? null) as number | null,
    application_deadline: (raw.application_deadline ?? extraction.application_deadline ?? null) as string | null,
    score_confidence: (raw.score_confidence ?? null) as number | null,
    formal_status: (raw.formal_status ?? null) as TrafficStatus | null,
    practical_status: (raw.practical_status ?? null) as TrafficStatus | null,
  };
}

async function loadData(): Promise<{
  jobs: JobSummary[]; companies: Company[]; meta: Meta; prefix: string; demo: boolean;
}> {
  const prefixes = new URLSearchParams(location.search).get("demo") === "1"
    ? [["data/demo", true]] as const
    : [["data", false], ["data/demo", true]] as const;
  for (const [prefix, demo] of prefixes) {
    try {
      const [rawJobs, companies, meta] = await Promise.all([
        fetch(`./${prefix}/jobs.json`).then((response) => response.ok ? response.json() : Promise.reject(response.status)),
        fetch(`./${prefix}/companies.json`).then((response) => response.ok ? response.json() : Promise.reject(response.status)),
        fetch(`./${prefix}/meta.json`).then((response) => response.ok ? response.json() : Promise.reject(response.status)),
      ]) as [Record<string, unknown>[], Company[], Meta];
      return { jobs: rawJobs.map(normalizeJob), companies, meta, prefix, demo };
    } catch { /* try demo */ }
  }
  throw new Error("Keine Daten gefunden — Pipeline oder Demo-Daten fehlen.");
}

function legacyDetail(job: JobSummary): JobDetail | null {
  if (!job.extraction || !job.last_seen) return null;
  return {
    url: job.url ?? null, alt_urls: job.alt_urls ?? [], last_seen: job.last_seen,
    extraction: job.extraction, fit_reasons: job.fit_reasons ?? null,
    gaps: job.gaps ?? null, angle: job.angle ?? null,
    score_breakdown: job.score_breakdown ?? null, score_evidence: job.score_evidence ?? null,
    formal_reasons: job.formal_reasons ?? [], practical_reasons: job.practical_reasons ?? [],
    fallback_model: job.fallback_model ?? null,
  };
}

function normalizeDetail(raw: Partial<JobDetail>): JobDetail {
  return {
    url: raw.url ?? null, alt_urls: raw.alt_urls ?? [], last_seen: raw.last_seen ?? "",
    extraction: raw.extraction ?? {}, fit_reasons: raw.fit_reasons ?? null,
    gaps: raw.gaps ?? null, angle: raw.angle ?? null,
    score_breakdown: raw.score_breakdown ?? null, score_evidence: raw.score_evidence ?? null,
    formal_reasons: raw.formal_reasons ?? [], practical_reasons: raw.practical_reasons ?? [],
    fallback_model: raw.fallback_model ?? null,
  };
}

async function main() {
  const mapModulePromise = import("./map-view");
  const { jobs, companies, meta, prefix, demo } = await loadData();
  performance.mark("heimspiel:data-loaded");
  if (demo) $("demo-badge").hidden = false;

  const jobById = new Map(jobs.map((job) => [job.id, job]));
  const stored = loadStoredState();
  let filters = readFilters(location.search);
  let locationFilter: string | null = null;
  let mapView: MapView | null = null;
  let detailsPromise: Promise<Record<string, JobDetail>> | null = null;
  let renderFrame = 0;
  let listDirty = true;
  let mapDirty = true;
  let inputTimer = 0;

  const statusBadge = (label: string, status: TrafficStatus | null | undefined): string => {
    if (!status) return "";
    const icon = status === "green" ? "●" : status === "yellow" ? "●" : "●";
    return `<span class="chip status-${status}" title="${esc(label)}">${icon} ${esc(label)}</span>`;
  };

  const updateLabelsButton = () => {
    const count = Object.keys(stored.labels).length;
    const button = $<HTMLButtonElement>("labels-export");
    button.hidden = count === 0;
    button.textContent = `Labels exportieren (${count})`;
  };
  updateLabelsButton();

  const fillSelect = (id: string, values: (string | null)[]) => {
    const select = $<HTMLSelectElement>(id);
    [...new Set(values.filter(Boolean) as string[])].sort().forEach((value) => {
      const option = document.createElement("option");
      option.value = option.textContent = value;
      select.appendChild(option);
    });
  };
  fillSelect("f-role", jobs.map((job) => job.role_family));
  fillSelect("f-source", jobs.map((job) => job.source));
  fillSelect("f-contract", jobs.map((job) => job.contract_type));
  meta.anchors.forEach((anchor) => {
    const option = document.createElement("option");
    option.value = anchor.id;
    option.textContent = `Anker: ${anchor.label}`;
    $<HTMLSelectElement>("f-anchor").appendChild(option);
  });

  const syncControls = () => {
    $<HTMLSelectElement>("f-segment").value = filters.segment;
    $<HTMLSelectElement>("f-sort").value = filters.sort;
    $<HTMLSelectElement>("f-color").value = filters.color;
    $<HTMLSelectElement>("f-role").value = filters.role;
    $<HTMLSelectElement>("f-source").value = filters.source;
    $<HTMLSelectElement>("f-contract").value = filters.contract;
    $<HTMLInputElement>("f-score").value = filters.score;
    $<HTMLInputElement>("f-days").value = filters.days;
    $<HTMLSelectElement>("f-anchor").value = filters.anchor;
    $<HTMLInputElement>("f-minutes").value = filters.minutes;
    $<HTMLInputElement>("f-initiative").checked = filters.initiative;
    $<HTMLInputElement>("f-saved").checked = filters.saved;
  };
  syncControls();

  const readControls = () => {
    filters = {
      ...filters,
      segment: $<HTMLSelectElement>("f-segment").value,
      sort: $<HTMLSelectElement>("f-sort").value,
      color: $<HTMLSelectElement>("f-color").value as Filters["color"],
      role: $<HTMLSelectElement>("f-role").value,
      source: $<HTMLSelectElement>("f-source").value,
      contract: $<HTMLSelectElement>("f-contract").value,
      score: $<HTMLInputElement>("f-score").value,
      days: $<HTMLInputElement>("f-days").value,
      anchor: $<HTMLSelectElement>("f-anchor").value,
      minutes: $<HTMLInputElement>("f-minutes").value,
      initiative: $<HTMLInputElement>("f-initiative").checked,
      saved: $<HTMLInputElement>("f-saved").checked,
    };
  };

  const writeUrl = () => {
    const next = filtersUrl(filters, location.pathname);
    const current = location.search || location.pathname;
    if (next !== current) history.replaceState(null, "", next);
  };

  const cardBadges = (job: JobSummary): string => {
    const badges = [`<span class="chip chip-role" data-action="role" title="Rollenfamilie korrigieren">${esc(effectiveRole(job, stored) || "?")}${stored.overrides[String(job.id)] ? " ✎" : ""}</span>`];
    for (const anchor of meta.anchors) {
      const travel = job.travel[anchor.id];
      if (travel?.minutes != null && (!filters.anchor || filters.anchor === anchor.id)) {
        const ok = travel.minutes <= anchor.max_minutes;
        badges.push(`<span class="chip" style="color:${ok ? "var(--ok)" : "var(--bad)"}">${esc(anchor.id)} ${travel.minutes}′</span>`);
      }
    }
    if (job.workplace_mode === "hybrid" || job.workplace_mode === "remote")
      badges.push(`<span class="chip chip-alt">${esc(job.workplace_mode)}</span>`);
    if (isForeign(job)) badges.push(`<span class="chip chip-warn" title="Standort außerhalb Österreichs">🌍 Ausland</span>`);
    if (job.salary_min_eur_month) badges.push(`<span class="chip">≥ ${job.salary_min_eur_month.toLocaleString("de-AT")} €</span>`);
    if (job.contract_type && !["permanent", "unknown"].includes(job.contract_type))
      badges.push(`<span class="chip chip-warn">${esc(job.contract_type)}</span>`);
    if (job.application_deadline) badges.push(`<span class="chip chip-warn">bis ${esc(job.application_deadline)}</span>`);
    badges.push(statusBadge("formal", job.formal_status));
    badges.push(statusBadge("praktisch", job.practical_status));
    const open = daysSince(job.first_seen);
    if (open) badges.push(`<span class="chip chip-dim">${open} d offen</span>`);
    return badges.join("");
  };

  const renderChips = () => {
    const chipbar = $("chipbar");
    chipbar.replaceChildren();
    if (locationFilter) {
      const sample = jobs.find((job) => job.lat != null && job.lon != null && `${job.lat.toFixed(7)},${job.lon.toFixed(7)}` === locationFilter);
      const chip = document.createElement("button");
      chip.className = "chip chip-filter";
      chip.textContent = `📍 ${sample?.site_label ?? sample?.location_text ?? "Standort"} ×`;
      chip.onclick = () => { locationFilter = null; scheduleRender(true, true); };
      chipbar.appendChild(chip);
    }
    const noLocation = document.createElement("button");
    noLocation.className = `chip chip-filter${filters.noLocation ? " on" : ""}`;
    noLocation.textContent = `ohne Standort (${jobs.filter((job) => job.lat == null || job.lon == null).length})`;
    noLocation.onclick = () => { filters.noLocation = !filters.noLocation; scheduleRender(true, true); };
    chipbar.appendChild(noLocation);
    const foreignCount = jobs.filter(isForeign).length;
    if (foreignCount) {
      const foreign = document.createElement("button");
      foreign.className = `chip chip-filter${filters.foreign ? " on" : ""}`;
      foreign.textContent = `Ausland ausblenden (${foreignCount})`;
      foreign.onclick = () => { filters.foreign = !filters.foreign; scheduleRender(true, true); };
      chipbar.appendChild(foreign);
    }
  };

  const renderList = (filtered: JobSummary[]) => {
    const fragment = document.createDocumentFragment();
    for (const job of filtered) {
      const card = document.createElement("article");
      card.className = `card${effectiveSegment(job, stored) === "raus" ? " muted" : ""}`;
      card.dataset.jobId = String(job.id);
      card.tabIndex = 0;
      card.innerHTML = `<div class="card-head"><span class="score" style="background:${scoreColor(job, stored)}">${job.fit_score ?? "–"}</span>
        <strong>${esc(job.title)}</strong><button class="star${stored.saved.has(job.id) ? " on" : ""}" data-action="save" title="merken" aria-label="Job merken">★</button></div>
        <div class="card-sub">${esc(job.company ?? "?")} · ${esc(job.location_text ?? "?")} · <em>${esc(job.source)}</em></div>
        <div class="card-badges">${cardBadges(job)}</div>`;
      fragment.appendChild(card);
    }
    if (!filtered.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Keine Treffer mit diesen Filtern.";
      fragment.appendChild(empty);
    }
    $("list").replaceChildren(fragment);
  };

  const renderInitiative = () => {
    const fragment = document.createDocumentFragment();
    for (const company of companies) {
      const card = document.createElement("article");
      card.className = "card";
      const careerUrl = safeUrl(company.career_url);
      card.innerHTML = `<div class="card-head"><span class="score initiative-score">${company.initiative_score}</span><strong>${esc(company.name)}</strong></div>
        <div class="card-sub">${esc(company.summary)}</div>${careerUrl ? `<a href="${esc(careerUrl)}" target="_blank" rel="noopener">Karriereseite ↗</a>` : ""}`;
      fragment.appendChild(card);
    }
    if (!companies.length) {
      const empty = document.createElement("p");
      empty.className = "empty";
      empty.textContent = "Noch keine Initiativ-Kandidaten — Historie wächst mit jedem Tag Laufzeit.";
      fragment.appendChild(empty);
    }
    $("list").replaceChildren(fragment);
  };

  const render = () => {
    renderFrame = 0;
    writeUrl();
    renderChips();
    if (filters.initiative) {
      if (listDirty) renderInitiative();
      if (mapDirty) mapView?.setInitiative(companies);
      $("meta-line").textContent = `${companies.length} Initiativ-Kandidaten · Stand ${new Date(meta.generated_at).toLocaleDateString("de-AT")}`;
    } else {
      const filtered = filterJobs(jobs, filters, stored, locationFilter);
      if (listDirty) renderList(filtered);
      if (mapDirty) mapView?.setLocations(groupJobsByLocation(filtered, filters, stored));
      const matches = jobs.filter((job) => effectiveSegment(job, stored) !== "raus").length;
      $("meta-line").textContent = `${filtered.length} angezeigt · ${matches} Treffer von ${jobs.length} · Stand ${new Date(meta.generated_at).toLocaleDateString("de-AT")}`;
    }
    listDirty = mapDirty = false;
    performance.mark("heimspiel:view-rendered");
  };

  function scheduleRender(updateList: boolean, updateMap: boolean) {
    listDirty ||= updateList;
    mapDirty ||= updateMap;
    if (!renderFrame) renderFrame = requestAnimationFrame(render);
  }

  const loadDetail = async (job: JobSummary): Promise<JobDetail> => {
    const legacy = legacyDetail(job);
    if (legacy) return legacy;
    detailsPromise ??= fetch(`./${prefix}/job-details.json`).then((response) => {
      if (!response.ok) throw new Error(`Details konnten nicht geladen werden (${response.status}).`);
      return response.json() as Promise<Record<string, JobDetail>>;
    });
    const detail = (await detailsPromise)[String(job.id)];
    if (!detail) throw new Error("Für diesen Job fehlen Detaildaten.");
    return normalizeDetail(detail);
  };

  const showDrawer = async (jobId: number) => {
    const job = jobById.get(jobId);
    if (!job) return;
    filters.job = String(job.id);
    writeUrl();
    $("drawer").hidden = false;
    $("drawer-content").innerHTML = `<h2>${esc(job.title)}</h2><p class="empty">Details werden geladen …</p>`;
    try {
      const detail = await loadDetail(job);
      if (filters.job !== String(job.id)) return;
      const extraction = detail.extraction;
      const travel = meta.anchors.flatMap((anchor) => {
        const value = job.travel[anchor.id];
        return value?.minutes == null ? [] : [`${anchor.label}: ${value.minutes} min (${value.transfers ?? "?"}×)`];
      }).join(" · ");
      const links = [detail.url, ...detail.alt_urls].flatMap((value) => {
        const url = safeUrl(value);
        return url ? [`<a href="${esc(url)}" target="_blank" rel="noopener">${esc(hostOf(url))} ↗</a>`] : [];
      }).join(" ");
      const rows = [
        "role_family", "seniority", "education_min", "german_required", "years_experience_min",
        "salary_min_eur_month", "workplace_mode", "contract_type", "contract_end",
        "application_deadline", "must_skills", "nice_skills", "domain_keywords",
      ].filter((key) => {
        const value = extraction[key];
        return value != null && value !== "" && !(Array.isArray(value) && !value.length);
      }).map((key) => `<tr><td>${key}</td><td>${esc(fmt(extraction[key]))}</td></tr>`).join("");
      const hard = job.hard_reasons;
      const breakdown = detail.score_breakdown;
      const breakdownRows = breakdown ? [
        ["Skills", `${breakdown.skills ?? 0}/60`],
        ["Domäne", `${breakdown.domain ?? 0}/25`],
        ["Interessen", `${breakdown.interests ?? 0}/15`],
      ].map(([name, value]) => `<tr><td>${name}</td><td>${value}</td></tr>`).join("") : "";
      const currentLabel = stored.labels[String(job.id)];
      $("drawer-content").innerHTML = `<h2>${esc(job.title)}</h2>
        <p class="card-sub">${esc(job.company ?? "?")} · ${esc(job.location_text ?? "?")} · seit ${daysSince(job.first_seen)} d · zuletzt gesehen ${new Date(detail.last_seen).toLocaleDateString("de-AT")}</p>
        <p>${esc(String(extraction.summary_2_lines ?? ""))}</p>${travel ? `<p>🚆 ${esc(travel)}</p>` : ""}
        ${job.fit_score != null ? `<h3>Fachfit: ${job.fit_score}/100</h3>
          ${breakdownRows ? `<table class="score-breakdown">${breakdownRows}</table>` : ""}
          <p class="confidence">Datenbasis: ${job.score_confidence ?? "?"}/100</p>
          <ul>${(detail.fit_reasons ?? []).map((reason) => `<li>${esc(reason)}</li>`).join("")}</ul>` : ""}
        <h3>Einordnung</h3><p>${statusBadge("formal", job.formal_status)} ${statusBadge("praktisch", job.practical_status)}</p>
        ${detail.formal_reasons.length ? `<ul>${detail.formal_reasons.map((reason) => `<li>Formal: ${esc(reason)}</li>`).join("")}</ul>` : ""}
        ${detail.practical_reasons.length ? `<ul>${detail.practical_reasons.map((reason) => `<li>Praktisch: ${esc(reason)}</li>`).join("")}</ul>` : ""}
        ${detail.gaps?.length ? `<h3>Lücken</h3><ul>${detail.gaps.map((gap) => `<li>${esc(gap)}</li>`).join("")}</ul>` : ""}
        ${detail.angle ? `<h3>Angle</h3><p><em>${esc(detail.angle)}</em></p>` : ""}
        <h3>Meine Bewertung</h3><div class="ranking-labels">
          ${([['yes', 'Passt'], ['maybe', 'Vielleicht'], ['no', 'Nein']] as [RankingLabel, string][]).map(([value, label]) => `<button data-action="label" data-label="${value}" class="label-button${currentLabel === value ? " on" : ""}">${label}</button>`).join("")}
        </div>
        ${hard && (hard.reasons.length || hard.flags.length) ? `<h3>Filter</h3><ul>${[...hard.reasons.map((reason) => `❌ ${reason}`), ...hard.flags.map((flag) => `⚠️ ${flag}`)].map((value) => `<li>${esc(value)}</li>`).join("")}</ul>` : ""}
        <h3>Extraktion</h3><table class="ex-table">${rows}</table><p>${links}</p>`;
    } catch (error) {
      $("drawer-content").innerHTML = `<h2>${esc(job.title)}</h2><p class="empty">${esc(String(error))}</p>`;
    }
  };

  const closeDrawer = () => {
    $("drawer").hidden = true;
    filters.job = "";
    writeUrl();
  };

  $("list").addEventListener("click", (event) => {
    const target = event.target as HTMLElement;
    const card = target.closest<HTMLElement>("[data-job-id]");
    if (!card) return;
    const jobId = Number(card.dataset.jobId);
    if (target.closest('[data-action="save"]')) {
      event.stopPropagation();
      stored.saved.has(jobId) ? stored.saved.delete(jobId) : stored.saved.add(jobId);
      persistStoredState(stored);
      if (filters.saved) scheduleRender(true, true);
      else target.closest(".star")?.classList.toggle("on", stored.saved.has(jobId));
      return;
    }
    const roleChip = target.closest<HTMLElement>('[data-action="role"]');
    if (roleChip) {
      event.stopPropagation();
      document.querySelector(".role-picker")?.remove();
      const select = document.createElement("select");
      select.className = "role-picker";
      select.innerHTML = `<option value="">– Original: ${esc(jobById.get(jobId)?.role_family ?? "?")} –</option>` +
        ROLE_FAMILIES.map((role) => `<option value="${role}">${role}</option>`).join("");
      select.value = stored.overrides[String(jobId)] ?? "";
      select.onchange = () => {
        if (select.value) stored.overrides[String(jobId)] = select.value;
        else delete stored.overrides[String(jobId)];
        persistStoredState(stored);
        select.remove();
        scheduleRender(true, true);
      };
      select.onblur = () => select.remove();
      roleChip.after(select);
      select.focus();
      return;
    }
    void showDrawer(jobId);
  });

  $("list").addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    const card = (event.target as HTMLElement).closest<HTMLElement>("[data-job-id]");
    if (card) void showDrawer(Number(card.dataset.jobId));
  });

  document.querySelectorAll<HTMLElement>("#filters select, #filters input").forEach((element) => {
    const handle = () => {
      readControls();
      scheduleRender(element.id !== "f-color", element.id !== "f-sort");
    };
    if (element instanceof HTMLInputElement && element.type === "number") {
      element.addEventListener("input", () => {
        window.clearTimeout(inputTimer);
        inputTimer = window.setTimeout(handle, 120);
      });
    } else element.addEventListener("change", handle);
  });
  $("drawer-close").addEventListener("click", closeDrawer);

  $("drawer-content").addEventListener("click", (event) => {
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>('[data-action="label"]');
    if (!button || !filters.job) return;
    const label = button.dataset.label as RankingLabel;
    const existing = stored.labels[filters.job];
    if (existing === label) delete stored.labels[filters.job];
    else stored.labels[filters.job] = label;
    persistStoredState(stored);
    button.parentElement?.querySelectorAll(".label-button").forEach((element) => element.classList.remove("on"));
    if (existing !== label) button.classList.add("on");
    updateLabelsButton();
  });

  $("labels-export").addEventListener("click", () => {
    const timestamp = new Date().toISOString();
    const lines = Object.entries(stored.labels).flatMap(([id, label]) => {
      const job = jobById.get(Number(id));
      return job ? [JSON.stringify({
        posting_id: job.id, label, profile_version: meta.profile_version ?? null,
        title: job.title, company: job.company, timestamp,
      })] : [];
    });
    const blob = new Blob([`${lines.join("\n")}\n`], { type: "application/x-ndjson" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `heimspiel-ranking-labels-${new Date().toISOString().slice(0, 10)}.jsonl`;
    link.click();
    URL.revokeObjectURL(link.href);
  });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeDrawer(); });

  render();
  performance.mark("heimspiel:list-ready");
  if (filters.job && jobById.has(Number(filters.job))) void showDrawer(Number(filters.job));

  requestAnimationFrame(() => {
    void mapModulePromise.then(({ createMapView }) => {
      mapView = createMapView({
        container: "map", meta, dataPrefix: prefix, filtersElement: $("filters"),
        onLocation: (key, firstJobId, count) => {
          if (count === 1) void showDrawer(firstJobId);
          else { locationFilter = key; scheduleRender(true, true); }
        },
        onStatus: (message, mode) => {
          const status = $("map-status");
          status.textContent = message;
          status.hidden = mode === "ready";
          $("map").dataset.status = mode;
        },
      });
      scheduleRender(false, true);
    }).catch(() => {
      const status = $("map-status");
      status.textContent = "Karte kann in diesem Browser nicht gestartet werden.";
      status.hidden = false;
      $("map").dataset.status = "fallback";
    });
  });
}

main().catch((error) => {
  $("list").innerHTML = `<p class="empty">${esc(String(error))}</p>`;
});
