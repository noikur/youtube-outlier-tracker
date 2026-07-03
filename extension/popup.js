async function updatePopup() {
  const pill    = document.getElementById('status-pill');
  const dot     = document.getElementById('status-dot');
  const text    = document.getElementById('status-text');
  const stats   = document.getElementById('stats');
  const offline = document.getElementById('offline-msg');
  const scan    = document.getElementById('last-scan');

  try {
    const result = await new Promise((resolve, reject) => {
      chrome.runtime.sendMessage({ type: 'FETCH_HEALTH' }, response => {
        if (chrome.runtime.lastError) reject(new Error(chrome.runtime.lastError.message));
        else resolve(response);
      });
    });

    if (!result.ok) throw new Error(result.error);
    const data = result.data;

    pill.className = 'status-pill status-online';
    text.textContent = 'Live';
    stats.style.display = 'grid';
    offline.style.display = 'none';

    document.getElementById('stat-channels').textContent =
      data.tracked_channels ?? '—';
    document.getElementById('stat-outliers').textContent =
      data.outliers_logged ?? '—';
    scan.textContent = 'Updated just now';
    document.getElementById('dash-link').href =
      'https://youtube-outlier-tracker-production.up.railway.app/dashboard';

  } catch {
    pill.className = 'status-pill status-offline';
    text.textContent = 'Offline';
    stats.style.display = 'none';
    offline.style.display = 'block';
    scan.textContent = '';
  }
}

updatePopup();