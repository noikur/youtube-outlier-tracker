/**
 * background.js -- Manifest V3 service worker.
 *
 * Minimal for now. In MV3, extensions require a service worker even if
 * it does very little. All the real work happens in content.js (which
 * runs on YouTube pages) and the local Python API server.
 *
 * This is where you'd add things like scheduled badge refresh, cross-tab
 * state syncing, or notifications when outliers are detected -- all future
 * extensions of the current architecture.
 */

chrome.runtime.onInstalled.addListener(() => {
  console.log('[OutlierTracker] Extension installed. Start the local API server with: python run_api.py');
});
