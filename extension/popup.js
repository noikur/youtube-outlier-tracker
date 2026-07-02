const API_BASE = 'https://youtube-outlier-tracker-production.up.railway.app';

async function updatePopup() {
  const pill    = document.getElementById('status-pill');
  const dot     = document.getElementById('status-dot');
  const text    = document.getElementById('status-text');
  const stats   = document.getElementById('stats');
  const offline = document.getElementById('offline-msg');
  const scan    = document.getElementById('last-scan');

  try {
    const res = await fetch(`${API_BASE}/api/health`, {
      signal: AbortSignal.timeout(3000)
    });
    if (!res.ok) throw new Error();
    const data = await res.json();

    pill.className = 'status-pill status-online';
    dot.className  = 'status-dot';
    text.textContent = 'Live';
    stats.style.display = 'grid';
    offline.style.display = 'none';

    document.getElementById('stat-channels').textContent =
      data.tracked_channels ?? '—';
    document.getElementById('stat-outliers').textContent =
      data.outliers_logged ?? '—';
    scan.textContent = 'Updated just now';
    document.getElementById('dash-link').href = 'https://youtube-outlier-tracker-production.up.railway.app/dashboard';

  } catch {
    pill.className = 'status-pill status-offline';
    dot.className  = 'status-dot';
    text.textContent = 'Offline';
    stats.style.display = 'none';
    offline.style.display = 'block';
    scan.textContent = '';
  }
}

updatePopup();