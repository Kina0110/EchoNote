// === State ===
let currentTranscript = null;
let audioSyncRAF = null;
let autoScrollEnabled = true;
let globalSearchTimeout = null;
let transcriptSearchMatches = [];
let currentMatchIndex = -1;

// Recording state
let mediaRecorder = null;
let recordedChunks = [];
let recordingStartTime = null;
let recordingTimerInterval = null;
let recordingBackupInterval = null;
let recordingBackupCounter = 0;
let recordingMimeType = '';
let recordingFileExtension = '';
let recordingSessionId = '';
let pendingRecordingSessionId = ''; // set before upload, cleared after transcript download

// Tag state
let activeTagFilter = null;
let allTags = {};
let allTranscripts = [];

// Staged files for multi-upload
let stagedFiles = [];

// Settings
let userSettings = {};

// Comments state
let commentsSidebarOpen = false;
let activeCommentPopover = null;

// Chat state
let chatOpen = false;
let chatThreads = [];
let activeChatId = null;

const SPEAKER_COLORS = [
  '#58a6ff', '#f78166', '#7ee787', '#d2a8ff',
  '#ff7b72', '#79c0ff', '#ffa657', '#a5d6ff',
];

// === Theme ===
function applyTheme(theme) {
  if (theme === 'system') {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light');
  } else {
    document.documentElement.setAttribute('data-theme', theme || 'dark');
  }
}

// Listen for system theme changes when set to 'system'
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  const theme = (userSettings.display || {}).theme || 'dark';
  if (theme === 'system') applyTheme('system');
});

// === Init ===
document.addEventListener('DOMContentLoaded', () => {
  checkHealth();
  loadSettings();
  loadTags().then(() => loadTranscripts());
  setupDragDrop();
  setupFileInput();
  setupKeyboardShortcut();
  
  // Restore last view on page refresh
  const lastView = localStorage.getItem('lastView');
  if (lastView === 'view-settings') {
    showSettings();
  } else {
    const lastTranscriptId = localStorage.getItem('lastTranscriptId');
    if (lastTranscriptId) {
      openTranscript(lastTranscriptId);
    }
  }
});

// === Health Check ===
async function checkHealth() {
  try {
    const res = await fetch('/api/health');
    const data = await res.json();
    const warnings = [];
    if (!data.ffmpeg) {
      warnings.push('ffmpeg is not installed. Install it with: <code>brew install ffmpeg</code>');
    }
    if (!data.api_key) {
      warnings.push('Deepgram API key not configured. Copy <code>.env.example</code> to <code>.env</code> and add your key.');
    }
    if (warnings.length > 0) {
      const el = document.getElementById('setup-warning');
      el.innerHTML = warnings.join('<br><br>');
      el.style.display = 'block';
    }
  } catch (e) {
    // Server not reachable - ignore
  }
}

// === Drag & Drop ===
function setupDragDrop() {
  const zone = document.getElementById('upload-zone');

  zone.addEventListener('dragover', (e) => {
    e.preventDefault();
    zone.classList.add('dragover');
  });

  zone.addEventListener('dragleave', () => {
    zone.classList.remove('dragover');
  });

  zone.addEventListener('drop', (e) => {
    e.preventDefault();
    zone.classList.remove('dragover');
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) stageFiles(files);
  });
}

function setupFileInput() {
  document.getElementById('file-input').addEventListener('change', (e) => {
    const files = Array.from(e.target.files);
    if (files.length > 0) stageFiles(files);
    e.target.value = '';
  });
}

function stageFiles(newFiles) {
  const allowed = ['.mp4', '.mov', '.avi', '.mkv', '.webm', '.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac', '.wma'];
  for (const file of newFiles) {
    const ext = '.' + file.name.split('.').pop().toLowerCase();
    if (!allowed.includes(ext)) {
      toast(`Unsupported format: ${file.name}`, 'error');
      return;
    }
    if (file.size > 2 * 1024 * 1024 * 1024) {
      toast(`File too large: ${file.name}`, 'error');
      return;
    }
  }
  stagedFiles = stagedFiles.concat(newFiles);
  renderStagedFiles();
}

function renderStagedFiles() {
  const container = document.getElementById('staged-files');
  const list = document.getElementById('staged-list');
  const zone = document.getElementById('upload-zone');

  if (stagedFiles.length === 0) {
    container.style.display = 'none';
    zone.style.display = '';
    return;
  }

  zone.style.display = 'none';
  container.style.display = '';

  list.innerHTML = stagedFiles.map((f, i) => {
    const sizeMB = (f.size / (1024 * 1024)).toFixed(1);
    return `
      <div class="staged-item">
        <span class="staged-name">${escapeHtml(f.name)}</span>
        <span class="staged-size">${sizeMB} MB</span>
        <button class="staged-remove" onclick="removeStagedFile(${i})">&times;</button>
      </div>
    `;
  }).join('');
}

function removeStagedFile(index) {
  stagedFiles.splice(index, 1);
  renderStagedFiles();
}

function clearStagedFiles() {
  stagedFiles = [];
  renderStagedFiles();
}

function transcribeStagedFiles() {
  if (stagedFiles.length === 0) return;
  if (stagedFiles.length === 1) {
    uploadFile(stagedFiles[0]);
  } else {
    uploadMultipleFiles(stagedFiles);
  }
  stagedFiles = [];
  document.getElementById('staged-files').style.display = 'none';
}

function setupKeyboardShortcut() {
  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'c' && currentTranscript && document.getElementById('view-transcript').classList.contains('active')) {
      // Only hijack Cmd+C if no text is selected
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed) {
        e.preventDefault();
        copyForChatGPT();
      }
    }
  });
}

// === Upload ===
async function _uploadAndTranscribe(formData, endpoint, { uploadLabel, processingLabel, doneLabel }) {
  const zone = document.getElementById('upload-zone');
  const progress = document.getElementById('upload-progress');
  const progressText = document.getElementById('progress-text');
  const progressBar = document.getElementById('progress-bar');

  const keepVideo = document.getElementById('keep-video-checkbox');
  if (keepVideo && keepVideo.checked) {
    formData.append('keep_video', 'true');
  }

  zone.style.display = 'none';
  progress.style.display = 'block';
  progressText.textContent = uploadLabel;
  progressBar.style.width = '0%';

  try {
    const xhr = new XMLHttpRequest();

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        progressBar.style.width = pct + '%';
        if (pct >= 100) {
          progressText.textContent = processingLabel;
        }
      }
    });

    const result = await new Promise((resolve, reject) => {
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          try {
            const err = JSON.parse(xhr.responseText);
            reject(new Error(err.detail || 'Upload failed'));
          } catch {
            reject(new Error('Upload failed'));
          }
        }
      };
      xhr.onerror = () => reject(new Error('Network error'));

      xhr.upload.addEventListener('loadend', () => {
        progressText.textContent = processingLabel;
        progressBar.style.width = '100%';
      });

      xhr.open('POST', endpoint);
      xhr.send(formData);
    });

    progressText.textContent = 'Done!';
    const durMin = (result.duration_seconds || 0) / 60;
    const estCost = (durMin * 0.0092).toFixed(2);
    toast(`${doneLabel} ${Math.round(durMin)} min · ~$${estCost}`, 'success');

    return result;
  } finally {
    await sleep(500);
    progress.style.display = 'none';
    zone.style.display = '';
  }
}

async function uploadFile(file) {
  const formData = new FormData();
  formData.append('file', file);

  try {
    const result = await _uploadAndTranscribe(formData, '/api/transcribe', {
      uploadLabel: 'Uploading file...',
      processingLabel: 'Transcribing with AI...',
      doneLabel: 'Transcription complete!',
    });

    // Auto-download transcript text if this came from a recording
    if (pendingRecordingSessionId) {
      try {
        const copyRes = await fetch(`/api/transcripts/${result.id}/copytext`);
        const text = await copyRes.text();
        downloadFile(text, 'rec-' + pendingRecordingSessionId + '-transcript.txt', 'text/plain');
      } catch (e) { /* non-critical */ }
      pendingRecordingSessionId = '';
    }

    showTranscript(result);
    loadTranscripts();
    checkCostAlert();
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function uploadMultipleFiles(fileList) {
  const files = Array.from(fileList);
  const formData = new FormData();
  for (const file of files) {
    formData.append('files', file);
  }

  try {
    const result = await _uploadAndTranscribe(formData, '/api/transcribe-multi', {
      uploadLabel: `Uploading ${files.length} files...`,
      processingLabel: 'Combining & transcribing with AI...',
      doneLabel: 'Combined transcription complete!',
    });

    showTranscript(result);
    loadTranscripts();
    checkCostAlert();
  } catch (e) {
    toast(e.message, 'error');
  }
}

// === Tags ===
async function loadTags() {
  try {
    const res = await fetch('/api/tags');
    allTags = await res.json();
  } catch (e) {
    allTags = {};
  }
}

function getTagStyle(tagName) {
  const color = allTags[tagName] || '#8b949e';
  const r = parseInt(color.slice(1, 3), 16);
  const g = parseInt(color.slice(3, 5), 16);
  const b = parseInt(color.slice(5, 7), 16);
  return { color: color, background: `rgba(${r}, ${g}, ${b}, 0.15)` };
}

function renderTagFilterBar() {
  const bar = document.getElementById('tag-filter-bar');
  const chips = document.getElementById('tag-filter-chips');
  const copyBar = document.getElementById('copy-all-bar');

  const tagSet = new Set();
  allTranscripts.forEach(t => (t.tags || []).forEach(tag => tagSet.add(tag)));

  if (tagSet.size === 0) {
    bar.style.display = 'none';
    return;
  }

  bar.style.display = 'block';
  const sortedTags = [...tagSet].sort();
  chips.innerHTML = sortedTags.map(tag => {
    const s = getTagStyle(tag);
    const isActive = activeTagFilter === tag;
    return `<span class="tag-filter-chip ${isActive ? 'active' : ''}" style="background:${s.background};color:${s.color}" onclick="filterByTag('${escapeAttr(tag)}')">${escapeHtml(tag)}</span>`;
  }).join('');

  if (activeTagFilter) {
    const count = allTranscripts.filter(t => (t.tags || []).includes(activeTagFilter)).length;
    document.getElementById('copy-all-count').textContent = `${count} transcript${count !== 1 ? 's' : ''} tagged "${activeTagFilter}"`;
    copyBar.style.display = 'flex';
  } else {
    copyBar.style.display = 'none';
  }
}

function filterByTag(tagName) {
  if (activeTagFilter === tagName) {
    activeTagFilter = null;
  } else {
    activeTagFilter = tagName;
  }
  renderTranscriptList();
  renderTagFilterBar();
}

async function addTag(transcriptId, tagName) {
  try {
    const res = await fetch(`/api/transcripts/${transcriptId}/tags`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ add: [tagName] }),
    });
    if (!res.ok) throw new Error('Failed to add tag');
    const data = await res.json();
    Object.assign(allTags, data.tags_map);
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function removeTag(transcriptId, tagName) {
  try {
    const res = await fetch(`/api/transcripts/${transcriptId}/tags`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remove: [tagName] }),
    });
    if (!res.ok) throw new Error('Failed to remove tag');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function refreshAfterTagChange() {
  loadTags().then(() => loadTranscripts());
  if (currentTranscript && document.getElementById('view-transcript').classList.contains('active')) {
    openTranscript(currentTranscript.id);
  }
}

async function showTagDialog(transcriptId) {
  let transcript;
  try {
    const res = await fetch(`/api/transcripts/${transcriptId}`);
    transcript = await res.json();
  } catch (e) {
    toast('Failed to load transcript', 'error');
    return;
  }

  const currentTags = transcript.tags || [];
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="tag-dialog">
      <h3>Manage Tags</h3>
      <div class="tag-dialog-current" id="dialog-current-tags">
        ${currentTags.map(tag => {
          const s = getTagStyle(tag);
          return `<span class="tag-chip" style="background:${s.background};color:${s.color}">${escapeHtml(tag)}<button class="tag-remove" data-tag="${escapeAttr(tag)}">&times;</button></span>`;
        }).join('')}
      </div>
      <div class="tag-input-wrapper">
        <input type="text" class="tag-input" id="tag-input" placeholder="Type a tag name..." autocomplete="off">
        <div class="tag-suggestions" id="tag-suggestions" style="display:none;"></div>
      </div>
      <div class="tag-dialog-footer">
        <button class="btn-secondary" id="tag-dialog-close">Done</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) { overlay.remove(); refreshAfterTagChange(); }
  });

  document.getElementById('tag-dialog-close').addEventListener('click', () => {
    overlay.remove(); refreshAfterTagChange();
  });

  overlay.querySelectorAll('.tag-remove').forEach(btn => {
    btn.addEventListener('click', async () => {
      const tag = btn.dataset.tag;
      await removeTag(transcriptId, tag);
      btn.closest('.tag-chip').remove();
    });
  });

  const input = document.getElementById('tag-input');
  const suggestionsEl = document.getElementById('tag-suggestions');

  input.addEventListener('input', () => {
    const val = input.value.trim().toLowerCase();
    if (!val) { suggestionsEl.style.display = 'none'; return; }

    const dialogCurrentTags = [...overlay.querySelectorAll('.tag-chip')].map(
      el => el.textContent.replace('\u00d7', '').trim()
    );
    const matches = Object.keys(allTags).filter(t =>
      t.toLowerCase().includes(val) && !dialogCurrentTags.includes(t)
    );

    if (matches.length === 0) {
      suggestionsEl.innerHTML = `<div class="tag-suggestion" data-tag="${escapeAttr(input.value.trim())}">Create "<strong>${escapeHtml(input.value.trim())}</strong>"</div>`;
    } else {
      suggestionsEl.innerHTML = matches.map(tag => {
        const s = getTagStyle(tag);
        return `<div class="tag-suggestion" data-tag="${escapeAttr(tag)}"><span class="tag-suggestion-dot" style="background:${s.color}"></span>${escapeHtml(tag)}</div>`;
      }).join('');
    }
    suggestionsEl.style.display = 'block';
  });

  function addChipToDialog(tag) {
    const s = getTagStyle(tag);
    const chip = document.createElement('span');
    chip.className = 'tag-chip';
    chip.style.background = s.background;
    chip.style.color = s.color;
    chip.innerHTML = `${escapeHtml(tag)}<button class="tag-remove" data-tag="${escapeAttr(tag)}">&times;</button>`;
    chip.querySelector('.tag-remove').addEventListener('click', async () => {
      await removeTag(transcriptId, tag);
      chip.remove();
    });
    document.getElementById('dialog-current-tags').appendChild(chip);
  }

  suggestionsEl.addEventListener('click', async (e) => {
    const el = e.target.closest('.tag-suggestion');
    if (!el) return;
    const tag = el.dataset.tag;
    await addTag(transcriptId, tag);
    addChipToDialog(tag);
    input.value = '';
    suggestionsEl.style.display = 'none';
  });

  input.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter' && input.value.trim()) {
      e.preventDefault();
      const tag = input.value.trim();
      await addTag(transcriptId, tag);
      addChipToDialog(tag);
      input.value = '';
      suggestionsEl.style.display = 'none';
    }
    if (e.key === 'Escape') { overlay.remove(); refreshAfterTagChange(); }
  });

  input.focus();
}

async function copyAllByTag() {
  if (!activeTagFilter) return;
  try {
    const res = await fetch(`/api/transcripts/copy-by-tag?tag=${encodeURIComponent(activeTagFilter)}`);
    const text = await res.text();
    await copyToClipboard(text);
    toast('All matching transcripts copied!', 'success');
  } catch (e) {
    toast('Failed to copy: ' + e.message, 'error');
  }
}

function renderTranscriptTags() {
  const container = document.getElementById('tag-chips');
  const tags = currentTranscript.tags || [];
  container.innerHTML = tags.map(tag => {
    const s = getTagStyle(tag);
    return `<span class="tag-chip" style="background:${s.background};color:${s.color}">${escapeHtml(tag)}<button class="tag-remove" onclick="event.stopPropagation(); removeTagAndRefresh('${currentTranscript.id}', '${escapeAttr(tag)}')">&times;</button></span>`;
  }).join('');
}

async function removeTagAndRefresh(transcriptId, tagName) {
  await removeTag(transcriptId, tagName);
  try {
    const res = await fetch(`/api/transcripts/${transcriptId}`);
    currentTranscript = await res.json();
    renderTranscriptTags();
  } catch (e) {
    toast('Failed to refresh', 'error');
  }
}

// === Transcript List ===
async function loadTranscripts() {
  try {
    const res = await fetch('/api/transcripts');
    allTranscripts = await res.json();
    renderTranscriptList();
    renderTagFilterBar();
  } catch (e) {
    // Silently fail on list load
  }
}

function renderTranscriptList() {
  const container = document.getElementById('transcripts-container');
  const empty = document.getElementById('no-transcripts');

  let list = allTranscripts;
  if (activeTagFilter) {
    list = list.filter(t => (t.tags || []).includes(activeTagFilter));
  }

  if (allTranscripts.length === 0) {
    container.innerHTML = '';
    empty.style.display = 'block';
    return;
  }

  if (list.length === 0 && activeTagFilter) {
    container.innerHTML = '';
    empty.textContent = `No transcripts tagged "${activeTagFilter}".`;
    empty.style.display = 'block';
    return;
  }

  empty.style.display = 'none';
  container.innerHTML = list.map(t => {
    const date = new Date(t.created_at).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    const dur = formatDuration(t.duration_seconds);
    const summaryHtml = t.summary ? `<div class="card-summary">${escapeHtml(t.summary)}</div>` : '';

    const tags = t.tags || [];
    let tagsHtml = `<div class="card-tags">`;
    tagsHtml += tags.map(tag => {
      const s = getTagStyle(tag);
      return `<span class="card-tag-chip" style="background:${s.background};color:${s.color}" onclick="event.stopPropagation(); filterByTag('${escapeAttr(tag)}')">${escapeHtml(tag)}</span>`;
    }).join('');
    tagsHtml += `<span class="card-tag-chip" style="background:var(--bg-tertiary);color:var(--text-muted);border:1px dashed var(--border);" onclick="event.stopPropagation(); showTagDialog('${t.id}')">+</span>`;
    tagsHtml += `</div>`;

    return `
      <div class="transcript-card" onclick="openTranscript('${t.id}')">
        <div class="card-info">
          <div class="card-filename">${escapeHtml(t.filename)}</div>
          <div class="card-meta">
            <span>${date}</span>
            <span class="meta-sep">&middot;</span>
            <span>${dur}</span>
            <span class="meta-sep">&middot;</span>
            <span>${t.num_speakers} speaker${t.num_speakers !== 1 ? 's' : ''}</span>
          </div>
          ${summaryHtml}
          ${tagsHtml}
        </div>
        <div class="card-actions">
          <button class="btn-delete" onclick="event.stopPropagation(); confirmDelete('${t.id}', '${escapeHtml(t.filename)}')" title="Delete">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="3 6 5 6 21 6"/>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
      </div>
    `;
  }).join('');
}

async function openTranscript(id) {
  try {
    const res = await fetch(`/api/transcripts/${id}`);
    if (!res.ok) throw new Error('Failed to load transcript');
    const transcript = await res.json();
    showTranscript(transcript);
    // Save to localStorage to restore on page refresh
    localStorage.setItem('lastTranscriptId', id);
  } catch (e) {
    toast(e.message, 'error');
  }
}

function confirmDelete(id, filename) {
  const overlay = document.createElement('div');
  overlay.className = 'confirm-overlay';
  overlay.innerHTML = `
    <div class="confirm-dialog">
      <p>Delete transcript for <strong>${filename}</strong>?</p>
      <div class="confirm-buttons">
        <button class="btn-secondary" onclick="this.closest('.confirm-overlay').remove()">Cancel</button>
        <button class="btn-danger" id="confirm-delete-btn">Delete</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) overlay.remove();
  });

  document.getElementById('confirm-delete-btn').addEventListener('click', async () => {
    overlay.remove();
    try {
      const res = await fetch(`/api/transcripts/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Delete failed');
      toast('Transcript deleted', 'success');
      loadTranscripts();
    } catch (e) {
      toast(e.message, 'error');
    }
  });
}

// === Transcript View ===
function showTranscript(transcript) {
  currentTranscript = transcript;

  document.getElementById('t-filename').textContent = transcript.filename;
  document.getElementById('t-date').textContent = new Date(transcript.created_at).toLocaleDateString('en-US', {
    year: 'numeric', month: 'long', day: 'numeric'
  });
  document.getElementById('t-duration').textContent = formatDuration(transcript.duration_seconds);

  // Show source files note for combined transcripts
  const sourceFilesEl = document.getElementById('t-source-files');
  if (transcript.source_files && transcript.source_files.length > 1) {
    sourceFilesEl.textContent = 'Combined from: ' + transcript.source_files.join(', ');
    sourceFilesEl.style.display = '';
  } else {
    sourceFilesEl.style.display = 'none';
  }

  const summaryEl = document.getElementById('t-summary');
  const summaryBtn = document.getElementById('btn-generate-summary');
  summaryEl.textContent = transcript.summary || '';
  summaryEl.style.display = transcript.summary ? '' : 'none';
  summaryBtn.style.display = transcript.summary ? 'none' : '';

  // Show re-transcribe button if utterances lack word-level data and audio exists
  const retranscribeBtn = document.getElementById('btn-retranscribe');
  const hasWords = transcript.utterances && transcript.utterances.some(u => u.words && u.words.length > 0);
  const hasAudio = !!transcript.audio_file;
  retranscribeBtn.style.display = (!hasWords && hasAudio) ? '' : 'none';

  actionItemsCollapsed = true;
  renderActionItems();
  renderChaptersNav();

  bookmarkFilterActive = false;
  const bmBtn = document.getElementById('btn-bookmark-filter');
  if (bmBtn) bmBtn.classList.remove('active');

  renderSpeakers();
  renderTranscriptTags();
  renderUtterances();

  // Apply font size setting
  const fontSize = (userSettings.display || {}).transcript_font_size || 'medium';
  const utterancesEl = document.getElementById('utterances');
  utterancesEl.classList.remove('font-small', 'font-medium', 'font-large');
  utterancesEl.classList.add('font-' + fontSize);

  setupAudioPlayer(transcript);
  updateCommentsBadge();
  commentsSidebarOpen = false;
  const cSidebar = document.getElementById('comments-sidebar');
  if (cSidebar) cSidebar.classList.remove('open');

  // Reset chat
  chatOpen = false;
  chatThreads = [];
  activeChatId = null;
  const chatPanel = document.getElementById('chat-panel');
  if (chatPanel) chatPanel.classList.remove('open');
  const chatBtn = document.getElementById('btn-chat-toggle');
  if (chatBtn) {
    chatBtn.classList.remove('active');
    chatBtn.style.display = (userSettings.features?.chat_enabled === false) ? 'none' : '';
  }
  loadChatThreads();

  switchView('view-transcript');
}

function renderSpeakers() {
  const legend = document.getElementById('speaker-legend');
  const speakers = Object.entries(currentTranscript.speakers);

  legend.innerHTML = speakers.map(([key, displayName], i) => {
    const color = SPEAKER_COLORS[i % SPEAKER_COLORS.length];
    return `
      <div class="speaker-chip" onclick="renameSpeaker('${escapeAttr(key)}', this)">
        <span class="speaker-dot" style="background:${color}"></span>
        <span class="speaker-label">${escapeHtml(displayName)}</span>
      </div>
    `;
  }).join('');
}

let bookmarkFilterActive = false;

function renderUtterances() {
  const container = document.getElementById('utterances');
  const speakers = Object.keys(currentTranscript.speakers);
  const bookmarks = currentTranscript.bookmarks || [];
  const chapterMap = buildChapterMap(currentTranscript.utterances, currentTranscript.chapters);
  const showTimestamps = (userSettings.display || {}).show_timestamps !== false;

  container.innerHTML = currentTranscript.utterances.map((u, i) => {
    const chapterHeader = chapterMap[i]
      ? `<div class="chapter-header" id="chapter-${i}"><span class="chapter-header-title">${escapeHtml(chapterMap[i])}</span></div>`
      : '';

    // Render file-boundary dividers for combined transcripts
    if (u.type === 'file-boundary') {
      return chapterHeader + `
        <div class="file-boundary" data-index="${i}">
          <div class="file-boundary-line"></div>
          <span class="file-boundary-label">${escapeHtml(u.filename)}</span>
          <div class="file-boundary-line"></div>
        </div>
      `;
    }

    const idx = speakers.indexOf(u.speaker);
    const color = SPEAKER_COLORS[(idx >= 0 ? idx : 0) % SPEAKER_COLORS.length];
    const displayName = currentTranscript.speakers[u.speaker] || u.speaker;
    const ts = formatTimestamp(u.start);
    const isBookmarked = bookmarks.includes(i);
    const hidden = bookmarkFilterActive && !isBookmarked ? 'style="display:none"' : '';
    const comments = currentTranscript.comments || {};
    const hasComments = comments[String(i)] && comments[String(i)].length > 0;
    const commentCount = hasComments ? comments[String(i)].length : 0;

    // Render words as individual spans if available, else fall back to plain text
    const textHtml = (u.words && u.words.length > 0)
      ? u.words.map(w => `<span class="word" data-start="${w.start}" data-end="${w.end}">${escapeHtml(w.word)}</span>`).join(' ')
      : escapeHtml(u.text);

    return chapterHeader + `
      <div class="utterance ${isBookmarked ? 'bookmarked' : ''} ${hasComments ? 'has-comments' : ''}" data-index="${i}" data-start="${u.start}" data-end="${u.end}" onclick="onUtteranceClick(event, ${i}, ${u.start})" ${hidden}>
        <div class="u-timestamp" ${showTimestamps ? '' : 'style="display:none"'}>${ts}</div>
        <div class="u-content">
          <div class="u-speaker" style="color:${color}">${escapeHtml(displayName)}</div>
          <div class="u-text">${textHtml}</div>
        </div>
        <button class="u-copy" onclick="event.stopPropagation(); copyUtterance(${i})" title="Copy">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        </button>
        <button class="u-comment ${hasComments ? 'has-comments' : ''}" onclick="event.stopPropagation(); openCommentInput(${i})" title="${hasComments ? commentCount + ' comment(s)' : 'Add comment'}">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="${hasComments ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
        </button>
        <button class="u-bookmark ${isBookmarked ? 'active' : ''}" onclick="event.stopPropagation(); toggleBookmark(${i})" title="Bookmark">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="${isBookmarked ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2"><path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/></svg>
        </button>
      </div>
    `;
  }).join('');
}

// Two-level click: first click seeks to utterance start, second click on a word seeks to word start
function onUtteranceClick(event, index, utteranceStart) {
  const utteranceEl = event.currentTarget;
  const wordEl = event.target.closest('.word');

  // If this utterance is already in word mode and a word was clicked, seek to word
  if (utteranceEl.classList.contains('words-active') && wordEl) {
    const wordStart = parseFloat(wordEl.dataset.start);
    seekToUtterance(wordStart);
    return;
  }

  // Deactivate word mode on any previously active utterance
  const prevWordsActive = document.querySelector('.utterance.words-active');
  if (prevWordsActive) prevWordsActive.classList.remove('words-active');

  // Seek to utterance start
  seekToUtterance(utteranceStart);

  // Activate word mode on this utterance (if it has word spans)
  if (utteranceEl.querySelector('.word')) {
    utteranceEl.classList.add('words-active');
  }
}

async function copyUtterance(index) {
  const u = currentTranscript.utterances[index];
  if (!u) return;
  const name = currentTranscript.speakers[u.speaker] || u.speaker;
  const text = `${name}: ${u.text}`;
  try {
    await copyToClipboard(text);
    toast('Copied!', 'success');
    const btn = document.querySelector(`.utterance[data-index="${index}"] .u-copy`);
    if (btn) { btn.classList.add('copied'); setTimeout(() => btn.classList.remove('copied'), 1500); }
  } catch (e) {
    toast('Failed to copy', 'error');
  }
}

async function toggleBookmark(index) {
  if (!currentTranscript) return;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/bookmark`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index })
    });
    if (!res.ok) throw new Error('Failed to toggle bookmark');
    const data = await res.json();
    currentTranscript.bookmarks = data.bookmarks;
    renderUtterances();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function toggleBookmarkFilter() {
  bookmarkFilterActive = !bookmarkFilterActive;
  const btn = document.getElementById('btn-bookmark-filter');
  btn.classList.toggle('active', bookmarkFilterActive);
  renderUtterances();
}

// === Comments ===

function openCommentInput(index) {
  closeCommentPopover();
  const utteranceEl = document.querySelector(`.utterance[data-index="${index}"]`);
  if (!utteranceEl) return;
  activeCommentPopover = index;
  const popover = document.createElement('div');
  popover.className = 'comment-input-popover';
  popover.id = 'comment-popover';
  popover.innerHTML = `
    <input type="text" placeholder="Add a comment..." id="comment-input-field" onkeydown="if(event.key==='Enter') submitComment(${index})">
    <button onclick="submitComment(${index})">Add</button>
  `;
  popover.addEventListener('click', (e) => e.stopPropagation());
  utteranceEl.appendChild(popover);
  setTimeout(() => {
    const input = document.getElementById('comment-input-field');
    if (input) input.focus();
  }, 0);
  document.addEventListener('click', handleCommentPopoverOutsideClick);
}

function handleCommentPopoverOutsideClick(e) {
  const popover = document.getElementById('comment-popover');
  if (popover && !popover.contains(e.target)) {
    closeCommentPopover();
  }
}

function closeCommentPopover() {
  const popover = document.getElementById('comment-popover');
  if (popover) popover.remove();
  activeCommentPopover = null;
  document.removeEventListener('click', handleCommentPopoverOutsideClick);
}

async function submitComment(index) {
  const input = document.getElementById('comment-input-field');
  if (!input || !input.value.trim()) return;
  const text = input.value.trim();
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/comments`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index, text })
    });
    if (!res.ok) throw new Error('Failed to add comment');
    const data = await res.json();
    currentTranscript.comments = data.comments;
    closeCommentPopover();
    renderUtterances();
    renderCommentsSidebar();
    updateCommentsBadge();
    toast('Comment added', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function deleteComment(index, commentId) {
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/comments`, {
      method: 'DELETE',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index, comment_id: commentId })
    });
    if (!res.ok) throw new Error('Failed to delete comment');
    const data = await res.json();
    currentTranscript.comments = data.comments;
    renderUtterances();
    renderCommentsSidebar();
    updateCommentsBadge();
    toast('Comment deleted', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function toggleCommentsSidebar() {
  commentsSidebarOpen = !commentsSidebarOpen;
  const sidebar = document.getElementById('comments-sidebar');
  const overlay = document.getElementById('comments-overlay');
  const btn = document.getElementById('btn-comments-toggle');
  sidebar.classList.toggle('open', commentsSidebarOpen);
  if (overlay) overlay.classList.toggle('open', commentsSidebarOpen);
  if (btn) btn.classList.toggle('active', commentsSidebarOpen);
  if (commentsSidebarOpen) renderCommentsSidebar();
}

function renderCommentsSidebar() {
  const list = document.getElementById('comments-sidebar-list');
  if (!currentTranscript || !list) return;
  const comments = currentTranscript.comments || {};
  const entries = Object.entries(comments).sort((a, b) => Number(a[0]) - Number(b[0]));
  if (entries.length === 0) {
    list.innerHTML = '<p class="comments-empty">No comments yet. Click the comment icon on any utterance to add one.</p>';
    return;
  }
  const speakers = currentTranscript.speakers || {};
  const utterances = currentTranscript.utterances || [];
  list.innerHTML = entries.map(([indexStr, commentArr]) => {
    const idx = Number(indexStr);
    const u = utterances[idx];
    if (!u || u.type === 'file-boundary') return '';
    const displayName = speakers[u.speaker] || u.speaker;
    const ts = formatTimestamp(u.start);
    const preview = u.text.length > 60 ? u.text.substring(0, 60) + '...' : u.text;
    const commentItems = commentArr.map(c => {
      const created = new Date(c.created_at);
      const timeStr = created.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
      return `
        <div class="comment-item">
          <div class="comment-text">${escapeHtml(c.text)}</div>
          <span class="comment-time">${timeStr}</span>
          <button class="comment-delete" onclick="event.stopPropagation(); deleteComment(${idx}, '${escapeAttr(c.id)}')" title="Delete">
            <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
      `;
    }).join('');
    return `
      <div class="comment-group" onclick="jumpToComment(${idx})">
        <div class="comment-group-header">
          <span class="speaker-name">${escapeHtml(displayName)}</span>
          <span>${ts}</span>
        </div>
        <div class="comment-group-preview">"${escapeHtml(preview)}"</div>
        ${commentItems}
      </div>
    `;
  }).join('');
}

function jumpToComment(index) {
  const el = document.querySelector(`.utterance[data-index="${index}"]`);
  if (!el) return;
  if (el.style.display === 'none') {
    bookmarkFilterActive = false;
    const btn = document.getElementById('btn-bookmark-filter');
    if (btn) btn.classList.remove('active');
    renderUtterances();
    const newEl = document.querySelector(`.utterance[data-index="${index}"]`);
    if (newEl) highlightAndScrollComment(newEl);
  } else {
    highlightAndScrollComment(el);
  }
}

function highlightAndScrollComment(el) {
  document.querySelectorAll('.utterance.action-highlight').forEach(e => e.classList.remove('action-highlight'));
  scrollToUtterance(el);
  el.classList.add('action-highlight');
  setTimeout(() => el.classList.remove('action-highlight'), 2000);
}

function updateCommentsBadge() {
  const badge = document.getElementById('comments-badge');
  if (!badge) return;
  const comments = currentTranscript?.comments || {};
  const total = Object.values(comments).reduce((sum, arr) => sum + arr.length, 0);
  badge.textContent = total;
  badge.style.display = total > 0 ? '' : 'none';
}

function renameTranscript() {
  if (!currentTranscript) return;
  const el = document.getElementById('t-filename');
  const current = currentTranscript.filename;

  const input = document.createElement('input');
  input.type = 'text';
  input.value = current;
  input.className = 't-filename-input';

  el.textContent = '';
  el.appendChild(input);
  input.focus();
  input.select();

  async function save() {
    const newName = input.value.trim() || current;
    el.textContent = newName;
    if (newName !== current) {
      try {
        await fetch(`/api/transcripts/${currentTranscript.id}/rename`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: newName })
        });
        currentTranscript.filename = newName;
        loadTranscripts();
      } catch (e) {
        toast('Rename failed', 'error');
        el.textContent = current;
      }
    }
  }

  input.addEventListener('blur', save);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') input.blur();
    if (e.key === 'Escape') { input.value = current; input.blur(); }
  });
}

function renameSpeaker(speakerKey, chipEl) {
  const currentName = currentTranscript.speakers[speakerKey];
  const label = chipEl.querySelector('.speaker-label');

  // Replace label with input
  const input = document.createElement('input');
  input.className = 'speaker-name-input';
  input.type = 'text';
  input.value = currentName;
  input.size = Math.max(currentName.length, 5);
  label.replaceWith(input);
  input.focus();
  input.select();

  const save = async () => {
    const newName = input.value.trim() || currentName;
    try {
      const res = await fetch(`/api/transcripts/${currentTranscript.id}/speakers`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [speakerKey]: newName }),
      });
      if (!res.ok) throw new Error('Rename failed');
      currentTranscript = await res.json();
      renderSpeakers();
      renderUtterances();
    } catch (e) {
      toast(e.message, 'error');
      renderSpeakers();
    }
  };

  input.addEventListener('blur', save);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); input.blur(); }
    if (e.key === 'Escape') { input.value = currentName; input.blur(); }
  });
}

// === Chapters ===

function buildChapterMap(utterances, chapters) {
  if (!chapters || !chapters.length) return {};
  const map = {};
  const sorted = [...chapters].sort((a, b) => a.start - b.start);
  for (const ch of sorted) {
    for (let i = 0; i < utterances.length; i++) {
      const u = utterances[i];
      if (u.type === 'file-boundary') continue;
      if (u.start >= ch.start) {
        if (!(i in map)) map[i] = ch.title;
        break;
      }
    }
  }
  return map;
}

function renderChaptersNav() {
  const nav = document.getElementById('chapters-nav');
  const btn = document.getElementById('btn-generate-chapters');
  const chapters = currentTranscript.chapters || [];
  const utterances = currentTranscript.utterances || [];

  if (!chapters.length) {
    nav.style.display = 'none';
    btn.style.display = '';
    return;
  }

  const chapterMap = buildChapterMap(utterances, chapters);
  const entries = Object.entries(chapterMap).sort((a, b) => Number(a[0]) - Number(b[0]));

  nav.style.display = '';
  nav.innerHTML = `
    <div class="chapters-nav-title">Chapters</div>
    <div class="chapters-nav-list">
      ${entries.map(([idx, title]) => {
        const u = utterances[Number(idx)];
        const ts = u ? formatTimestamp(u.start) : '';
        return `<button class="chapter-nav-item" onclick="jumpToChapter(${idx})">
          <span class="chapter-nav-time">${ts}</span>
          <span class="chapter-nav-label">${escapeHtml(title)}</span>
        </button>`;
      }).join('')}
    </div>
  `;
  btn.style.display = 'none';
}

function jumpToChapter(utteranceIndex) {
  const headerEl = document.getElementById(`chapter-${utteranceIndex}`);
  if (headerEl) headerEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  const u = currentTranscript.utterances[utteranceIndex];
  if (u) seekToUtterance(u.start);
}

async function generateChapters() {
  if (!currentTranscript) return;
  const btn = document.getElementById('btn-generate-chapters');
  btn.textContent = 'Generating...';
  btn.disabled = true;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/chapters`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to generate chapters');
    }
    const data = await res.json();
    currentTranscript.chapters = data.chapters;
    renderChaptersNav();
    renderUtterances();
    toast('Chapters generated!', 'success');
  } catch (e) {
    toast(e.message, 'error');
    btn.textContent = 'Generate Chapters';
    btn.disabled = false;
  }
}

// === Copy & Export ===
async function generateSummary() {
  if (!currentTranscript) return;
  const btn = document.getElementById('btn-generate-summary');
  btn.textContent = 'Generating...';
  btn.disabled = true;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/summary`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Failed to generate summary');
    }
    const data = await res.json();
    currentTranscript.summary = data.summary;
    currentTranscript.action_items = data.action_items || [];
    const summaryEl = document.getElementById('t-summary');
    summaryEl.textContent = data.summary;
    summaryEl.style.display = '';
    btn.style.display = 'none';
    renderActionItems();
    toast('Summary generated!', 'success');
  } catch (e) {
    toast(e.message, 'error');
    btn.textContent = 'Generate Summary';
    btn.disabled = false;
  }
}

async function retranscribe() {
  if (!currentTranscript) return;
  const btn = document.getElementById('btn-retranscribe');
  btn.textContent = 'Re-transcribing...';
  btn.disabled = true;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/retranscribe`, { method: 'POST' });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Re-transcription failed');
    }
    const data = await res.json();
    currentTranscript = data;
    renderSpeakers();
    renderUtterances();
    btn.style.display = 'none';
    toast('Word timestamps added!', 'success');
  } catch (e) {
    toast(e.message, 'error');
    btn.textContent = 'Refresh Word Timestamps';
    btn.disabled = false;
  }
}

let actionItemsCollapsed = false;

function toggleActionItems() {
  actionItemsCollapsed = !actionItemsCollapsed;
  document.getElementById('action-items-body').style.display = actionItemsCollapsed ? 'none' : '';
  document.getElementById('action-items-chevron').style.transform = actionItemsCollapsed ? 'rotate(-90deg)' : '';
}

function renderActionItems() {
  const section = document.getElementById('action-items-section');
  const items = currentTranscript.action_items || [];
  if (!items.length) {
    section.style.display = 'none';
    return;
  }
  section.style.display = '';
  const visibleCount = items.filter(i => i.status !== 'deleted').length;
  document.getElementById('action-items-toggle-label').textContent = `Action Items (${visibleCount})`;
  document.getElementById('action-items-body').style.display = actionItemsCollapsed ? 'none' : '';
  document.getElementById('action-items-chevron').style.transform = actionItemsCollapsed ? 'rotate(-90deg)' : '';

  const userName = (userSettings.profile && userSettings.profile.name) || '';
  const hasUserItems = userName && items.some(item => item.assigned_to === 'user');

  const userGroup = document.getElementById('user-action-items-group');
  const userList = document.getElementById('user-action-items-list');
  const allGroup = document.getElementById('all-action-items-group');
  const allList = document.getElementById('all-action-items-list');
  const allHeader = document.getElementById('all-action-items-header');

  if (hasUserItems) {
    userGroup.style.display = '';
    allHeader.textContent = 'Other Action Items';
    const userItems = items.map((item, i) => ({ item, i })).filter(({ item }) => item.assigned_to === 'user');
    const otherItems = items.map((item, i) => ({ item, i })).filter(({ item }) => item.assigned_to !== 'user');
    userList.innerHTML = userItems.map(({ item, i }) => renderSingleActionItem(item, i)).join('');
    if (otherItems.length) {
      allGroup.style.display = '';
      allList.innerHTML = otherItems.map(({ item, i }) => renderSingleActionItem(item, i)).join('');
    } else {
      allGroup.style.display = 'none';
    }
  } else {
    userGroup.style.display = 'none';
    allGroup.style.display = '';
    allHeader.textContent = 'Action Items';
    allList.innerHTML = items.map((item, i) => renderSingleActionItem(item, i)).join('');
  }
}

function renderSingleActionItem(item, i) {
  const status = item.status || 'pending';
  const checkSvg = status === 'accepted'
    ? '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="3"><path d="M5 12l5 5L19 7"/></svg>'
    : '';
  return `
    <div class="action-item ${status}" data-index="${i}">
      <button class="action-item-check" onclick="toggleActionItem(${i})" title="${status === 'accepted' ? 'Mark pending' : 'Accept'}">${checkSvg}</button>
      <span class="action-item-text" onclick="event.stopPropagation(); highlightActionSource(${i})" style="cursor:pointer">${escapeHtml(item.text)}</span>
      <button class="action-item-dismiss" onclick="dismissActionItem(${i})" title="${status === 'dismissed' ? 'Restore' : 'Dismiss'}">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
      </button>
      <button class="action-item-delete" onclick="deleteActionItem(${i})" title="Delete">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18M8 6V4h8v2M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>
      </button>
    </div>
  `;
}

async function updateActionItemStatus(index, status) {
  if (!currentTranscript) return;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/action-items`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ index, status })
    });
    if (!res.ok) throw new Error('Failed to update');
    const data = await res.json();
    currentTranscript.action_items = data.action_items;
    renderActionItems();
  } catch (e) {
    toast('Failed to update action item', 'error');
  }
}

function toggleActionItem(index) {
  const items = currentTranscript.action_items || [];
  const current = items[index]?.status || 'pending';
  const next = current === 'accepted' ? 'pending' : 'accepted';
  updateActionItemStatus(index, next);
}

function dismissActionItem(index) {
  const items = currentTranscript.action_items || [];
  const current = items[index]?.status || 'pending';
  const next = current === 'dismissed' ? 'pending' : 'dismissed';
  updateActionItemStatus(index, next);
}

function deleteActionItem(index) {
  updateActionItemStatus(index, 'deleted');
}

function highlightActionSource(index) {
  const items = currentTranscript.action_items || [];
  const item = items[index];
  if (!item || !currentTranscript.utterances) return;

  // Extract meaningful keywords from the action item (3+ chars, skip stop words)
  const stopWords = new Set(['the','and','for','with','that','this','from','are','was','were','will','have','has','been','being','would','could','should','into','about','also','then','than','them','they','their','when','what','which','where','who','how','not','but','all','can','had','her','his','its','our','out','use','may','need','new','now','one','two','get','set','add','run','let','put','via','per']);
  const keywords = item.text.toLowerCase()
    .replace(/[^a-z0-9\s]/g, '')
    .split(/\s+/)
    .filter(w => w.length >= 3 && !stopWords.has(w));

  if (!keywords.length) return;

  // Score each utterance by how many keywords it contains
  let bestIdx = -1;
  let bestScore = 0;
  currentTranscript.utterances.forEach((u, i) => {
    if (u.type === 'file-boundary') return;
    const text = u.text.toLowerCase();
    let score = 0;
    keywords.forEach(kw => {
      if (text.includes(kw)) score++;
    });
    if (score > bestScore) {
      bestScore = score;
      bestIdx = i;
    }
  });

  if (bestIdx < 0) {
    toast('Could not find matching section', 'error');
    return;
  }

  // Remove any existing action highlight
  document.querySelectorAll('.utterance.action-highlight').forEach(el => {
    el.classList.remove('action-highlight');
  });

  // Highlight and scroll to the best match
  const utteranceEl = document.querySelector(`.utterance[data-index="${bestIdx}"]`);
  if (utteranceEl) {
    utteranceEl.classList.add('action-highlight');
    utteranceEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    // Remove highlight after 3 seconds
    setTimeout(() => utteranceEl.classList.remove('action-highlight'), 3000);
  }
}

function copyToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  // Fallback for iOS Safari over HTTP
  // Use contentEditable div - more reliable on iOS than textarea
  const el = document.createElement('div');
  el.contentEditable = true;
  el.textContent = text;
  el.style.position = 'fixed';
  el.style.top = '0';
  el.style.left = '0';
  el.style.width = '1px';
  el.style.height = '1px';
  el.style.overflow = 'hidden';
  el.style.opacity = '0.01';
  document.body.appendChild(el);
  
  const range = document.createRange();
  range.selectNodeContents(el);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
  
  document.execCommand('copy');
  selection.removeAllRanges();
  document.body.removeChild(el);
  return Promise.resolve();
}

async function copyForChatGPT() {
  if (!currentTranscript) return;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/copytext`);
    const text = await res.text();
    await copyToClipboard(text);
    toast('Copied to clipboard!', 'success');
  } catch (e) {
    toast('Failed to copy: ' + e.message, 'error');
  }
}

function exportTxt() {
  if (!currentTranscript) return;
  const exportSettings = userSettings.export || {};
  const includeSpeakers = exportSettings.include_speaker_names !== false;
  const includeTimestamps = exportSettings.include_timestamps !== false;

  let lines = [];
  currentTranscript.utterances.forEach((u) => {
    if (u.type === 'file-boundary') {
      lines.push('--- ' + u.filename + ' ---');
      return;
    }
    const name = currentTranscript.speakers[u.speaker] || u.speaker;
    const ts = formatTimestamp(u.start);
    let line = '';
    if (includeTimestamps) line += '[' + ts + '] ';
    if (includeSpeakers) line += name + ': ';
    line += u.text;
    lines.push(line);
  });
  const text = lines.join('\n');
  downloadFile(text, currentTranscript.filename.replace(/\.[^.]+$/, '') + '.txt', 'text/plain');
}

function exportSrt() {
  if (!currentTranscript) return;
  const exportSettings = userSettings.export || {};
  const includeSpeakers = exportSettings.include_speaker_names !== false;
  let srt = '';
  let srtIndex = 1;
  currentTranscript.utterances.forEach((u) => {
    if (u.type === 'file-boundary') return;
    const startSrt = toSrtTime(u.start);
    const endSrt = toSrtTime(u.end);
    const name = currentTranscript.speakers[u.speaker] || u.speaker;
    const text = includeSpeakers ? `${name}: ${u.text}` : u.text;
    srt += `${srtIndex}\n${startSrt} --> ${endSrt}\n${text}\n\n`;
    srtIndex++;
  });
  downloadFile(srt, currentTranscript.filename.replace(/\.[^.]+$/, '') + '.srt', 'text/srt');
}

function downloadZip() {
  if (!currentTranscript) return;
  const a = document.createElement('a');
  a.href = `/api/transcripts/${currentTranscript.id}/download`;
  a.download = '';
  a.click();
}

function downloadFile(content, filename, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// === Export Menu ===
function toggleExportMenu() {
  const menu = document.getElementById('export-menu');
  const isOpen = menu.classList.contains('open');
  if (isOpen) {
    closeExportMenu();
  } else {
    menu.classList.add('open');
    document.addEventListener('click', handleExportMenuOutsideClick);
  }
}

function closeExportMenu() {
  const menu = document.getElementById('export-menu');
  if (menu) menu.classList.remove('open');
  document.removeEventListener('click', handleExportMenuOutsideClick);
}

function handleExportMenuOutsideClick(e) {
  const dropdown = document.getElementById('export-dropdown');
  if (dropdown && !dropdown.contains(e.target)) {
    closeExportMenu();
  }
}

// === Settings ===
async function loadSettings() {
  try {
    const res = await fetch('/api/settings');
    userSettings = await res.json();
  } catch (e) {
    userSettings = {};
  }
  applyTheme((userSettings.display || {}).theme || 'dark');
}

function setSelectValue(id, value) {
  const el = document.getElementById(id);
  if (!el) return;
  const strVal = String(value ?? '');
  for (const opt of el.options) {
    if (opt.value === strVal) { el.value = strVal; return; }
  }
}

async function showSettings() {
  await loadSettings();
  const profile = userSettings.profile || {};
  document.getElementById('settings-name').value = profile.name || '';
  document.getElementById('settings-role').value = profile.role || '';
  document.getElementById('settings-saved-indicator').style.display = 'none';
  const chatCheckbox = document.getElementById('settings-chat-enabled');
  if (chatCheckbox) chatCheckbox.checked = userSettings.features?.chat_enabled !== false;

  // Transcription
  const ts = userSettings.transcription || {};
  setSelectValue('settings-language', ts.default_language || 'auto');
  const autoSummary = document.getElementById('settings-auto-summary');
  if (autoSummary) autoSummary.checked = ts.auto_summary !== false;
  const autoChapters = document.getElementById('settings-auto-chapters');
  if (autoChapters) autoChapters.checked = ts.auto_chapters !== false;

  // Display
  const ds = userSettings.display || {};
  setSelectValue('settings-theme', ds.theme || 'dark');
  setSelectValue('settings-playback-speed', ds.default_playback_speed || 1);
  const showTs = document.getElementById('settings-show-timestamps');
  if (showTs) showTs.checked = ds.show_timestamps !== false;
  setSelectValue('settings-font-size', ds.transcript_font_size || 'medium');

  // Export
  const ex = userSettings.export || {};
  setSelectValue('settings-export-format', ex.default_format || 'txt');
  const inclSpeakers = document.getElementById('settings-include-speakers');
  if (inclSpeakers) inclSpeakers.checked = ex.include_speaker_names !== false;
  const inclTs = document.getElementById('settings-include-timestamps');
  if (inclTs) inclTs.checked = ex.include_timestamps !== false;

  // AI
  const ai = userSettings.ai || {};
  setSelectValue('settings-chat-model', ai.chat_model || 'gpt-5-mini');
  setSelectValue('settings-max-history', ai.max_chat_history || 10);

  // Notifications
  const notif = userSettings.notifications || {};
  const threshold = document.getElementById('settings-cost-threshold');
  if (threshold) threshold.value = notif.cost_alert_threshold || '';

  switchView('view-settings');
}

async function saveAutoSettings(section) {
  let data = {};
  if (section === 'transcription') {
    data = {
      default_language: document.getElementById('settings-language')?.value || 'auto',
      auto_summary: document.getElementById('settings-auto-summary')?.checked ?? true,
      auto_chapters: document.getElementById('settings-auto-chapters')?.checked ?? true,
    };
  } else if (section === 'display') {
    data = {
      theme: document.getElementById('settings-theme')?.value || 'dark',
      default_playback_speed: parseFloat(document.getElementById('settings-playback-speed')?.value || '1'),
      show_timestamps: document.getElementById('settings-show-timestamps')?.checked ?? true,
      transcript_font_size: document.getElementById('settings-font-size')?.value || 'medium',
    };
    applyTheme(data.theme);
  } else if (section === 'export') {
    data = {
      default_format: document.getElementById('settings-export-format')?.value || 'txt',
      include_speaker_names: document.getElementById('settings-include-speakers')?.checked ?? true,
      include_timestamps: document.getElementById('settings-include-timestamps')?.checked ?? true,
    };
  } else if (section === 'ai') {
    data = {
      chat_model: document.getElementById('settings-chat-model')?.value || 'gpt-5-mini',
      max_chat_history: parseInt(document.getElementById('settings-max-history')?.value || '10', 10),
    };
  } else if (section === 'notifications') {
    const val = document.getElementById('settings-cost-threshold')?.value;
    data = {
      cost_alert_threshold: val ? parseFloat(val) : null,
    };
  }
  try {
    const res = await fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ [section]: data }),
    });
    if (!res.ok) throw new Error('Failed to save');
    userSettings = await res.json();
    toast('Settings saved', 'success');
  } catch (e) {
    toast('Failed to save settings', 'error');
  }
}

async function saveSettings() {
  const name = document.getElementById('settings-name').value.trim();
  const role = document.getElementById('settings-role').value.trim();
  const btn = document.getElementById('btn-save-settings');
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const res = await fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: { name, role } }),
    });
    if (!res.ok) throw new Error('Failed to save');
    userSettings = await res.json();
    document.getElementById('settings-saved-indicator').style.display = '';
    toast('Settings saved', 'success');
  } catch (e) {
    toast('Failed to save settings', 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Profile';
  }
}

// === Chat ===
function toggleChat() {
  chatOpen = !chatOpen;
  const panel = document.getElementById('chat-panel');
  const btn = document.getElementById('btn-chat-toggle');
  if (!chatOpen) panel.style.height = '';
  panel.classList.toggle('open', chatOpen);
  if (btn) btn.classList.toggle('active', chatOpen);
  if (chatOpen && !activeChatId) {
    renderChatThreadList();
  }
}

async function loadChatThreads() {
  if (!currentTranscript) { chatThreads = []; return; }
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/chats`);
    chatThreads = await res.json();
  } catch (e) {
    chatThreads = [];
  }
  if (chatOpen) renderChatThreadList();
}

function renderChatThreadList() {
  const listView = document.getElementById('chat-thread-list-view');
  const msgView = document.getElementById('chat-message-view');
  listView.style.display = '';
  msgView.style.display = 'none';
  activeChatId = null;

  const list = document.getElementById('chat-thread-list');
  if (chatThreads.length === 0) {
    list.innerHTML = '<div class="chat-empty">No chats yet. Start a new one!</div>';
    return;
  }
  list.innerHTML = chatThreads.map(t => {
    const date = new Date(t.created_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    return `
      <div class="chat-thread-item" onclick="selectChatThread('${t.id}')">
        <div class="chat-thread-info">
          <div class="chat-thread-title">${escapeHtml(t.title)}</div>
          <div class="chat-thread-meta">${date} &middot; ${t.message_count} messages</div>
        </div>
        <button class="chat-thread-delete" onclick="event.stopPropagation(); deleteChatThread('${t.id}')" title="Delete">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>
        </button>
      </div>
    `;
  }).join('');
}

async function selectChatThread(chatId) {
  if (!currentTranscript) return;
  activeChatId = chatId;

  // Fetch full thread data
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}`);
    const transcript = await res.json();
    const thread = (transcript.chat_threads || []).find(t => t.id === chatId);
    if (!thread) { toast('Chat not found', 'error'); return; }

    // Switch to message view
    document.getElementById('chat-thread-list-view').style.display = 'none';
    document.getElementById('chat-message-view').style.display = '';
    document.getElementById('chat-thread-title').textContent = thread.title;

    renderChatMessages(thread.messages);
    setTimeout(() => document.getElementById('chat-input')?.focus(), 100);
  } catch (e) {
    toast('Failed to load chat', 'error');
  }
}

function renderChatMessages(messages) {
  const container = document.getElementById('chat-messages');
  if (!messages || messages.length === 0) {
    container.innerHTML = '<div class="chat-empty">Ask a question about this transcript...</div>';
    return;
  }
  container.innerHTML = messages.map((msg, i) => {
    const cls = msg.role === 'user' ? 'chat-msg user' : 'chat-msg ai';
    return `<div class="${cls}"><span class="chat-msg-text">${escapeHtml(msg.content)}</span><button class="chat-msg-copy" onclick="event.stopPropagation(); copyChatMsg(this)" title="Copy"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button></div>`;
  }).join('');
  container.scrollTop = container.scrollHeight;
}

async function newChatThread() {
  if (!currentTranscript) return;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/chats`, { method: 'POST' });
    if (!res.ok) throw new Error('Failed to create chat');
    const thread = await res.json();
    chatThreads.unshift({ id: thread.id, title: thread.title, created_at: thread.created_at, message_count: 0 });
    selectChatThread(thread.id);
  } catch (e) {
    toast(e.message, 'error');
  }
}

async function deleteChatThread(chatId) {
  if (!currentTranscript) return;
  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/chats/${chatId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('Failed to delete chat');
    chatThreads = chatThreads.filter(t => t.id !== chatId);
    if (activeChatId === chatId) activeChatId = null;
    renderChatThreadList();
    toast('Chat deleted', 'success');
  } catch (e) {
    toast(e.message, 'error');
  }
}

function backToChatList() {
  activeChatId = null;
  loadChatThreads();
  renderChatThreadList();
}

async function copyChatMsg(btn) {
  const msgEl = btn.closest('.chat-msg');
  const textEl = msgEl.querySelector('.chat-msg-text');
  const text = textEl ? textEl.textContent : msgEl.textContent;
  try {
    await copyToClipboard(text);
    toast('Copied!', 'success');
    btn.classList.add('copied');
    setTimeout(() => btn.classList.remove('copied'), 1500);
  } catch (e) {
    toast('Failed to copy', 'error');
  }
}

async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const message = input?.value.trim();
  if (!message || !currentTranscript || !activeChatId) return;
  input.value = '';

  // Optimistically show user message
  const container = document.getElementById('chat-messages');
  const emptyEl = container.querySelector('.chat-empty');
  if (emptyEl) emptyEl.remove();
  const userEl = document.createElement('div');
  userEl.className = 'chat-msg user';
  userEl.innerHTML = `<span class="chat-msg-text">${escapeHtml(message)}</span><button class="chat-msg-copy" onclick="event.stopPropagation(); copyChatMsg(this)" title="Copy"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>`;
  container.appendChild(userEl);

  // Show typing indicator
  const typingEl = document.createElement('div');
  typingEl.className = 'chat-msg ai chat-typing';
  typingEl.textContent = 'Thinking...';
  container.appendChild(typingEl);
  container.scrollTop = container.scrollHeight;

  const sendBtn = document.getElementById('chat-send-btn');
  if (sendBtn) sendBtn.disabled = true;

  try {
    const res = await fetch(`/api/transcripts/${currentTranscript.id}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, chat_id: activeChatId }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || 'Chat failed');
    }
    const data = await res.json();

    typingEl.remove();
    const aiEl = document.createElement('div');
    aiEl.className = 'chat-msg ai';
    aiEl.innerHTML = `<span class="chat-msg-text">${escapeHtml(data.reply)}</span><button class="chat-msg-copy" onclick="event.stopPropagation(); copyChatMsg(this)" title="Copy"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>`;
    container.appendChild(aiEl);
    container.scrollTop = container.scrollHeight;

    // Update thread title in sidebar list if it was auto-titled
    const thread = chatThreads.find(t => t.id === activeChatId);
    if (thread && thread.title === 'New chat') {
      thread.title = message.substring(0, 50) + (message.length > 50 ? '...' : '');
      document.getElementById('chat-thread-title').textContent = thread.title;
    }
    if (thread) thread.message_count += 2;
  } catch (e) {
    typingEl.remove();
    userEl.remove();
    toast('Chat error: ' + e.message, 'error');
  } finally {
    if (sendBtn) sendBtn.disabled = false;
  }
}

async function saveFeatureSettings() {
  const chatEnabled = document.getElementById('settings-chat-enabled')?.checked ?? true;
  try {
    const res = await fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ features: { chat_enabled: chatEnabled } }),
    });
    if (!res.ok) throw new Error('Failed to save');
    userSettings = await res.json();
    toast('Settings saved', 'success');
  } catch (e) {
    toast('Failed to save settings', 'error');
  }
}

async function checkCostAlert() {
  const threshold = (userSettings.notifications || {}).cost_alert_threshold;
  if (!threshold || threshold <= 0) return;
  try {
    const res = await fetch('/api/stats');
    const stats = await res.json();
    if (stats.month_cost >= threshold) {
      toast(`Monthly cost ($${stats.month_cost.toFixed(2)}) has exceeded your alert threshold ($${threshold.toFixed(2)})`, 'error');
    }
  } catch (e) {
    // Non-critical
  }
}

// === Chat Panel Resize ===
(function setupChatResize() {
  let dragging = false;
  let startY = 0;
  let startH = 0;
  const MIN_H = 200;
  const MAX_H_RATIO = 0.85;

  function getHandle() { return document.getElementById('chat-resize-handle'); }
  function getPanel() { return document.getElementById('chat-panel'); }

  document.addEventListener('mousedown', (e) => {
    if (e.target === getHandle() || e.target.closest('#chat-resize-handle')) {
      e.preventDefault();
      const panel = getPanel();
      dragging = true;
      startY = e.clientY;
      startH = panel.offsetHeight;
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'ns-resize';
    }
  });

  document.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const panel = getPanel();
    const delta = startY - e.clientY;
    const maxH = window.innerHeight * MAX_H_RATIO;
    const newH = Math.min(maxH, Math.max(MIN_H, startH + delta));
    panel.style.height = newH + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (dragging) {
      dragging = false;
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
    }
  });

  // Touch support
  document.addEventListener('touchstart', (e) => {
    const touch = e.touches[0];
    if (e.target === getHandle() || e.target.closest('#chat-resize-handle')) {
      const panel = getPanel();
      dragging = true;
      startY = touch.clientY;
      startH = panel.offsetHeight;
    }
  }, { passive: true });

  document.addEventListener('touchmove', (e) => {
    if (!dragging) return;
    e.preventDefault();
    const touch = e.touches[0];
    const panel = getPanel();
    const delta = startY - touch.clientY;
    const maxH = window.innerHeight * MAX_H_RATIO;
    const newH = Math.min(maxH, Math.max(MIN_H, startH + delta));
    panel.style.height = newH + 'px';
  }, { passive: false });

  document.addEventListener('touchend', () => { dragging = false; });
})();

// === Cost Tracker ===
async function showCosts() {
  await toggleCostsSection(true);
}

async function toggleCostsSection(forceOpen) {
  const section = document.getElementById('costs-collapsible');
  const chevron = document.getElementById('costs-chevron');
  const isOpen = section.style.display !== 'none';
  if (isOpen && !forceOpen) {
    section.style.display = 'none';
    chevron.style.transform = '';
    return;
  }
  section.style.display = '';
  chevron.style.transform = 'rotate(180deg)';

  try {
    const [statsRes, perFileRes] = await Promise.all([
      fetch('/api/stats'),
      fetch('/api/stats/per-file'),
    ]);
    const stats = await statsRes.json();
    const perFile = await perFileRes.json();

    document.getElementById('stat-month-files').textContent = stats.month_files;
    document.getElementById('stat-month-min').textContent = stats.month_minutes;
    document.getElementById('stat-month-cost').textContent = stats.month_cost.toFixed(2);
    document.getElementById('stat-month-deepgram').textContent = stats.month_deepgram_cost.toFixed(4);
    document.getElementById('stat-month-gpt').textContent = stats.month_gpt_cost.toFixed(4);
    document.getElementById('stat-total-files').textContent = stats.total_files;
    document.getElementById('stat-total-min').textContent = stats.total_minutes;
    document.getElementById('stat-total-cost').textContent = stats.total_cost.toFixed(2);
    document.getElementById('stat-total-deepgram').textContent = stats.total_deepgram_cost.toFixed(4);
    document.getElementById('stat-total-gpt').textContent = stats.total_gpt_cost.toFixed(4);
    document.getElementById('stat-credit').textContent = stats.credit_remaining.toFixed(2);

    const pct = (stats.credit_remaining / 200) * 100;
    const bar = document.getElementById('credit-bar');
    bar.style.width = pct + '%';
    bar.style.background = pct > 30 ? 'var(--success)' : pct > 10 ? 'var(--warning)' : 'var(--danger)';

    const estimate = document.getElementById('credit-estimate');
    if (stats.months_remaining !== null) {
      estimate.textContent = `At current usage, credit lasts ~${stats.months_remaining} more months`;
    } else {
      estimate.textContent = '';
    }

    const videoStorage = document.getElementById('stat-video-storage');
    if (videoStorage) {
      const mb = stats.video_storage_mb || 0;
      videoStorage.textContent = mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : mb + ' MB';
    }

    const table = document.getElementById('per-file-table');
    if (perFile.length === 0) {
      table.innerHTML = '<p class="empty-state">No transcriptions yet.</p>';
    } else {
      table.innerHTML = perFile.map(f => {
        const date = new Date(f.created_at).toLocaleDateString();
        const kimiStr = f.gpt_cost > 0 ? ` + $${f.gpt_cost.toFixed(4)} AI` : '';
        return `
          <div class="per-file-row">
            <span class="pf-name">${escapeHtml(f.filename)}</span>
            <span class="pf-meta">
              <span>${date}</span>
              <span>${f.duration_minutes} min</span>
              <span>~$${f.estimated_cost.toFixed(4)}${kimiStr}</span>
            </span>
          </div>
        `;
      }).join('');
    }
  } catch (e) {
    toast('Failed to load costs', 'error');
  }
}

// === Navigation ===
function switchView(viewId) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById(viewId).classList.add('active');
  localStorage.setItem('lastView', viewId);
  window.scrollTo(0, 0);
}

function showHome() {
  cleanupRecording();
  stopAudioPlayer();
  clearTranscriptSearch();
  closeCommentPopover();
  commentsSidebarOpen = false;
  const cSidebar = document.getElementById('comments-sidebar');
  if (cSidebar) cSidebar.classList.remove('open');
  chatOpen = false;
  chatThreads = [];
  activeChatId = null;
  const chatPanel = document.getElementById('chat-panel');
  if (chatPanel) chatPanel.classList.remove('open');
  currentTranscript = null;
  localStorage.removeItem('lastTranscriptId');
  switchView('view-home');
  loadTranscripts();
}

// === Toast ===
function toast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = message;
  container.appendChild(el);
  setTimeout(() => {
    el.style.opacity = '0';
    el.style.transition = 'opacity 0.3s';
    setTimeout(() => el.remove(), 300);
  }, 3000);
}

// === Global Search ===
function onGlobalSearch(query) {
  clearTimeout(globalSearchTimeout);
  const resultsEl = document.getElementById('search-results');
  const clearBtn = document.getElementById('global-search-clear');
  const uploadZone = document.getElementById('upload-zone');
  const listEl = document.getElementById('transcript-list');

  if (!query.trim()) {
    clearGlobalSearch();
    return;
  }

  clearBtn.style.display = '';

  globalSearchTimeout = setTimeout(async () => {
    try {
      const res = await fetch(`/api/search?q=${encodeURIComponent(query.trim())}`);
      const results = await res.json();

      if (results.length === 0) {
        resultsEl.innerHTML = '<p class="empty-state">No matches found.</p>';
        resultsEl.style.display = 'block';
        uploadZone.style.display = 'none';
        listEl.style.display = 'none';
        return;
      }

      uploadZone.style.display = 'none';
      listEl.style.display = 'none';
      resultsEl.style.display = 'block';

      resultsEl.innerHTML = `<h2 class="section-title">${results.length} transcript${results.length !== 1 ? 's' : ''} matched</h2>` +
        results.map(r => {
          const date = new Date(r.created_at).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
          const dur = formatDuration(r.duration_seconds);
          // Show first few snippets with highlighting
          const snippetsHtml = r.snippets.slice(0, 3).map(s => {
            const highlighted = highlightText(s.text, query.trim());
            return `<div class="sr-snippet">${highlighted}</div>`;
          }).join('');
          return `
            <div class="search-result-card" onclick="openTranscript('${r.id}')">
              <div class="sr-filename">${escapeHtml(r.filename)}</div>
              <div class="sr-meta">${date} &middot; ${dur} &middot; ${r.num_speakers} speaker${r.num_speakers !== 1 ? 's' : ''}</div>
              ${snippetsHtml}
            </div>
          `;
        }).join('');
    } catch (e) {
      // Silently fail
    }
  }, 300);
}

function clearGlobalSearch() {
  document.getElementById('global-search').value = '';
  document.getElementById('global-search-clear').style.display = 'none';
  document.getElementById('search-results').style.display = 'none';
  document.getElementById('upload-zone').style.display = '';
  document.getElementById('transcript-list').style.display = '';
}

function highlightText(text, query) {
  const escaped = escapeHtml(text);
  const queryEscaped = escapeHtml(query);
  const regex = new RegExp(`(${queryEscaped.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
  return escaped.replace(regex, '<mark>$1</mark>');
}

// === In-Transcript Search ===
function onTranscriptSearch(query) {
  const nav = document.getElementById('transcript-search-nav');
  const clearBtn = document.getElementById('transcript-search-clear');

  // Reset highlights
  document.querySelectorAll('.utterance .u-text').forEach(el => {
    // Restore original text (strip highlights)
    el.innerHTML = el.textContent;
  });
  transcriptSearchMatches = [];
  currentMatchIndex = -1;

  if (!query.trim()) {
    nav.style.display = 'none';
    clearBtn.style.display = 'none';
    return;
  }

  clearBtn.style.display = '';
  const q = query.trim().toLowerCase();

  // Find and highlight matching utterances
  document.querySelectorAll('.utterance').forEach(el => {
    const textEl = el.querySelector('.u-text');
    const text = textEl.textContent;
    if (text.toLowerCase().includes(q)) {
      transcriptSearchMatches.push(el);
      // Highlight matches in text
      const regex = new RegExp(`(${query.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
      textEl.innerHTML = escapeHtml(text).replace(regex, '<mark class="highlight">$1</mark>');
    }
  });

  // Show nav
  if (transcriptSearchMatches.length > 0) {
    currentMatchIndex = 0;
    nav.style.display = 'flex';
    updateMatchCounter();
    scrollToMatch();
  } else {
    nav.style.display = 'flex';
    document.getElementById('match-counter').textContent = '0 matches';
  }
}

function nextMatch() {
  if (transcriptSearchMatches.length === 0) return;
  currentMatchIndex = (currentMatchIndex + 1) % transcriptSearchMatches.length;
  updateMatchCounter();
  scrollToMatch();
}

function prevMatch() {
  if (transcriptSearchMatches.length === 0) return;
  currentMatchIndex = (currentMatchIndex - 1 + transcriptSearchMatches.length) % transcriptSearchMatches.length;
  updateMatchCounter();
  scrollToMatch();
}

function updateMatchCounter() {
  const counter = document.getElementById('match-counter');
  if (transcriptSearchMatches.length === 0) {
    counter.textContent = '0 matches';
  } else {
    counter.textContent = `${currentMatchIndex + 1} of ${transcriptSearchMatches.length}`;
  }
}

function scrollToMatch() {
  // Remove current highlight from all
  transcriptSearchMatches.forEach(el => el.classList.remove('search-active'));

  if (currentMatchIndex >= 0 && currentMatchIndex < transcriptSearchMatches.length) {
    const el = transcriptSearchMatches[currentMatchIndex];
    el.classList.add('search-active');
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function clearTranscriptSearch() {
  const input = document.getElementById('transcript-search');
  if (input) input.value = '';
  const nav = document.getElementById('transcript-search-nav');
  if (nav) nav.style.display = 'none';
  const clearBtn = document.getElementById('transcript-search-clear');
  if (clearBtn) clearBtn.style.display = 'none';
  transcriptSearchMatches = [];
  currentMatchIndex = -1;
  // Restore original text
  document.querySelectorAll('.utterance .u-text').forEach(el => {
    el.innerHTML = el.textContent;
  });
  document.querySelectorAll('.utterance.search-active').forEach(el => {
    el.classList.remove('search-active');
  });
}

// === Audio Player ===
let usingVideo = false;

function getMediaElement() {
  return usingVideo
    ? document.getElementById('video-player')
    : document.getElementById('audio-player');
}

function setupAudioPlayer(transcript) {
  const section = document.getElementById('audio-player-section');
  const audio = document.getElementById('audio-player');
  const video = document.getElementById('video-player');
  const barContainer = document.getElementById('player-bar-container');

  // Stop any previous playback
  stopAudioPlayer();

  if (!transcript.audio_file && !transcript.video_file) {
    section.style.display = 'none';
    return;
  }

  // Decide: video or audio player
  usingVideo = !!transcript.video_file;
  if (usingVideo) {
    video.src = `/api/transcripts/${transcript.id}/video`;
    video.load();
    video.style.display = 'block';
    audio.style.display = 'none';
  } else {
    audio.src = `/api/transcripts/${transcript.id}/audio`;
    audio.load();
    audio.style.display = 'none'; // hidden element, plays in background
    video.style.display = 'none';
  }

  const media = getMediaElement();
  section.style.display = 'block';
  section.classList.remove('collapsed');
  autoScrollEnabled = true;

  // Position player below action bar
  const actionBar = document.querySelector('.action-bar');
  if (actionBar) {
    section.style.top = actionBar.offsetHeight + 'px';
  }

  // Set playback speed from settings
  const defaultSpeed = (userSettings.display || {}).default_playback_speed || 1;
  const speedIdx = SPEED_OPTIONS.indexOf(defaultSpeed);
  currentSpeedIndex = speedIdx >= 0 ? speedIdx : 0;
  media.playbackRate = SPEED_OPTIONS[currentSpeedIndex];
  document.getElementById('speed-btn').textContent = SPEED_OPTIONS[currentSpeedIndex] + 'x';

  // Update total time when metadata loads
  media.addEventListener('loadedmetadata', function onMeta() {
    document.getElementById('player-total').textContent = formatTimestamp(media.duration);
    media.removeEventListener('loadedmetadata', onMeta);
  });

  // Play/pause state
  media.addEventListener('play', () => {
    document.getElementById('play-icon').style.display = 'none';
    document.getElementById('pause-icon').style.display = '';
    startSyncLoop();
  });

  media.addEventListener('pause', () => {
    document.getElementById('play-icon').style.display = '';
    document.getElementById('pause-icon').style.display = 'none';
    stopSyncLoop();
  });

  media.addEventListener('ended', () => {
    document.getElementById('play-icon').style.display = '';
    document.getElementById('pause-icon').style.display = 'none';
    stopSyncLoop();
    clearActiveUtterance();
  });

  // Seek bar click/touch
  const seekHandler = (e) => {
    const rect = barContainer.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    if (media.duration) {
      media.currentTime = pct * media.duration;
      updatePlayerUI();
    }
  };

  barContainer.addEventListener('click', seekHandler);

  // Mini progress bar seek
  const miniBar = document.querySelector('.player-mini-progress');
  if (miniBar) {
    const miniSeek = (e) => {
      const rect = miniBar.getBoundingClientRect();
      const clientX = e.touches ? e.touches[0].clientX : e.clientX;
      const pct = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      if (media.duration) {
        media.currentTime = pct * media.duration;
        updatePlayerUI();
      }
    };
    miniBar.addEventListener('click', miniSeek);
    let miniDragging = false;
    miniBar.addEventListener('touchstart', (e) => { miniDragging = true; miniSeek(e); }, { passive: true });
    document.addEventListener('touchmove', (e) => { if (miniDragging) miniSeek(e); }, { passive: true });
    document.addEventListener('touchend', () => { miniDragging = false; });
  }

  // Touch drag on seek bar
  let dragging = false;
  barContainer.addEventListener('touchstart', (e) => {
    dragging = true;
    seekHandler(e);
  }, { passive: true });
  document.addEventListener('touchmove', (e) => {
    if (dragging) seekHandler(e);
  }, { passive: true });
  document.addEventListener('touchend', () => { dragging = false; });
}

function stopAudioPlayer() {
  for (const el of [document.getElementById('audio-player'), document.getElementById('video-player')]) {
    if (el) {
      el.pause();
      el.removeAttribute('src');
      el.load();
    }
  }
  const video = document.getElementById('video-player');
  if (video) video.style.display = 'none';
  stopSyncLoop();
  clearActiveUtterance();
  document.getElementById('play-icon').style.display = '';
  document.getElementById('pause-icon').style.display = 'none';
  document.getElementById('player-bar-fill').style.width = '0%';
  document.getElementById('player-current').textContent = '0:00';
  document.getElementById('player-total').textContent = '0:00';
}

const SPEED_OPTIONS = [1, 1.25, 1.5, 1.75, 2, 3];
let currentSpeedIndex = 0;

function cycleSpeed() {
  currentSpeedIndex = (currentSpeedIndex + 1) % SPEED_OPTIONS.length;
  const speed = SPEED_OPTIONS[currentSpeedIndex];
  const media = getMediaElement();
  media.playbackRate = speed;
  document.getElementById('speed-btn').textContent = speed + 'x';
}

function setVolume(val) {
  const v = parseFloat(val);
  const media = getMediaElement();
  media.volume = v;
  media.muted = v === 0;
  updateVolumeIcon(v);
}

function toggleMute() {
  const media = getMediaElement();
  media.muted = !media.muted;
  const slider = document.getElementById('volume-slider');
  if (media.muted) {
    updateVolumeIcon(0);
  } else {
    updateVolumeIcon(media.volume);
    if (slider) slider.value = media.volume;
  }
  // Toggle slider popup
  const control = document.getElementById('volume-control');
  control.classList.toggle('open');
}

function updateVolumeIcon(vol) {
  const on = document.getElementById('volume-icon-on');
  const off = document.getElementById('volume-icon-off');
  if (vol === 0) {
    on.style.display = 'none';
    off.style.display = '';
  } else {
    on.style.display = '';
    off.style.display = 'none';
  }
}

// Close volume popup when clicking outside
document.addEventListener('click', (e) => {
  const control = document.getElementById('volume-control');
  if (control && !control.contains(e.target)) {
    control.classList.remove('open');
  }
});

function togglePlayback() {
  const media = getMediaElement();
  if (!media.src || media.src === window.location.href) return;
  if (media.paused) {
    const p = media.play();
    if (p) p.catch(() => {});
  } else {
    media.pause();
  }
}

function seekToUtterance(startTime) {
  const media = getMediaElement();
  if (!media.src || media.src === window.location.href) return;

  // On iOS, if media hasn't loaded yet, wait for it
  if (media.readyState < 1) {
    media.addEventListener('loadedmetadata', function onReady() {
      media.removeEventListener('loadedmetadata', onReady);
      media.currentTime = startTime;
    });
    media.load();
    return;
  }

  media.currentTime = startTime;
  autoScrollEnabled = true;
  if (media.paused) {
    const p = media.play();
    if (p) p.catch(() => {});
  }
  updatePlayerUI();
}

function startSyncLoop() {
  stopSyncLoop();
  const tick = () => {
    updatePlayerUI();
    highlightCurrentUtterance();
    audioSyncRAF = requestAnimationFrame(tick);
  };
  audioSyncRAF = requestAnimationFrame(tick);
}

function stopSyncLoop() {
  if (audioSyncRAF) {
    cancelAnimationFrame(audioSyncRAF);
    audioSyncRAF = null;
  }
}

function updatePlayerUI() {
  const media = getMediaElement();
  if (!media.duration) return;
  const pct = (media.currentTime / media.duration) * 100;
  document.getElementById('player-bar-fill').style.width = pct + '%';
  document.getElementById('player-current').textContent = formatTimestamp(media.currentTime);
  // Sync mini progress bar
  const miniFill = document.getElementById('player-mini-progress-fill');
  if (miniFill) miniFill.style.width = pct + '%';
}

let playerManualExpand = false;
function togglePlayerCollapse() {
  const section = document.getElementById('audio-player-section');
  const isCollapsed = section.classList.contains('collapsed');
  section.classList.toggle('collapsed');
  // If user manually expands, prevent auto-collapse for a while
  if (isCollapsed) {
    playerManualExpand = true;
    setTimeout(() => { playerManualExpand = false; }, 5000);
  }
}

function highlightCurrentUtterance() {
  const media = getMediaElement();
  const time = media.currentTime;
  const utteranceEls = document.querySelectorAll('.utterance[data-start]');
  let activeEl = null;

  // Find the utterance that contains the current time
  for (const el of utteranceEls) {
    const start = parseFloat(el.dataset.start);
    const end = parseFloat(el.dataset.end);
    if (time >= start && time < end) {
      activeEl = el;
      break;
    }
  }

  // If between utterances, highlight the last one before current time
  if (!activeEl) {
    for (const el of utteranceEls) {
      const start = parseFloat(el.dataset.start);
      if (start <= time) {
        activeEl = el;
      } else {
        break;
      }
    }
  }

  // Update active class
  const prev = document.querySelector('.utterance.active');
  if (prev && prev !== activeEl) {
    prev.classList.remove('active');
    // Clear word highlights on the previous utterance
    const prevWord = prev.querySelector('.word-active');
    if (prevWord) prevWord.classList.remove('word-active');
  }
  if (activeEl && !activeEl.classList.contains('active')) {
    activeEl.classList.add('active');
    // Auto-scroll to keep active utterance visible
    if (autoScrollEnabled) {
      scrollToUtterance(activeEl);
    }
  }

  // Word-level highlight within active utterance
  if (activeEl) {
    const wordSpans = activeEl.querySelectorAll('.word');
    if (wordSpans.length > 0) {
      let activeWord = null;
      for (const ws of wordSpans) {
        const wStart = parseFloat(ws.dataset.start);
        const wEnd = parseFloat(ws.dataset.end);
        if (time >= wStart && time < wEnd) {
          activeWord = ws;
          break;
        }
      }
      const prevWord = activeEl.querySelector('.word-active');
      if (prevWord && prevWord !== activeWord) {
        prevWord.classList.remove('word-active');
      }
      if (activeWord && !activeWord.classList.contains('word-active')) {
        activeWord.classList.add('word-active');
      }
    }
  }
}

function scrollToUtterance(el) {
  const rect = el.getBoundingClientRect();
  const viewH = window.innerHeight;
  // Only scroll if the element is outside the middle third of the viewport
  if (rect.top < viewH * 0.25 || rect.bottom > viewH * 0.75) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function clearActiveUtterance() {
  const prev = document.querySelector('.utterance.active');
  if (prev) prev.classList.remove('active');
}

// Disable auto-scroll when user manually scrolls + auto-collapse player
let scrollTimeout = null;
let lastScrollY = 0;
window.addEventListener('scroll', () => {
  // If media is playing, briefly disable auto-scroll on manual scroll
  const media = getMediaElement();
  if (media && !media.paused) {
    autoScrollEnabled = false;
    clearTimeout(scrollTimeout);
    scrollTimeout = setTimeout(() => {
      autoScrollEnabled = true;
    }, 3000);
  }

  // Auto-collapse/expand player on scroll
  const section = document.getElementById('audio-player-section');
  if (section && section.style.display !== 'none' && !playerManualExpand) {
    const scrollY = window.scrollY;
    const collapseThreshold = window.innerWidth <= 600 ? 100 : 150;
    if (scrollY > collapseThreshold && scrollY > lastScrollY) {
      section.classList.add('collapsed');
    } else if (scrollY < 80) {
      section.classList.remove('collapsed');
    }
    lastScrollY = scrollY;
  }

  // Dynamically position player below action bar
  if (section && section.style.display !== 'none') {
    const actionBar = document.querySelector('.action-bar');
    if (actionBar) {
      const abHeight = actionBar.offsetHeight;
      section.style.top = abHeight + 'px';
    }
  }
}, { passive: true });

// === Recording ===

function getRecordingMimeType() {
  if (typeof MediaRecorder === 'undefined') return null;
  if (MediaRecorder.isTypeSupported('audio/mp4')) {
    return { mimeType: 'audio/mp4', extension: '.m4a' };
  }
  if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
    return { mimeType: 'audio/webm;codecs=opus', extension: '.webm' };
  }
  if (MediaRecorder.isTypeSupported('audio/webm')) {
    return { mimeType: 'audio/webm', extension: '.webm' };
  }
  return null;
}

async function startRecording() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    toast('Your browser does not support audio recording.', 'error');
    return;
  }

  const format = getRecordingMimeType();
  if (!format) {
    toast('Your browser does not support audio recording formats.', 'error');
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
      toast('Microphone permission denied. Please allow access and try again.', 'error');
    } else {
      toast('Could not access microphone: ' + err.message, 'error');
    }
    return;
  }

  recordingMimeType = format.mimeType;
  recordingFileExtension = format.extension;
  recordedChunks = [];
  recordingBackupCounter = 0;
  recordingSessionId = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);

  try {
    mediaRecorder = new MediaRecorder(stream, { mimeType: format.mimeType });
  } catch (err) {
    toast('Failed to start recorder: ' + err.message, 'error');
    stream.getTracks().forEach(t => t.stop());
    return;
  }

  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) {
      recordedChunks.push(e.data);
    }
  };

  mediaRecorder.onerror = () => {
    toast('Recording error occurred.', 'error');
    cleanupRecording();
  };

  // Store stream reference for cleanup
  mediaRecorder._stream = stream;

  // Start with 5-second timeslice
  mediaRecorder.start(5000);
  recordingStartTime = Date.now();

  // Show recording UI, hide upload zone
  document.getElementById('upload-zone').style.display = 'none';
  document.getElementById('recording-ui').style.display = 'block';
  document.getElementById('recording-backup-status').textContent = '';
  document.getElementById('recording-timer').textContent = '00:00';

  // Timer updates every second
  updateRecordingTimer();
  recordingTimerInterval = setInterval(updateRecordingTimer, 1000);

  // Backup every 30 seconds
  recordingBackupInterval = setInterval(saveRecordingBackup, 30000);
}

function updateRecordingTimer() {
  if (!recordingStartTime) return;
  const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
  const mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
  const ss = String(elapsed % 60).padStart(2, '0');
  document.getElementById('recording-timer').textContent = mm + ':' + ss;
}

function saveRecordingBackup() {
  if (recordedChunks.length === 0) return;

  recordingBackupCounter++;
  const blob = new Blob(recordedChunks, { type: recordingMimeType });
  const filename = 'rec-' + recordingSessionId + '-backup-' + recordingBackupCounter + recordingFileExtension;

  downloadFile(blob, filename, recordingMimeType);

  // Update backup status text
  const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
  const mm = Math.floor(elapsed / 60);
  const ss = elapsed % 60;
  document.getElementById('recording-backup-status').textContent =
    'Last backup saved: ' + mm + ':' + String(ss).padStart(2, '0');
}

async function stopRecording() {
  if (!mediaRecorder || mediaRecorder.state === 'inactive') return;

  // Clear timers
  clearInterval(recordingTimerInterval);
  clearInterval(recordingBackupInterval);
  recordingTimerInterval = null;
  recordingBackupInterval = null;

  // Wait for final data
  const stopped = new Promise((resolve) => {
    mediaRecorder.addEventListener('stop', resolve, { once: true });
  });

  mediaRecorder.requestData();
  mediaRecorder.stop();
  await stopped;

  // Release microphone
  if (mediaRecorder._stream) {
    mediaRecorder._stream.getTracks().forEach(t => t.stop());
  }

  // Build final blob
  const blob = new Blob(recordedChunks, { type: recordingMimeType });

  if (blob.size === 0) {
    toast('No audio was recorded.', 'error');
    cleanupRecording();
    return;
  }

  // Save final file locally
  const finalFilename = 'rec-' + recordingSessionId + '-final' + recordingFileExtension;
  downloadFile(blob, finalFilename, recordingMimeType);

  // Hide recording UI
  document.getElementById('recording-ui').style.display = 'none';

  // Create File for upload
  const serverFilename = 'rec-' + recordingSessionId + recordingFileExtension;
  const file = new File([blob], serverFilename, { type: recordingMimeType });

  // Set pending session so uploadFile auto-downloads transcript
  pendingRecordingSessionId = recordingSessionId;

  // Reset state
  recordedChunks = [];
  mediaRecorder = null;
  recordingStartTime = null;
  recordingBackupCounter = 0;
  recordingSessionId = '';

  // Upload to server via existing flow
  uploadFile(file);
}

function cleanupRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    try { mediaRecorder.stop(); } catch (e) { /* ignore */ }
  }
  if (mediaRecorder && mediaRecorder._stream) {
    mediaRecorder._stream.getTracks().forEach(t => t.stop());
  }

  clearInterval(recordingTimerInterval);
  clearInterval(recordingBackupInterval);
  recordingTimerInterval = null;
  recordingBackupInterval = null;

  recordedChunks = [];
  mediaRecorder = null;
  recordingStartTime = null;
  recordingBackupCounter = 0;

  document.getElementById('recording-ui').style.display = 'none';
  document.getElementById('upload-zone').style.display = '';
}

// === Helpers ===
function formatDuration(seconds) {
  if (!seconds) return '0s';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function formatTimestamp(seconds) {
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function toSrtTime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.round((seconds % 1) * 1000);
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms).padStart(3, '0')}`;
}

function escapeHtml(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function escapeAttr(str) {
  return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}
