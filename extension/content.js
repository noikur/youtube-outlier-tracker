/**
 * content.js -- runs on every YouTube page.
 *
 * WHAT IT DOES
 * ------------
 * 1. Watches the page for video card elements being added to the DOM
 *    (YouTube is a single-page app -- new cards appear constantly as
 *    you scroll, and navigation between pages doesn't reload the page).
 * 2. Extracts video IDs from thumbnail links in those cards.
 * 3. Batches them and sends to the local Python API (localhost:8000).
 * 4. Injects a small badge onto the thumbnail of any outlier video.
 *
 * YOUTUBE'S DOM STRUCTURE
 * -----------------------
 * YouTube uses custom HTML elements for different contexts:
 *   ytd-rich-item-renderer    -> homepage video cards
 *   ytd-video-renderer        -> search results
 *   ytd-compact-video-renderer -> sidebar recommendations
 *   ytd-grid-video-renderer   -> channel upload grids
 *
 * Within each, the thumbnail link is an <a> tag with id="thumbnail"
 * whose href contains ?v=VIDEO_ID.
 *
 * GRACEFUL DEGRADATION
 * --------------------
 * If the local API isn't running, fetch() throws a network error.
 * We catch it silently so YouTube works completely normally -- the
 * extension adds nothing and breaks nothing when the server is off.
 */

const API_BASE = 'https://youtube-outlier-tracker-production.up.railway.app';
const PROCESSED_ATTR = 'data-outlier-checked';
const BADGE_CLASS = 'yt-outlier-badge';

const CARD_SELECTORS = [
  '[class*="content-id-"]',
  'ytd-rich-item-renderer',
  'ytd-video-renderer',
  'ytd-compact-video-renderer',
  'ytd-grid-video-renderer',
];

function extractVideoId(href) {
  if (!href) return null;
  try {
    const url = new URL(href);
    const v = url.searchParams.get('v');
    if (v) return v;
    const shorts = url.pathname.match(/\/shorts\/([a-zA-Z0-9_-]{11})/);
    if (shorts) return shorts[1];
    return null;
  } catch {
    return null;
  }
}

function getVideoIdFromElement(el) {
  const classMatch = el.className?.match?.(/content-id-([a-zA-Z0-9_-]{11})/);
  if (classMatch) return classMatch[1];
  const link = el.querySelector('a#thumbnail, ytd-thumbnail a');
  return link ? extractVideoId(link.href) : null;
}

function getThumbnailContainer(card) {
  return card.querySelector('ytd-thumbnail #thumbnail, a#thumbnail, yt-image, img');
}

function injectBadge(card, scoreData) {
  if (!scoreData.is_outlier || !scoreData.badge_text) return;

  const existing = card.querySelector(`.${BADGE_CLASS}`);
  if (existing) existing.remove();

  const badge = document.createElement('div');
  badge.className = BADGE_CLASS;

  if (scoreData.multiplier >= 10) {
    badge.classList.add('yt-outlier-badge--fire');
  } else if (scoreData.multiplier >= 3) {
    badge.classList.add('yt-outlier-badge--spark');
  } else {
    badge.classList.add('yt-outlier-badge--low');
  }

  badge.textContent = `${scoreData.multiplier}x`;
  badge.title = `${scoreData.channel_title}: ${scoreData.multiplier}x normal\nz=${scoreData.z_score}`;

  const thumbnail = card.querySelector('yt-image, img, ytd-thumbnail, a#thumbnail');
  const target = thumbnail || card;
  target.style.position = 'relative';
  target.style.overflow = 'visible';
  target.appendChild(badge);
}

async function fetchScores(videoIdToCard) {
  const videoIds = Array.from(videoIdToCard.keys());
  if (videoIds.length === 0) return;

  try {
    const res = await fetch(`${API_BASE}/api/score`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_ids: videoIds }),
    });

    if (!res.ok) return;

    const data = await res.json();
    for (const [videoId, scoreData] of Object.entries(data.results || {})) {
      const card = videoIdToCard.get(videoId);
      if (card) injectBadge(card, scoreData);
    }
  } catch {
    // Backend not running -- fail silently
  }
}

let pendingCards = [];
let debounceTimer = null;

function queueCards(cards) {
  pendingCards.push(...cards);
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    const batch = pendingCards.splice(0);
    if (batch.length === 0) return;

    const videoIdToCard = new Map();
    for (const card of batch) {
      const videoId = getVideoIdFromElement(card);
      if (videoId) videoIdToCard.set(videoId, card);
    }

    fetchScores(videoIdToCard);
  }, 600);
}

function findUnprocessedCards() {
  const cards = [];
  for (const sel of CARD_SELECTORS) {
    document.querySelectorAll(`${sel}:not([${PROCESSED_ATTR}])`).forEach(el => {
      el.setAttribute(PROCESSED_ATTR, 'true');
      cards.push(el);
    });
  }
  return cards;
}

const observer = new MutationObserver((mutations) => {
  const newCards = [];
  for (const mutation of mutations) {
    for (const node of mutation.addedNodes) {
      if (node.nodeType !== Node.ELEMENT_NODE) continue;
      for (const sel of CARD_SELECTORS) {
        if (node.matches?.(sel) && !node.hasAttribute(PROCESSED_ATTR)) {
          node.setAttribute(PROCESSED_ATTR, 'true');
          newCards.push(node);
        }
        node.querySelectorAll?.(`${sel}:not([${PROCESSED_ATTR}])`).forEach(el => {
          el.setAttribute(PROCESSED_ATTR, 'true');
          newCards.push(el);
        });
      }
    }
  }
  if (newCards.length > 0) queueCards(newCards);
});

observer.observe(document.body, { childList: true, subtree: true });

document.addEventListener('yt-navigate-finish', () => {
  setTimeout(() => queueCards(findUnprocessedCards()), 800);
});

queueCards(findUnprocessedCards());