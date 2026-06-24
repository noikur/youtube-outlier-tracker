/**
 * popup.js -- runs when the user clicks the extension icon.
 * Calls the local API health endpoint and renders live stats.
 */

const API_BASE = 'http://localhost:8000';

async function updatePopup() {
  const dot  = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  const stats = document.getElementById('stats');
  const instructions = document.getElementById('instructions');

  try {
    const res = await fetch(`${API_BASE}/api/health`, { signal: AbortSignal.timeout(2000) });
    if (!res.ok) throw new Error('not ok');

    const data = await res.json();

    dot.className = 'dot dot--green';
    text.textContent = 'Backend running';
    stats.style.display = 'flex';
    instructions.style.display = 'none';

    document.getElementById('stat-channels').textContent =
      data.tracked_channels ?? '—';

    // Fetch outlier count separately from the DB via a second endpoint
    // For now, show a dash -- add /api/stats endpoint later if wanted
    document.getElementById('stat-outliers').textContent = '—';

  } catch {
    dot.className = 'dot dot--red';
    text.textContent = 'Backend not running';
    stats.style.display = 'none';
    instructions.style.display = 'block';
  }
}

updatePopup();
