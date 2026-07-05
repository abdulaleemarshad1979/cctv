"use client";

import { useEffect, useState } from "react";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8000";
const HLS_URL = process.env.NEXT_PUBLIC_HLS_URL || "http://localhost:8088";

interface Camera {
  id: string;
  name: string;
  location: string;
  stream_url: string;
  status: string;
  error_type: string | null;
}

export default function Home() {
  const [cameras, setCameras] = useState<Camera[]>([]);
  const [filter, setFilter] = useState<string>("All"); // "All", "Rjy", "Pushkaralu", "Active"
  const [loading, setLoading] = useState(true);

  // Fetch cameras from backend
  const fetchCameras = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/cameras`);
      if (res.ok) {
        const data = await res.json();
        setCameras(data);
      }
    } catch (err) {
      console.error("Failed to fetch camera states:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCameras();
    const interval = setInterval(fetchCameras, 3000);
    return () => clearInterval(interval);
  }, []);

  const filteredCameras = cameras.filter((cam) => {
    if (filter === "Rjy") return cam.location === "Rjy";
    if (filter === "Pushkaralu") return cam.location === "Pushkaralu";
    if (filter === "Active") return cam.status === "online";
    return true;
  });

  const getStatusContent = (cam: Camera) => {
    if (cam.status === "online") {
      return (
        <div className="relative w-full h-full bg-black flex items-center justify-center overflow-hidden">
          {/* Live Video Player using HLS iframe pointing to port 8088 */}
          <iframe
            src={`${HLS_URL}/live/${cam.id}/`}
            className="w-full h-full border-0"
            allow="autoplay; encrypted-media; picture-in-picture"
            allowFullScreen
          />
          <div className="absolute top-2 left-2 flex items-center space-x-1 bg-slate-900/80 px-2 py-0.5 rounded text-[10px]">
            <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-ping" />
            <span className="text-emerald-400 font-bold uppercase tracking-wider">LIVE</span>
          </div>
        </div>
      );
    }

    if (cam.error_type === "authentication failed") {
      return (
        <div className="flex flex-col items-center justify-center h-full p-6 text-center bg-slate-900/40">
          <p className="text-red-400 font-medium text-sm leading-relaxed max-w-[250px]">
            Error: authentication failed, retrying in some seconds
          </p>
          <div className="mt-4 flex space-x-1 justify-center items-center">
            <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-bounce delay-100" />
            <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-bounce delay-200" />
            <span className="w-1.5 h-1.5 rounded-full bg-red-400 animate-bounce delay-300" />
          </div>
        </div>
      );
    }

    if (cam.error_type === "authorization failed") {
      return (
        <div className="flex flex-col items-center justify-center h-full p-6 text-center bg-slate-900/40">
          <p className="text-amber-500 font-medium text-sm leading-relaxed max-w-[250px]">
            Error: authorization failed, retrying in some seconds
          </p>
          <div className="mt-4 flex space-x-1 justify-center items-center">
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-bounce delay-100" />
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-bounce delay-200" />
            <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-bounce delay-300" />
          </div>
        </div>
      );
    }

    // Default Offline / Stream Not Found
    return (
      <div className="flex flex-col items-center justify-center h-full p-6 text-center bg-slate-900/40">
        <p className="text-slate-300 font-normal text-sm leading-relaxed max-w-[250px]">
          Drone stream not found, retrying in some seconds
        </p>
        <div className="mt-4 flex space-x-1 justify-center items-center">
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-pulse delay-100" />
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-pulse delay-200" />
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-pulse delay-300" />
        </div>
      </div>
    );
  };

  return (
    <div className="min-h-screen bg-[#0d131f] text-slate-100 font-sans antialiased selection:bg-blue-500/30 selection:text-blue-200">
      
      {/* Premium Header */}
      <header className="sticky top-0 z-50 bg-[#0d131fa0] backdrop-blur-md border-b border-slate-800/80 px-6 py-4 flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center space-x-4">
          
          {/* Logo Group */}
          <div className="flex items-center space-x-2 bg-slate-900/60 p-2 rounded-xl border border-slate-800/50">
            {/* Government Emblem Symbol (SVG) */}
            <svg className="w-8 h-8 text-amber-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 21a9.004 9.004 0 008.716-6.747M12 21a9.004 9.004 0 01-8.716-6.747M12 21c2.485 0 4.5-4.03 4.5-9S14.485 3 12 3m0 18c-2.485 0-4.5-4.03-4.5-9S9.515 3 12 3m0 0a8.997 8.997 0 017.843 4.582M12 3a8.997 8.997 0 00-7.843 4.582m15.686 0A11.953 11.953 0 0112 10.5c-2.998 0-5.74-1.1-7.843-2.918m15.686 0A8.959 8.959 0 0121 12c0 .778-.099 1.533-.284 2.253m0 0A17.919 17.919 0 0112 16.5c-3.162 0-6.133-.815-8.716-2.247m0 0A9.015 9.015 0 013 12c0-.778.099-1.533.284-2.253" />
            </svg>
            {/* Police Shield Symbol (SVG) */}
            <svg className="w-8 h-8 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.746 3.746 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z" />
            </svg>
          </div>

          <div>
            <h1 className="text-lg font-bold text-slate-100 tracking-wide uppercase leading-none">
              Andhra Pradesh Police Department
            </h1>
            <p className="text-xs text-blue-400 font-semibold tracking-wider mt-1">
              NTR District Police - CDMF
            </p>
            <p className="text-[10px] text-slate-500 font-mono tracking-tight mt-0.5">
              Centralized Drone Monitoring Portal - ICDMP1
            </p>
          </div>
        </div>

        {/* Filters and Navigation */}
        <div className="flex flex-wrap items-center gap-2">
          <button
            onClick={() => setFilter("All")}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold tracking-wide transition-all duration-200 border ${
              filter === "All"
                ? "bg-blue-600 border-blue-500 text-white shadow-lg shadow-blue-900/40"
                : "bg-slate-900/60 border-slate-800 text-slate-300 hover:bg-slate-800"
            }`}
          >
            All
          </button>
          <button
            onClick={() => setFilter("Rjy")}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold tracking-wide transition-all duration-200 border ${
              filter === "Rjy"
                ? "bg-blue-600 border-blue-500 text-white shadow-lg shadow-blue-900/40"
                : "bg-slate-900/60 border-slate-800 text-slate-300 hover:bg-slate-800"
            }`}
          >
            Rjy
          </button>
          <button
            onClick={() => setFilter("Pushkaralu")}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold tracking-wide transition-all duration-200 border ${
              filter === "Pushkaralu"
                ? "bg-blue-600 border-blue-500 text-white shadow-lg shadow-blue-900/40"
                : "bg-slate-900/60 border-slate-800 text-slate-300 hover:bg-slate-800"
            }`}
          >
            Pushkaralu
          </button>
          <button
            onClick={() => setFilter("Active")}
            className={`px-4 py-1.5 rounded-full text-xs font-semibold tracking-wide transition-all duration-200 border ${
              filter === "Active"
                ? "bg-blue-600 border-blue-500 text-white shadow-lg shadow-blue-900/40"
                : "bg-slate-900/60 border-slate-800 text-slate-300 hover:bg-slate-800"
            }`}
          >
            Active Drones
          </button>
          
          <div className="h-6 w-[1px] bg-slate-800 mx-2 hidden sm:block" />

          <button
            onClick={() => alert("Logging out...")}
            className="px-4 py-1.5 rounded-full text-xs font-semibold tracking-wide bg-red-950/40 border border-red-900/50 text-red-400 hover:bg-red-900/30 transition-all duration-200"
          >
            Logout
          </button>
        </div>
      </header>

      {/* Grid Content */}
      <main className="p-6">
        {loading ? (
          <div className="flex flex-col items-center justify-center min-h-[60vh] space-y-4">
            <div className="w-10 h-10 border-4 border-blue-500/20 border-t-blue-500 rounded-full animate-spin" />
            <p className="text-sm text-slate-400">Loading monitoring grid...</p>
          </div>
        ) : filteredCameras.length === 0 ? (
          <div className="flex flex-col items-center justify-center min-h-[60vh] text-center p-8 bg-slate-900/20 rounded-2xl border border-dashed border-slate-800/80">
            <svg className="w-12 h-12 text-slate-600 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
            </svg>
            <p className="text-slate-400 font-medium">No cameras match the selected filter</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {filteredCameras.map((cam) => (
              <div
                key={cam.id}
                className="bg-[#121a28] rounded-xl border border-slate-800/80 overflow-hidden flex flex-col h-[280px] shadow-lg shadow-black/30 hover:border-slate-700/80 transition-all duration-350 hover:-translate-y-0.5 group"
              >
                
                {/* Tile Header */}
                <div className="bg-[#182336] px-4 py-2 border-b border-slate-800/80 flex items-center justify-between">
                  <span className="text-xs font-semibold tracking-wider text-slate-300 uppercase">
                    {cam.name}
                  </span>
                  <div className="flex items-center space-x-1.5">
                    <span className="text-[10px] text-slate-500 font-mono">
                      {cam.location}
                    </span>
                    <span
                      className={`w-2 h-2 rounded-full ${
                        cam.status === "online"
                          ? "bg-emerald-500 animate-pulse"
                          : cam.status === "error"
                          ? "bg-red-500"
                          : "bg-slate-500"
                      }`}
                    />
                  </div>
                </div>

                {/* Stream / Status Display */}
                <div className="flex-1 relative overflow-hidden bg-slate-950/30">
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
