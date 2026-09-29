# TODO

Keine offenen Punkte.

## Erledigt am 2026-08-25

- Map-Rendering stabilisiert. Ursachen waren hunderte exakt überlagerte Jobpunkte, doppelte
  Full-Renders und ein Race beim sehr schnellen Laden des Kartenstils. Die geografischen
  Cluster wurden nach Nutzerfeedback entfernt: gruppierte Standortmarker bleiben in jeder
  Zoomstufe sichtbar. Ein inkompatibler dynamischer Zahlen-`symbol`-Layer, der sämtliche
  GeoJSON-Tiles auf `errored` setzte, wurde ebenfalls entfernt. Getrennte UI-/Map-Updates
  sowie Vitest- und Playwright-Regressionstests sichern nun echte gerenderte Features ab.
