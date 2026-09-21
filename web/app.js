// --- Tonarr Host Frontend Controller ---

const state = {
  config: {},
  stats: {},
  tracks: [],
  filteredTracks: [],
  isScanning: false,
  browseCurrentPath: '/music'
};

const $ = id => document.getElementById(id);

const elements = {
  connectionUrlText: $('connectionUrlText'),
  btnCopyUrl: $('btnCopyUrl'),
  btnTriggerScan: $('btnTriggerScan'),
  scanCard: $('scanCard'),
  scanPhaseText: $('scanPhaseText'),
  scanCurrentTrackText: $('scanCurrentTrackText'),
  scanCounterText: $('scanCounterText'),
  scanProgressFill: $('scanProgressFill'),
  
  // Stats
  statTracksCount: $('statTracksCount'),
  statSyncedRate: $('statSyncedRate'),
  statArtistsCount: $('statArtistsCount'),
  libSubtitle: $('libSubtitle'),

  // Config Form
  hostConfigForm: $('hostConfigForm'),
  cfgMusicDir: $('cfgMusicDir'),
  btnBrowseDir: $('btnBrowseDir'),
  cfgPlexAsOnlyPlayerSource: $('cfgPlexAsOnlyPlayerSource'),
  cfgFolderForCreatorOnly: $('cfgFolderForCreatorOnly') || $('cfgPlexAsOnlyPlayerSource'),
  cfgPreferLocalMeta: $('cfgPreferLocalMeta'),
  cfgPreferLocalLyrics: $('cfgPreferLocalLyrics'),
  cfgFetchMissingOnline: $('cfgFetchMissingOnline'),
  cfgSaveFetchedLrc: $('cfgSaveFetchedLrc'),

  // Source Checkboxes
  srcId3: $('srcId3'),
  srcLocalLrc: $('srcLocalLrc'),
  srcLocalCovers: $('srcLocalCovers'),
  srcLrclib: $('srcLrclib'),
  srcMusicBrainz: $('srcMusicBrainz'),
  srcGenius: $('srcGenius'),

  // Plex Integration
  hostPlexBadge: $('hostPlexBadge'),
  hostPlexBadgeText: $('hostPlexBadgeText'),
  hostPlexConnectedView: $('hostPlexConnectedView'),
  hostPlexLoggedOutView: $('hostPlexLoggedOutView'),
  hostPlexServerInfo: $('hostPlexServerInfo'),
  btnHostPlexSync: $('btnHostPlexSync'),
  btnHostPlexLogout: $('btnHostPlexLogout'),
  btnRefreshHostPlexSections: $('btnRefreshHostPlexSections'),
  hostPlexSectionSelect: $('hostPlexSectionSelect'),
  cfgPreferPlexMeta: $('cfgPreferPlexMeta'),
  btnHostPlexOAuth: $('btnHostPlexOAuth'),
  btnToggleManualHostPlex: $('btnToggleManualHostPlex'),
  hostPlexManualContainer: $('hostPlexManualContainer'),
  hostPlexUrlInput: $('hostPlexUrlInput'),
  hostPlexTokenInput: $('hostPlexTokenInput'),
  btnSaveManualHostPlex: $('btnSaveManualHostPlex'),
  hostPlexStatusMsg: $('hostPlexStatusMsg'),

  // Library Table & Search
  libSearchInput: $('libSearchInput'),
  libTableBody: $('libTableBody'),

  // Preview Player
  previewPlayer: $('previewPlayer'),
  previewTitle: $('previewTitle'),
  previewArtist: $('previewArtist'),
  previewAudioEl: $('previewAudioEl'),

  // Browse Modal
  browseModal: $('browseModal'),
  browseModalBackdrop: $('browseModalBackdrop'),
  btnCloseBrowseModal: $('btnCloseBrowseModal'),
  btnCancelBrowse: $('btnCancelBrowse'),
  btnSelectCurrentBrowse: $('btnSelectCurrentBrowse'),
  browseCurrentPath: $('browseCurrentPath'),
  browseDirList: $('browseDirList'),

  // Toast
  toast: $('toast')
};

function showToast(msg, duration = 3000) {
  if (!elements.toast) return;
  elements.toast.textContent = msg;
  elements.toast.classList.remove('hidden');
  clearTimeout(elements.toast._t);
  elements.toast._t = setTimeout(() => {
    elements.toast.classList.add('hidden');
  }, duration);
}

// 1. Fetch Info & Config on Boot
async function fetchHostInfo() {
  try {
    const res = await fetch('/api/info');
    if (res.ok) {
      const data = await res.json();
      state.stats = data.stats || {};
      
      const hostUrl = data.connection_url || window.location.origin;
      if (elements.connectionUrlText) elements.connectionUrlText.textContent = hostUrl;

      updateStatsUI(state.stats);

      if (data.is_scanning) {
        startSseScanListener();
      }
    }
  } catch (err) {
    console.warn('Error fetching host info:', err);
  }
}

function updateStatsUI(stats) {
  const total = stats.total_tracks || 0;
  const synced = stats.synced_lyrics || 0;
  const artists = stats.total_artists || 0;
  const rate = total > 0 ? Math.round((synced / total) * 100) : 0;

  if (elements.statTracksCount) elements.statTracksCount.textContent = total;
  if (elements.statSyncedRate) elements.statSyncedRate.textContent = `${rate}%`;
  if (elements.statArtistsCount) elements.statArtistsCount.textContent = artists;
  if (elements.libSubtitle) elements.libSubtitle.textContent = `${total} Songs indiziert (${synced} mit synchronisierten Lyrics)`;
}

async function fetchHostConfig() {
  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      const cfg = await res.json();
      state.config = cfg;

      if (elements.cfgMusicDir) elements.cfgMusicDir.value = cfg.music_directory || '';
      const isPlexOnly = !!(cfg.plex_as_only_player_source || cfg.folder_source_for_creator_and_manager_only);
      if (elements.cfgPlexAsOnlyPlayerSource) elements.cfgPlexAsOnlyPlayerSource.checked = isPlexOnly;
      if (elements.cfgFolderForCreatorOnly) elements.cfgFolderForCreatorOnly.checked = isPlexOnly;
      if (elements.cfgPreferLocalMeta) elements.cfgPreferLocalMeta.checked = !!cfg.prefer_local_metadata;
      if (elements.cfgPreferLocalLyrics) elements.cfgPreferLocalLyrics.checked = !!cfg.prefer_local_lyrics;
      if (elements.cfgFetchMissingOnline) elements.cfgFetchMissingOnline.checked = !!cfg.fetch_missing_online;
      if (elements.cfgSaveFetchedLrc) elements.cfgSaveFetchedLrc.checked = !!cfg.save_fetched_lrc_locally;
      if (elements.cfgPreferPlexMeta) elements.cfgPreferPlexMeta.checked = cfg.prefer_plex_metadata !== false;

      const src = cfg.sources || {};
      if (elements.srcId3) elements.srcId3.checked = src.id3_tags !== false;
      if (elements.srcLocalLrc) elements.srcLocalLrc.checked = src.local_lrc_files !== false;
      if (elements.srcLocalCovers) elements.srcLocalCovers.checked = src.local_cover_images !== false;
      if (elements.srcLrclib) elements.srcLrclib.checked = src.lrclib !== false;
      if (elements.srcMusicBrainz) elements.srcMusicBrainz.checked = src.musicbrainz !== false;
      if (elements.srcGenius) elements.srcGenius.checked = !!src.genius;
    }
  } catch (err) {
    console.warn('Error fetching config:', err);
  }
}

// Instant toggle for "Plex als einzige Player Quelle verwenden"
const plexOnlyToggleEl = elements.cfgPlexAsOnlyPlayerSource || elements.cfgFolderForCreatorOnly;
if (plexOnlyToggleEl) {
  plexOnlyToggleEl.addEventListener('change', async (e) => {
    const val = e.target.checked;
    if (elements.cfgPlexAsOnlyPlayerSource) elements.cfgPlexAsOnlyPlayerSource.checked = val;
    if (elements.cfgFolderForCreatorOnly) elements.cfgFolderForCreatorOnly.checked = val;

    state.config = {
      ...(state.config || {}),
      plex_as_only_player_source: val,
      folder_source_for_creator_and_manager_only: val
    };

    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state.config)
      });
      if (res.ok) {
        showToast(val ? '📺 Plex als einzige Player-Quelle aktiviert!' : '📁 Lokale Musikordner für Player wieder freigegeben.');
        fetchTracksList();
      }
    } catch (err) {
      showToast('Fehler beim Speichern der Einstellung.');
    }
  });
}

// 2. Save Config
if (elements.hostConfigForm) {
  elements.hostConfigForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const isPlexOnly = elements.cfgPlexAsOnlyPlayerSource ? elements.cfgPlexAsOnlyPlayerSource.checked : (elements.cfgFolderForCreatorOnly ? elements.cfgFolderForCreatorOnly.checked : false);
    const updated = {
      ...state.config,
      music_directory: elements.cfgMusicDir.value.trim(),
      plex_as_only_player_source: isPlexOnly,
      folder_source_for_creator_and_manager_only: isPlexOnly,
      prefer_local_metadata: elements.cfgPreferLocalMeta.checked,
      prefer_local_lyrics: elements.cfgPreferLocalLyrics.checked,
      prefer_plex_metadata: elements.cfgPreferPlexMeta ? elements.cfgPreferPlexMeta.checked : true,
      fetch_missing_online: elements.cfgFetchMissingOnline.checked,
      save_fetched_lrc_locally: elements.cfgSaveFetchedLrc.checked,
      sources: {
        id3_tags: elements.srcId3.checked,
        local_lrc_files: elements.srcLocalLrc.checked,
        local_cover_images: elements.srcLocalCovers.checked,
        lrclib: elements.srcLrclib.checked,
        musicbrainz: elements.srcMusicBrainz.checked,
        genius: elements.srcGenius.checked,
        spotify: false
      }
    };

    try {
      const res = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updated)
      });
      if (res.ok) {
        state.config = updated;
        showToast('⚙️ Einstellungen gespeichert!');
      }
    } catch (err) {
      showToast('Fehler beim Speichern.');
    }
  });
}

// 3. Scan Streaming (SSE)
function startSseScanListener(customDir = null) {
  if (state.isScanning) return;
  state.isScanning = true;

  if (elements.scanCard) elements.scanCard.classList.remove('hidden');
  if (elements.btnTriggerScan) elements.btnTriggerScan.disabled = true;

  const url = customDir ? `/api/scan-stream?directory=${encodeURIComponent(customDir)}` : '/api/scan-stream';
  const es = new EventSource(url);

  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      handleScanEvent(data, es);
    } catch (err) {}
  };

  es.onerror = () => {
    es.close();
    state.isScanning = false;
    if (elements.scanCard) elements.scanCard.classList.add('hidden');
    if (elements.btnTriggerScan) elements.btnTriggerScan.disabled = false;
  };
}

function handleScanEvent(data, es) {
  const phase = data.phase;
  const current = data.current_index || 0;
  const total = data.total || 0;
  const pct = total > 0 ? Math.round((current / total) * 100) : 0;

  if (elements.scanProgressFill) elements.scanProgressFill.style.width = `${pct}%`;
  if (elements.scanCounterText) elements.scanCounterText.textContent = `${current} / ${total}`;

  if (phase === 'discovery') {
    if (elements.scanPhaseText) elements.scanPhaseText.textContent = 'Suche Musikdateien...';
    if (elements.scanCurrentTrackText) elements.scanCurrentTrackText.textContent = data.message || '';
  } else if (phase === 'reading' || phase === 'scanning') {
    if (elements.scanPhaseText) elements.scanPhaseText.textContent = `Analysiere Metadaten (${pct}%)...`;
    if (elements.scanCurrentTrackText) elements.scanCurrentTrackText.textContent = data.current_track || data.message || '';
  } else if (phase === 'complete') {
    if (elements.scanPhaseText) elements.scanPhaseText.textContent = '✅ Scan abgeschlossen!';
    if (elements.scanCurrentTrackText) elements.scanCurrentTrackText.textContent = data.message || '';
    if (elements.scanProgressFill) elements.scanProgressFill.style.width = '100%';

    es.close();
    state.isScanning = false;
    if (elements.btnTriggerScan) elements.btnTriggerScan.disabled = false;
    showToast(`🎉 ${data.tracks_count || 0} Songs erfolgreich indiziert!`);

    setTimeout(() => {
      if (elements.scanCard) elements.scanCard.classList.add('hidden');
    }, 2000);

    fetchHostInfo();
    fetchTracksList();
  } else if (phase === 'error') {
    es.close();
    state.isScanning = false;
    if (elements.btnTriggerScan) elements.btnTriggerScan.disabled = false;
    showToast(`❌ Fehler: ${data.message}`);
  }
}

if (elements.btnTriggerScan) {
  elements.btnTriggerScan.addEventListener('click', () => {
    const dir = elements.cfgMusicDir ? elements.cfgMusicDir.value.trim() : null;
    startSseScanListener(dir);
  });
}

// 4. Fetch Tracks List
async function fetchTracksList() {
  try {
    const res = await fetch('/api/tracks');
    if (res.ok) {
      const data = await res.json();
      state.tracks = data.tracks || [];
      state.filteredTracks = state.tracks;
      renderTracksTable();
    }
  } catch (err) {
    console.warn('Error fetching tracks:', err);
  }
}

function renderTracksTable() {
  if (!elements.libTableBody) return;
  const list = state.filteredTracks;

  if (list.length === 0) {
    elements.libTableBody.innerHTML = `
      <tr>
        <td colspan="6" style="text-align:center; padding: 48px; color:var(--text-muted);">
          Keine Titel gefunden. Klicke oben auf "Mediathek scannen".
        </td>
      </tr>
    `;
    return;
  }

  // Display top 100 for fast UI rendering
  const displayList = list.slice(0, 100);

  elements.libTableBody.innerHTML = displayList.map((t, idx) => {
    let tagHtml = '<span class="lyrics-tag missing">Keine</span>';
    if (t.is_synced) {
      tagHtml = '<span class="lyrics-tag synced">⚡ Synced</span>';
    } else if (t.has_lyrics) {
      tagHtml = '<span class="lyrics-tag plain">📝 Plain</span>';
    }

    return `
      <tr data-id="${t.id}">
        <td style="color:var(--text-dim); font-size:0.8rem;">${idx + 1}</td>
        <td><strong style="color:#fff;">${escapeHtml(t.title)}</strong></td>
        <td>${escapeHtml(t.artist || 'Unbekannt')}</td>
        <td style="color:var(--text-muted);">${escapeHtml(t.album || '—')}</td>
        <td style="text-align:center;">${tagHtml}</td>
        <td style="text-align:right; font-family:var(--font-mono); font-size:0.8rem;">${t.duration_str || '00:00'}</td>
      </tr>
    `;
  }).join('');

  elements.libTableBody.querySelectorAll('tr[data-id]').forEach(row => {
    row.addEventListener('click', () => {
      const tid = row.getAttribute('data-id');
      const track = state.tracks.find(t => t.id === tid);
      if (track) playPreviewTrack(track);
    });
  });
}

function playPreviewTrack(track) {
  if (!elements.previewPlayer || !elements.previewAudioEl) return;
  elements.previewTitle.textContent = track.title || 'Unbekannter Titel';
  elements.previewArtist.textContent = track.artist || 'Unbekannter Interpret';
  
  elements.previewAudioEl.src = `/api/audio/stream?id=${encodeURIComponent(track.id)}`;
  elements.previewPlayer.classList.remove('hidden');
  elements.previewAudioEl.play().catch(() => {});
}

// 5. Search Filter
if (elements.libSearchInput) {
  elements.libSearchInput.addEventListener('input', (e) => {
    const q = e.target.value.toLowerCase().trim();
    if (!q) {
      state.filteredTracks = state.tracks;
    } else {
      const tokens = q.split(/\s+/);
      state.filteredTracks = state.tracks.filter(t => {
        const full = `${t.title || ''} ${t.artist || ''} ${t.album || ''}`.toLowerCase();
        return tokens.every(tok => full.includes(tok));
      });
    }
    renderTracksTable();
  });
}

// 6. Directory Browser Modal
async function openBrowseModal(startPath = null) {
  const p = startPath || (elements.cfgMusicDir ? elements.cfgMusicDir.value.trim() : '/music');
  state.browseCurrentPath = p;
  if (elements.browseModal) elements.browseModal.classList.remove('hidden');
  await loadBrowseDirectory(p);
}

async function loadBrowseDirectory(path) {
  if (elements.browseCurrentPath) elements.browseCurrentPath.textContent = path;
  if (elements.browseDirList) elements.browseDirList.innerHTML = '<div style="padding:10px; color:var(--text-muted);">Lade Ordner...</div>';

  try {
    const res = await fetch(`/api/browse-directory?path=${encodeURIComponent(path)}`);
    if (res.ok) {
      const data = await res.json();
      state.browseCurrentPath = data.current;
      if (elements.browseCurrentPath) elements.browseCurrentPath.textContent = data.current;

      let html = '';
      if (data.parent) {
        html += `
          <div class="browse-item" data-path="${escapeHtml(data.parent)}">
            <span>📁 .. (Übergeordneter Ordner)</span>
          </div>
        `;
      }

      if (data.directories && data.directories.length > 0) {
        html += data.directories.map(d => `
          <div class="browse-item" data-path="${escapeHtml(d.path)}">
            <span>📁 ${escapeHtml(d.name)}</span>
          </div>
        `).join('');
      } else if (!data.parent) {
        html += '<div style="padding:10px; color:var(--text-dim);">Keine Unterordner vorhanden.</div>';
      }

      elements.browseDirList.innerHTML = html;

      elements.browseDirList.querySelectorAll('.browse-item').forEach(item => {
        item.addEventListener('click', () => {
          const target = item.getAttribute('data-path');
          loadBrowseDirectory(target);
        });
      });
    }
  } catch (err) {
    elements.browseDirList.innerHTML = '<div style="padding:10px; color:var(--accent-rose);">Fehler beim Laden des Ordners.</div>';
  }
}

if (elements.btnBrowseDir) {
  elements.btnBrowseDir.addEventListener('click', () => openBrowseModal());
}

if (elements.btnCloseBrowseModal) elements.btnCloseBrowseModal.addEventListener('click', () => elements.browseModal.classList.add('hidden'));
if (elements.btnCancelBrowse) elements.btnCancelBrowse.addEventListener('click', () => elements.browseModal.classList.add('hidden'));
if (elements.browseModalBackdrop) elements.browseModalBackdrop.addEventListener('click', () => elements.browseModal.classList.add('hidden'));

if (elements.btnSelectCurrentBrowse) {
  elements.btnSelectCurrentBrowse.addEventListener('click', () => {
    if (elements.cfgMusicDir) elements.cfgMusicDir.value = state.browseCurrentPath;
    elements.browseModal.classList.add('hidden');
    showToast(`📁 Pfad "${state.browseCurrentPath}" übernommen.`);
  });
}

// 7. Copy URL Helper
if (elements.btnCopyUrl) {
  elements.btnCopyUrl.addEventListener('click', () => {
    const txt = elements.connectionUrlText ? elements.connectionUrlText.textContent : '';
    if (txt) {
      navigator.clipboard.writeText(txt).then(() => {
        showToast('📋 Server-URL in die Zwischenablage kopiert!');
      });
    }
  });
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// 8. Plex Media Server Integration
let plexPinPollInterval = null;

async function fetchHostPlexStatus() {
  if (!elements.hostPlexBadge) return;
  try {
    const res = await fetch('/api/plex/status');
    if (res.ok) {
      const data = await res.json();
      const connected = !!(data.configured && data.reachable);
      
      if (connected) {
        elements.hostPlexBadge.classList.remove('disconnected');
        elements.hostPlexBadge.classList.add('connected');
        if (elements.hostPlexBadgeText) elements.hostPlexBadgeText.textContent = 'Verbunden';
        if (elements.hostPlexConnectedView) elements.hostPlexConnectedView.style.display = 'flex';
        if (elements.hostPlexLoggedOutView) elements.hostPlexLoggedOutView.style.display = 'none';
        
        const serverName = data.server_name || data.name || 'Plex Media Server';
        const serverVer = data.server_version || data.version || '';
        const serverUrl = data.effective_url || data.url || '';
        if (elements.hostPlexServerInfo) {
          elements.hostPlexServerInfo.textContent = `Server: ${serverName} ${serverVer ? '(' + serverVer + ')' : ''} · ${serverUrl}`;
        }
        if (elements.hostPlexStatusMsg) {
          elements.hostPlexStatusMsg.textContent = '';
        }
        
        loadHostPlexSections(data.section || (state.config && state.config.plex_section));
      } else {
        elements.hostPlexBadge.classList.remove('connected');
        elements.hostPlexBadge.classList.add('disconnected');
        if (elements.hostPlexBadgeText) elements.hostPlexBadgeText.textContent = 'Nicht verbunden';
        if (elements.hostPlexConnectedView) elements.hostPlexConnectedView.style.display = 'none';
        if (elements.hostPlexLoggedOutView) elements.hostPlexLoggedOutView.style.display = 'flex';
        if (data.configured && !data.reachable) {
          const detail = data.error ? ` (${data.error})` : '';
          if (elements.hostPlexStatusMsg) elements.hostPlexStatusMsg.textContent = `⚠️ Plex Server konfiguriert, aber nicht erreichbar${detail}`;
        }
      }
    }
  } catch (err) {
    console.warn('Error checking host plex status:', err);
  }
}

async function loadHostPlexSections(selectedSectionKey = null) {
  if (!elements.hostPlexSectionSelect) return;
  try {
    const res = await fetch('/api/plex/sections');
    if (res.ok) {
      const sections = await res.json();
      if (Array.isArray(sections) && sections.length > 0) {
        let html = '<option value="">-- Alle Musik-Mediatheken --</option>';
        sections.forEach(sec => {
          const isSel = (selectedSectionKey && String(sec.key) === String(selectedSectionKey)) ? 'selected' : '';
          html += `<option value="${escapeHtml(sec.key)}" ${isSel}>${escapeHtml(sec.title || sec.name)}</option>`;
        });
        elements.hostPlexSectionSelect.innerHTML = html;
      } else {
        elements.hostPlexSectionSelect.innerHTML = '<option value="">Keine Musik-Mediathek gefunden</option>';
      }
    }
  } catch (err) {
    console.warn('Error loading plex sections:', err);
  }
}

if (elements.hostPlexSectionSelect) {
  elements.hostPlexSectionSelect.addEventListener('change', async (e) => {
    const sec = e.target.value;
    try {
      const res = await fetch('/api/plex/set-section', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ section: sec })
      });
      if (res.ok) {
        state.config.plex_section = sec;
        showToast('📁 Plex-Mediathek aktualisiert!');
      }
    } catch (err) {
      showToast('Fehler beim Auswählen der Mediathek.');
    }
  });
}

if (elements.btnRefreshHostPlexSections) {
  elements.btnRefreshHostPlexSections.addEventListener('click', () => {
    loadHostPlexSections(state.config.plex_section);
    showToast('🔄 Plex-Mediatheken aktualisiert');
  });
}

// Plex Sync Button
if (elements.btnHostPlexSync) {
  elements.btnHostPlexSync.addEventListener('click', async () => {
    elements.btnHostPlexSync.disabled = true;
    elements.btnHostPlexSync.textContent = '⏳ Synchronisiere...';
    try {
      const res = await fetch('/api/plex/sync', { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        showToast(`🎉 ${data.message || 'Plex Mediathek synchronisiert!'}`);
        fetchHostInfo();
        fetchTracksList();
      } else {
        const err = await res.json();
        showToast(`Fehler: ${err.detail || 'Sync fehlgeschlagen'}`);
      }
    } catch (err) {
      showToast('Fehler beim Synchronisieren.');
    } finally {
      elements.btnHostPlexSync.disabled = false;
      elements.btnHostPlexSync.innerHTML = '<span>🔄 Songs & Playlists synchronisieren</span>';
    }
  });
}

// Plex Logout
if (elements.btnHostPlexLogout) {
  elements.btnHostPlexLogout.addEventListener('click', async () => {
    if (!confirm('Plex wirklich vom Host trennen?')) return;
    try {
      const res = await fetch('/api/plex/auth/logout', { method: 'POST' });
      if (res.ok) {
        showToast('Plex Server getrennt.');
        fetchHostPlexStatus();
      }
    } catch (err) {
      showToast('Fehler beim Abmelden.');
    }
  });
}

// Toggle Manual Plex Inputs
if (elements.btnToggleManualHostPlex) {
  elements.btnToggleManualHostPlex.addEventListener('click', () => {
    if (!elements.hostPlexManualContainer) return;
    const isHidden = elements.hostPlexManualContainer.style.display === 'none';
    elements.hostPlexManualContainer.style.display = isHidden ? 'flex' : 'none';
    if (isHidden) {
      if (elements.hostPlexUrlInput) elements.hostPlexUrlInput.value = (state.config && state.config.plex_url) || '';
      if (elements.hostPlexTokenInput) elements.hostPlexTokenInput.value = (state.config && state.config.plex_token) || '';
    }
  });
}

// Save Manual Plex Config
if (elements.btnSaveManualHostPlex) {
  elements.btnSaveManualHostPlex.addEventListener('click', async () => {
    const url = (elements.hostPlexUrlInput ? elements.hostPlexUrlInput.value : '').trim();
    const token = (elements.hostPlexTokenInput ? elements.hostPlexTokenInput.value : '').trim();
    if (!url || !token) {
      showToast('Bitte URL und Token eingeben.');
      return;
    }

    elements.btnSaveManualHostPlex.disabled = true;
    elements.btnSaveManualHostPlex.textContent = '⏳ Prüfe...';

    try {
      const testRes = await fetch(`/api/plex/test?url=${encodeURIComponent(url)}&token=${encodeURIComponent(token)}`);
      const testData = await testRes.json();
      if (!testData.success) {
        showToast(`Verbindungsfehler: ${testData.error || 'Server nicht erreichbar'}`);
        elements.btnSaveManualHostPlex.disabled = false;
        elements.btnSaveManualHostPlex.textContent = 'Verbindung testen & speichern';
        return;
      }

      // Save to config
      const updated = {
        ...state.config,
        plex_enabled: true,
        plex_url: url,
        plex_token: token
      };
      const saveRes = await fetch('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updated)
      });
      if (saveRes.ok) {
        state.config = updated;
        showToast('🎉 Plex erfolgreich verbunden & gespeichert!');
        if (elements.hostPlexManualContainer) elements.hostPlexManualContainer.style.display = 'none';
        fetchHostPlexStatus();
      }
    } catch (err) {
      showToast('Fehler beim Verbinden.');
    } finally {
      elements.btnSaveManualHostPlex.disabled = false;
      elements.btnSaveManualHostPlex.textContent = 'Verbindung testen & speichern';
    }
  });
}

function openCenteredPlexPopup(url) {
  if (!url) return null;
  const width = 600;
  const height = 700;
  const left = window.screenLeft !== undefined
    ? window.screenLeft + Math.max(0, (window.outerWidth - width) / 2)
    : (window.screen.width - width) / 2;
  const top = window.screenTop !== undefined
    ? window.screenTop + Math.max(0, (window.outerHeight - height) / 2)
    : (window.screen.height - height) / 2;

  const popup = window.open(
    url,
    'PlexOAuthWindow',
    `width=${width},height=${height},top=${top},left=${left},scrollbars=yes,status=no,resizable=yes,menubar=no,toolbar=no,location=yes`
  );
  if (popup) {
    try { popup.focus(); } catch (e) {}
  }
  return popup;
}

// 1-Click OAuth PIN (Official Centered Popup Window)
if (elements.btnHostPlexOAuth) {
  elements.btnHostPlexOAuth.addEventListener('click', async () => {
    let popup = null;
    elements.btnHostPlexOAuth.disabled = true;
    elements.btnHostPlexOAuth.textContent = '⏳ PIN wird erstellt...';

    try {
      const callbackUrl = `${window.location.origin}/api/plex/callback`;
      const res = await fetch('/api/plex/auth/pin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callback_url: callbackUrl })
      });
      if (res.ok) {
        const pinData = await res.json();
        const authUrl = pinData.auth_url;
        const pinId = pinData.pin_id;
        const code = pinData.code;

        if (authUrl) {
          popup = openCenteredPlexPopup(authUrl);
        }

        if (elements.hostPlexStatusMsg) {
          elements.hostPlexStatusMsg.innerHTML = `Bitte bestätige die Anmeldung im geöffneten Plex-Fenster. (PIN-Code: <strong>${escapeHtml(code)}</strong>)`;
        }

        if (plexPinPollInterval) clearInterval(plexPinPollInterval);
        let attempts = 0;
        let finished = false;

        const handleSuccess = async () => {
          if (finished) return;
          finished = true;
          if (plexPinPollInterval) clearInterval(plexPinPollInterval);
          window.removeEventListener('message', onHostPlexMessage);
          if (popup && !popup.closed) {
            try { popup.close(); } catch (e) {}
          }
          elements.btnHostPlexOAuth.disabled = false;
          elements.btnHostPlexOAuth.innerHTML = '<span>📺 Mit Plex anmelden (1-Klick OAuth)</span>';
          if (elements.hostPlexStatusMsg) elements.hostPlexStatusMsg.textContent = '';
          showToast('🎉 Plex erfolgreich verknüpft!');
          await fetchHostConfig();
          await fetchHostPlexStatus();
          await fetchTracksList();
        };

        const onHostPlexMessage = async (e) => {
          if (e && e.data && e.data.type === 'PLEX_AUTH_SUCCESS') {
            try {
              const checkRes = await fetch(`/api/plex/auth/check?pin_id=${pinId}&code=${encodeURIComponent(code || '')}`);
              if (checkRes.ok) {
                const checkData = await checkRes.json();
                if (checkData.authorized) {
                  await handleSuccess();
                }
              }
            } catch (err) {}
          }
        };
        window.addEventListener('message', onHostPlexMessage);

        plexPinPollInterval = setInterval(async () => {
          if (finished) {
            clearInterval(plexPinPollInterval);
            return;
          }
          attempts++;
          if (attempts > 90) {
            clearInterval(plexPinPollInterval);
            window.removeEventListener('message', onHostPlexMessage);
            if (popup && !popup.closed) {
              try { popup.close(); } catch (e) {}
            }
            elements.btnHostPlexOAuth.disabled = false;
            elements.btnHostPlexOAuth.innerHTML = '<span>📺 Mit Plex anmelden (1-Klick OAuth)</span>';
            if (elements.hostPlexStatusMsg) elements.hostPlexStatusMsg.textContent = 'Zeitüberschreitung bei Plex-Anmeldung.';
            return;
          }

          try {
            const checkRes = await fetch(`/api/plex/auth/check?pin_id=${pinId}&code=${encodeURIComponent(code || '')}`);
            if (checkRes.ok) {
              const checkData = await checkRes.json();
              if (checkData.authorized) {
                await handleSuccess();
              }
            }
          } catch (e) {}
        }, 1500);
      } else {
        if (popup && !popup.closed) popup.close();
        elements.btnHostPlexOAuth.disabled = false;
        elements.btnHostPlexOAuth.innerHTML = '<span>📺 Mit Plex anmelden (1-Klick OAuth)</span>';
        showToast('Fehler beim Abrufen des Plex-Pins.');
      }
    } catch (err) {
      if (popup && !popup.closed) popup.close();
      showToast('Fehler beim Starten der Plex-Anmeldung.');
      elements.btnHostPlexOAuth.disabled = false;
      elements.btnHostPlexOAuth.innerHTML = '<span>📺 Mit Plex anmelden (1-Klick OAuth)</span>';
    }
  });
}

// Initial Boot
fetchHostInfo();
fetchHostConfig();
fetchHostPlexStatus();
fetchTracksList();
