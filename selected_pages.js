(() => {
  const app = window.Chartroom;
  if (!app) return;

  const { byId, requestJson, escapeHtml, spotifyLink, getCachedChart } = app;
  const state = { cadence: "daily", entityType: "track", view: "release", marketScope: "global" };
  let activePage = "charts";
  let liveOverview = null;
  let liveKey = "";
  let liveLoadedAt = 0;
  let historyOverview = null;
  let historyChart = null;
  let historyTrendData = null;
  let liveRequest = 0;
  let isAutoRegionScanning = false;
  let historyRequest = 0;
  const authChannel = "BroadcastChannel" in window ? new BroadcastChannel("spotify-charts-auth") : null;
  const focusedArtists = [
    { uri: "spotify:artist:06HL4z0CvFAxyc27GXpf02", name: "Taylor Swift" },
    { uri: "spotify:artist:4UIOuc84ExWojcUzFGtbW8", name: "Felix" },
  ];
  const focusedArtistUris = focusedArtists.map((artist) => artist.uri);
  const focusedArtistNames = focusedArtists.map((artist) => artist.name.toLowerCase());
  const focusedTrackUri = "spotify:track:15fpZUMOQD9ONTjxITFXFT";

  function focusedSelectionName(entityType = state.entityType) {
    if (entityType === "artist") return "Taylor Swift and Felix";
    if (entityType === "track") return "Taylor Swift, Felix, and Unfair";
    return "Taylor Swift and Felix";
  }

  function setStatus(id, message, isError = false) {
    const element = byId(id);
    if (!element) return;
    element.textContent = message;
    element.classList.toggle("error", isError);
  }

  function currentKey() {
    return `${state.cadence}:${state.entityType}:${state.marketScope}`;
  }

  function updateLiveActionLabel() {
    byId("live-refresh").textContent = state.marketScope === "global" ? "Load all regions" : "Refresh all regions";
  }

  function chartTypeName() {
    const names = { track: "songs", artist: "artists", album: "albums" };
    return `${state.cadence === "daily" ? "Daily" : "Weekly"} top ${names[state.entityType]}`;
  }

  function entityTypeName() {
    return { track: "song", artist: "artist", album: "album" }[state.entityType];
  }

  function updateFocusControls() {
    document.querySelectorAll("[data-focus-key]").forEach((button) => {
      button.setAttribute("aria-pressed", String(state[button.dataset.focusKey] === button.dataset.focusValue));
    });
    const isArtistChart = state.entityType === "artist";
    const streamsOption = byId("history-metric").querySelector('option[value="streams"]');
    streamsOption.disabled = isArtistChart;
    if (isArtistChart && byId("history-metric").value === "streams") byId("history-metric").value = "rank";
  }

  function coverMarkup(imageUrl, title) {
    if (imageUrl) return `<span class="focus-artwork"><img src="${escapeHtml(imageUrl)}" alt="" loading="lazy" /></span>`;
    return `<span class="focus-artwork" aria-hidden="true">${escapeHtml((title || "♫").slice(0, 1).toUpperCase())}</span>`;
  }

  function formatNumber(value) {
    if (value == null || value === "") return "—";
    const number = Number(String(value).replaceAll(",", ""));
    return Number.isFinite(number) ? number.toLocaleString() : String(value);
  }

  function movementMarkup(item) {
    const current = Number(item.chartRank);
    const previous = Number(item.previousRank);
    if (!Number.isFinite(current) || !Number.isFinite(previous) || current <= 0 || previous <= 0) {
      return '<span class="focus-movement same" aria-label="Rank movement unavailable">—</span>';
    }
    const change = previous - current;
    if (change > 0) return `<span class="focus-movement up" aria-label="Up ${change} positions">↑${change}</span>`;
    if (change < 0) return `<span class="focus-movement down" aria-label="Down ${Math.abs(change)} positions">↓${Math.abs(change)}</span>`;
    return '<span class="focus-movement same" aria-label="No rank change">—</span>';
  }

  function chartDetails(item) {
    const unit = state.cadence === "weekly" ? "w" : "d";
    const details = [];
    if (item.peakRank != null) details.push(`Peak: #${item.peakRank}`);
    if (item.consecutiveAppearancesOnChart != null) details.push(`Cons: ${item.consecutiveAppearancesOnChart}${unit}`);
    if (item.appearancesOnChart != null) details.push(`${state.cadence === "weekly" ? "WoC" : "Appearances"}: ${item.appearancesOnChart}${unit}`);
    if (!details.length && item.entryDate) details.push(`Since ${item.entryDate}`);
    return details.join(" · ") || "Chart details unavailable";
  }

  function trendButton(entityUri, entityName) {
    return `<button class="quiet-button focus-trend-button" type="button" data-history-uri="${escapeHtml(entityUri)}" aria-label="View ${escapeHtml(entityName)} history across regions">View trend</button>`;
  }

  function releaseRegionRow(item, showTrend = false) {
    return `<div class="focus-data-row"><div class="focus-row-rank">${escapeHtml(item.chartRank ?? "—")}${movementMarkup(item)}</div><div class="focus-row-copy"><strong class="focus-row-title">${escapeHtml(item.regionName)}</strong><span class="focus-row-subtitle">${escapeHtml(chartDetails(item))}</span></div><div class="focus-row-metric"><strong>${escapeHtml(formatNumber(item.metricValue ?? item.streamCount))}</strong><span>${escapeHtml((item.metricType || (item.streamCount == null ? "rank" : "streams")).toLowerCase())}</span>${showTrend ? trendButton(item.entityUri, item.entityName) : ""}</div></div>`;
  }

  function regionEntryRow(item) {
    const url = spotifyLink(item.entityUri);
    const title = escapeHtml(item.entityName || "Untitled");
    const titleMarkup = url
      ? `<a class="focus-row-title" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${title}</a>`
      : `<strong class="focus-row-title">${title}</strong>`;
    const subtitle = item.artistNames || (state.entityType === "artist" ? "Selected artist" : "");
    const artwork = item.imageUrl
      ? `<img class="focus-row-art" src="${escapeHtml(item.imageUrl)}" alt="" loading="lazy" />`
      : '<span class="focus-row-art" aria-hidden="true"></span>';
    return `<div class="focus-data-row has-art"><div class="focus-row-rank">${escapeHtml(item.chartRank ?? "—")}${movementMarkup(item)}</div>${artwork}<div class="focus-row-copy">${titleMarkup}<span class="focus-row-subtitle">${escapeHtml(subtitle)}</span></div><div class="focus-row-metric"><strong>${escapeHtml(formatNumber(item.metricValue ?? item.streamCount))}</strong><span>${escapeHtml((item.metricType || (item.streamCount == null ? "rank" : "streams")).toLowerCase())}</span>${item.entityUri ? trendButton(item.entityUri, item.entityName) : ""}</div></div>`;
  }

  function renderReleaseCard(release, isHistory = false) {
    const url = spotifyLink(release.entityUri);
    const title = escapeHtml(release.entityName || "Untitled");
    const titleMarkup = url
      ? `<a class="focus-release-title" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">${title}</a>`
      : `<strong class="focus-release-title">${title}</strong>`;
    const artistNames = release.artistNames || (state.entityType === "artist" ? "Selected artist" : entityTypeName());
    const regions = release.regions || [];
    return `<article class="focus-release-card"><header class="focus-release-heading">${coverMarkup(release.imageUrl, release.entityName)}<div class="focus-release-copy">${titleMarkup}<p class="focus-release-subtitle">${escapeHtml(artistNames)}</p></div><span class="focus-region-count">${regions.length} ${regions.length === 1 ? "region" : "regions"}</span></header><div class="focus-data-list">${regions.map((region) => releaseRegionRow(region, isHistory)).join("")}</div></article>`;
  }

  function renderRegionCard(region, isHistory = false) {
    const entries = region.entries || [];
    const subtitle = isHistory ? `${entries.length} saved releases` : chartTypeName();
    const date = !isHistory && region.chartDate ? `<time class="focus-date">${escapeHtml(region.chartDate)}</time>` : "";
    return `<article class="focus-region-card"><header class="focus-card-title"><strong>${escapeHtml(region.regionName)}</strong><span>${escapeHtml(subtitle)}</span>${date}</header><div class="focus-data-list">${entries.map((entry) => regionEntryRow(entry)).join("")}</div></article>`;
  }

  function renderResultCards(containerId, releases, regions, isHistory = false) {
    const container = byId(containerId);
    const items = state.view === "release" ? releases : regions;
    if (!items.length) {
      container.innerHTML = `<p class="focus-empty">No ${escapeHtml(focusedSelectionName())} ${escapeHtml(entityTypeName())} chart entries were found in ${isHistory ? "the saved history range" : "the current charts"}.${isHistory ? " More history appears as regional chart snapshots are collected." : ""}</p>`;
      return;
    }
    container.innerHTML = state.view === "release"
      ? items.map((release) => renderReleaseCard(release, isHistory)).join("")
      : items.map((region) => renderRegionCard(region, isHistory)).join("");
  }

  function renderLive(payload) {
    byId("live-results-title").textContent = state.view === "release"
      ? `${focusedSelectionName()} ${state.entityType === "artist" ? "in the artist chart" : `${state.entityType === "track" ? "songs" : "albums"} charting now`}`
      : `${chartTypeName()} by region`;
    const count = state.view === "release" ? payload.releases.length : payload.regions.length;
    const matchingRegionLabel = payload.matchingRegions === 1 ? "region" : "regions";
    byId("live-results-count").textContent = state.view === "release"
      ? `${count} ${count === 1 ? "result" : "results"} · ${payload.matchingRegions} ${matchingRegionLabel}`
      : `${count} regions · ${payload.marketsScanned} markets scanned`;
    renderResultCards("live-results", payload.releases, payload.regions);
    const failures = payload.failedRegions?.length || 0;
    const marketMessage = payload.marketScope === "global"
      ? "Global chart loaded. Scan all regions to find more placements."
      : payload.snapshotReused
        ? `Today’s saved results loaded from History (${payload.marketsScanned} of ${payload.marketsTotal} markets).`
        : `${payload.marketsScanned} of ${payload.marketsTotal} markets scanned.`;
    const regionLabel = payload.matchingRegions === 1 ? "region" : "regions";
    const message = `${payload.artist.entityName}: ${payload.matchingRegions} ${regionLabel} charting ${entityTypeName()}s in the ${state.cadence} charts. ${marketMessage}`;
    const storageMessage = payload.historyStorageAvailable == null
      ? ""
      : payload.historyStorageAvailable
        ? `${payload.historySavedRegions} regional snapshots saved for History.`
        : "Live charts loaded; history storage is unavailable right now.";
    const partialMessage = failures ? ` ${failures} regions could not be loaded.` : "";
    setStatus("live-status", `${message}${storageMessage ? ` ${storageMessage}` : ""}${partialMessage}`, failures > 0 && payload.marketsScanned === 0);
    updateLiveActionLabel();
  }

  function buildGlobalOverview(chart) {
    const chartDate = String(chart.displayChart?.date || "Latest");
    const metadataKey = { track: "trackMetadata", artist: "artistMetadata", album: "albumMetadata" }[state.entityType];
    const entries = (chart.entries || []).filter((entry) => {
      const metadata = entry[metadataKey] || {};
      if (state.entityType === "artist") {
        const uri = metadata.artistUri || entry.artistUri;
        const name = String(metadata.artistName || "").toLowerCase();
        return focusedArtistUris.includes(uri) || focusedArtistNames.includes(name);
      }
      const entityUri = metadata.trackUri || metadata.albumUri || entry.trackUri || entry.albumUri;
      if (state.entityType === "track" && entityUri === focusedTrackUri) return true;
      return (metadata.artists || []).some((artist) => artist && (
        focusedArtistUris.includes(artist.spotifyUri || artist.uri)
        || focusedArtistNames.includes(String(artist.name || "").toLowerCase())
      ));
    }).map((entry) => {
      const metadata = entry[metadataKey] || {};
      const stats = entry.chartEntryData || {};
      const metric = stats.rankingMetric || {};
      const uri = metadata.trackUri || metadata.artistUri || metadata.albumUri || entry.trackUri || entry.artistUri || entry.albumUri;
      if (!uri) return null;
      return {
        entityUri: uri,
        entityType: state.entityType,
        entityName: metadata.trackName || metadata.artistName || metadata.albumName || "Untitled",
        artistNames: state.entityType === "artist" ? (metadata.artistName || "Selected artist") : (metadata.artists || []).map((artist) => artist?.name).filter(Boolean).join(", " ),
        imageUrl: metadata.displayImageUri || null,
        regionCode: "GLOBAL",
        regionName: "Global",
        chartRank: stats.currentRank,
        previousRank: stats.previousRank,
        peakRank: stats.peakRank,
        consecutiveAppearancesOnChart: stats.consecutiveAppearancesOnChart,
        appearancesOnChart: stats.appearancesOnChart,
        entryDate: stats.entryDate,
        metricType: metric.type,
        metricValue: metric.value ?? null,
        chartDate,
      };
    }).filter(Boolean);
    const regions = entries.length ? [{ regionCode: "GLOBAL", regionName: "Global", chartDate, entries }] : [];
    const releases = entries.map((entry) => ({
      entityUri: entry.entityUri,
      entityType: entry.entityType,
      entityName: entry.entityName,
      artistNames: entry.artistNames,
      imageUrl: entry.imageUrl,
      regions: [{
        regionCode: entry.regionCode,
        regionName: entry.regionName,
        chartDate: entry.chartDate,
        chartRank: entry.chartRank,
        previousRank: entry.previousRank,
        peakRank: entry.peakRank,
        consecutiveAppearancesOnChart: entry.consecutiveAppearancesOnChart,
        appearancesOnChart: entry.appearancesOnChart,
        entryDate: entry.entryDate,
        metricType: entry.metricType,
        metricValue: entry.metricValue,
      }],
    }));
    return {
      artist: { entityName: "Taylor Swift and Felix" },
      cadence: state.cadence,
      entityType: state.entityType,
      chartType: `${state.cadence}_top_${{ track: "songs", artist: "artists", album: "albums" }[state.entityType]}`,
      marketScope: "global",
      marketsTotal: 1,
      marketsScanned: 1,
      matchingRegions: regions.length,
      failedRegions: [],
      releases,
      regions,
    };
  }

  async function fetchAllRegionsOverview(cadence, entityType, refresh = false) {
    const params = new URLSearchParams({ cadence, entity_type: entityType, scope: "all" });
    let refreshScan = refresh;
    while (true) {
      if (refreshScan) params.set("refresh", "true");
      const payload = await requestJson(`/api/focused/live?${params}`);
      params.delete("refresh");
      refreshScan = false;
      if (payload.scanError) throw new Error(payload.scanError);
      if (!payload.scanInProgress) return payload;
      if (state.cadence !== cadence || state.entityType !== entityType) throw new Error("The chart selection changed while regions were loading.");
      setStatus("live-status", "Global chart is loaded. The regional scan is running in the background and will update automatically.");
      await new Promise((resolve) => window.setTimeout(resolve, 3000));
    }
  }

  async function fetchLiveOverview(refresh = false) {
    let payload;
    if (state.marketScope === "global") {
      const chartType = `${state.cadence}_top_${{ track: "songs", artist: "artists", album: "albums" }[state.entityType]}`;
      const cachedChart = getCachedChart(chartType, "GLOBAL", "latest");
      const params = new URLSearchParams({ type: chartType, country: "GLOBAL", date: "latest" });
      const chart = cachedChart || await requestJson(`/api/charts?${params}`);
      payload = buildGlobalOverview(chart);
    } else {
      payload = await fetchAllRegionsOverview(state.cadence, state.entityType, refresh);
    }
    return payload;
  }

  async function loadAllRegionsAutomatically(requestId) {
    isAutoRegionScanning = true;
    const cadence = state.cadence;
    const entityType = state.entityType;
    const button = byId("live-refresh");
    button.disabled = true;
    button.textContent = "Scanning regions…";
    setStatus("live-status", `Global chart loaded. Scanning all regions for ${focusedSelectionName()} ${entityTypeName()} charts…`);

    try {
      const payload = await fetchAllRegionsOverview(cadence, entityType);
      if (requestId !== liveRequest || activePage !== "live" || state.cadence !== cadence || state.entityType !== entityType) return;
      state.marketScope = "all";
      liveOverview = payload;
      liveKey = currentKey();
      liveLoadedAt = Date.now();
      renderLive(payload);
    } catch (error) {
      if (requestId === liveRequest && activePage === "live") {
        setStatus("live-status", `The Global chart is loaded, but the automatic regional scan failed: ${error.message}`, true);
      }
    } finally {
      if (requestId === liveRequest) {
        isAutoRegionScanning = false;
        button.disabled = false;
        updateLiveActionLabel();
      }
    }
  }

  async function loadFocusedLive(refresh = false) {
    isAutoRegionScanning = false;
    const requestId = ++liveRequest;
    byId("live-refresh").disabled = true;
    byId("live-refresh").textContent = state.marketScope === "global" ? "Loading Global…" : "Loading regions…";
    byId("live-results-count").textContent = state.marketScope === "global" ? "Loading Global chart…" : "Scanning all Spotify regions…";
    setStatus("live-status", state.marketScope === "global"
      ? `Loading ${focusedSelectionName()} ${entityTypeName()} chart for Global…`
      : `Loading ${focusedSelectionName()} ${entityTypeName()} charts across all regions…`);
    try {
      const payload = await fetchLiveOverview(refresh);
      if (requestId === liveRequest && activePage === "live") {
        liveOverview = payload;
        liveKey = currentKey();
        liveLoadedAt = Date.now();
        renderLive(payload);
        if (payload.marketScope === "global") void loadAllRegionsAutomatically(requestId);
      }
    } catch (error) {
      if (requestId === liveRequest) {
        if (state.marketScope === "all" && liveOverview?.marketScope === "global") {
          state.marketScope = "global";
          renderLive(liveOverview);
          setStatus("live-status", `The full regional scan failed: ${error.message} The Global chart is still shown.`, true);
        } else {
          byId("live-results-count").textContent = "Charts unavailable";
          byId("live-results").innerHTML = `<p class="focus-empty">${escapeHtml(error.message)}</p>`;
          setStatus("live-status", error.message, true);
        }
      }
    } finally {
      if (requestId === liveRequest && !isAutoRegionScanning) {
        byId("live-refresh").disabled = false;
        updateLiveActionLabel();
      }
    }
  }

  function renderHistory(payload) {
    historyOverview = payload;
    historyTrendData = null;
    byId("history-chart-card").hidden = true;
    const covers = new Map((liveOverview?.releases || []).map((release) => [release.entityUri, release.imageUrl]));
    payload.releases.forEach((release) => { release.imageUrl = covers.get(release.entityUri) || null; });
    byId("history-results-title").textContent = state.view === "release" ? `${focusedSelectionName()} history by release` : `${focusedSelectionName()} history by region`;
    byId("history-results-count").textContent = `${payload.releases.length} releases · ${payload.regions.length} regions · ${payload.snapshotRows.toLocaleString()} snapshots`;
    renderResultCards("history-results", payload.releases, payload.regions, true);
    const empty = payload.snapshotRows === 0;
    const nextStep = empty ? "No saved snapshots are available in this range yet. Current regional charts will be captured as they load." : "Choose View trend on a release to compare its rank or streams across regions.";
    setStatus("selected-history-status", `${payload.snapshotRows.toLocaleString()} ${focusedSelectionName()} ${entityTypeName()} snapshots across ${payload.regions.length} regions. ${nextStep}`);
    byId("history-chart").hidden = true;
    byId("history-empty").hidden = false;
    byId("history-empty").textContent = "Choose a release below to compare its chart history across regions.";
    byId("history-chart-title").textContent = `${focusedSelectionName()} history by region`;
    byId("history-chart-range").textContent = "Choose a release to view its trend";
    if (historyChart) {
      historyChart.destroy();
      historyChart = null;
    }
  }

  async function loadFocusedHistory(refreshLive = false) {
    const requestId = ++historyRequest;
    setStatus("selected-history-status", `Loading ${focusedSelectionName()} snapshots across regions…`);
    try {
      const key = currentKey();
      if (refreshLive || liveKey !== key || Date.now() - liveLoadedAt > 120000) {
        const payload = await fetchLiveOverview(refreshLive);
        if (requestId !== historyRequest) return;
        liveOverview = payload;
        liveKey = key;
        liveLoadedAt = Date.now();
      }
      if (requestId !== historyRequest) return;
      const params = new URLSearchParams({
        cadence: state.cadence,
        entity_type: state.entityType,
        range: byId("history-range").value,
      });
      const payload = await requestJson(`/api/focused/history?${params}`);
      if (requestId === historyRequest && activePage === "history") renderHistory(payload);
    } catch (error) {
      if (requestId === historyRequest) {
        byId("history-results-count").textContent = "History unavailable";
        byId("history-results").innerHTML = `<p class="focus-empty">${escapeHtml(error.message)}</p>`;
        setStatus("selected-history-status", error.message, true);
      }
    }
  }

  function renderHistoryTrend(payload) {
    historyTrendData = payload;
    const points = payload.points || [];
    const metric = byId("history-metric").value;
    const canvas = byId("history-chart");
    const empty = byId("history-empty");
    const labels = [...new Set(points.map((point) => point.chartDate))].sort();
    const regions = [...new Set(points.map((point) => point.regionCode))].sort((left, right) => {
      if (left === "GLOBAL") return -1;
      if (right === "GLOBAL") return 1;
      const leftName = points.find((point) => point.regionCode === left)?.regionName || left;
      const rightName = points.find((point) => point.regionCode === right)?.regionName || right;
      return leftName.localeCompare(rightName);
    });
    const palette = ["#187a45", "#3478c5", "#e37b26", "#8b5cc7", "#ca4f69", "#148b99", "#6d7c2e", "#525f73"];
    const hasValues = points.some((point) => (metric === "rank" ? point.chartRank : point.streamCount) != null);
    if (historyChart) {
      historyChart.destroy();
      historyChart = null;
    }
    canvas.hidden = !hasValues;
    empty.hidden = hasValues;
    if (hasValues && window.Chart) {
      const datasets = regions.map((regionCode, index) => {
        const regionPoints = points.filter((point) => point.regionCode === regionCode);
        const values = new Map(regionPoints.map((point) => [point.chartDate, metric === "rank" ? point.chartRank : point.streamCount]));
        return {
          label: regionPoints[0]?.regionName || regionCode,
          data: labels.map((label) => values.get(label) ?? null),
          borderColor: palette[index % palette.length],
          backgroundColor: palette[index % palette.length] + "20",
          borderWidth: 2,
          pointRadius: labels.length > 40 ? 0 : 2,
          pointHoverRadius: 5,
          tension: 0.25,
          spanGaps: false,
        };
      });
      historyChart = new window.Chart(canvas.getContext("2d"), {
        type: "line",
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { intersect: false, mode: "index" },
          plugins: {
            legend: { display: regions.length > 1, position: "bottom", labels: { color: "#64748b", usePointStyle: true, boxWidth: 8 } },
            tooltip: { callbacks: { label: (context) => `${context.dataset.label}: ${Number(context.parsed.y).toLocaleString()}` } },
          },
          scales: {
            x: { ticks: { color: "#7b8798", maxTicksLimit: 8 }, grid: { color: "rgba(100,116,139,.10)" } },
            y: {
              reverse: metric === "rank",
              beginAtZero: metric === "streams",
              ticks: { color: "#7b8798", callback: (value) => Number(value).toLocaleString() },
              grid: { color: "rgba(100,116,139,.10)" },
            },
          },
        },
      });
    }
    const entityName = payload.entity?.entityName || focusedSelectionName();
    byId("history-chart-card").hidden = false;
    byId("history-chart-title").textContent = `${entityName} · ${metric === "rank" ? "rank" : "streams"} by region`;
    byId("history-chart-range").textContent = `${points.length} snapshots · ${regions.length} regions`;
    if (!hasValues) empty.textContent = "No saved chart snapshots were found for this release in the selected range.";
    setStatus("selected-history-status", `${points.length.toLocaleString()} stored snapshots for ${entityName} across ${regions.length} regions.`);
    byId("history-chart-card").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function viewHistoryTrend(entityUri) {
    const params = new URLSearchParams({
      entity_uri: entityUri,
      cadence: state.cadence,
      entity_type: state.entityType,
      range: byId("history-range").value,
    });
    setStatus("selected-history-status", "Loading the release history across regions…");
    try {
      const payload = await requestJson(`/api/focused/history/trend?${params}`);
      renderHistoryTrend(payload);
    } catch (error) {
      setStatus("selected-history-status", error.message, true);
    }
  }

  function activatePage(page) {
    const validPage = ["charts", "live", "history"].includes(page) ? page : "charts";
    activePage = validPage;
    document.querySelectorAll("[data-page-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.pagePanel !== validPage;
    });
    document.querySelectorAll("[data-page-nav]").forEach((link) => {
      const active = link.dataset.pageNav === validPage;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    if (validPage === "live") loadFocusedLive();
    if (validPage === "history") loadFocusedHistory();
  }

  document.querySelectorAll("[data-focus-key]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.focusKey;
      const value = button.dataset.focusValue;
      if (state[key] === value) return;
      state[key] = value;
      updateFocusControls();
      if (key === "view") {
        if (activePage === "live" && liveOverview) renderLive(liveOverview);
        if (activePage === "history" && historyOverview) renderHistory(historyOverview);
      } else if (activePage === "live") {
        state.marketScope = "global";
        updateLiveActionLabel();
        loadFocusedLive();
      } else if (activePage === "history") {
        loadFocusedHistory();
      }
    });
  });

  byId("live-refresh").addEventListener("click", () => {
    state.marketScope = "all";
    loadFocusedLive(true);
  });
  byId("history-refresh").addEventListener("click", () => loadFocusedHistory(true));
  byId("history-range").addEventListener("change", () => loadFocusedHistory());
  byId("history-metric").addEventListener("change", () => {
    if (historyTrendData) renderHistoryTrend(historyTrendData);
  });
  byId("history-results").addEventListener("click", (event) => {
    const button = event.target.closest("[data-history-uri]");
    if (button) viewHistoryTrend(button.dataset.historyUri);
  });
  byId("live-connect").addEventListener("click", () => {
    window.open("/auth/login", "_blank", "noopener,noreferrer");
    setStatus("live-status", "Spotify sign-in opened in a new tab. Return here after authorization; charts will refresh automatically.");
  });
  byId("history-connect").addEventListener("click", () => {
    window.open("/auth/login", "_blank", "noopener,noreferrer");
    setStatus("selected-history-status", "Spotify sign-in opened in a new tab. Return here after authorization; charts will refresh automatically.");
  });

  authChannel?.addEventListener("message", (event) => {
    if (event.data === "connected") {
      if (activePage === "live") loadFocusedLive(true);
      if (activePage === "history") loadFocusedHistory(true);
    }
  });
  window.addEventListener("hashchange", () => activatePage(location.hash.slice(1)));
  updateFocusControls();
  activatePage(location.hash.slice(1) || "charts");
})();
