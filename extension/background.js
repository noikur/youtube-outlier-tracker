/**
 * background.js -- Manifest V3 service worker.
 */

chrome.runtime.onInstalled.addListener(() => {
  console.log('[OutlierTracker] Extension installed. Start the local API server with: python run_api.py');
});
