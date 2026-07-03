chrome.runtime.onInstalled.addListener(() => {
  console.log('[OutlierTracker] Extension installed.');
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'FETCH_HEALTH') {
    fetch('https://youtube-outlier-tracker-production.up.railway.app/api/health')
      .then(r => r.json())
      .then(data => sendResponse({ ok: true, data }))
      .catch(err => sendResponse({ ok: false, error: err.message }));
    return true;
  }
});