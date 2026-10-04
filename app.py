from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import folium
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_folium import st_folium
from skyfield.api import EarthSatellite, load, wgs84
from skyfield.toposlib import GeographicPosition


GP_URL = "https://celestrak.org/NORAD/elements/gp.php"
HISTORY_FILE = Path(__file__).parent / "data" / "element_history.json"
TIMESCALE = load.timescale()
LOCATION_PRESETS = {
    "Moscow, Russia": (55.7558, 37.6173),
    "ITER Organization, France": (43.7087, 5.7754),
    "Baikonur Cosmodrome, Kazakhstan": (45.9650, 63.3050),
    "Custom coordinates": None,
}


st.set_page_config(page_title="Orbit Desk | Russian satellites", page_icon="Satellite", layout="wide")
st.markdown(
    """
    <style>
    .stApp { background: #f5f6f2; }
    [data-testid="stHeader"] { background: rgba(245, 246, 242, .92); }
    h1, h2, h3 { color: #172b2a; }
    div[data-testid="stMetric"] { background: #fff; border: 1px solid #dce3df; padding: 14px 16px; border-radius: 6px; }
    .source-note { color: #53635f; font-size: .9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


def load_saved_elements() -> list[dict]:
    try:
        history = json.loads(HISTORY_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    return [
        item["elements"]
        for item in history.values()
        if isinstance(item, dict) and isinstance(item.get("elements"), dict)
    ]


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_tles(catalog_id: int) -> tuple[dict, bool]:
    """Fetch one satellite's orbital elements by its CelesTrak catalog number."""
    import requests

    try:
        response = requests.get(
            GP_URL,
            params={"CATNR": catalog_id, "FORMAT": "JSON"},
            timeout=30,
            headers={"User-Agent": "OrbitDesk/1.0"},
        )
        response.raise_for_status()
        elements = response.json()
    except requests.RequestException as error:
        saved_record = next(
            (
                record
                for record in load_saved_elements()
                if str(record.get("NORAD_CAT_ID")) == str(catalog_id)
            ),
            None,
        )
        if saved_record:
            return saved_record, True
        if getattr(error.response, "status_code", None) == 403:
            raise RuntimeError(
                f"CelesTrak returned HTTP 403 for catalog number {catalog_id}, "
                "and no saved elements are available for this satellite. Try again later."
            ) from error
        raise
    if isinstance(elements, list):
        elements = elements[0] if elements else None
    if not isinstance(elements, dict) or str(elements.get("NORAD_CAT_ID")) != str(catalog_id):
        raise ValueError(f"No current orbital elements found for catalog number {catalog_id}.")
    return elements, False


def make_satellite(record: dict) -> EarthSatellite:
    return EarthSatellite.from_omm(TIMESCALE, record)


def satellite_state(satellite: EarthSatellite, observer: GeographicPosition) -> dict:
    now = TIMESCALE.now()
    geocentric = satellite.at(now)
    subpoint = wgs84.subpoint(geocentric)
    topocentric = (satellite - observer).at(now)
    altitude, azimuth, distance = topocentric.altaz()
    velocity_km_s = (geocentric.velocity.km_per_s**2).sum() ** 0.5
    return {
        "latitude": subpoint.latitude.degrees,
        "longitude": subpoint.longitude.degrees,
        "altitude_km": subpoint.elevation.km,
        "speed_km_s": velocity_km_s,
        "elevation": altitude.degrees,
        "azimuth": azimuth.degrees,
        "range_km": distance.km,
    }


def ground_track(satellite: EarthSatellite, minutes: int = 100) -> pd.DataFrame:
    start = datetime.now(timezone.utc)
    datetimes = [start + timedelta(minutes=minute) for minute in range(0, minutes + 1, 2)]
    positions = satellite.at(TIMESCALE.from_datetimes(datetimes))
    subpoints = wgs84.subpoint(positions)
    return pd.DataFrame(
        {
            "Latitude": subpoints.latitude.degrees,
            "Longitude": subpoints.longitude.degrees,
            "Time (UTC)": [value.strftime("%H:%M") for value in datetimes],
        }
    )


def is_land_cell(latitude: float, longitude: float) -> bool:
    """Return a coarse land mask for the Earth sphere used in the 3D globe texture."""
    return (
        (35.0 < latitude < 72.0 and -12.0 < longitude < 40.0)
        or (5.0 < latitude < 72.0 and 40.0 < longitude < 180.0)
        or (-55.0 < latitude < 15.0 and -82.0 < longitude < -34.0)
        or (10.0 < latitude < 72.0 and -179.0 < longitude < -52.0)
        or (-48.0 < latitude < -10.0 and 110.0 < longitude < 155.0)
        or (-35.0 < latitude < 37.0 and -18.0 < longitude < 55.0)
        or (12.0 < latitude < 72.0 and -170.0 < longitude < -10.0)
    )


def build_earth_texture(earth_radius_km: float) -> tuple[list[list[float]], list[list[float]], list[list[float]], list[list[float]]]:
    latitudes = [-90 + 180 * step / 79 for step in range(80)]
    longitudes = [-180 + 360 * step / 159 for step in range(160)]
    x_grid = []
    y_grid = []
    z_grid = []
    color_grid = []
    for latitude in latitudes:
        x_row = []
        y_row = []
        z_row = []
        color_row = []
        for longitude in longitudes:
            latitude_radians = math.radians(float(latitude))
            longitude_radians = math.radians(float(longitude))
            x = earth_radius_km * math.cos(latitude_radians) * math.cos(longitude_radians)
            y = earth_radius_km * math.cos(latitude_radians) * math.sin(longitude_radians)
            z = earth_radius_km * math.sin(latitude_radians)
            x_row.append(x)
            y_row.append(y)
            z_row.append(z)
            color_row.append(1.0 if is_land_cell(latitude, longitude) else 0.0)
        x_grid.append(x_row)
        y_grid.append(y_row)
        z_grid.append(z_row)
        color_grid.append(color_row)
    return x_grid, y_grid, z_grid, color_grid


def make_globe_figure(
    satellites: list[EarthSatellite], highlighted_catalog_id: int
) -> go.Figure:
    """Build an Earth-centered comparison of the selected satellites' orbits."""
    earth_radius_km = 6371.0
    start = datetime.now(timezone.utc)

    def to_cartesian(latitude: float, longitude: float, radius: float) -> tuple[float, float, float]:
        latitude_radians = math.radians(float(latitude))
        longitude_radians = math.radians(float(longitude))
        return (
            radius * math.cos(latitude_radians) * math.cos(longitude_radians),
            radius * math.cos(latitude_radians) * math.sin(longitude_radians),
            radius * math.sin(latitude_radians),
        )

    orbit_data = []
    for satellite in satellites:
        period_minutes = 2 * math.pi / satellite.model.no_kozai
        datetimes = [
            start + timedelta(minutes=period_minutes * step / 180)
            for step in range(181)
        ]
        positions = satellite.at(TIMESCALE.from_datetimes(datetimes))
        subpoints = wgs84.subpoint(positions)
        latitudes = subpoints.latitude.degrees
        longitudes = subpoints.longitude.degrees
        radii = earth_radius_km + subpoints.elevation.km
        orbit = [
            to_cartesian(latitude, longitude, radius)
            for latitude, longitude, radius in zip(latitudes, longitudes, radii)
        ]
        orbit_data.append((satellite, subpoints, latitudes, longitudes, radii, orbit))

    sphere_x, sphere_y, sphere_z, sphere_colors = build_earth_texture(earth_radius_km)

    figure = go.Figure()
    figure.add_trace(
        go.Surface(
            x=sphere_x,
            y=sphere_y,
            z=sphere_z,
            surfacecolor=sphere_colors,
            colorscale=[[0.0, "#0d4f6d"], [0.5, "#0d4f6d"], [0.5, "#2a7f62"], [1.0, "#2a7f62"]],
            cmin=0,
            cmax=1,
            showscale=False,
            hoverinfo="skip",
            lighting={"ambient": 0.6, "diffuse": 0.9, "specular": 0.25, "roughness": 0.8},
            lightposition={"x": 20000, "y": 12000, "z": 20000},
            name="Earth",
        )
    )

    for latitude in range(-60, 90, 30):
        parallel = [
            to_cartesian(latitude, longitude, earth_radius_km * 1.001)
            for longitude in range(-180, 181, 3)
        ]
        figure.add_trace(
            go.Scatter3d(
                x=[point[0] for point in parallel],
                y=[point[1] for point in parallel],
                z=[point[2] for point in parallel],
                mode="lines",
                line={"color": "#8ab8b5", "width": 1},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    for longitude in range(-180, 180, 30):
        meridian = [
            to_cartesian(latitude, longitude, earth_radius_km * 1.001)
            for latitude in range(-90, 91, 3)
        ]
        figure.add_trace(
            go.Scatter3d(
                x=[point[0] for point in meridian],
                y=[point[1] for point in meridian],
                z=[point[2] for point in meridian],
                mode="lines",
                line={"color": "#8ab8b5", "width": 1},
                hoverinfo="skip",
                showlegend=False,
            )
        )

    orbit_colors = ["#36a89a", "#e28c52", "#6b91d4", "#ce6b8b", "#8b9e47", "#a477c2", "#d4a83f"]
    max_orbit_radius = earth_radius_km
    for index, (satellite, subpoints, latitudes, longitudes, radii, orbit) in enumerate(orbit_data):
        highlighted = satellite.model.satnum == highlighted_catalog_id
        orbit_color = "#f4c95d" if highlighted else orbit_colors[index % len(orbit_colors)]
        figure.add_trace(
            go.Scatter3d(
                x=[point[0] for point in orbit],
                y=[point[1] for point in orbit],
                z=[point[2] for point in orbit],
                mode="lines",
                line={"color": orbit_color, "width": 6 if highlighted else 3},
                name=f"{satellite.name} orbit",
                opacity=1.0 if highlighted else 0.72,
                hovertemplate=(
                    f"{satellite.name}<br>Altitude: "
                    "%{customdata:.0f} km<extra></extra>"
                ),
                customdata=subpoints.elevation.km,
            )
        )
        current = to_cartesian(
            float(latitudes[0]),
            float(longitudes[0]),
            float(radii[0]),
        )
        figure.add_trace(
            go.Scatter3d(
                x=[current[0]],
                y=[current[1]],
                z=[current[2]],
                mode="markers",
                marker={
                    "size": 10 if highlighted else 6,
                    "color": orbit_color,
                    "line": {"color": "#172b2a", "width": 1},
                },
                name=f"{satellite.name}{' · highlighted' if highlighted else ''}",
                hovertemplate=(
                    f"{satellite.name}<br>Altitude: "
                    f"{float(subpoints.elevation.km[0]):.1f} km<extra></extra>"
                ),
            )
        )
        max_orbit_radius = max(max_orbit_radius, float(max(radii)))

    max_radius = max_orbit_radius * 1.08
    figure.update_layout(
        height=620,
        margin={"l": 0, "r": 0, "t": 10, "b": 0},
        paper_bgcolor="#f5f6f2",
        plot_bgcolor="#f5f6f2",
        legend={"orientation": "h", "y": 0.02, "x": 0.02},
        scene={
            "bgcolor": "#f5f6f2",
            "aspectmode": "cube",
            "aspectratio": {"x": 1, "y": 1, "z": 1},
            "xaxis": {"visible": False, "range": [-max_radius, max_radius]},
            "yaxis": {"visible": False, "range": [-max_radius, max_radius]},
            "zaxis": {"visible": False, "range": [-max_radius, max_radius]},
            "camera": {"eye": {"x": 1.6, "y": 1.6, "z": 1.2}},
        },
    )
    return figure


def make_osm_map(
    track: pd.DataFrame,
    observer_latitude: float,
    observer_longitude: float,
    observer_name: str,
    satellite_name: str,
    satellite_latitude: float,
    satellite_longitude: float,
) -> folium.Map:
    orbital_map = folium.Map(
        location=[observer_latitude, observer_longitude],
        zoom_start=3,
        tiles="OpenStreetMap",
        control_scale=True,
    )
    folium.Marker(
        [observer_latitude, observer_longitude],
        tooltip=f"Observer: {observer_name}",
        icon=folium.Icon(color="blue", icon="eye", prefix="fa"),
    ).add_to(orbital_map)
    folium.CircleMarker(
        [satellite_latitude, satellite_longitude],
        radius=7,
        color="#d45b43",
        fill=True,
        fill_opacity=0.9,
        tooltip=f"{satellite_name} current subpoint",
    ).add_to(orbital_map)

    segments = [[]]
    previous_longitude = None
    for latitude, longitude in track[["Latitude", "Longitude"]].itertuples(index=False):
        if previous_longitude is not None and abs(longitude - previous_longitude) > 180:
            segments.append([])
        segments[-1].append((latitude, longitude))
        previous_longitude = longitude
    for segment in segments:
        if len(segment) > 1:
            folium.PolyLine(
                segment,
                color="#147d72",
                weight=3,
                opacity=0.85,
                tooltip="Projected ground track · next 100 minutes",
            ).add_to(orbital_map)
    return orbital_map


def predict_passes(
    satellite: EarthSatellite,
    observer: GeographicPosition,
    hours: int,
    min_elevation: float,
) -> pd.DataFrame:
    start = TIMESCALE.now()
    end = TIMESCALE.from_datetime(datetime.now(timezone.utc) + timedelta(hours=hours))
    times, events = satellite.find_events(
        observer, start, end, altitude_degrees=min_elevation
    )
    passes = []
    rise = None
    culmination = None
    for time, event in zip(times, events):
        if event == 0:
            rise, culmination = time, None
        elif event == 1 and rise is not None:
            culmination = time
        elif event == 2 and rise is not None:
            passes.append(
                {
                    "AOS (UTC)": rise.utc_datetime().strftime("%b %d, %H:%M:%S"),
                    "Peak (UTC)": culmination.utc_datetime().strftime("%H:%M:%S")
                    if culmination is not None
                    else "—",
                    "LOS (UTC)": time.utc_datetime().strftime("%H:%M:%S"),
                    "Peak elevation": f"{(satellite - observer).at(culmination).altaz()[0].degrees:.1f}°"
                    if culmination is not None
                    else "—",
                }
            )
            rise, culmination = None, None
    return pd.DataFrame(passes)


def record_element_changes(records: list[dict]) -> list[dict]:
    """Persist latest elements and compare successive predictions at a common epoch."""
    try:
        history = json.loads(HISTORY_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        history = {}

    changes = []
    updated = False
    comparison_time = TIMESCALE.now()
    for record in records:
        satellite = make_satellite(record)
        catalog_id = str(satellite.model.satnum)
        previous = history.get(catalog_id)
        epoch = satellite.epoch.utc_iso()
        if previous and previous.get("epoch") != epoch:
            try:
                old_satellite = make_satellite(previous["elements"])
                separation_km = (
                    satellite.at(comparison_time).position.km
                    - old_satellite.at(comparison_time).position.km
                )
                distance_km = float((separation_km**2).sum() ** 0.5)
                changes.append(
                    {
                        "Satellite": satellite.name,
                        "NORAD ID": catalog_id,
                        "Previous TLE": previous["epoch"],
                        "Current TLE": epoch,
                        "Prediction shift": f"{distance_km:.1f} km",
                        "Assessment": (
                            "Large element shift"
                            if distance_km >= 25
                            else "Noticeable element shift"
                            if distance_km >= 5
                            else "Routine update"
                        ),
                    }
                )
            except (KeyError, ValueError, TypeError):
                pass
        if not previous or previous.get("epoch") != epoch:
            history[catalog_id] = {"epoch": epoch, "elements": record}
            updated = True

    if updated:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(json.dumps(history, indent=2))
    return sorted(changes, key=lambda item: float(item["Prediction shift"].split()[0]), reverse=True)


st.title("Orbit Desk")
st.caption("Satellite orbital estimates · public CelesTrak elements and OpenStreetMap")

if "available_norad_ids" not in st.session_state:
    st.session_state.available_norad_ids = [19751]
if "globe_norad_ids" not in st.session_state:
    st.session_state.globe_norad_ids = [19751]
if "norad_id" not in st.session_state:
    st.session_state.norad_id = 19751

with st.sidebar:
    st.subheader("Observer")
    location_name = st.selectbox("Location", options=list(LOCATION_PRESETS))
    preset_coordinates = LOCATION_PRESETS[location_name]
    if preset_coordinates is None:
        latitude = st.number_input("Latitude", min_value=-90.0, max_value=90.0, value=55.7558, step=0.01)
        longitude = st.number_input("Longitude", min_value=-180.0, max_value=180.0, value=37.6173, step=0.01)
    else:
        latitude, longitude = preset_coordinates

    with st.form("norad_lookup"):
        norad_input = st.text_input("NORAD ID", value=str(st.session_state.norad_id))
        lookup_submitted = st.form_submit_button("Add satellite", width="stretch")
    lookup_error = None
    if lookup_submitted:
        try:
            entered_id = int(norad_input.strip())
            if not 1 <= entered_id <= 339999:
                raise ValueError
            st.session_state.norad_id = entered_id
            if entered_id not in st.session_state.available_norad_ids:
                st.session_state.available_norad_ids.append(entered_id)
            if entered_id not in st.session_state.globe_norad_ids:
                if len(st.session_state.globe_norad_ids) >= 8:
                    lookup_error = "Remove a satellite from the globe before adding another (maximum 8)."
                else:
                    st.session_state.globe_norad_ids.append(entered_id)
        except ValueError:
            lookup_error = "Enter a NORAD ID between 1 and 339999."
    if lookup_error:
        st.error(lookup_error)

    selected_norad_ids = st.multiselect(
        "Satellites on globe",
        options=st.session_state.available_norad_ids,
        key="globe_norad_ids",
        max_selections=8,
        format_func=lambda catalog_id: f"NORAD {catalog_id}",
    )

    min_elevation = st.slider("Minimum pass elevation", min_value=0, max_value=30, value=10)
    horizon_hours = st.select_slider("Prediction window", options=[6, 12, 24, 48], value=24, format_func=lambda value: f"{value} h")

if not selected_norad_ids:
    st.info("Add a NORAD ID or select a satellite to display its orbit.")
    st.stop()

tle_records = []
stale_catalog_ids = []
fetch_errors = []
with st.spinner(f"Loading {len(selected_norad_ids)} selected satellite(s)…"):
    for catalog_id in selected_norad_ids:
        try:
            record, using_saved_data = fetch_tles(catalog_id)
            tle_records.append(record)
            if using_saved_data:
                stale_catalog_ids.append(catalog_id)
        except Exception as error:
            fetch_errors.append((catalog_id, error))

for catalog_id, error in fetch_errors:
    st.warning(f"NORAD {catalog_id} could not be loaded: {error}")
if not tle_records:
    st.error("No selected satellite has orbital elements available.")
    st.stop()
if stale_catalog_ids:
    st.warning(
        "Showing saved orbital elements for NORAD "
        + ", ".join(map(str, stale_catalog_ids))
        + "; positions and flyover predictions may be stale."
    )

satellite_by_id = {
    satellite.model.satnum: satellite
    for satellite in (make_satellite(record) for record in tle_records)
}
satellite_name_by_id = {
    satellite.model.satnum: satellite.name for satellite in satellite_by_id.values()
}
if st.session_state.get("highlighted_norad_id") not in satellite_by_id:
    st.session_state.highlighted_norad_id = next(iter(satellite_by_id))
with st.sidebar:
    highlighted_catalog_id = st.selectbox(
        "Highlight satellite",
        options=list(satellite_by_id),
        key="highlighted_norad_id",
        format_func=lambda catalog_id: f"{satellite_name_by_id[catalog_id]} · {catalog_id}",
    )

selected_satellite = satellite_by_id[highlighted_catalog_id]
observer = wgs84.latlon(latitude, longitude)
state = satellite_state(selected_satellite, observer)
track = ground_track(selected_satellite)
element_changes = record_element_changes(tle_records)

overview, flyovers, changes_tab, sources = st.tabs(
    ["Orbit overview", "Flyovers", "Element changes", "Sources & limits"]
)

with overview:
    metric_columns = st.columns(4)
    metric_columns[0].metric("Satellites in view", str(len(satellite_by_id)))
    metric_columns[1].metric("Altitude", f"{state['altitude_km']:,.1f} km")
    metric_columns[2].metric("Orbital speed", f"{state['speed_km_s']:.2f} km/s")
    metric_columns[3].metric("Observer elevation", f"{state['elevation']:.1f}°")

    st.subheader(f"3D globe · {len(satellite_by_id)} predicted orbit(s)")
    st.plotly_chart(
        make_globe_figure(list(satellite_by_id.values()), highlighted_catalog_id),
        width="stretch",
    )
    st.caption(
        f"Highlighted: {selected_satellite.name} · NORAD {highlighted_catalog_id}. "
        "Drag to rotate; scroll to zoom. Positions are propagated estimates, not live telemetry."
    )

    st.subheader(f"Ground track · next 100 minutes · {location_name}")
    orbital_map = make_osm_map(
        track,
        latitude,
        longitude,
        location_name,
        selected_satellite.name,
        state["latitude"],
        state["longitude"],
    )
    st_folium(orbital_map, height=520, width=1100, returned_objects=[])

    left, right = st.columns(2)
    with left:
        st.metric("Current subpoint", f"{state['latitude']:.3f}°, {state['longitude']:.3f}°")
        st.metric("Range to observer", f"{state['range_km']:,.1f} km")
    with right:
        st.metric("Azimuth from observer", f"{state['azimuth']:.1f}°")
        st.caption(f"Element epoch: {selected_satellite.epoch.utc_datetime().strftime('%Y-%m-%d %H:%M UTC')}")

with flyovers:
    st.subheader(f"Upcoming passes over {latitude:.3f}°, {longitude:.3f}°")
    passes = predict_passes(selected_satellite, observer, horizon_hours, min_elevation)
    if passes.empty:
        st.info(f"No complete passes above {min_elevation}° found in the next {horizon_hours} hours.")
    else:
        st.dataframe(passes, width="stretch", hide_index=True)
    st.caption("Times are UTC. AOS/LOS are approximate crossings of the selected elevation mask.")

with changes_tab:
    st.subheader("Orbital element updates")
    st.caption("Changes compare consecutive element-based position predictions at a common time.")
    if element_changes:
        st.dataframe(pd.DataFrame(element_changes), width="stretch", hide_index=True)
    else:
        st.info("No previous local snapshot is available yet. Changes will appear after newer TLEs are published.")
    st.warning("A position shift is an alert for review, not confirmation of a maneuver. TLE noise, age, and model error can also cause shifts.")

with sources:
    st.subheader("Data source")
    st.markdown("[CelesTrak General Perturbations (GP) data](https://celestrak.org/NORAD/elements/gp.php)")
    st.write("Enter a NORAD catalog number to retrieve its orbital elements. The lookup can include objects from any operator; a NORAD ID does not itself establish ownership or nationality.")
    st.subheader("What these numbers mean")
    st.write("General Perturbations (GP) element sets describe estimated orbits, not live spacecraft telemetry. Position, altitude, speed, ground tracks, and passes here are propagated estimates. This app does not establish intent or confirm a maneuver.")
    st.write("Flyover predictions depend on the selected observer coordinates and minimum elevation. Local element snapshots are stored in `data/element_history.json` to compare updates between app runs.")