import React, { useState, useEffect, useRef, useMemo } from 'react';
import { MapContainer, TileLayer, Marker, Popup, Circle } from 'react-leaflet';
import * as satellite from 'satellite.js';
import L from 'leaflet';

// Inject Leaflet CSS for the map to render correctly in environments without it pre-loaded
const injectLeafletCSS = () => {
  if (!document.getElementById('leaflet-css')) {
    const link = document.createElement('link');
    link.id = 'leaflet-css';
    link.rel = 'stylesheet';
    link.href = 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';
    document.head.appendChild(link);
  }
};

// Fallback TLE data for classified/military satellites in case CelesTrak is unreachable/CORS blocked
const FALLBACK_TLE = `USA 276 (NRO L-76)
1 42681U 17022A   23277.58333333  .00000000  00000-0  00000-0 0  9997
2 42681  50.0000 120.0000 0010000  90.0000 270.0000 15.20000000    00
USA 326 (NRO L-85)
1 51445U 22009A   23277.58333333  .00000000  00000-0  00000-0 0  9997
2 51445  97.4000  10.0000 0010000   0.0000 360.0000 15.00000000    00
X-37B OTV-6
1 45606U 20030A   23277.58333333  .00000000  00000-0  00000-0 0  9997
2 45606  45.0000  10.0000 0010000   0.0000 360.0000 15.50000000    00
KSM-1 (POLAND)
1 99999U 26001A   26276.50000000  .00000000  00000-0  00000-0 0  9991
2 99999  97.0000  20.0000 0010000  10.0000 350.0000 14.80000000    01`;

// Haversine formula to calculate distance between two coordinates in kilometers
const calculateDistance = (lat1, lon1, lat2, lon2) => {
  const R = 6371; // Earth radius in km
  const dLat = (lat2 - lat1) * (Math.PI / 180);
  const dLon = (lon2 - lon1) * (Math.PI / 180);
  const a = 
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1 * (Math.PI / 180)) * Math.cos(lat2 * (Math.PI / 180)) *
    Math.sin(dLon / 2) * Math.sin(dLon / 2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  return R * c;
};

// Custom SVG icon for satellites
const createSatIcon = (color = '#3b82f6') => new L.DivIcon({
  html: `<svg viewBox="0 0 24 24" fill="${color}" class="w-6 h-6 shadow-lg rounded-full" style="filter: drop-shadow(0 0 4px ${color});"><path d="M12 2L10 6H14L12 2Z" /><path d="M4 10L2 12L4 14V10Z" /><path d="M20 10V14L22 12L20 10Z" /><path d="M12 22L14 18H10L12 22Z" /><circle cx="12" cy="12" r="4" /></svg>`,
  className: 'bg-transparent',
  iconSize: [24, 24],
  iconAnchor: [12, 12],
  popupAnchor: [0, -12]
});

const defaultSatIcon = createSatIcon('#3b82f6');
const alertSatIcon = createSatIcon('#ef4444');

export default function App() {
  const [satellites, setSatellites] = useState([]);
  const [positions, setPositions] = useState({});
  const [alerts, setAlerts] = useState([]);
  
  // Default target location (Krakow, Poland based on context)
  const [targetLat, setTargetLat] = useState(50.0647);
  const [targetLon, setTargetLon] = useState(19.9450);
  const [flyoverRadius, setFlyoverRadius] = useState(500); // km

  // Setup Leaflet
  useEffect(() => {
    injectLeafletCSS();
  }, []);

  const fetchAndParseTLEs = async () => {
    let tleData = FALLBACK_TLE;
    try {
      // Attempting to fetch real data via a public CORS proxy
      const response = await fetch('https://api.allorigins.win/raw?url=https://celestrak.org/NORAD/elements/supplemental/sup-gp.php?FILE=classified&FORMAT=tle');
      if (response.ok) {
        tleData = await response.text();
      } else {
        console.warn("CelesTrak fetch failed, using fallback data.");
      }
    } catch (e) {
      console.warn("CORS/Network error, using fallback data.");
    }

    const lines = tleData.split('\n').map(l => l.trim()).filter(l => l.length > 0);
    const parsedSats = [];

    // Parse 3-line sets (Name, Line 1, Line 2)
    for (let i = 0; i < lines.length; i += 3) {
      if (i + 2 < lines.length && lines[i+1].startsWith('1 ') && lines[i+2].startsWith('2 ')) {
        const name = lines[i];
        const tle1 = lines[i+1];
        const tle2 = lines[i+2];
        
        try {
          const satrec = satellite.twoline2satrec(tle1, tle2);
          // Calculate Mean Motion (revolutions per day)
          const meanMotion = satrec.no * 1440.0 / (2.0 * Math.PI);
          
          parsedSats.push({
            id: name,
            name,
            satrec,
            originalMeanMotion: meanMotion,
            currentMeanMotion: meanMotion,
            tle1, tle2
          });
        } catch (err) {
          console.error(`Failed to parse TLE for ${name}`);
        }
      }
    }
    setSatellites(parsedSats);
    addAlert('System', `Ingested ${parsedSats.length} orbital trajectories.`, 'info');
  };

  useEffect(() => {
    fetchAndParseTLEs();
  }, []);

  useEffect(() => {
    if (satellites.length === 0) return;

    const interval = setInterval(() => {
      const now = new Date();
      const newPositions = {};
      const newAlerts = [];
      const currentAlerts = new Set(alerts.map(a => a.id));

      satellites.forEach(sat => {
        try {
          const positionAndVelocity = satellite.propagate(sat.satrec, now);
          const gmst = satellite.gstime(now);
          
          if (positionAndVelocity.position) {
            const positionGd = satellite.eciToGeodetic(positionAndVelocity.position, gmst);
            const lat = satellite.degreesLat(positionGd.latitude);
            const lon = satellite.degreesLong(positionGd.longitude);
            const alt = positionGd.height;

            newPositions[sat.id] = { lat, lon, alt };

            // Check Flyovers
            const distance = calculateDistance(lat, lon, targetLat, targetLon);
            if (distance < flyoverRadius) {
              const alertId = `flyover-${sat.id}-${Math.floor(Date.now() / 60000)}`; // One alert per minute per sat
              if (!currentAlerts.has(alertId)) {
                newAlerts.push({
                  id: alertId,
                  type: 'warning',
                  satName: sat.name,
                  message: `${sat.name} overhead! Distance: ${distance.toFixed(1)}km, Alt: ${alt.toFixed(1)}km`,
                  timestamp: now.toLocaleTimeString()
                });
              }
            }
          }
        } catch (e) {
          // Satrec propagation failed (often happens with old TLEs decaying)
        }
      });

      setPositions(newPositions);
      
      if (newAlerts.length > 0) {
        setAlerts(prev => [...newAlerts, ...prev].slice(0, 50)); // Keep last 50
      }
    }, 1000); // Update every second

    return () => clearInterval(interval);
  }, [satellites, targetLat, targetLon, flyoverRadius, alerts]);

  const addAlert = (satName, message, type) => {
    const newAlert = {
      id: Math.random().toString(),
      type,
      satName,
      message,
      timestamp: new Date().toLocaleTimeString()
    };
    setAlerts(prev => [newAlert, ...prev].slice(0, 50));
  };

  const simulateManeuver = (satId) => {
    setSatellites(prevSats => prevSats.map(sat => {
      if (sat.id === satId) {
        // Artificially change Mean Motion to simulate an orbit raise
        const delta = (Math.random() * 0.1) + 0.05; 
        const newMeanMotion = sat.currentMeanMotion - delta; 
        
        addAlert(sat.name, `MANEUVER DETECTED! Orbit raised. Mean motion Δ: -${delta.toFixed(4)} rev/day.`, 'danger');
        
        return { ...sat, currentMeanMotion: newMeanMotion };
      }
      return sat;
    }));
  };

  return (
    <div className="flex h-screen w-full bg-slate-950 text-slate-200 font-sans overflow-hidden">
      
      {/* Left Sidebar - Controls & Status */}
      <div className="w-1/3 max-w-md h-full flex flex-col border-r border-slate-800 bg-slate-900 shadow-2xl z-10 relative">
        <div className="p-5 border-b border-slate-800 bg-slate-950">
          <h1 className="text-2xl font-bold tracking-tight text-blue-400 flex items-center gap-2">
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M3.055 11H5a2 2 0 012 2v1a2 2 0 002 2 2 2 0 012 2v2.945M8 3.935V5.5A2.5 2.5 0 0010.5 8h.5a2 2 0 012 2 2 2 0 104 0 2 2 0 012-2h1.064M15 20.488V18a2 2 0 012-2h3.064M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
            ORBITAL COMMAND
          </h1>
          <p className="text-xs text-slate-500 mt-1 uppercase tracking-widest">Tactical Tracking & Maneuver Analysis</p>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar p-4 space-y-6">
          
          {/* Target Configuration */}
          <div className="bg-slate-800/50 p-4 rounded-xl border border-slate-700/50">
            <h2 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
              Target Sector (Flyover Zone)
            </h2>
            <div className="grid grid-cols-2 gap-3 mb-3">
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Latitude</label>
                <input type="number" value={targetLat} onChange={e => setTargetLat(Number(e.target.value))} className="w-full bg-slate-900 border border-slate-700 rounded p-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500 transition-colors" />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Longitude</label>
                <input type="number" value={targetLon} onChange={e => setTargetLon(Number(e.target.value))} className="w-full bg-slate-900 border border-slate-700 rounded p-2 text-sm text-slate-200 focus:outline-none focus:border-blue-500 transition-colors" />
              </div>
            </div>
            <div>
               <label className="text-xs text-slate-500 mb-1 block">Detection Radius (km)</label>
               <input type="range" min="100" max="2000" value={flyoverRadius} onChange={e => setFlyoverRadius(Number(e.target.value))} className="w-full accent-blue-500" />
               <div className="text-right text-xs text-slate-400">{flyoverRadius} km</div>
            </div>
          </div>

          {/* Active Satellites */}
          <div>
            <h2 className="text-sm font-semibold text-slate-300 mb-3 uppercase tracking-wider">Tracked Assets ({satellites.length})</h2>
            <div className="space-y-2">
              {satellites.map(sat => {
                const pos = positions[sat.id];
                const isManeuvering = sat.originalMeanMotion !== sat.currentMeanMotion;
                return (
                  <div key={sat.id} className={`p-3 rounded-lg border ${isManeuvering ? 'bg-red-900/20 border-red-700' : 'bg-slate-800 border-slate-700'} transition-all`}>
                    <div className="flex justify-between items-start mb-2">
                      <span className="font-medium text-blue-300">{sat.name}</span>
                      <button 
                        onClick={() => simulateManeuver(sat.id)}
                        className="text-[10px] bg-slate-700 hover:bg-red-600 text-white px-2 py-1 rounded transition-colors"
                        title="Simulate engine burn / orbit raise"
                      >
                        Simulate Burn
                      </button>
                    </div>
                    {pos ? (
                      <div className="grid grid-cols-3 gap-2 text-xs font-mono text-slate-400">
                        <div>Lat: <span className="text-slate-200">{pos.lat.toFixed(2)}°</span></div>
                        <div>Lon: <span className="text-slate-200">{pos.lon.toFixed(2)}°</span></div>
                        <div>Alt: <span className="text-slate-200">{pos.alt.toFixed(0)}km</span></div>
                      </div>
                    ) : (
                      <div className="text-xs text-slate-500 animate-pulse">Calculating telemetry...</div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        {/* Alerts Log */}
        <div className="h-64 border-t border-slate-800 bg-slate-950 flex flex-col">
          <div className="p-3 border-b border-slate-800 flex justify-between items-center bg-slate-900">
            <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-widest">Event Log</h2>
            <span className="text-xs bg-slate-800 text-slate-400 px-2 py-1 rounded-full">{alerts.length} events</span>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-2 custom-scrollbar">
            {alerts.length === 0 && <div className="text-xs text-slate-600 text-center mt-10 italic">No anomalous activity detected.</div>}
            {alerts.map(alert => (
              <div key={alert.id} className={`text-xs p-2 rounded border-l-2 ${alert.type === 'danger' ? 'border-red-500 bg-red-500/10 text-red-200' : alert.type === 'warning' ? 'border-amber-500 bg-amber-500/10 text-amber-200' : 'border-blue-500 bg-blue-500/10 text-blue-200'}`}>
                <div className="flex justify-between opacity-50 mb-1 text-[10px]">
                  <span>{alert.satName}</span>
                  <span>{alert.timestamp}</span>
                </div>
                <div>{alert.message}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Right Content - Map */}
      <div className="flex-1 bg-slate-900 relative">
        <MapContainer 
          center={[20, 0]} 
          zoom={3} 
          className="w-full h-full"
          zoomControl={false}
          worldCopyJump={true}
        >
          {/* Dark-themed tactical map tiles */}
          <TileLayer
            url="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; <a href="https://carto.com/attributions">CARTO</a>'
          />
          
          {/* Target Zone Indicator */}
          <Marker position={[targetLat, targetLon]} icon={new L.DivIcon({
            html: `<div class="w-4 h-4 bg-emerald-500 rounded-full animate-ping opacity-75"></div><div class="w-4 h-4 bg-emerald-500 rounded-full absolute top-0 left-0"></div>`,
            className: 'bg-transparent relative'
          })}>
            <Popup className="tactical-popup">
              <div className="text-center font-semibold text-slate-800">Observation Target</div>
              <div className="text-xs text-slate-500">{targetLat.toFixed(4)}, {targetLon.toFixed(4)}</div>
            </Popup>
          </Marker>
          <Circle center={[targetLat, targetLon]} radius={flyoverRadius * 1000} pathOptions={{ color: '#10b981', fillColor: '#10b981', fillOpacity: 0.1, weight: 1 }} />

          {/* Satellite Markers */}
          {satellites.map(sat => {
            const pos = positions[sat.id];
            if (!pos) return null;
            
            const isManeuvering = sat.originalMeanMotion !== sat.currentMeanMotion;
            const distance = calculateDistance(pos.lat, pos.lon, targetLat, targetLon);
            const isFlyover = distance < flyoverRadius;

            return (
              <Marker 
                key={sat.id} 
                position={[pos.lat, pos.lon]} 
                icon={isManeuvering || isFlyover ? alertSatIcon : defaultSatIcon}
              >
                <Popup className="tactical-popup">
                  <div className="font-bold text-sm mb-1">{sat.name}</div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-slate-600 font-mono">
                    <div>Alt:</div><div className="text-right">{pos.alt.toFixed(1)} km</div>
                    <div>Lat:</div><div className="text-right">{pos.lat.toFixed(4)}°</div>
                    <div>Lon:</div><div className="text-right">{pos.lon.toFixed(4)}°</div>
                    <div>Dist:</div><div className="text-right">{distance.toFixed(0)} km</div>
                  </div>
                </Popup>
              </Marker>
            );
          })}
        </MapContainer>

        {/* HUD Overlay layer */}
        <div className="absolute top-4 right-4 z-[400] pointer-events-none text-right">
          <div className="bg-slate-900/80 backdrop-blur border border-slate-700 text-blue-400 p-3 rounded-lg shadow-lg">
             <div className="text-xs uppercase tracking-widest opacity-75 mb-1">System Status</div>
             <div className="font-mono text-sm font-semibold flex items-center justify-end gap-2">
               <span className="w-2 h-2 rounded-full bg-emerald-500 shadow-[0_0_8px_#10b981]"></span>
               TRACKING ACTIVE
             </div>
          </div>
        </div>
      </div>
      
      {/* Global Styles injected for scrollbars and popups */}
      <style>{`
        .custom-scrollbar::-webkit-scrollbar { width: 6px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: #0f172a; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: #334155; border-radius: 3px; }
        .custom-scrollbar::-webkit-scrollbar-thumb:hover { background: #475569; }
        
        .tactical-popup .leaflet-popup-content-wrapper { background: #f8fafc; border-radius: 8px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5); }
        .tactical-popup .leaflet-popup-tip { background: #f8fafc; }
      `}</style>
    </div>
  );
}
