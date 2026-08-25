import maplibregl from "maplibre-gl";
import type { GeoJSONSource, MapLayerMouseEvent } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { Company, LocationGroup, Meta } from "./types";

const EMPTY_COLLECTION: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const FALLBACK_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: "background", type: "background", paint: { "background-color": "#e5e7eb" } }],
};

type StatusCallback = (message: string, mode: "loading" | "ready" | "fallback") => void;

export interface MapView {
  setLocations(groups: LocationGroup[]): void;
  setInitiative(companies: Company[]): void;
  destroy(): void;
}

function locationGeojson(groups: LocationGroup[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: groups.map((group) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [group.lon, group.lat] },
      properties: {
        location_key: group.key,
        job_count: group.jobCount,
        first_job_id: group.firstJobId,
        has_eligible: group.hasEligible,
        has_scored: group.hasScored,
        max_score: group.maxScore,
        min_travel: group.minTravel,
        color: group.color,
      },
    })),
  };
}

function initiativeGeojson(companies: Company[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: companies.flatMap((company) => company.sites.flatMap((site) =>
      site.lat == null || site.lon == null ? [] : [{
        type: "Feature" as const,
        geometry: { type: "Point" as const, coordinates: [site.lon, site.lat] },
        properties: { company: company.name, score: company.initiative_score },
      }]
    )),
  };
}

export function createMapView(options: {
  container: string;
  meta: Meta;
  dataPrefix: string;
  filtersElement: HTMLElement;
  onLocation: (key: string, firstJobId: number, count: number) => void;
  onStatus: StatusCallback;
}): MapView {
  let fallback = false;
  let fallbackRequested = false;
  let overlayReady = false;
  let overlayScheduled = false;
  let interactionHandlersAdded = false;
  let latest: { kind: "jobs"; groups: LocationGroup[] } |
    { kind: "initiative"; companies: Company[] } | null = null;

  options.onStatus("Karte wird geladen …", "loading");
  const map = new maplibregl.Map({
    container: options.container,
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: [14.5, 47.8],
    zoom: 6.3,
    attributionControl: { compact: true },
  });
  if (import.meta.env.DEV)
    (window as unknown as { __heimspielMap: maplibregl.Map }).__heimspielMap = map;
  map.addControl(new maplibregl.NavigationControl());

  const requestFallback = () => {
    if (overlayReady || fallbackRequested) return;
    fallbackRequested = true;
    fallback = true;
    map.setStyle(FALLBACK_STYLE);
  };
  const fallbackTimer = window.setTimeout(requestFallback, 8_000);

  map.on("error", (event) => {
    if (!overlayReady) requestFallback();
    else console.error("MapLibre:", event.error);
  });

  const applyLatest = () => {
    if (!overlayReady || !latest) return;
    const jobsSource = map.getSource("jobs") as GeoJSONSource;
    const initiativeSource = map.getSource("initiative") as GeoJSONSource;
    if (latest.kind === "initiative") {
      jobsSource.setData(EMPTY_COLLECTION);
      initiativeSource.setData(initiativeGeojson(latest.companies));
      map.getContainer().dataset.locationCount = String(latest.companies.flatMap((company) => company.sites).length);
      map.getContainer().dataset.sourceUpdates = String(Number(map.getContainer().dataset.sourceUpdates ?? 0) + 1);
      return;
    }
    initiativeSource.setData(EMPTY_COLLECTION);
    jobsSource.setData(locationGeojson(latest.groups));
    map.getContainer().dataset.locationCount = String(latest.groups.length);
    map.getContainer().dataset.sourceUpdates = String(Number(map.getContainer().dataset.sourceUpdates ?? 0) + 1);
  };

  const addInteractions = () => {
    if (interactionHandlersAdded) return;
    interactionHandlersAdded = true;
    map.on("click", "job-points", (event: MapLayerMouseEvent) => {
      const properties = event.features?.[0]?.properties;
      if (!properties) return;
      options.onLocation(
        String(properties.location_key),
        Number(properties.first_job_id),
        Number(properties.job_count),
      );
    });
    map.on("mouseenter", "job-points", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "job-points", () => { map.getCanvas().style.cursor = ""; });
  };

  const loadIsochrones = async () => {
    await Promise.all(options.meta.anchors.map(async (anchor) => {
      try {
        const response = await fetch(`./${options.dataPrefix}/isochrones/${anchor.id}.geojson`);
        if (!response.ok) return;
        const geojson = await response.json() as GeoJSON.GeoJSON;
        const sourceId = `iso-${anchor.id}`;
        if (!map.getSource(sourceId)) map.addSource(sourceId, { type: "geojson", data: geojson });
        if (!map.getLayer(sourceId)) {
          map.addLayer({
            id: sourceId, type: "fill", source: sourceId,
            paint: {
              "fill-color": ["step", ["get", "class"], "#16a34a", 46, "#84cc16", 61, "#f59e0b", 91, "#ef4444"],
              "fill-opacity": 0.18,
            },
          }, "job-points");
        }
        if (!options.filtersElement.querySelector(`[data-iso="${anchor.id}"]`)) {
          const label = document.createElement("label");
          label.className = "toggle iso-toggle";
          label.dataset.iso = anchor.id;
          label.innerHTML = `<input type="checkbox" checked /> Iso: ${anchor.label}`;
          label.querySelector("input")!.addEventListener("change", (event) => {
            map.setLayoutProperty(sourceId, "visibility",
              (event.target as HTMLInputElement).checked ? "visible" : "none");
          });
          options.filtersElement.appendChild(label);
        }
      } catch {
        // An optional isochrone must never block jobs or the base map.
      }
    }));
  };

  const setupOverlay = () => {
    if (overlayReady) return;
    // Mark first because addSource/addLayer emit styledata synchronously.
    overlayReady = true;
    window.clearTimeout(fallbackTimer);
    const initialJobs = latest?.kind === "jobs" ? locationGeojson(latest.groups) : EMPTY_COLLECTION;
    const initialInitiative = latest?.kind === "initiative" ? initiativeGeojson(latest.companies) : EMPTY_COLLECTION;
    map.addSource("jobs", {
      type: "geojson", data: initialJobs,
    });
    map.addSource("initiative", { type: "geojson", data: initialInitiative });
    map.addLayer({
      id: "job-points", type: "circle", source: "jobs",
      paint: {
        "circle-color": ["get", "color"],
        "circle-radius": ["step", ["get", "job_count"], 7, 2, 9, 10, 12, 50, 15],
        "circle-opacity": 0.85,
        "circle-stroke-width": 1.5, "circle-stroke-color": "#ffffff",
      },
    });
    map.addLayer({
      id: "initiative-points", type: "circle", source: "initiative",
      paint: { "circle-color": "#7c3aed", "circle-radius": 9, "circle-stroke-width": 1.5, "circle-stroke-color": "#ffffff" },
    });
    addInteractions();
    if (latest) {
      const count = latest.kind === "jobs"
        ? latest.groups.length
        : latest.companies.flatMap((company) => company.sites).length;
      map.getContainer().dataset.locationCount = String(count);
      map.getContainer().dataset.sourceUpdates = "1";
    }
    void loadIsochrones();
    options.onStatus(fallback ? "Basiskarte nicht verfügbar – Standorte bleiben nutzbar" : "", fallback ? "fallback" : "ready");
  };
  const scheduleOverlay = () => {
    if (overlayReady || overlayScheduled) return;
    overlayScheduled = true;
    // Adding a GeoJSON source from inside Liberty's load callback races its worker
    // layer broadcast in MapLibre 4. Defer it to the next task.
    window.setTimeout(setupOverlay, 0);
  };
  map.on("load", scheduleOverlay);
  if (map.loaded()) scheduleOverlay();

  return {
    setLocations(groups) {
      latest = { kind: "jobs", groups };
      applyLatest();
    },
    setInitiative(companies) {
      latest = { kind: "initiative", companies };
      applyLatest();
    },
    destroy() {
      window.clearTimeout(fallbackTimer);
      map.remove();
    },
  };
}
