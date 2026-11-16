"use strict";
(() => {
  const root = document.querySelector("#fw-map-page");
  if (!root) return;

  const stationsDataEl = document.querySelector("#fw-stations-json");
  let stations = [];
  try {
    stations = JSON.parse(stationsDataEl?.textContent || "[]");
  } catch {
    stations = [];
  }

  const csrfToken = root.dataset.csrf || "";
  const stationsApiUrl = root.dataset.stationsUrl || "/api/stations/";
  const weatherApiUrl = root.dataset.weatherUrl || "/api/weather-risk/";
  const defaultLat = Number(root.dataset.centerLat || "16.2");
  const defaultLon = Number(root.dataset.centerLon || "107.85");

  const mapEl = document.querySelector("#fw-leaflet-map");
  const offlineEl = document.querySelector("#fw-map-offline");
  const stationListEl = document.querySelector("#fw-station-list");

  const form = document.querySelector("#fw-geo-form");
  const channelInput = document.querySelector("#geo-channel");
  const nameInput = document.querySelector("#geo-name");
  const locInput = document.querySelector("#geo-location");
  const latInput = document.querySelector("#geo-lat");
  const lonInput = document.querySelector("#geo-lon");
  const azimuthInput = document.querySelector("#geo-azimuth");
  const rangeInput = document.querySelector("#geo-range");
  const saveMsg = document.querySelector("#geo-save-msg");

  let map = null;
  let layerGroup = null;

  const STATUS_COLOR = {
    ALERTING: "#ef4444",
    ONLINE: "#10b981",
    IDLE: "#f59e0b",
    OFFLINE: "#6b7280",
  };
  const STATUS_TEXT = {
    ALERTING: "ĐANG BÁO KHÓI",
    ONLINE: "TRỰC TUYẾN",
    IDLE: "CHỜ KẾT NỐI",
    OFFLINE: "MẤT KẾT NỐI",
  };

  function destinationPoint(lat, lon, bearingDeg, distanceKm) {
    const R = 6371.0;
    const brng = (bearingDeg * Math.PI) / 180;
    const d = distanceKm / R;
    const lat1 = (lat * Math.PI) / 180;
    const lon1 = (lon * Math.PI) / 180;
    const lat2 = Math.asin(
      Math.sin(lat1) * Math.cos(d) + Math.cos(lat1) * Math.sin(d) * Math.cos(brng)
    );
    const lon2 =
      lon1 +
      Math.atan2(
        Math.sin(brng) * Math.sin(d) * Math.cos(lat1),
        Math.cos(d) - Math.sin(lat1) * Math.sin(lat2)
      );
    return [(lat2 * 180) / Math.PI, (lon2 * 180) / Math.PI];
  }

  function buildFovPolygon(lat, lon, azimuth, fovDeg, rangeKm) {
    const half = Math.max(5, Math.min(180, fovDeg / 2));
    const steps = 14;
    const pts = [[lat, lon]];
    for (let i = 0; i <= steps; i++) {
      const ang = azimuth - half + (2 * half * i) / steps;
      pts.push(destinationPoint(lat, lon, ang, rangeKm));
    }
    return pts;
  }

  function initMap() {
    if (!mapEl || typeof window.L === "undefined") {
      if (offlineEl) offlineEl.hidden = false;
      return;
    }
    map = window.L.map(mapEl, { zoomControl: true }).setView([defaultLat, defaultLon], 11);

    const satellite = window.L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Esri World Imagery", maxZoom: 18 }
    );
    const darkMap = window.L.tileLayer(
      "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      { attribution: "CARTO Dark", maxZoom: 19 }
    );
    satellite.addTo(map);
    window.L.control.layers({ "Vệ tinh (Satellite)": satellite, "Bản đồ tối (Dark)": darkMap }).addTo(map);

    layerGroup = window.L.layerGroup().addTo(map);
    map.on("click", (e) => {
      if (latInput) latInput.value = e.latlng.lat.toFixed(6);
      if (lonInput) lonInput.value = e.latlng.lng.toFixed(6);
      void refreshWeather(e.latlng.lat, e.latlng.lng);
    });
    renderStationsOnMap();
  }

  function renderStationsOnMap() {
    if (!layerGroup || typeof window.L === "undefined") return;
    layerGroup.clearLayers();
    const bounds = [];

    for (const s of stations) {
      if (s.latitude === null || s.longitude === null) continue;
      const color = STATUS_COLOR[s.status] || STATUS_COLOR.IDLE;
      const pts = buildFovPolygon(
        s.latitude,
        s.longitude,
        Number(s.azimuth || 0),
        Number(s.fov_degrees || 60),
        Number(s.range_km || 5)
      );
      window.L.polygon(pts, {
        color,
        weight: 1.5,
        fillColor: color,
        fillOpacity: s.status === "ALERTING" ? 0.34 : 0.16,
      }).addTo(layerGroup);

      const marker = window.L.circleMarker([s.latitude, s.longitude], {
        radius: s.status === "ALERTING" ? 10 : 7,
        color: "#ffffff",
        weight: 2,
        fillColor: color,
        fillOpacity: 0.95,
      }).addTo(layerGroup);

      const alertHtml = s.active_alert_url
        ? `<br><a href="${s.active_alert_url}" style="color:#ef4444;font-weight:700;">🚨 Xem bằng chứng khói ↗</a>`
        : "";
      const mapsHtml = s.maps_url
        ? `<br><a href="${s.maps_url}" target="_blank" rel="noopener" style="color:#10b981;">📍 Chỉ đường Google Maps ↗</a>`
        : "";
      marker.bindPopup(
        `<div style="min-width:190px;font-family:sans-serif;">
          <strong>CAM ${String(s.channel_number).padStart(2, "0")} · ${s.name}</strong><br>
          <span style="color:${color};font-weight:700;">● ${STATUS_TEXT[s.status] || s.status}</span><br>
          ${s.location_name ? `Khu vực: ${s.location_name}<br>` : ""}
          Hướng nhìn: ${Math.round(s.azimuth)}° · Tầm quét ${s.range_km} km
          ${alertHtml}${mapsHtml}
        </div>`
      );
      bounds.push([s.latitude, s.longitude]);
    }

    if (bounds.length > 0 && map) {
      map.fitBounds(bounds, { padding: [50, 50], maxZoom: 13 });
    }
  }

  function renderStationList() {
    if (!stationListEl) return;
    if (stations.length === 0) {
      stationListEl.innerHTML =
        '<div class="empty"><span>🗺️</span><h3>Chưa có trạm camera nào được lưu</h3><p>Hãy chọn vị trí trên bản đồ hoặc lưu nguồn ở Phòng giám sát.</p></div>';
      return;
    }
    stationListEl.replaceChildren(
      ...stations.map((s) => {
        const card = document.createElement("div");
        card.className = "fw-map-station-card";
        card.dataset.status = s.status;
        const coords =
          s.latitude !== null && s.longitude !== null
            ? `${s.latitude.toFixed(5)}, ${s.longitude.toFixed(5)} · Hướng ${Math.round(s.azimuth)}°`
            : "Chưa gắn tọa độ (nhấp chọn từ bản đồ để điền)";
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;">
            <strong>CAM ${String(s.channel_number).padStart(2, "0")} · ${s.name}</strong>
            <span class="pill ${s.status === "ALERTING" ? "danger" : s.status === "ONLINE" ? "" : "warning"}">${
          STATUS_TEXT[s.status] || s.status
        }</span>
          </div>
          <small>${s.location_name ? `${s.location_name} · ` : ""}${coords}</small>
          <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:8px;font-size:11px;">
            <button type="button" class="cctv-btn" data-edit-station>✏️ Chỉnh tọa độ</button>
            ${s.maps_url ? `<a class="cctv-btn" href="${s.maps_url}" target="_blank" rel="noopener">Google Maps ↗</a>` : ""}
            ${s.active_alert_url ? `<a class="cctv-btn cctv-danger" href="${s.active_alert_url}">Xem khói ↗</a>` : ""}
          </div>
        `;
        card.querySelector("[data-edit-station]")?.addEventListener("click", () => {
          if (channelInput) channelInput.value = String(s.channel_number);
          if (nameInput) nameInput.value = s.name || "";
          if (locInput) locInput.value = s.location_name || "";
          if (latInput) latInput.value = s.latitude !== null ? String(s.latitude) : "";
          if (lonInput) lonInput.value = s.longitude !== null ? String(s.longitude) : "";
          if (azimuthInput) azimuthInput.value = String(Math.round(s.azimuth || 0));
          if (rangeInput) rangeInput.value = String(s.range_km || 5);
          if (map && s.latitude !== null && s.longitude !== null) {
            map.setView([s.latitude, s.longitude], 13);
            void refreshWeather(s.latitude, s.longitude);
          }
        });
        return card;
      })
    );
  }

  async function refreshWeather(lat, lon) {
    try {
      const resp = await fetch(`${weatherApiUrl}?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}`);
      if (!resp.ok) return;
      const w = await resp.json();
      const tEl = document.querySelector("#weather-temp");
      const hEl = document.querySelector("#weather-hum");
      const windEl = document.querySelector("#weather-wind");
      const riskEl = document.querySelector("#weather-risk");
      if (tEl) tEl.textContent = `${w.temperature_c} °C`;
      if (hEl) hEl.textContent = `${w.humidity_pct}%`;
      if (windEl) windEl.textContent = `${w.wind_speed_kmh} km/h · ${w.wind_direction_deg}°`;
      if (riskEl) riskEl.textContent = w.danger_label;
    } catch {
      /* Keep current weather snapshot */
    }
  }

  form?.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (saveMsg) {
      saveMsg.hidden = false;
      saveMsg.textContent = "Đang lưu vị trí trạm camera…";
    }
    const existing = stations.find((s) => String(s.channel_number) === String(channelInput?.value));
    const payload = {
      channel_number: Number(channelInput?.value || 1),
      name: (nameInput?.value || "").trim() || `CAM ${channelInput?.value || 1}`,
      source_type: existing?.source_type || "simulator",
      location_name: (locInput?.value || "").trim(),
      latitude: (latInput?.value || "").trim(),
      longitude: (lonInput?.value || "").trim(),
      azimuth: Number(azimuthInput?.value || 0),
      range_km: Number(rangeInput?.value || 5),
    };
    try {
      const resp = await fetch(stationsApiUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(payload),
      });
      const data = await resp.json();
      if (!resp.ok || !data.station) {
        throw new Error(data.message || "Không lưu được trạm camera.");
      }
      const idx = stations.findIndex((s) => s.channel_number === data.station.channel_number);
      if (idx >= 0) stations[idx] = data.station;
      else stations.push(data.station);
      stations.sort((a, b) => a.channel_number - b.channel_number);
      renderStationList();
      renderStationsOnMap();
      if (saveMsg) saveMsg.textContent = "✅ Đã lưu tọa độ trạm camera và cập nhật bản đồ!";
    } catch (err) {
      if (saveMsg) saveMsg.textContent = `❌ ${err instanceof Error ? err.message : "Lỗi lưu tọa độ"}`;
    }
  });

  const seedBtn = document.querySelector("#fw-seed-demo-stations");
  seedBtn?.addEventListener("click", async () => {
    seedBtn.disabled = true;
    seedBtn.textContent = "Đang tạo 3 trạm mẫu…";
    const samples = [
      {
        channel_number: 1,
        name: "Đồi Thông Bắc",
        location_name: "Tiểu khu 142 · VQG Bạch Mã",
        source_type: "simulator",
        simulator_scenario: "smoke_rise",
        latitude: 16.2015,
        longitude: 107.8520,
        azimuth: 55,
        fov_degrees: 65,
        range_km: 5.5,
      },
      {
        channel_number: 2,
        name: "Tháp Canh Đèo Hải Vân",
        location_name: "Đỉnh Đèo Hải Vân · Phía Nam",
        source_type: "simulator",
        simulator_scenario: "fog_false_positive",
        latitude: 16.1890,
        longitude: 108.1310,
        azimuth: 310,
        fov_degrees: 70,
        range_km: 6.0,
      },
      {
        channel_number: 3,
        name: "Khe Rừng Nam",
        location_name: "Vùng Đệm Khe Su · Thừa Thiên Huế",
        source_type: "simulator",
        simulator_scenario: "clear_forest",
        latitude: 16.2340,
        longitude: 107.8010,
        azimuth: 140,
        fov_degrees: 60,
        range_km: 4.5,
      },
    ];
    try {
      for (const sample of samples) {
        const resp = await fetch(stationsApiUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          body: JSON.stringify(sample),
        });
        const data = await resp.json().catch(() => null);
        if (resp.ok && data?.station) {
          const idx = stations.findIndex((s) => s.channel_number === data.station.channel_number);
          if (idx >= 0) stations[idx] = data.station;
          else stations.push(data.station);
        }
      }
      stations.sort((a, b) => a.channel_number - b.channel_number);
      renderStationList();
      renderStationsOnMap();
      await refreshWeather(16.2015, 107.8520);
      if (saveMsg) {
        saveMsg.hidden = false;
        saveMsg.textContent = "✅ Đã nạp 3 trạm rừng mẫu! Bạn có thể bấm từng trạm hoặc sang Phòng giám sát để xem live.";
      }
    } finally {
      seedBtn.disabled = false;
      seedBtn.textContent = "✨ Nạp nhanh 3 Trạm Rừng Mẫu (Demo)";
    }
  });

  renderStationList();
  initMap();
})();
