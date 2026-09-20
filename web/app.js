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
      if (elements.cfgPreferLocalMeta) elements.cfgPreferLocalMeta.checked = !!cfg.prefer_local_metadata;
      if (elements.cfgPreferLocalLyrics) elements.cfgPreferLocalLyrics.checked = !!cfg.prefer_local_lyrics;
      if (elements.cfgFetchMissingOnline) elements.cfgFetchMissingOnline.checked = !!cfg.fetch_missing_online;
      if (elements.cfgSaveFetchedLrc) elements.cfgSaveFetchedLrc.checked = !!cfg.save_fetched_lrc_locally;

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

// 2. Save Config
if (elements.hostConfigForm) {
  elements.hostConfigForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const updated = {
      ...state.config,
      music_directory: elements.cfgMusicDir.value.trim(),
      prefer_local_metadata: elements.cfgPreferLocalMeta.checked,
      prefer_local_lyrics: elements.cfgPreferLocalLyrics.checked,
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

// Initial Boot
fetchHostInfo();
fetchHostConfig();
fetchTracksList();
