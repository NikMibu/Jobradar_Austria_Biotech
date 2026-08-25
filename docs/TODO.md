# TODO

## Frontend

- Kartendarstellung ist nach dem Cluster-Farb-Fix (2026-08-25) nur noch "semi gut" —
  Cluster/Marker rendern laut Nutzer-Feedback immer noch nicht durchgehend zuverlässig.
  Root Cause nicht abschließend geklärt (Cluster-Farb-Aggregation via `clusterProperties`
  wurde gefixt, das war aber offenbar nicht die ganze Geschichte). Braucht gezielte
  Frontend-Debug-Session (z. B. Playwright-Screenshot + Console-Log beim Pannen/Zoomen).
