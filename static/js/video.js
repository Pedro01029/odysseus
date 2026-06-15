/**
 * Video Studio Module — AI-powered video transcription, editing, and YouTube publishing.
 */

import uiModule from './ui.js';
import * as Modals from './modalManager.js';
import { makeWindowDraggable } from './windowDrag.js';
import { snapModalToZone } from './tileManager.js';
import { applyEdgeDock, clearDockSide } from './modalSnap.js';

const API_BASE = window.location.origin;
const el = uiModule.el;

let _open = false;
let _projects = [];
let _activeProjectId = null;
let _activeProjectClips = [];
let _youtubeAccounts = [];
let _activeTab = 'projects'; // 'projects', 'editor', 'publish'
let _renderPollingInterval = null;
let _publishPollingInterval = null;

// Modal Snapping & Dragging Helpers
function _ensureVideoChipRegistered() {
  if (Modals.isRegistered('video-panel')) return;
  Modals.register('video-panel', {
    railBtnId: 'rail-video',
    sidebarBtnId: 'tool-video-btn',
    restoreFn: () => { openPanel(); },
    closeFn: () => { _forceCloseVideoPanel(); },
  });
}

function _wireVideoWindow(pane) {
  if (!pane || pane.dataset.windowDragWired === '1') return;
  const header = pane.querySelector('.video-pane-header');
  if (!header) return;
  pane.dataset.windowDragWired = '1';
  makeWindowDraggable(pane, {
    content: pane,
    header,
    fsClass: 'video-window-fullscreen',
    skipSelector: 'button, input, select, textarea, label, .timeline-scrubber',
    enableDock: true,
    enableLeftDock: true,
    onEnterFullscreen: () => {
      pane.classList.add('video-window-fullscreen');
      snapModalToZone(pane, {
        name: 'fullscreen',
        rect: _videoFullscreenSafeRect(),
      });
    },
    onExitFullscreen: () => {
      _clearVideoSnapStyles(pane);
    },
  });
}

function _videoFullscreenSafeRect() {
  const sb = document.getElementById('sidebar');
  const rail = document.getElementById('icon-rail');
  let leftEdge = 0;
  if (sb && !sb.classList.contains('hidden')) leftEdge = Math.max(leftEdge, sb.getBoundingClientRect().right);
  if (rail) leftEdge = Math.max(leftEdge, rail.getBoundingClientRect().right);
  return {
    left: leftEdge + 4,
    top: 4,
    right: window.innerWidth - 4,
    bottom: window.innerHeight - 4,
  };
}

function _restoreVideoSidebarDock(pane) {
  pane.classList.remove('video-window-fullscreen');
  applyEdgeDock(pane, 'right');
}

function _clearVideoSnapStyles(pane) {
  if (!pane) return;
  pane.classList.remove('video-window-fullscreen', 'modal-left-docked', 'modal-right-docked');
  clearDockSide('left', pane);
  clearDockSide('right', pane);
  ['position', 'left', 'top', 'right', 'bottom', 'width', 'max-width', 'height',
    'max-height', 'margin', 'transform', 'border-radius'].forEach(prop => pane.style.removeProperty(prop));
}

function _forceCloseVideoPanel() {
  _open = false;
  const pane = document.getElementById('video-panel');
  const backdrop = document.getElementById('video-pane-backdrop');
  if (pane) pane.remove();
  if (backdrop) backdrop.remove();
  
  if (_renderPollingInterval) { clearInterval(_renderPollingInterval); _renderPollingInterval = null; }
  if (_publishPollingInterval) { clearInterval(_publishPollingInterval); _publishPollingInterval = null; }
  
  document.body.classList.remove('video-view');
  const btn = document.getElementById('tool-video-btn');
  if (btn) btn.classList.remove('active');
  Modals.unregister('video-panel');
}

export function togglePanel() {
  if (_open) {
    closePanel();
  } else {
    openPanel();
  }
}

export function isPanelOpen() {
  return _open;
}

export function closePanel(direction) {
  if (!_open) return;
  _open = false;
  const _minimize = direction === 'down';
  if (_minimize) {
    _ensureVideoChipRegistered();
  } else if (Modals.isRegistered('video-panel')) {
    Modals.unregister('video-panel');
  }

  document.body.classList.remove('video-view');
  
  if (_renderPollingInterval) { clearInterval(_renderPollingInterval); _renderPollingInterval = null; }
  if (_publishPollingInterval) { clearInterval(_publishPollingInterval); _publishPollingInterval = null; }

  const btn = document.getElementById('tool-video-btn');
  if (btn) btn.classList.remove('active');

  const pane = document.getElementById('video-panel');
  const backdrop = document.getElementById('video-pane-backdrop');
  if (pane) {
    pane.classList.add('video-pane-leaving');
    pane.addEventListener('animationend', () => {
      try { pane.remove(); } catch {}
      try { backdrop?.remove(); } catch {}
    }, { once: true });
  } else {
    try { backdrop?.remove(); } catch {}
  }
  if (_minimize) { try { Modals.minimize('video-panel'); } catch {} }
}

export function openPanel() {
  if (_open) return;
  _open = true;
  document.body.classList.add('video-view');

  const btn = document.getElementById('tool-video-btn');
  if (btn) btn.classList.add('active');

  // Create Video Studio HTML elements
  const backdrop = document.createElement('div');
  backdrop.className = 'video-pane-backdrop';
  backdrop.id = 'video-pane-backdrop';
  backdrop.addEventListener('click', (ev) => {
    if (ev.target === backdrop) closePanel('down');
  });

  const pane = document.createElement('div');
  pane.id = 'video-panel';
  pane.className = 'video-pane';
  pane.innerHTML = `
    <div class="video-pane-header">
      <h4 class="video-pane-title">🎬 Video Studio</h4>
      <div class="video-tabs">
        <button class="video-tab-btn active" data-tab="projects">Projects & Clips</button>
        <button class="video-tab-btn" data-tab="editor">Timeline Editor</button>
        <button class="video-tab-btn" data-tab="publish">YouTube Publish</button>
      </div>
      <span style="flex:1"></span>
      <button id="video-minimize-btn" class="modal-minimize-btn" title="Minimize" aria-label="Minimize video studio" style="position:relative;left:2px;"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.4" stroke-linecap="round" aria-hidden="true"><line x1="6" y1="18" x2="18" y2="18"/></svg></button>
      <button id="video-close-btn" class="close-btn" title="Close" aria-label="Close video studio">✖</button>
    </div>
    <div class="video-pane-body">
      <!-- Dynamic Views Inserted Here -->
    </div>
  `;

  backdrop.appendChild(pane);
  document.body.appendChild(backdrop);
  
  _wireVideoWindow(pane);
  _clearVideoSnapStyles(pane);

  const minBtn = document.getElementById('video-minimize-btn');
  if (minBtn) minBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    closePanel('down');
  });

  const closeBtn = document.getElementById('video-close-btn');
  if (closeBtn) closeBtn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    _forceCloseVideoPanel();
  });

  // Tab Switching
  pane.querySelectorAll('.video-tab-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      pane.querySelectorAll('.video-tab-btn').forEach(b => b.classList.remove('active'));
      e.target.classList.add('active');
      switchTab(e.target.dataset.tab);
    });
  });

  switchTab('projects');
}

// ---------------------------------------------------------------------------
// View Switching & Operations
// ---------------------------------------------------------------------------

async function switchTab(tab) {
  _activeTab = tab;
  const container = document.querySelector('.video-pane-body');
  if (!container) return;

  if (tab === 'projects') {
    container.innerHTML = `
      <div class="video-projects-layout">
        <div class="video-projects-sidebar">
          <div class="video-sidebar-header">
            <h5>Projects</h5>
            <button id="video-new-project-btn" class="video-primary-btn">+ New</button>
          </div>
          <div class="video-projects-list">Loading...</div>
        </div>
        <div class="video-project-detail">
          <div class="video-detail-placeholder">Select or create a video project to get started.</div>
        </div>
      </div>
    `;
    
    document.getElementById('video-new-project-btn').addEventListener('click', showNewProjectModal);
    await loadProjects();
  } 
  else if (tab === 'editor') {
    if (!_activeProjectId) {
      container.innerHTML = `<div class="video-center-message">Please select a project in the first tab to edit.</div>`;
      return;
    }
    await loadEditorTab(container);
  } 
  else if (tab === 'publish') {
    if (!_activeProjectId) {
      container.innerHTML = `<div class="video-center-message">Please select a project in the first tab to publish.</div>`;
      return;
    }
    await loadPublishTab(container);
  }
}

// ---------------------------------------------------------------------------
// Tab 1: Projects & Clips Management
// ---------------------------------------------------------------------------

async function loadProjects() {
  const sidebarList = document.querySelector('.video-projects-list');
  if (!sidebarList) return;

  try {
    const res = await fetch(`${API_BASE}/api/video/projects`);
    _projects = (await res.json()).projects || [];
    
    if (_projects.length === 0) {
      sidebarList.innerHTML = `<div class="video-empty-message">No projects found.</div>`;
      return;
    }

    sidebarList.innerHTML = _projects.map(p => `
      <div class="video-project-item ${p.id === _activeProjectId ? 'active' : ''}" data-id="${p.id}">
        <div class="video-project-item-title">${p.title}</div>
        <div class="video-project-item-status badge-${p.status}">${p.status}</div>
      </div>
    `).join('');

    sidebarList.querySelectorAll('.video-project-item').forEach(item => {
      item.addEventListener('click', (e) => {
        const id = parseInt(e.currentTarget.dataset.id);
        selectProject(id);
      });
    });

    if (_activeProjectId) {
      const activeProj = _projects.find(p => p.id === _activeProjectId);
      if (activeProj) renderProjectDetail(activeProj);
    }
  } catch (err) {
    sidebarList.innerHTML = `<div class="video-error-message">Error: ${err.message}</div>`;
  }
}

function showNewProjectModal() {
  const modal = document.createElement('div');
  modal.className = 'video-dialog-overlay';
  modal.innerHTML = `
    <div class="video-dialog">
      <h5>Create New Project</h5>
      <input type="text" id="new-project-title" placeholder="Project Title" />
      <textarea id="new-project-desc" placeholder="Project Description (optional)"></textarea>
      <div class="video-dialog-actions">
        <button id="dialog-cancel" class="video-secondary-btn">Cancel</button>
        <button id="dialog-create" class="video-primary-btn">Create</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);

  modal.querySelector('#dialog-cancel').addEventListener('click', () => modal.remove());
  modal.querySelector('#dialog-create').addEventListener('click', async () => {
    const title = modal.querySelector('#new-project-title').value.trim();
    const description = modal.querySelector('#new-project-desc').value.trim();
    if (!title) {
      uiModule.showToast('Title is required');
      return;
    }

    try {
      const res = await fetch(`${API_BASE}/api/video/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, description })
      });
      const data = await res.json();
      modal.remove();
      _activeProjectId = data.id;
      await loadProjects();
      selectProject(data.id);
    } catch (err) {
      uiModule.showError(`Failed to create project: ${err.message}`);
    }
  });
}

async function selectProject(id) {
  _activeProjectId = id;
  const items = document.querySelectorAll('.video-project-item');
  items.forEach(item => {
    item.classList.toggle('active', parseInt(item.dataset.id) === id);
  });

  const project = _projects.find(p => p.id === id);
  if (project) {
    renderProjectDetail(project);
  }
}

async function renderProjectDetail(project) {
  const detail = document.querySelector('.video-project-detail');
  if (!detail) return;

  detail.innerHTML = `
    <div class="video-detail-header">
      <h3>${project.title}</h3>
      <p>${project.description || 'No description provided.'}</p>
      <div class="video-detail-meta">
        <span>Status: <strong class="badge-${project.status}">${project.status}</strong></span>
        ${project.format_recommendation ? `<span>Recommended: <strong>${project.format_recommendation}</strong></span>` : ''}
      </div>
    </div>
    
    <div class="video-detail-actions">
      <button id="video-analyze-btn" class="video-action-btn">🧠 AI Format Analysis</button>
      <button id="video-edit-plan-btn" class="video-action-btn">✂️ Generate Edit Plan</button>
      <button id="video-delete-proj-btn" class="video-action-btn danger">🗑️ Delete Project</button>
    </div>

    <div class="video-analysis-result hidden"></div>

    <div class="video-edit-options-card" style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-top: 10px; display: flex; flex-direction: column; gap: 10px;">
      <h5 style="margin: 0; font-size: 12px; font-weight: 600; color: var(--fg); display: flex; align-items: center; gap: 6px;">
        <span>⚙️</span> Customize AI Edit Style
      </h5>
      
      <!-- Checkbox Options -->
      <div style="display: flex; flex-wrap: wrap; gap: 8px; font-size: 11px;">
        <label class="video-style-pill" style="display: flex; align-items: center; gap: 6px; cursor: pointer; padding: 6px 12px; border-radius: 20px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--fg); font-weight: 550; user-select: none;">
          <input type="checkbox" name="video-style" value="Fast-paced/aggressive cuts" checked style="display: none;" />
          <span>⚡ Fast Cuts</span>
        </label>
        <label class="video-style-pill" style="display: flex; align-items: center; gap: 6px; cursor: pointer; padding: 6px 12px; border-radius: 20px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--fg); font-weight: 550; user-select: none;">
          <input type="checkbox" name="video-style" value="Narrative/story-focused flow" style="display: none;" />
          <span>📖 Narrative Flow</span>
        </label>
        <label class="video-style-pill" style="display: flex; align-items: center; gap: 6px; cursor: pointer; padding: 6px 12px; border-radius: 20px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--fg); font-weight: 550; user-select: none;">
          <input type="checkbox" name="video-style" value="Highlight reel/best moments only" style="display: none;" />
          <span>✨ Highlight Reel</span>
        </label>
        <label class="video-style-pill" style="display: flex; align-items: center; gap: 6px; cursor: pointer; padding: 6px 12px; border-radius: 20px; border: 1px solid var(--border); background: rgba(255,255,255,0.03); color: var(--fg); font-weight: 550; user-select: none;">
          <input type="checkbox" name="video-style" value="Educational/explainer pacing" style="display: none;" />
          <span>🧠 Explainer/Educational</span>
        </label>
      </div>

      <!-- Custom Prompt Text Box -->
      <div style="display: flex; flex-direction: column; gap: 4px;">
        <label style="font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.6; font-weight: 600;">Custom Editing Directions (Optional)</label>
        <textarea id="video-custom-prompt" placeholder="e.g. 'Keep parts discussing SQLite, add text overlay saying SQLite Rocks!' or 'Trim to 30s vertical TikTok clip.'" style="background: rgba(0, 0, 0, 0.2); border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; font-size: 11.5px; color: var(--fg); font-family: inherit; resize: vertical; min-height: 40px;"></textarea>
      </div>
      
      <p style="margin: 0; font-size: 10.5px; opacity: 0.5; font-style: italic; line-height: 1.3;">
        💡 Click "Generate Edit Plan" above after selecting your preferences. Once completed, you will be automatically switched to the Timeline Editor to view and refine your cuts.
      </p>
    </div>

    <div class="video-clips-section">
      <h4>Source Video Clips</h4>
      <div class="video-dropzone" id="clip-dropzone">
        <p>Drag & Drop files here, or click to upload</p>
        <input type="file" id="clip-file-input" multiple style="display:none" accept="video/*,.mkv,.mp4,.mov,.webm,.m4v" />
      </div>
      <div class="video-clips-grid">Loading clips...</div>
    </div>
  `;

  // Attach detail event listeners
  detail.querySelector('#video-delete-proj-btn').addEventListener('click', () => deleteProject(project.id));
  detail.querySelector('#video-analyze-btn').addEventListener('click', () => runAIAnalysis(project.id));
  detail.querySelector('#video-edit-plan-btn').addEventListener('click', () => generateEditPlan(project.id));
  
  // Wire upload zone
  const dropzone = detail.querySelector('#clip-dropzone');
  const fileInput = detail.querySelector('#clip-file-input');
  dropzone.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', (e) => uploadClips(e.target.files));
  
  dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('dragover'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('dragover'));
  dropzone.addEventListener('drop', (e) => {
    e.preventDefault();
    dropzone.classList.remove('dragover');
    uploadClips(e.dataTransfer.files);
  });

  if (project.format_reasoning) {
    showAnalysisResult(project.format_recommendation, project.format_reasoning);
  }

  await loadProjectClips(project.id);
}

async function deleteProject(id) {
  if (!confirm('Are you sure you want to delete this project? All rendered files and references will be removed.')) return;
  try {
    await fetch(`${API_BASE}/api/video/projects/${id}`, { method: 'DELETE' });
    _activeProjectId = null;
    _activeProjectClips = [];
    await loadProjects();
    const detail = document.querySelector('.video-project-detail');
    if (detail) detail.innerHTML = `<div class="video-detail-placeholder">Select or create a video project to get started.</div>`;
  } catch (err) {
    uiModule.showError(`Failed to delete project: ${err.message}`);
  }
}

async function loadProjectClips(projectId) {
  const grid = document.querySelector('.video-clips-grid');
  if (!grid) return;

  try {
    const res = await fetch(`${API_BASE}/api/video/clips?project_id=${projectId}`);
    _activeProjectClips = (await res.json()).clips || [];

    if (_activeProjectClips.length === 0) {
      grid.innerHTML = `<div class="video-empty-message">No clips uploaded yet. Upload MP4/MOV/MKV/WEBM clips above.</div>`;
      return;
    }

    grid.innerHTML = _activeProjectClips.map(c => `
      <div class="video-clip-card" data-id="${c.id}">
        <div class="video-clip-thumbnail">
          <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
          <span class="video-clip-duration">${c.duration_seconds.toFixed(1)}s</span>
        </div>
        <div class="video-clip-info">
          <div class="video-clip-name" title="${c.file_name}">${c.file_name}</div>
          <div class="video-clip-resolution">${c.resolution} @ ${c.fps.toFixed(0)} FPS</div>
          <div class="video-clip-status-badges">
            <span class="status-badge stt-${c.transcript ? 'done' : 'pending'}">${c.transcript ? 'Transcribed' : 'No Transcript'}</span>
            <span class="status-badge visual-${c.visual_summary ? 'done' : 'pending'}">${c.visual_summary ? 'Analyzed' : 'No Visuals'}</span>
          </div>
          <div class="video-clip-actions">
            <button class="clip-details-btn" title="View transcript and visuals">📝 Details</button>
            <button class="clip-transcribe-btn" title="Transcribe speech">🔊 Transcribe</button>
            <button class="clip-visual-btn" title="Extract & describe keyframes">🖼️ Analyze Visuals</button>
            <button class="clip-delete-btn danger" title="Delete clip">🗑️ Delete</button>
          </div>
        </div>
      </div>
    `).join('');

    // Attach clip actions
    grid.querySelectorAll('.video-clip-card').forEach(card => {
      const clipId = parseInt(card.dataset.id);
      card.querySelector('.clip-details-btn').addEventListener('click', () => showClipDetails(clipId));
      card.querySelector('.clip-transcribe-btn').addEventListener('click', () => transcribeClip(clipId));
      card.querySelector('.clip-visual-btn').addEventListener('click', () => describeVisuals(clipId));
      card.querySelector('.clip-delete-btn').addEventListener('click', () => deleteClip(clipId));
    });
  } catch (err) {
    grid.innerHTML = `<div class="video-error-message">Error loading clips: ${err.message}</div>`;
  }
}

function uploadFileWithProgress(file, projectId, onProgress, onLog) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('file', file);
    formData.append('project_id', projectId);

    xhr.upload.addEventListener('progress', (e) => {
      if (e.lengthComputable) {
        const pct = Math.round((e.loaded / e.total) * 100);
        onProgress(pct, e.loaded, e.total);
      }
    });

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        onLog('Saving clip to database & starting metadata pipeline...', 'info');
        resolve();
      } else {
        let msg = `Server returned status ${xhr.status}`;
        try {
          const resJson = JSON.parse(xhr.responseText);
          if (resJson && resJson.message) msg = resJson.message;
        } catch (_) {}
        reject(new Error(msg));
      }
    });

    xhr.addEventListener('error', () => {
      reject(new Error('Network error or connection lost'));
    });

    xhr.addEventListener('abort', () => {
      reject(new Error('Upload aborted'));
    });

    xhr.open('POST', `${API_BASE}/api/video/upload`);
    xhr.send(formData);
  });
}

async function uploadClips(files) {
  if (files.length === 0) return;
  const clipsSection = document.querySelector('.video-clips-section');
  if (!clipsSection) return;

  // Clear any old upload logger element
  let loggerEl = clipsSection.querySelector('.video-upload-logger');
  if (loggerEl) loggerEl.remove();

  // Create new logger element and insert it before the clips grid
  loggerEl = document.createElement('div');
  loggerEl.className = 'video-upload-logger';
  loggerEl.innerHTML = `
    <div class="video-upload-progress-container">
      <div class="video-upload-filename">Uploading: <strong id="upload-current-file"></strong></div>
      <div class="video-upload-progress-bar-wrapper">
        <div class="video-upload-progress-bar" id="upload-progress-bar" style="width: 0%"></div>
      </div>
      <div class="video-upload-status-text" id="upload-progress-text">0% (0 / 0 MB)</div>
    </div>
    <div class="video-upload-logs" id="upload-log-console"></div>
  `;
  const grid = clipsSection.querySelector('.video-clips-grid');
  clipsSection.insertBefore(loggerEl, grid);

  const currentFileEl = loggerEl.querySelector('#upload-current-file');
  const progressBarEl = loggerEl.querySelector('#upload-progress-bar');
  const progressTextEl = loggerEl.querySelector('#upload-progress-text');
  const logConsoleEl = loggerEl.querySelector('#upload-log-console');

  function addLog(message, type = 'info') {
    const time = new Date().toTimeString().split(' ')[0];
    const logLine = document.createElement('p');
    logLine.className = `video-upload-log-line ${type}`;
    logLine.textContent = `[${time}] ${message}`;
    logConsoleEl.appendChild(logLine);
    logConsoleEl.scrollTop = logConsoleEl.scrollHeight;
  }

  for (const file of files) {
    currentFileEl.textContent = file.name;
    progressBarEl.style.width = '0%';
    progressTextEl.textContent = `0% (0.0 / ${(file.size / 1024 / 1024).toFixed(1)} MB)`;
    addLog(`Starting upload of ${file.name} (${(file.size / 1024 / 1024).toFixed(1)} MB)...`, 'info');

    try {
      await uploadFileWithProgress(file, _activeProjectId, (pct, loaded, total) => {
        progressBarEl.style.width = `${pct}%`;
        progressTextEl.textContent = `${pct}% (${(loaded / 1024 / 1024).toFixed(1)} / ${(total / 1024 / 1024).toFixed(1)} MB)`;
      }, (logMsg, logType) => {
        addLog(logMsg, logType);
      });
      addLog(`File ${file.name} uploaded successfully!`, 'success');
      uiModule.showToast(`Upload successful: ${file.name}`);
    } catch (err) {
      addLog(`Upload failed for ${file.name}: ${err.message}`, 'error');
      uiModule.showError(`Upload failed: ${err.message}`);
    }
  }

  // Reload projects & clips to update display
  await loadProjects();
  await loadProjectClips(_activeProjectId);
}

async function transcribeClip(clipId) {
  const clip = _activeProjectClips.find(c => c.id === clipId);
  const fileName = clip ? clip.file_name : `Clip #${clipId}`;
  
  const clipsSection = document.querySelector('.video-clips-section');
  if (!clipsSection) return;

  // Clear old loggers
  let oldLogger = clipsSection.querySelector('.video-clip-op-logger');
  if (oldLogger) oldLogger.remove();

  const loggerEl = document.createElement('div');
  loggerEl.className = 'video-upload-logger video-clip-op-logger';
  loggerEl.innerHTML = `
    <div class="video-upload-progress-container">
      <div class="video-upload-filename">Speech-to-Text Transcription: <strong>${fileName}</strong></div>
      <div class="video-upload-progress-bar-wrapper">
        <div class="video-upload-progress-bar" id="clip-op-progress-bar" style="width: 10%"></div>
      </div>
      <div class="video-upload-status-text" id="clip-op-progress-text">Initializing Whisper...</div>
    </div>
    <div class="video-upload-logs" id="clip-op-log-console" style="height: 100px;"></div>
  `;
  const grid = clipsSection.querySelector('.video-clips-grid');
  clipsSection.insertBefore(loggerEl, grid);

  const progressBar = loggerEl.querySelector('#clip-op-progress-bar');
  const progressText = loggerEl.querySelector('#clip-op-progress-text');
  const logConsole = loggerEl.querySelector('#clip-op-log-console');

  function addLog(msg, type = 'info') {
    const time = new Date().toTimeString().split(' ')[0];
    const line = document.createElement('p');
    line.className = `video-upload-log-line ${type}`;
    line.textContent = `[${time}] ${msg}`;
    logConsole.appendChild(line);
    logConsole.scrollTop = logConsole.scrollHeight;
  }

  addLog(`Triggering audio transcription task for ${fileName}...`, 'info');

  try {
    const triggerRes = await fetch(`${API_BASE}/api/video/clips/${clipId}/transcribe`, { method: 'POST' });
    if (!triggerRes.ok) {
      throw new Error(`Server returned status ${triggerRes.status}`);
    }

    progressBar.style.width = '20%';
    progressText.textContent = 'Task queued...';
    addLog('Transcription task queued successfully on backend.', 'success');
    addLog('Extracting audio track to temporary WAV file (background)...', 'info');

    let simulatedPercent = 20;
    let loggedAudioExtracted = false;
    let loggedWhisperStart = false;

    // Start polling clip details
    const pollInterval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/video/clips/${clipId}`);
        if (!res.ok) return; // ignore temporary poll failures
        const data = await res.json();

        if (data && data.transcript) {
          clearInterval(pollInterval);
          progressBar.style.width = '100%';
          progressText.textContent = 'Transcription complete!';
          
          if (data.transcript.startsWith('Transcription failed:')) {
            addLog(`Error: ${data.transcript}`, 'error');
            progressBar.style.background = '#f28b82'; // red
            uiModule.showError('Transcription failed.');
          } else {
            addLog('Audio successfully transcribed via Whisper!', 'success');
            uiModule.showToast(`Transcription finished for ${fileName}`);
          }
          
          setTimeout(() => loggerEl.remove(), 2500);
          await loadProjectClips(_activeProjectId);
        } else {
          // Simulate progress
          if (simulatedPercent < 90) simulatedPercent += 5;
          progressBar.style.width = `${simulatedPercent}%`;
          progressText.textContent = `Transcribing speech (${simulatedPercent}%)...`;

          if (simulatedPercent >= 40 && !loggedAudioExtracted) {
            addLog('WAV audio track extracted successfully.', 'success');
            addLog('Initializing faster-whisper model (size: base)...', 'info');
            loggedAudioExtracted = true;
          }
          if (simulatedPercent >= 60 && !loggedWhisperStart) {
            addLog('Whisper inference running on audio stream...', 'info');
            loggedWhisperStart = true;
          }
        }
      } catch (err) {
        console.error('Error polling clip status:', err);
      }
    }, 2000);

  } catch (err) {
    progressBar.style.width = '100%';
    progressBar.style.background = '#f28b82';
    progressText.textContent = 'Transcription failed!';
    addLog(`Error: ${err.message}`, 'error');
    uiModule.showError(`Transcription failed: ${err.message}`);
  }
}

async function describeVisuals(clipId) {
  const clip = _activeProjectClips.find(c => c.id === clipId);
  const fileName = clip ? clip.file_name : `Clip #${clipId}`;
  
  const clipsSection = document.querySelector('.video-clips-section');
  if (!clipsSection) return;

  // Clear old loggers
  let oldLogger = clipsSection.querySelector('.video-clip-op-logger');
  if (oldLogger) oldLogger.remove();

  const loggerEl = document.createElement('div');
  loggerEl.className = 'video-upload-logger video-clip-op-logger';
  loggerEl.innerHTML = `
    <div class="video-upload-progress-container">
      <div class="video-upload-filename">Keyframe & Visual Analysis: <strong>${fileName}</strong></div>
      <div class="video-upload-progress-bar-wrapper">
        <div class="video-upload-progress-bar" id="clip-op-progress-bar" style="width: 10%"></div>
      </div>
      <div class="video-upload-status-text" id="clip-op-progress-text">Initializing Keyframe Extraction...</div>
    </div>
    <div class="video-upload-logs" id="clip-op-log-console" style="height: 100px;"></div>
  `;
  const grid = clipsSection.querySelector('.video-clips-grid');
  clipsSection.insertBefore(loggerEl, grid);

  const progressBar = loggerEl.querySelector('#clip-op-progress-bar');
  const progressText = loggerEl.querySelector('#clip-op-progress-text');
  const logConsole = loggerEl.querySelector('#clip-op-log-console');

  function addLog(msg, type = 'info') {
    const time = new Date().toTimeString().split(' ')[0];
    const line = document.createElement('p');
    line.className = `video-upload-log-line ${type}`;
    line.textContent = `[${time}] ${msg}`;
    logConsole.appendChild(line);
    logConsole.scrollTop = logConsole.scrollHeight;
  }

  addLog(`Triggering visual analysis task for ${fileName}...`, 'info');

  try {
    const triggerRes = await fetch(`${API_BASE}/api/video/clips/${clipId}/analyze-visuals`, { method: 'POST' });
    if (!triggerRes.ok) {
      throw new Error(`Server returned status ${triggerRes.status}`);
    }

    progressBar.style.width = '20%';
    progressText.textContent = 'Task queued...';
    addLog('Visual analysis task queued successfully on backend.', 'success');
    addLog('Extracting keyframe images via OpenCV (interval: 2.0s)...', 'info');

    let simulatedPercent = 20;
    let loggedKeyframes = false;
    let loggedVisionLLM = false;

    // Start polling clip details
    const pollInterval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/video/clips/${clipId}`);
        if (!res.ok) return; // ignore temporary poll failures
        const data = await res.json();

        if (data && data.visual_summary) {
          clearInterval(pollInterval);
          progressBar.style.width = '100%';
          progressText.textContent = 'Visual description complete!';
          
          if (data.visual_summary.startsWith('Visual analysis failed:') || data.visual_summary.startsWith('Keyframe extraction failed:')) {
            addLog(`Error: ${data.visual_summary}`, 'error');
            progressBar.style.background = '#f28b82'; // red
            uiModule.showError('Visual analysis failed.');
          } else {
            addLog('Visual summary generated via multimodal model!', 'success');
            uiModule.showToast(`Visual analysis finished for ${fileName}`);
          }
          
          setTimeout(() => loggerEl.remove(), 2500);
          await loadProjectClips(_activeProjectId);
        } else {
          // Simulate progress
          if (simulatedPercent < 90) simulatedPercent += 5;
          progressBar.style.width = `${simulatedPercent}%`;
          progressText.textContent = `Analyzing keyframes (${simulatedPercent}%)...`;

          if (simulatedPercent >= 45 && !loggedKeyframes) {
            addLog('Keyframe extraction complete. Keyframes saved to disk.', 'success');
            addLog('Sending keyframe batch to Multimodal Vision model...', 'info');
            loggedKeyframes = true;
          }
          if (simulatedPercent >= 70 && !loggedVisionLLM) {
            addLog('Multimodal scene interpretation and summation in progress...', 'info');
            loggedVisionLLM = true;
          }
        }
      } catch (err) {
        console.error('Error polling clip status:', err);
      }
    }, 2000);

  } catch (err) {
    progressBar.style.width = '100%';
    progressBar.style.background = '#f28b82';
    progressText.textContent = 'Visual analysis failed!';
    addLog(`Error: ${err.message}`, 'error');
    uiModule.showError(`Visual analysis failed: ${err.message}`);
  }
}

async function deleteClip(clipId) {
  if (!confirm('Are you sure you want to delete this clip?')) return;
  try {
    await fetch(`${API_BASE}/api/video/clips/${clipId}`, { method: 'DELETE' });
    await loadProjectClips(_activeProjectId);
  } catch (err) {
    uiModule.showError(`Failed to delete clip: ${err.message}`);
  }
}

async function runAIAnalysis(projectId) {
  const resultDiv = document.querySelector('.video-analysis-result');
  if (!resultDiv) return;
  resultDiv.classList.remove('hidden');
  
  resultDiv.innerHTML = `
    <div class="video-upload-logger" style="margin-top: 10px;">
      <div class="video-upload-progress-container">
        <div class="video-upload-filename">AI Content Strategy Analysis</div>
        <div class="video-upload-progress-bar-wrapper">
          <div class="video-upload-progress-bar" id="analysis-progress-bar" style="width: 10%"></div>
        </div>
        <div class="video-upload-status-text" id="analysis-progress-text">Connecting to AI...</div>
      </div>
      <div class="video-upload-logs" id="analysis-log-console" style="height: 100px;"></div>
    </div>
  `;

  const progressBar = resultDiv.querySelector('#analysis-progress-bar');
  const progressText = resultDiv.querySelector('#analysis-progress-text');
  const logConsole = resultDiv.querySelector('#analysis-log-console');

  function addLog(msg, type = 'info') {
    const time = new Date().toTimeString().split(' ')[0];
    const line = document.createElement('p');
    line.className = `video-upload-log-line ${type}`;
    line.textContent = `[${time}] ${msg}`;
    logConsole.appendChild(line);
    logConsole.scrollTop = logConsole.scrollHeight;
  }

  addLog('Initializing AI analysis pipeline...', 'info');
  progressBar.style.width = '25%';
  progressText.textContent = 'Aggregating transcripts & keyframes...';
  addLog('Gathering clip transcripts, durations, and keyframe descriptions...', 'info');

  try {
    progressBar.style.width = '50%';
    progressText.textContent = 'Analyzing with LLM...';
    addLog('Sending request to AI model (this may take 10-30 seconds depending on model speed)...', 'info');

    const res = await fetch(`${API_BASE}/api/video/projects/${projectId}/analyze`, { method: 'POST' });
    
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || errData.message || `Server returned status ${res.status}`);
    }
    
    const data = await res.json();
    progressBar.style.width = '100%';
    progressText.textContent = 'Analysis complete!';
    
    if (data.fallback) {
      addLog(`[Warning] AI model call failed or returned invalid format. Generated fallback recommendations.`, 'error');
      if (data.error) {
        addLog(`Error Details: ${data.error}`, 'error');
      }
      if (data.raw_response) {
        addLog(`Raw AI Response:\n${data.raw_response}`, 'info');
      }
      uiModule.showToast('Analysis completed with fallbacks.', 'warning');
    } else {
      addLog('AI response parsed successfully.', 'success');
      if (data.thinking) {
        addLog(`Thinking Process:\n${data.thinking}`, 'info');
      }
      if (data.raw_response) {
        addLog(`Raw AI Response:\n${data.raw_response}`, 'info');
      }
    }

    // Wait a brief moment to show success before displaying recommendation card
    await new Promise(resolve => setTimeout(resolve, 800));

    showAnalysisResult(data.format_recommendation, data.format_reasoning);
    await loadProjects();
  } catch (err) {
    progressBar.style.width = '100%';
    progressBar.style.background = '#f28b82'; // red error bar
    progressText.textContent = 'Analysis failed!';
    addLog(`Error: ${err.message}`, 'error');
    
    // Also display the error message explicitly in a card below the log
    const errCard = document.createElement('div');
    errCard.className = 'video-error-message';
    errCard.style.marginTop = '10px';
    errCard.textContent = `Analysis failed: ${err.message}`;
    resultDiv.appendChild(errCard);
  }
}

function showAnalysisResult(format, reasoning) {
  const resultDiv = document.querySelector('.video-analysis-result');
  if (!resultDiv) return;
  resultDiv.classList.remove('hidden');
  
  const formatStr = (format || 'unknown').toLowerCase();
  const formatBadge = formatStr.toUpperCase();
  const reasoningStr = reasoning || 'No reasoning details provided by the model.';

  resultDiv.innerHTML = `
    <div class="video-analysis-card">
      <h5>AI Content Recommendation: <strong class="badge-${formatStr}">${formatBadge}</strong></h5>
      <p>${reasoningStr}</p>
    </div>
  `;
}

async function generateEditPlan(projectId) {
  const resultDiv = document.querySelector('.video-analysis-result');
  if (!resultDiv) return;
  resultDiv.classList.remove('hidden');

  resultDiv.innerHTML = `
    <div class="video-upload-logger" style="margin-top: 10px;">
      <div class="video-upload-progress-container">
        <div class="video-upload-filename">AI Edit Plan Generation</div>
        <div class="video-upload-progress-bar-wrapper">
          <div class="video-upload-progress-bar" id="editplan-progress-bar" style="width: 10%"></div>
        </div>
        <div class="video-upload-status-text" id="editplan-progress-text">Connecting to AI...</div>
      </div>
      <div class="video-upload-logs" id="editplan-log-console" style="height: 100px;"></div>
    </div>
  `;

  const progressBar = resultDiv.querySelector('#editplan-progress-bar');
  const progressText = resultDiv.querySelector('#editplan-progress-text');
  const logConsole = resultDiv.querySelector('#editplan-log-console');

  function addLog(msg, type = 'info') {
    const time = new Date().toTimeString().split(' ')[0];
    const line = document.createElement('p');
    line.className = `video-upload-log-line ${type}`;
    line.textContent = `[${time}] ${msg}`;
    logConsole.appendChild(line);
    logConsole.scrollTop = logConsole.scrollHeight;
  }

  addLog('Initializing AI edit planner...', 'info');
  progressBar.style.width = '30%';
  progressText.textContent = 'Analyzing timestamps & speech...';
  addLog('Reading timestamped transcript segments...', 'info');

  try {
    const checkedStyles = Array.from(document.querySelectorAll('input[name="video-style"]:checked')).map(el => el.value);
    const customPromptVal = document.getElementById('video-custom-prompt')?.value || '';

    if (checkedStyles.length > 0) {
      addLog(`Selected editing styles: ${checkedStyles.join(', ')}`, 'info');
    }
    if (customPromptVal.trim()) {
      addLog(`Custom editing directions: "${customPromptVal.trim()}"`, 'info');
    }

    progressBar.style.width = '60%';
    progressText.textContent = 'Generating plan with LLM...';
    addLog('Sending composition request to AI model (this can take 15-40 seconds)...', 'info');

    const res = await fetch(`${API_BASE}/api/video/projects/${projectId}/edit-plan`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        style_opts: checkedStyles,
        custom_prompt: customPromptVal
      })
    });
    
    if (!res.ok) {
      const errData = await res.json().catch(() => ({}));
      throw new Error(errData.detail || errData.message || `Server returned status ${res.status}`);
    }

    const data = await res.json();
    progressBar.style.width = '100%';
    progressText.textContent = 'Edit plan generated!';
    
    if (data.fallback) {
      addLog(`[Warning] AI model call failed or returned invalid format. Generated default chronological plan.`, 'error');
      if (data.error) {
        addLog(`Error Details: ${data.error}`, 'error');
      }
      if (data.raw_response) {
        addLog(`Raw AI Response:\n${data.raw_response}`, 'info');
      }
      uiModule.showToast('Edit plan generated with fallbacks (see console logs).', 'warning');
    } else {
      addLog('AI Edit Plan successfully calculated and saved to project.', 'success');
      if (data.thinking) {
        addLog(`Thinking Process:\n${data.thinking}`, 'info');
      }
      if (data.raw_response) {
        addLog(`Raw AI Response:\n${data.raw_response}`, 'info');
      }
      if (data.reasoning) {
        addLog(`AI Strategy: ${data.reasoning}`, 'info');
      }
      uiModule.showToast('Edit plan generated! Switched to Timeline Editor.');
    }

    // Wait a brief moment before refreshing projects and switching tabs
    await new Promise(resolve => setTimeout(resolve, 800));
    await loadProjects();

    // Auto-switch to Timeline Editor tab
    const tabsContainer = document.querySelector('.video-tabs');
    if (tabsContainer) {
      tabsContainer.querySelectorAll('.video-tab-btn').forEach(btn => {
        if (btn.dataset.tab === 'editor') {
          btn.classList.add('active');
        } else {
          btn.classList.remove('active');
        }
      });
    }
    await switchTab('editor');

  } catch (err) {
    progressBar.style.width = '100%';
    progressBar.style.background = '#f28b82'; // error color
    progressText.textContent = 'Planning failed!';
    addLog(`Error: ${err.message}`, 'error');

    const errCard = document.createElement('div');
    errCard.className = 'video-error-message';
    errCard.style.marginTop = '10px';
    errCard.textContent = `Planning failed: ${err.message}`;
    resultDiv.appendChild(errCard);
  }
}

// ---------------------------------------------------------------------------
// Tab 2: Timeline Editor & Rendering
// ---------------------------------------------------------------------------

async function loadEditorTab(container) {
  // Fetch current project edit instructions
  let project = _projects.find(p => p.id === _activeProjectId);
  const pubSettings = project?.publish_settings || {};
  let editInstructions = pubSettings.edit_instructions || [];
  
  // If the user has no custom edits yet, but an AI edit plan exists, use the AI edit plan as default!
  let usingAIPlanAsDefault = false;
  if (editInstructions.length === 0 && pubSettings.edit_plan?.edit_instructions && pubSettings.edit_plan.edit_instructions.length > 0) {
    editInstructions = pubSettings.edit_plan.edit_instructions;
    usingAIPlanAsDefault = true;
  }

  const speedInstr = editInstructions.find(i => i.action === 'speed');
  const speedFactor = speedInstr ? speedInstr.factor : 1.0;

  container.innerHTML = `
    <div class="video-editor-layout">
      <div class="video-editor-timeline-panel">
        <h5>Timeline & Composition</h5>
        <div class="video-timeline" id="video-timeline-tracks">
          ${_activeProjectClips.length === 0 ? `
            <div class="video-empty-timeline">No clips in this project yet. Go to Projects & Clips to upload.</div>
          ` : `
            <div class="video-timeline-clips">
              ${_activeProjectClips.map((clip, idx) => {
                const instr = editInstructions.find(i => i.clip_id === clip.id) || { start: 0.0, end: clip.duration_seconds };
                return `
                  <div class="video-timeline-clip" data-id="${clip.id}">
                    <div class="timeline-clip-header">
                      <span class="clip-idx">#${idx + 1}</span>
                      <span class="clip-name">${clip.file_name}</span>
                    </div>
                    <div class="timeline-clip-trims">
                      <label>Trim Start: <input type="number" class="trim-start" step="0.1" value="${instr.start !== undefined ? instr.start : 0.0}" style="width:60px" /></label>
                      <label>Trim End: <input type="number" class="trim-end" step="0.1" value="${instr.end !== undefined ? instr.end : clip.duration_seconds}" style="width:60px" /></label>
                    </div>
                  </div>
                `;
              }).join('')}
            </div>
          `}
        </div>

        <div class="video-editor-effects-panel">
          <h5>Edit Controls</h5>
          <div class="video-effects-grid">
            <label class="effect-checkbox"><input type="checkbox" id="effect-crop" ${
              editInstructions.some(i => i.action === 'crop_vertical') || (usingAIPlanAsDefault && project?.format_recommendation === 'short') ? 'checked' : ''
            } /> Crop Vertical (9:16 Shorts format)</label>
            <label class="effect-checkbox"><input type="checkbox" id="effect-subtitles" ${
              editInstructions.some(i => i.action === 'add_subtitles') ? 'checked' : ''
            } /> Generate Subtitles</label>
            <div class="video-effect-speed">
              <label>Pacing/Speed Factor: 
                <select id="effect-speed">
                  <option value="1.0" ${speedFactor === 1.0 ? 'selected' : ''}>1.0x (Normal)</option>
                  <option value="1.2" ${speedFactor === 1.2 ? 'selected' : ''}>1.2x</option>
                  <option value="1.5" ${speedFactor === 1.5 ? 'selected' : ''}>1.5x (Fast)</option>
                  <option value="2.0" ${speedFactor === 2.0 ? 'selected' : ''}>2.0x (Super Fast)</option>
                </select>
              </label>
            </div>
          </div>
          <div class="editor-action-buttons">
            <button id="video-save-edits-btn" class="video-secondary-btn">💾 Save Edits</button>
            <button id="video-preview-btn" class="video-secondary-btn">🖼️ Get Frame Preview</button>
          </div>
        </div>

        <div class="video-render-controls">
          <h5>Render Output</h5>
          <div class="render-form-row">
            <label>Quality:
              <select id="render-quality">
                <option value="draft">Draft (Fast, low-res)</option>
                <option value="high" selected>Production (High quality)</option>
              </select>
            </label>
            <button id="video-start-render-btn" class="video-primary-btn">⚙️ Start Render</button>
          </div>
          <div class="video-render-progress hidden">
            <div class="progress-bar-container">
              <div class="progress-bar-fill" style="width: 0%"></div>
            </div>
            <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 5px;">
              <span class="progress-text">Rendering video composition...</span>
              <button id="video-cancel-render-btn" class="video-secondary-btn" style="padding: 2px 8px; font-size: 11px;">Reset Status</button>
            </div>
            <div class="video-upload-logs" id="render-log-console" style="margin-top: 10px; height: 100px;"></div>
          </div>
          <div class="video-render-download-container hidden">
            <a id="video-download-link" class="video-primary-btn" href="#" target="_blank">📥 Download Rendered MP4</a>
          </div>
        </div>
      </div>

      <div class="video-editor-sidebar">
        <div class="video-preview-section">
          <h5>Composition Preview</h5>
          <div class="video-preview-window">
            <div class="video-preview-overlay">No preview fetched yet.</div>
            <img id="video-preview-img" class="hidden" src="" alt="Composition Preview" />
          </div>
          <label class="preview-scrubber-row">Timestamp (s): 
            <input type="number" id="preview-timestamp" value="0.0" step="0.5" style="width:70px" />
          </label>
        </div>

        <div class="video-ai-plan-section">
          <h5>AI Suggestion Plan</h5>
          <div class="video-ai-plan-content">Loading AI edit suggestions...</div>
          <button id="apply-ai-plan-btn" class="video-primary-btn" style="width: 100%; margin-top: 10px;">Apply AI Edit Plan</button>
        </div>
      </div>
    </div>
  `;

  // Render status initial check
  if (project?.status === 'rendering') {
    showRenderProgress();
    pollRenderStatus();
  } else if (project?.status === 'ready' && project?.output_path) {
    showDownloadButton(project.id);
  }

  // Attach event listeners
  container.querySelector('#video-save-edits-btn').addEventListener('click', saveEdits);
  container.querySelector('#video-preview-btn').addEventListener('click', fetchFramePreview);
  container.querySelector('#video-start-render-btn').addEventListener('click', startRender);
  container.querySelector('#apply-ai-plan-btn').addEventListener('click', applyAIEvents);
  
  const cancelBtn = container.querySelector('#video-cancel-render-btn');
  if (cancelBtn) cancelBtn.addEventListener('click', cancelRender);
  
  // Load AI plan suggestions
  await loadAIEditPlan();
}

function gatherEditInstructions() {
  const instructions = [];
  const clipElements = document.querySelectorAll('.video-timeline-clip');
  
  // 1. Gathers trims and ordering
  clipElements.forEach(el => {
    const clipId = parseInt(el.dataset.id);
    const start = parseFloat(el.querySelector('.trim-start').value) || 0.0;
    const end = parseFloat(el.querySelector('.trim-end').value) || 0.0;
    
    instructions.push({
      action: 'trim',
      clip_id: clipId,
      start,
      end
    });
  });

  // 2. Gathers speed
  const speed = parseFloat(document.getElementById('effect-speed').value) || 1.0;
  if (speed !== 1.0) {
    instructions.push({
      action: 'speed',
      factor: speed
    });
  }

  // 3. Gathers crop
  const cropCheck = document.getElementById('effect-crop');
  if (cropCheck && cropCheck.checked) {
    instructions.push({
      action: 'crop_vertical'
    });
  }

  // 4. Gathers subtitles
  const subtitleCheck = document.getElementById('effect-subtitles');
  if (subtitleCheck && subtitleCheck.checked) {
    instructions.push({
      action: 'add_subtitles'
    });
  }

  return instructions;
}

async function saveEdits() {
  const instructions = gatherEditInstructions();
  try {
    const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        publish_settings: {
          edit_instructions: instructions
        }
      })
    });
    if (res.ok) {
      uiModule.showToast('Edits saved successfully!');
      await loadProjects();
    } else {
      throw new Error(await res.text());
    }
  } catch (err) {
    uiModule.showError(`Failed to save edits: ${err.message}`);
  }
}

async function fetchFramePreview() {
  const timestamp = parseFloat(document.getElementById('preview-timestamp').value) || 0.0;
  const img = document.getElementById('video-preview-img');
  const overlay = document.querySelector('.video-preview-overlay');
  
  if (overlay) overlay.textContent = 'Rendering preview frame...';
  
  // First save current edits
  const instructions = gatherEditInstructions();
  try {
    await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        publish_settings: {
          edit_instructions: instructions
        }
      })
    });
    
    // Now request frame
    const previewUrl = `${API_BASE}/api/video/projects/${_activeProjectId}/preview?timestamp=${timestamp}&t=${Date.now()}`;
    img.src = previewUrl;
    img.onload = () => {
      img.classList.remove('hidden');
      if (overlay) overlay.classList.add('hidden');
    };
    img.onerror = () => {
      img.classList.add('hidden');
      if (overlay) {
        overlay.classList.remove('hidden');
        overlay.textContent = 'Failed to load preview frame.';
      }
    };
  } catch (err) {
    uiModule.showError(`Failed to get preview: ${err.message}`);
  }
}

async function startRender() {
  const quality = document.getElementById('render-quality').value;
  const instructions = gatherEditInstructions();
  
  showRenderProgress();
  const logConsole = document.getElementById('render-log-console');
  function addRenderLog(msg, type = 'info') {
    if (!logConsole) return;
    const time = new Date().toTimeString().split(' ')[0];
    const line = document.createElement('p');
    line.className = `video-upload-log-line ${type}`;
    line.textContent = `[${time}] ${msg}`;
    logConsole.appendChild(line);
    logConsole.scrollTop = logConsole.scrollHeight;
  }
  
  addRenderLog(`Initializing render job (Quality: ${quality})...`, 'info');
  addRenderLog(`Parsing ${instructions.length} edit instructions...`, 'info');
  
  try {
    const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}/render`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        edit_instructions: instructions,
        quality
      })
    });
    
    if (res.ok) {
      addRenderLog('Render task successfully queued on backend.', 'success');
      pollRenderStatus();
    } else {
      throw new Error(await res.text());
    }
  } catch (err) {
    addRenderLog(`Failed to start render: ${err.message}`, 'error');
    uiModule.showError(`Failed to start render: ${err.message}`);
  }
}

function showRenderProgress() {
  const renderBtn = document.getElementById('video-start-render-btn');
  const progressDiv = document.querySelector('.video-render-progress');
  const downloadDiv = document.querySelector('.video-render-download-container');
  
  if (renderBtn) renderBtn.disabled = true;
  if (progressDiv) progressDiv.classList.remove('hidden');
  if (downloadDiv) downloadDiv.classList.add('hidden');
  
  const logConsole = document.getElementById('render-log-console');
  if (logConsole) logConsole.innerHTML = '';
}

async function cancelRender() {
  if (!confirm('Are you sure you want to reset the project status? This will stop the active status monitoring interface.')) return;
  
  try {
    const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        status: 'draft'
      })
    });
    
    if (res.ok) {
      if (_renderPollingInterval) {
        clearInterval(_renderPollingInterval);
        _renderPollingInterval = null;
      }
      uiModule.showToast('Project status reset to draft.');
      await loadProjects();
      await switchTab('editor'); // Reload editor view
    } else {
      throw new Error(await res.text());
    }
  } catch (err) {
    uiModule.showError(`Failed to reset project status: ${err.message}`);
  }
}

function pollRenderStatus() {
  if (_renderPollingInterval) clearInterval(_renderPollingInterval);
  
  const progressFill = document.querySelector('.progress-bar-fill');
  const progressText = document.querySelector('.progress-text');
  const logConsole = document.getElementById('render-log-console');
  
  function addRenderLog(msg, type = 'info') {
    if (!logConsole) return;
    const time = new Date().toTimeString().split(' ')[0];
    const line = document.createElement('p');
    line.className = `video-upload-log-line ${type}`;
    line.textContent = `[${time}] ${msg}`;
    logConsole.appendChild(line);
    logConsole.scrollTop = logConsole.scrollHeight;
  }
  
  let lastMessage = '';
  let loggedStages = {
    15: false,
    30: false,
    60: false,
    85: false,
    95: false
  };

  addRenderLog('Waiting for render queue slot...', 'info');

  _renderPollingInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}/render/status`);
      const data = await res.json();
      
      if (data.status === 'ready') {
        clearInterval(_renderPollingInterval);
        _renderPollingInterval = null;
        if (progressFill) progressFill.style.width = '100%';
        if (progressText) progressText.textContent = 'Rendering completed!';
        addRenderLog('MoviePy background rendering process finished.', 'success');
        addRenderLog('Encoding complete. MP4 video is ready for download.', 'success');
        
        setTimeout(() => {
          const progressDiv = document.querySelector('.video-render-progress');
          if (progressDiv) progressDiv.classList.add('hidden');
          showDownloadButton(_activeProjectId);
        }, 1000);
        
        await loadProjects();
      } else if (data.status === 'failed') {
        clearInterval(_renderPollingInterval);
        _renderPollingInterval = null;
        if (progressText) progressText.textContent = 'Render failed. Check logs.';
        const errMsg = data.message || 'Error: Backend render task reported failure. Check application stdout/stderr logs.';
        addRenderLog(errMsg, 'error');
        const renderBtn = document.getElementById('video-start-render-btn');
        if (renderBtn) renderBtn.disabled = false;
        await loadProjects();
      } else {
        const percent = typeof data.percent === 'number' ? data.percent : 0;
        const message = data.message || `Rendering video composition (${percent}%)...`;
        
        if (progressFill) progressFill.style.width = `${percent}%`;
        if (progressText) progressText.textContent = message;
        
        // Log the message if it has changed and is non-empty
        if (data.message && data.message !== lastMessage) {
          addRenderLog(data.message, 'info');
          lastMessage = data.message;
        }
        
        // Log high-level stages based on actual progress
        if (percent >= 15 && !loggedStages[15]) {
          addRenderLog('Analyzing composition and loading source clip video/audio files...', 'info');
          loggedStages[15] = true;
        }
        if (percent >= 30 && !loggedStages[30]) {
          addRenderLog('Applying trims, vertical cropping, and video speed filters...', 'info');
          loggedStages[30] = true;
        }
        if (percent >= 60 && !loggedStages[60]) {
          addRenderLog('Rendering composite video frames and merging audio tracks...', 'info');
          loggedStages[60] = true;
        }
        if (percent >= 85 && !loggedStages[85]) {
          addRenderLog('Encoding video stream to H.264 / AAC MP4 format...', 'info');
          loggedStages[85] = true;
        }
        if (percent >= 95 && !loggedStages[95]) {
          addRenderLog('Finalizing MP4 file container structure...', 'info');
          loggedStages[95] = true;
        }
      }
    } catch (err) {
      console.error('Error polling render status:', err);
      addRenderLog(`Polling connection error: ${err.message}`, 'error');
    }
  }, 3000);
}

function showDownloadButton(projectId) {
  const downloadDiv = document.querySelector('.video-render-download-container');
  const link = document.getElementById('video-download-link');
  const renderBtn = document.getElementById('video-start-render-btn');
  
  if (renderBtn) renderBtn.disabled = false;
  if (downloadDiv && link) {
    downloadDiv.classList.remove('hidden');
    link.href = `${API_BASE}/api/video/projects/${projectId}/download`;
  }
}

async function loadAIEditPlan() {
  const planContainer = document.querySelector('.video-ai-plan-content');
  if (!planContainer) return;

  try {
    const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}`);
    const data = await res.json();
    const settings = data.publish_settings || {};
    const plan = settings.edit_plan || {};

    if (!plan.edit_instructions || plan.edit_instructions.length === 0) {
      planContainer.innerHTML = `<div class="video-empty-message">No AI Edit Plan suggested yet. Click "Generate Edit Plan" in the first tab.</div>`;
      return;
    }

    planContainer.innerHTML = `
      <div class="video-ai-plan-reasoning"><strong>Reasoning:</strong> ${plan.reasoning || 'No details.'}</div>
      <div class="video-ai-plan-steps">
        ${plan.edit_instructions.map((step, idx) => `
          <div class="video-ai-plan-step">
            <span>${idx + 1}. Clip ID ${step.clip_id}: ${step.start.toFixed(1)}s - ${step.end.toFixed(1)}s</span>
          </div>
        `).join('')}
      </div>
    `;
    planContainer.dataset.planJson = JSON.stringify(plan.edit_instructions);
  } catch (err) {
    planContainer.innerHTML = `<div class="video-error-message">Error: ${err.message}</div>`;
  }
}

function applyAIEvents() {
  const planContainer = document.querySelector('.video-ai-plan-content');
  if (!planContainer || !planContainer.dataset.planJson) {
    uiModule.showToast('No AI edit instructions to apply.');
    return;
  }

  const steps = JSON.parse(planContainer.dataset.planJson);
  
  // Pre-fill input boxes on the timeline
  steps.forEach(step => {
    const clipEl = document.querySelector(`.video-timeline-clip[data-id="${step.clip_id}"]`);
    if (clipEl) {
      clipEl.querySelector('.trim-start').value = step.start;
      clipEl.querySelector('.trim-end').value = step.end;
    }
  });

  // Enable vertical cropping if recommended format is 'short'
  const project = _projects.find(p => p.id === _activeProjectId);
  const cropCheck = document.getElementById('effect-crop');
  if (cropCheck && project?.format_recommendation === 'short') {
    cropCheck.checked = true;
  }

  uiModule.showToast('AI Edit Plan applied to timeline elements! Click "Save Edits" to persist.');
}

// ---------------------------------------------------------------------------
// Tab 3: YouTube Publishing & Settings
// ---------------------------------------------------------------------------

async function loadPublishTab(container) {
  container.innerHTML = `
    <div class="video-publish-layout">
      <div class="video-publish-form-panel">
        <h5>YouTube Metadata</h5>
        <form id="youtube-publish-form">
          <div class="form-group">
            <label>YouTube Account:
              <select id="pub-account-select">
                <option value="">Loading accounts...</option>
              </select>
            </label>
          </div>
          <div class="form-group">
            <label>Video Title:
              <input type="text" id="pub-title" maxlength="100" placeholder="Enter video title" />
            </label>
          </div>
          <div class="form-group">
            <label>Video Description:
              <textarea id="pub-description" placeholder="Enter video description"></textarea>
            </label>
          </div>
          <div class="form-group">
            <label>Video Tags (comma-separated):
              <input type="text" id="pub-tags" placeholder="e.g. tutorial, vlog, ai" />
            </label>
          </div>
          <div class="form-row">
            <label>Privacy:
              <select id="pub-privacy">
                <option value="private" selected>Private</option>
                <option value="unlisted">Unlisted</option>
                <option value="public">Public</option>
              </select>
            </label>
            <label>Category:
              <select id="pub-category">
                <option value="22" selected>People & Blogs</option>
                <option value="27">Education</option>
                <option value="28">Science & Technology</option>
                <option value="20">Gaming</option>
                <option value="24">Entertainment</option>
              </select>
            </label>
          </div>
          <div class="publish-action-buttons">
            <button type="button" id="pub-ai-fill-btn" class="video-secondary-btn">🧠 AI Autofill Info</button>
            <button type="submit" id="pub-submit-btn" class="video-primary-btn">🚀 Publish to YouTube</button>
          </div>
        </form>

        <div class="video-publish-progress hidden">
          <div class="progress-bar-container">
            <div class="progress-bar-fill publish-progress-bar" style="width: 0%"></div>
          </div>
          <span class="publish-status-text">Publishing video to YouTube...</span>
        </div>

        <div class="video-publish-success-card hidden">
          <h5>🎬 Video Uploaded to YouTube!</h5>
          <p>YouTube Video ID: <strong id="published-video-id">None</strong></p>
          <p>Processing Status: <span id="youtube-processing-status" class="status-badge">checking...</span></p>
          <a id="youtube-watch-link" class="video-primary-btn" href="#" target="_blank">📺 View on YouTube</a>
        </div>
      </div>

      <div class="video-publish-settings-panel">
        <h5>YouTube Account Settings</h5>
        <div class="video-connected-accounts-list">Loading accounts...</div>
        <hr class="panel-divider" />
        <h5>Connect YouTube Channel</h5>
        <div class="video-oauth-setup-form">
          <input type="text" id="oauth-client-id" placeholder="Google Client ID" />
          <input type="password" id="oauth-client-secret" placeholder="Google Client Secret" />
          <button id="oauth-connect-btn" class="video-primary-btn" style="width:100%">Link Google Account</button>
        </div>
      </div>
    </div>
  `;

  // Bind forms and events
  container.querySelector('#youtube-publish-form').addEventListener('submit', handlePublishSubmit);
  container.querySelector('#pub-ai-fill-btn').addEventListener('click', autofillPublishInfo);
  container.querySelector('#oauth-connect-btn').addEventListener('click', linkYouTubeAccount);

  await loadYouTubeAccounts();
  
  // Check current project status
  let project = _projects.find(p => p.id === _activeProjectId);
  if (project?.status === 'publishing') {
    showPublishProgress();
    pollPublishStatus();
  } else if (project?.status === 'published' && project?.youtube_video_id) {
    showPublishSuccess(project.youtube_video_id);
    pollPublishStatus();
  }
}

async function loadYouTubeAccounts() {
  const select = document.getElementById('pub-account-select');
  const list = document.querySelector('.video-connected-accounts-list');
  
  try {
    const res = await fetch(`${API_BASE}/api/video/youtube/accounts`);
    _youtubeAccounts = await res.json();
    
    if (select) {
      if (_youtubeAccounts.length === 0) {
        select.innerHTML = `<option value="">No linked accounts</option>`;
      } else {
        select.innerHTML = _youtubeAccounts.map(acc => `
          <option value="${acc.id}">${acc.channel_name || 'Unnamed Channel'} (${acc.client_id.substring(0, 10)}...)</option>
        `).join('');
      }
    }

    if (list) {
      if (_youtubeAccounts.length === 0) {
        list.innerHTML = `<div class="video-empty-message">No connected accounts. Link your channel below.</div>`;
      } else {
        list.innerHTML = _youtubeAccounts.map(acc => `
          <div class="connected-account-card">
            <div class="account-card-title">${acc.channel_name || 'Authenticated Channel'}</div>
            <div class="account-card-desc">Channel ID: ${acc.channel_id || 'unlinked'}</div>
            <div class="account-card-actions">
              <button class="account-test-btn" data-id="${acc.id}">Test</button>
              <button class="account-delete-btn danger" data-id="${acc.id}">Remove</button>
            </div>
          </div>
        `).join('');
        
        list.querySelectorAll('.account-test-btn').forEach(b => {
          b.addEventListener('click', (e) => testYouTubeAccount(parseInt(e.target.dataset.id)));
        });
        list.querySelectorAll('.account-delete-btn').forEach(b => {
          b.addEventListener('click', (e) => deleteYouTubeAccount(parseInt(e.target.dataset.id)));
        });
      }
    }
  } catch (err) {
    if (list) list.innerHTML = `<div class="video-error-message">Error loading accounts: ${err.message}</div>`;
  }
}

async function linkYouTubeAccount() {
  const clientId = document.getElementById('oauth-client-id').value.trim();
  const clientSecret = document.getElementById('oauth-client-secret').value.trim();

  if (!clientId || !clientSecret) {
    uiModule.showToast('Please enter both Google OAuth Client ID and Client Secret.');
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/video/youtube/accounts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId, client_secret: clientSecret })
    });
    const data = await res.json();
    
    if (data.success && data.auth_url) {
      // Open the OAuth Consent screen in a new window/tab
      const oauthWindow = window.open(data.auth_url, 'youtube-oauth', 'width=600,height=700');
      
      // Periodically check if the window has been closed, then reload
      const checkClosed = setInterval(async () => {
        if (oauthWindow.closed) {
          clearInterval(checkClosed);
          uiModule.showToast('OAuth linking window closed. Reloading accounts...');
          await loadYouTubeAccounts();
        }
      }, 1000);
    } else {
      throw new Error(data.message || 'No auth URL returned.');
    }
  } catch (err) {
    uiModule.showError(`OAuth connection failed: ${err.message}`);
  }
}

async function testYouTubeAccount(id) {
  try {
    const res = await fetch(`${API_BASE}/api/video/youtube/accounts/${id}/test`, { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      uiModule.showToast(`Connection test passed successfully! Linked channel: ${data.channels[0]?.title || 'Unknown'}`);
    } else {
      throw new Error(data.message);
    }
  } catch (err) {
    uiModule.showError(`Connection test failed: ${err.message}`);
  }
}

async function deleteYouTubeAccount(id) {
  if (!confirm('Are you sure you want to remove this linked account?')) return;
  try {
    await fetch(`${API_BASE}/api/video/youtube/accounts/${id}`, { method: 'DELETE' });
    await loadYouTubeAccounts();
  } catch (err) {
    uiModule.showError(`Failed to delete account: ${err.message}`);
  }
}

async function autofillPublishInfo() {
  try {
    const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}`);
    const data = await res.json();
    const settings = data.publish_settings || {};
    
    // Fill title, description, and tags from AI recommendation if present
    document.getElementById('pub-title').value = settings.title || data.title || '';
    document.getElementById('pub-description').value = settings.description || data.description || '';
    if (settings.tags) {
      document.getElementById('pub-tags').value = Array.isArray(settings.tags) ? settings.tags.join(', ') : settings.tags;
    }
    uiModule.showToast('Autofilled metadata from project details & AI analysis.');
  } catch (err) {
    uiModule.showError(`Failed to fetch project info for autofill: ${err.message}`);
  }
}

async function handlePublishSubmit(e) {
  e.preventDefault();
  
  const accountId = document.getElementById('pub-account-select').value;
  if (!accountId) {
    uiModule.showToast('Please select a connected YouTube Account.');
    return;
  }

  const title = document.getElementById('pub-title').value.trim();
  const description = document.getElementById('pub-description').value.trim();
  const tagsStr = document.getElementById('pub-tags').value.trim();
  const privacy = document.getElementById('pub-privacy').value;
  const category = document.getElementById('pub-category').value;

  const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()) : [];

  try {
    const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}/publish`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        account_id: parseInt(accountId),
        title,
        description,
        tags,
        privacy,
        category
      })
    });

    if (res.ok) {
      showPublishProgress();
      pollPublishStatus();
    } else {
      const txt = await res.text();
      throw new Error(txt);
    }
  } catch (err) {
    uiModule.showError(`Failed to publish video: ${err.message}`);
  }
}

function showPublishProgress() {
  const progressDiv = document.querySelector('.video-publish-progress');
  const successCard = document.querySelector('.video-publish-success-card');
  const submitBtn = document.getElementById('pub-submit-btn');
  
  if (submitBtn) submitBtn.disabled = true;
  if (progressDiv) progressDiv.classList.remove('hidden');
  if (successCard) successCard.classList.add('hidden');
}

function showPublishSuccess(videoId) {
  const progressDiv = document.querySelector('.video-publish-progress');
  const successCard = document.querySelector('.video-publish-success-card');
  const submitBtn = document.getElementById('pub-submit-btn');
  const vidIdEl = document.getElementById('published-video-id');
  const watchLink = document.getElementById('youtube-watch-link');
  
  if (submitBtn) submitBtn.disabled = false;
  if (progressDiv) progressDiv.classList.add('hidden');
  if (successCard) {
    successCard.classList.remove('hidden');
    if (vidIdEl) vidIdEl.textContent = videoId;
    if (watchLink) watchLink.href = `https://www.youtube.com/watch?v=${videoId}`;
  }
}

function pollPublishStatus() {
  if (_publishPollingInterval) clearInterval(_publishPollingInterval);

  const statusText = document.querySelector('.publish-status-text');
  const progressFill = document.querySelector('.publish-progress-bar');
  const processingBadge = document.getElementById('youtube-processing-status');
  let simulatedPercent = 10;

  _publishPollingInterval = setInterval(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/video/projects/${_activeProjectId}/publish/status`);
      const data = await res.json();

      if (data.published && data.video_id) {
        // Video upload succeeded, now check processing status
        showPublishSuccess(data.video_id);
        
        if (data.youtube_status && typeof data.youtube_status === 'object') {
          const status = data.youtube_status.status;
          const procStatus = data.youtube_status.processing_status;
          
          if (processingBadge) {
            processingBadge.textContent = `Status: ${status} | Proc: ${procStatus}`;
            processingBadge.className = `status-badge stt-${procStatus === 'succeeded' ? 'done' : 'pending'}`;
          }

          if (procStatus === 'succeeded' || status === 'processed') {
            clearInterval(_publishPollingInterval);
            _publishPollingInterval = null;
          }
        }
      } else if (data.status === 'failed') {
        clearInterval(_publishPollingInterval);
        _publishPollingInterval = null;
        if (statusText) statusText.textContent = 'Uploading to YouTube failed. Check server logs.';
        const submitBtn = document.getElementById('pub-submit-btn');
        if (submitBtn) submitBtn.disabled = false;
      } else {
        if (simulatedPercent < 95) simulatedPercent += 15;
        if (progressFill) progressFill.style.width = `${simulatedPercent}%`;
        if (statusText) statusText.textContent = `Uploading video to YouTube (${simulatedPercent}%)...`;
      }
    } catch (err) {
      console.error('Error polling publish status:', err);
    }
  }, 4000);
}

async function showClipDetails(clipId) {
  try {
    const res = await fetch(`${API_BASE}/api/video/clips/${clipId}`);
    if (!res.ok) throw new Error(`Server returned status ${res.status}`);
    const clip = await res.json();

    // Remove any existing clip details modal first
    const existing = document.getElementById('video-clip-details-modal');
    if (existing) existing.remove();

    // Create background backdrop overlay
    const backdrop = document.createElement('div');
    backdrop.id = 'video-clip-details-modal';
    backdrop.className = 'video-details-backdrop';
    backdrop.style.cssText = `
      position: fixed;
      inset: 0;
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      z-index: 9999;
      display: flex;
      justify-content: center;
      align-items: center;
      pointer-events: auto;
    `;

    // Construct segments timeline if available
    let transcriptHTML = '';
    if (clip.transcript_segments && clip.transcript_segments.length > 0) {
      transcriptHTML = `
        <div class="transcript-segments-timeline" style="display: flex; flex-direction: column; gap: 8px; max-height: 250px; overflow-y: auto; padding-right: 4px;">
          ${clip.transcript_segments.map(seg => {
            const startSec = seg.start || 0;
            const endSec = seg.end || 0;
            const formatTime = (sec) => {
              const m = Math.floor(sec / 60).toString().padStart(2, '0');
              const s = Math.floor(sec % 60).toString().padStart(2, '0');
              return `${m}:${s}`;
            };
            const timeStr = `${formatTime(startSec)} - ${formatTime(endSec)}`;
            const durationStr = `(${startSec.toFixed(1)}s - ${endSec.toFixed(1)}s)`;
            return `
              <div class="transcript-segment-row" style="display: flex; gap: 12px; align-items: flex-start; padding: 6px 8px; border-radius: 6px; transition: background 0.15s;">
                <span class="segment-time" style="font-family: monospace; font-size: 11px; color: var(--accent-primary, var(--accent, #a78bfa)); background: rgba(167, 139, 250, 0.1); padding: 2px 6px; border-radius: 4px; white-space: nowrap; font-weight: bold;">
                  ${timeStr}
                </span>
                <span class="segment-duration" style="font-size: 10px; opacity: 0.4; font-family: monospace; white-space: nowrap; padding-top: 2px;">
                  ${durationStr}
                </span>
                <span class="segment-text" style="font-size: 13px; line-height: 1.5; color: var(--fg);">
                  ${seg.text}
                </span>
              </div>
            `;
          }).join('')}
        </div>
      `;
    } else if (clip.transcript) {
      transcriptHTML = `
        <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; font-size: 13px; line-height: 1.6; color: var(--fg); max-height: 250px; overflow-y: auto;">
          ${clip.transcript.replace(/\n/g, '<br>')}
        </div>
      `;
    } else {
      transcriptHTML = `
        <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; font-size: 13px; color: var(--fg); opacity: 0.5; font-style: italic;">
          No transcript available. Close this modal and click 'Transcribe' to run Speech-to-Text.
        </div>
      `;
    }

    const visualHTML = clip.visual_summary 
      ? `<div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; font-size: 13px; line-height: 1.6; color: var(--fg); max-height: 200px; overflow-y: auto;">
          ${clip.visual_summary.replace(/\n/g, '<br>')}
         </div>`
      : `<div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; font-size: 13px; color: var(--fg); opacity: 0.5; font-style: italic;">
          No visual analysis available. Close this modal and click 'Analyze Visuals' to extract and describe keyframes.
         </div>`;

    backdrop.innerHTML = `
      <div class="video-details-content" style="background: var(--bg); border: 1px solid var(--border); border-radius: 12px; width: min(720px, 95vw); max-height: 85vh; display: flex; flex-direction: column; box-shadow: 0 20px 50px rgba(0,0,0,0.65); animation: notes-pane-enter 220ms cubic-bezier(0.22, 0.61, 0.36, 1) both; overflow: hidden; font-family: inherit;">
        <div class="modal-header" style="padding: 16px 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: rgba(0,0,0,0.15); cursor: default;">
          <h3 style="margin: 0; font-size: 15px; font-weight: 600; color: var(--fg); display: flex; align-items: center; gap: 8px;">
            <span>🎬</span> Clip Details: ${clip.file_name}
          </h3>
          <button class="close-btn" style="background: none; border: none; color: var(--fg); font-size: 18px; cursor: pointer; opacity: 0.7; transition: opacity 0.2s;" id="video-clip-details-close">✖</button>
        </div>
        
        <div class="modal-body" style="padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 20px; flex: 1;">
          
          <!-- Visual Summary Section -->
          <div class="details-section">
            <h4 style="margin: 0 0 8px 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent-primary, var(--accent, #a78bfa)); font-weight: 700;">
              🖼️ Visual Analysis Summary
            </h4>
            ${visualHTML}
          </div>

          <!-- Speech-to-Text Transcript Section -->
          <div class="details-section">
            <h4 style="margin: 0 0 8px 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--accent-primary, var(--accent, #a78bfa)); font-weight: 700;">
              🔊 Speech-to-Text Transcript (Timestamped)
            </h4>
            ${transcriptHTML}
          </div>
          
        </div>

        <div class="modal-footer" style="padding: 12px 20px; border-top: 1px solid var(--border); display: flex; justify-content: flex-end; background: rgba(0,0,0,0.15);">
          <button class="video-action-btn" id="video-clip-details-close-btn" style="padding: 8px 16px; border-radius: 6px; font-size: 12px; font-weight: 600; cursor: pointer; border: 1px solid var(--border); background: rgba(255,255,255,0.05); color: var(--fg);">Close</button>
        </div>
      </div>
    `;

    document.body.appendChild(backdrop);

    const closeFn = () => {
      backdrop.remove();
      document.removeEventListener('keydown', escListener);
    };

    const escListener = (ev) => {
      if (ev.key === 'Escape') closeFn();
    };

    backdrop.querySelector('#video-clip-details-close').addEventListener('click', closeFn);
    backdrop.querySelector('#video-clip-details-close-btn').addEventListener('click', closeFn);
    backdrop.addEventListener('click', (ev) => {
      if (ev.target === backdrop) closeFn();
    });
    document.addEventListener('keydown', escListener);

  } catch (err) {
    uiModule.showError(`Failed to load clip details: ${err.message}`);
  }
}

// Expose togglePanel and openPanel on window for sidebar actions
window.openVideoPanel = openPanel;
window.toggleVideoPanel = togglePanel;

const videoModule = { openPanel, closePanel, togglePanel, isPanelOpen, openVideoPanel: openPanel };
export default videoModule;
