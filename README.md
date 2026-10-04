# Orbit Desk

A Python dashboard for exploring satellite orbital estimates from CelesTrak's public General Perturbations catalog. Add multiple NORAD IDs to compare their predicted orbits on an interactive 3D globe, highlight one satellite, choose an observer preset (including the ITER facility) or enter custom coordinates, view its ground track on OpenStreetMap, predict passes, and review large changes between successive orbit predictions.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

The app needs an internet connection to retrieve the selected satellites' orbital elements and OpenStreetMap tiles. It requests each selected catalog number individually and supports up to eight satellites on the globe. CelesTrak updates GP data on a two-hour cycle and may return HTTP 403 when an object's data has already been requested during the window. When available, the app falls back to its last locally saved orbital elements and labels them as potentially stale. The observer defaults to Moscow; choose ITER, Baikonur, or custom coordinates in the sidebar. Drag the globe to rotate it and scroll to zoom.

## Data and interpretation

The app retrieves [CelesTrak GP data](https://celestrak.org/NORAD/elements/gp.php) by NORAD catalog number. GP elements are estimates, not live spacecraft telemetry. Derived positions and pass times are approximate. Element-change alerts can reflect normal element updates, element age, or model error; they do not confirm a maneuver.

The app stores previous element snapshots in `data/element_history.json` so updates can be compared across runs. This file is local and can be removed to clear the comparison history.
