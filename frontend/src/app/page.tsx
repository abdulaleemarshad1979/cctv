"use client";

import { useEffect, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
const HLS_URL = process.env.NEXT_PUBLIC_HLS_URL || "http://localhost:8088";

interface Camera {
  id: string;
  name: string;
  location: string;
  stream_path: string;
  stream_url: string;
  status: string;
  error_type: string | null;
}

const DEFAULT_VIDEO_OPTIONS = [
  { value: "Videos/Kumbh.mp4", label: "Kumbh Crowd (Normal)" },
  { value: "Videos/mecca.mp4", label: "Mecca Crowd (High Density)" },
  { value: "Videos/Crowd.mp4", label: "City Crowd (Dense Movement)" },
  { value: "Videos/Rjy.mp4", label: "Rajahmundry Ghat (Static)" },
  { value: "Videos/K.mp4", label: "Festival Crowd (Rapid Flow)" },
  { value: "custom", label: "Custom RTMP/RTSP/MP4 Url..." }
];

export default function Home() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [filter, setFilter] = useState<string>("All"); // "All", "Anantapur", "Anantapuramu", "Anakapalle", "Active"
  const [loading, setLoading] = useState(true);
  const [backendConnected, setBackendConnected] = useState(false);

  // States for stream control inputs per camera ID
  const [selectedSources, setSelectedSources] = useState<Record<string, string>>({});
  const [customSources, setCustomSources] = useState<Record<string, string>>({});
  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({});

  // Fetch cameras from backend
  const fetchCameras = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/cameras`);
      if (res.ok) {
        const data = await res.json();
        setCameras(data);
        setBackendConnected(true);
      } else {
        setBackendConnected(false);
      }
    } catch (err) {
      console.error("Failed to fetch camera states:", err);
      setBackendConnected(false);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCameras();
    const interval = setInterval(fetchCameras, 3000);
    return () => clearInterval(interval);
  }, []);

  // Map user's location filters to DB camera locations
  const filteredCameras = cameras.filter((cam) => {
    if (filter === "Anantapur") return cam.location === "Rjy";
    if (filter === "Anantapuramu") return cam.location === "Pushkaralu";
    if (filter === "Anakapalle") return cam.location === "Pushkaralu Swarm";
    if (filter === "Active") return cam.status === "online";
    return true;
  });

  // Call start stream backend API
  const handleStartStream = async (camId: string) => {
    const selectedValue = selectedSources[camId] || "Videos/Kumbh.mp4";
    let finalSource = selectedValue;
    if (selectedValue === "custom") {
      finalSource = customSources[camId] || "";
      if (!finalSource) {
        alert("Please enter a custom video or RTSP URL.");
        return;
      }
    }

    setActionLoading((prev) => ({ ...prev, [camId]: true }));
    try {
      const res = await fetch(`${BACKEND_URL}/cameras/${camId}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_url: finalSource }),
      });
      if (res.ok) {
        // Wait briefly for process setup
        setTimeout(fetchCameras, 1000);
      } else {
        alert(`Failed to start stream: ${res.statusText}`);
      }
    } catch (err) {
      console.error(err);
      alert("Error contacting the backend server.");
    } finally {
      setActionLoading((prev) => ({ ...prev, [camId]: false }));
    }
  };

  // Call stop stream backend API
  const handleStopStream = async (camId: string) => {
    setActionLoading((prev) => ({ ...prev, [camId]: true }));
    try {
      const res = await fetch(`${BACKEND_URL}/cameras/${camId}/stop`, {
        method: "POST",
      });
      if (res.ok) {
        setTimeout(fetchCameras, 1000);
      } else {
        alert(`Failed to stop stream: ${res.statusText}`);
      }
    } catch (err) {
      console.error(err);
      alert("Error stopping the stream.");
    } finally {
      setActionLoading((prev) => ({ ...prev, [camId]: false }));
    }
  };

  const getStatusContent = (cam: Camera) => {
    if (cam.status === "online") {
      return (
        <div className="relative w-full h-full bg-black flex items-center justify-center overflow-hidden">
          {/* Live Video Player using HLS iframe pointing to the specific stream path */}
          <iframe
            src={`${HLS_URL}/${cam.stream_path}/`}
            className="w-full h-full border-0 absolute inset-0"
            allow="autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
          />
          {/* Overlay Status Badge */}
          <div className="absolute top-3 left-3 flex items-center space-x-1.5 bg-slate-950/80 backdrop-blur px-2.5 py-1 rounded border border-emerald-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping" />
            <span className="text-emerald-400 font-bold uppercase text-[9px] tracking-widest">LIVE FEED</span>
          </div>

          {/* Floating Stop Stream Button */}
          <div className="absolute bottom-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity duration-200 z-10">
            <button
              onClick={() => handleStopStream(cam.id)}
              disabled={actionLoading[cam.id]}
              className="bg-red-600 hover:bg-red-500 text-white font-semibold text-[10px] uppercase tracking-wider py-1.5 px-3 rounded-lg shadow-lg hover:shadow-red-950/50 hover:scale-105 active:scale-95 transition-all duration-200 flex items-center gap-1"
            >
              {actionLoading[cam.id] ? (
                <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />
              ) : (
                <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M5.25 7.5A2.25 2.25 0 017.5 5.25h9a2.25 2.25 0 012.25 2.25v9a2.25 2.25 0 01-2.25 2.25h-9a2.25 2.25 0 01-2.25-2.25v-9z" />
                </svg>
              )}
              Stop Stream
            </button>
          </div>
        </div>
      );
    }

    // Default error/offline states
    let errorText = "Drone stream not found, retrying in some seconds";
    let isError = false;
    let isAuthError = false;

    if (cam.error_type === "authentication failed") {
      errorText = "Error: authentication failed, retrying in some seconds";
      isAuthError = true;
      isError = true;
    } else if (cam.error_type === "authorization failed") {
      errorText = "Error: authorization failed, retrying in some seconds";
      isError = true;
    }

    const selectedValue = selectedSources[cam.id] || "Videos/Kumbh.mp4";

    return (
      <div className="flex flex-col items-center justify-between h-full p-4 bg-[#0a0f1d] border-t border-slate-900">
        
        {/* Offline status and reconnect loading */}
        <div className="flex-1 flex flex-col items-center justify-center text-center py-4">
          <p className={`font-medium text-xs leading-relaxed max-w-[240px] ${
            isAuthError ? "text-red-400" : isError ? "text-amber-500" : "text-slate-400"
          }`}>
            {errorText}
          </p>
          <div className="mt-2.5 flex space-x-1.5 justify-center items-center">
            <span className={`w-1.5 h-1.5 rounded-full animate-bounce delay-100 ${
              isAuthError ? "bg-red-400" : isError ? "bg-amber-500" : "bg-slate-500"
            }`} />
            <span className={`w-1.5 h-1.5 rounded-full animate-bounce delay-200 ${
              isAuthError ? "bg-red-400" : isError ? "bg-amber-500" : "bg-slate-500"
            }`} />
            <span className={`w-1.5 h-1.5 rounded-full animate-bounce delay-300 ${
              isAuthError ? "bg-red-400" : isError ? "bg-amber-500" : "bg-slate-500"
            }`} />
          </div>
        </div>

        {/* Input Panel for Stream Activation */}
        <div className="w-full bg-[#11192e] p-3 rounded-lg border border-slate-800/80 flex flex-col space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[9px] uppercase tracking-wider text-blue-400 font-bold">Simulator Launcher</span>
            <span className="text-[8px] text-slate-500 font-mono">Stream target: {cam.stream_path}</span>
          </div>

          <div className="flex gap-2">
            <div className="flex-1 flex flex-col gap-1.5">
              <select
                value={selectedValue}
                onChange={(e) => setSelectedSources((prev) => ({ ...prev, [cam.id]: e.target.value }))}
                className="bg-[#080d1a] text-slate-300 text-[11px] rounded border border-slate-800 p-1.5 w-full outline-none focus:border-blue-500/50"
              >
                {DEFAULT_VIDEO_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>

              {selectedValue === "custom" && (
                <input
                  type="text"
                  placeholder="Paste video path or RTMP/RTSP URL"
                  value={customSources[cam.id] || ""}
                  onChange={(e) => setCustomSources((prev) => ({ ...prev, [cam.id]: e.target.value }))}
                  className="bg-[#080d1a] text-slate-200 text-[11px] rounded border border-slate-800 p-1.5 w-full outline-none focus:border-blue-500/50 placeholder:text-slate-600"
                />
              )}
            </div>

            <button
              onClick={() => handleStartStream(cam.id)}
              disabled={actionLoading[cam.id]}
              className="bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 text-white rounded font-bold text-[11px] px-3.5 tracking-wider hover:scale-105 active:scale-95 transition-all duration-200 flex items-center justify-center shrink-0"
            >
              {actionLoading[cam.id] ? (
                <div className="w-3.5 h-3.5 border-2 border-white/20 border-t-white rounded-full animate-spin" />
              ) : (
                "LAUNCH"
              )}
            </button>
          </div>
        </div>

      </div>
    );
  };

  return (
    <div className="min-h-screen bg-[#eaeeef] text-slate-900 font-sans antialiased selection:bg-blue-500/20 selection:text-blue-900">
      
      {/* Light-theme AP Police Branding Header */}
      <header className="sticky top-0 z-50 bg-gradient-to-b from-white to-slate-50 border-b border-slate-200 px-6 py-4 flex flex-col md:flex-row items-center justify-between gap-4 shadow-sm">
        
        {/* Left Side: National Emblem & Logo */}
        <div className="flex items-center space-x-4">
          
          {/* Authentic Emblem Layout */}
          <div className="flex items-center space-x-2 bg-gradient-to-r from-blue-900 to-blue-950 px-3 py-2 rounded-xl shadow-md shadow-blue-950/20">
            {/* India National Emblem representation SVG */}
            <svg className="w-9 h-9 text-amber-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9 9 0 100-18 9 9 0 000 18z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 7v10M12 9a3 3 0 000 6M9 12h6M8 9.5l8 5M16 9.5l-8 5" />
            </svg>
            <div className="h-6 w-[1px] bg-blue-800" />
            {/* AP Police Shield SVG */}
            <svg className="w-9 h-9 text-slate-100" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.746 3.746 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z" />
            </svg>
          </div>

          <div>
            <h1 className="text-xl font-black text-blue-950 tracking-wide uppercase leading-none">
              Andhra Pradesh Police Department
            </h1>
            <p className="text-xs text-blue-700 font-bold tracking-wider mt-1 uppercase">
              NTR District Police - COMF
            </p>
            <p className="text-[10px] text-slate-500 font-mono tracking-tight mt-0.5">
              Centralized Drone Monitoring Portal - ICDMP
            </p>
          </div>
        </div>

        {/* Center/Right Leadership Photo Avatars */}
        <div className="flex items-center space-x-6">
          <div className="flex items-center space-x-3 bg-slate-100 border border-slate-200 py-1.5 px-3.5 rounded-xl">
            {/* CM Portrait Avatar */}
            <div className="text-center">
              <div className="w-10 h-10 rounded-full border-2 border-amber-500 bg-slate-300 flex items-center justify-center font-bold text-xs text-blue-900 shadow-sm overflow-hidden relative">
                {/* Fallback initials styling representing leadership portraits in visual */}
                <div className="absolute inset-0 bg-gradient-to-tr from-blue-950 to-blue-800 flex items-center justify-center text-[10px] text-amber-300 font-black">
                  CM
                </div>
              </div>
              <p className="text-[8px] font-extrabold text-blue-950 mt-1 uppercase tracking-tight">Hon'ble CM</p>
            </div>
            
            {/* DGP Portrait Avatar */}
            <div className="text-center">
              <div className="w-10 h-10 rounded-full border-2 border-amber-500 bg-slate-300 flex items-center justify-center font-bold text-xs text-blue-900 shadow-sm overflow-hidden relative">
                <div className="absolute inset-0 bg-gradient-to-tr from-blue-950 to-blue-800 flex items-center justify-center text-[10px] text-amber-300 font-black">
                  DGP
                </div>
              </div>
              <p className="text-[8px] font-extrabold text-blue-950 mt-1 uppercase tracking-tight">DGP AP</p>
            </div>

            {/* SP Portrait Avatar */}
            <div className="text-center">
              <div className="w-10 h-10 rounded-full border-2 border-amber-500 bg-slate-300 flex items-center justify-center font-bold text-xs text-blue-900 shadow-sm overflow-hidden relative">
                <div className="absolute inset-0 bg-gradient-to-tr from-blue-950 to-blue-800 flex items-center justify-center text-[10px] text-amber-300 font-black">
                  SP
                </div>
              </div>
              <p className="text-[8px] font-extrabold text-blue-950 mt-1 uppercase tracking-tight">SP NTR</p>
            </div>
          </div>
        </div>

      </header>

      {/* Subheader Filter and Status Area */}
      <section className="bg-white border-b border-slate-200/80 px-6 py-3 flex flex-wrap items-center justify-between gap-4 shadow-xs">
        
        {/* District Filter Pills */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setFilter("All")}
            className={`px-4.5 py-1.5 rounded-full text-xs font-bold tracking-wide transition-all duration-200 border ${
              filter === "All"
                ? "bg-blue-900 border-blue-950 text-white shadow-md shadow-blue-950/20"
                : "bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200"
            }`}
          >
            All Sectors
          </button>
          <button
            onClick={() => setFilter("Anantapur")}
            className={`px-4.5 py-1.5 rounded-full text-xs font-bold tracking-wide transition-all duration-200 border ${
              filter === "Anantapur"
                ? "bg-blue-900 border-blue-950 text-white shadow-md shadow-blue-950/20"
                : "bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200"
            }`}
          >
            Anantapur
          </button>
          <button
            onClick={() => setFilter("Anantapuramu")}
            className={`px-4.5 py-1.5 rounded-full text-xs font-bold tracking-wide transition-all duration-200 border ${
              filter === "Anantapuramu"
                ? "bg-blue-900 border-blue-950 text-white shadow-md shadow-blue-950/20"
                : "bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200"
            }`}
          >
            Anantapuramu
          </button>
          <button
            onClick={() => setFilter("Anakapalle")}
            className={`px-4.5 py-1.5 rounded-full text-xs font-bold tracking-wide transition-all duration-200 border ${
              filter === "Anakapalle"
                ? "bg-blue-900 border-blue-950 text-white shadow-md shadow-blue-950/20"
                : "bg-slate-100 border-slate-200 text-slate-700 hover:bg-slate-200"
            }`}
          >
            Anakapalle
          </button>
          
          <div className="h-4 w-[1px] bg-slate-300 mx-2" />

          <button
            onClick={() => setFilter("Active")}
            className={`px-4.5 py-1.5 rounded-full text-xs font-bold tracking-wide transition-all duration-200 border flex items-center gap-1.5 ${
              filter === "Active"
                ? "bg-emerald-600 border-emerald-700 text-white shadow-md"
                : "bg-emerald-50/50 border-emerald-200/80 text-emerald-700 hover:bg-emerald-100/50"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full bg-emerald-500 ${filter === "Active" ? "bg-white" : "animate-pulse"}`} />
            Active Streams
          </button>
        </div>

        {/* Port / Connection Status */}
        <div className="flex items-center space-x-3">
          <div className={`text-[11px] font-bold px-3 py-1.5 rounded-lg border flex items-center gap-2 ${
            backendConnected
              ? "bg-emerald-50 text-emerald-700 border-emerald-200"
              : "bg-red-50 text-red-700 border-red-200"
          }`}>
            <span className={`w-1.5 h-1.5 rounded-full ${backendConnected ? "bg-emerald-500" : "bg-red-500 animate-ping"}`} />
            <span>{backendConnected ? "API SERVER: CONNECTED" : "API SERVER: OFFLINE"}</span>
          </div>

          <button
            onClick={() => alert("Logging out of Centralized Command Center...")}
            className="px-4 py-1.5 rounded-full text-xs font-bold bg-red-50 border border-red-200 text-red-600 hover:bg-red-100 transition-all duration-200"
          >
            Logout
          </button>
        </div>

      </section>

      {/* Grid Content Area */}
      <main className="p-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center min-h-[50vh] space-y-4">
            <div className="w-10 h-10 border-4 border-blue-900/10 border-t-blue-900 rounded-full animate-spin" />
            <p className="text-sm font-semibold text-slate-500">Connecting to monitor server...</p>
          </div>
        ) : filteredCameras.length === 0 ? (
          <div className="flex flex-col items-center justify-center min-h-[50vh] text-center p-8 bg-white rounded-2xl border border-slate-200 shadow-sm">
            <svg className="w-12 h-12 text-slate-400 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            <p className="text-slate-600 font-bold">No active feeds or drones match the selected sector</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredCameras.map((cam) => (
              <div
                key={cam.id}
                className="bg-[#0f172a] rounded-xl border border-slate-800/80 overflow-hidden flex flex-col h-[320px] shadow-md hover:shadow-xl hover:border-slate-700/85 transition-all duration-300 group"
              >
                
                {/* Tile Header Bar matching visual card layout */}
                <div className="bg-[#1e293b] px-4 py-2.5 border-b border-slate-800/80 flex items-center justify-between">
                  <span className="text-xs font-black tracking-wider text-slate-100 uppercase">
                    {cam.name}
                  </span>
                  <div className="flex items-center space-x-2">
                    <span className="text-[10px] text-slate-400 font-bold uppercase bg-slate-950/40 px-2 py-0.5 rounded">
                      {cam.location}
                    </span>
                    <span
                      className={`w-2.5 h-2.5 rounded-full border border-slate-950 ${
                        cam.status === "online"
                          ? "bg-emerald-500 animate-pulse"
                          : cam.status === "error"
                          ? "bg-red-500"
                          : "bg-slate-600"
                      }`}
                    />
                  </div>
                </div>

                {/* Stream Content View */}
                <div className="flex-1 relative overflow-hidden bg-slate-950/20">
                  {getStatusContent(cam)}
                </div>

              </div>
            ))}
          </div>
        )}
      </main>

    </div>
  );
}
