"use strict";

// URLs may carry session signatures. Keep them in extension session storage,
// never in console logs, exported metadata, sync storage or a remote service.
const LIMIT = 60;
const queues = new Map();

function moodlePage(raw) {
  try {
    const url = new URL(raw);
    if (url.protocol !== "https:" || url.hostname !== "moodle.ffhs.ch" || url.port) return null;
    const id = url.searchParams.get("id");
    const context = /^\/mod\/[^/]+\/view\.php$/.test(url.pathname) && /^\d+$/.test(id || "")
      ? "cm_" + id : url.pathname === "/course/view.php" && /^\d+$/.test(id || "")
      ? "course_" + id : "moodle";
    return { key: url.origin + url.pathname + (id ? "?id=" + encodeURIComponent(id) : ""), context };
  } catch { return null; }
}

function excludedHost(host) {
  return ["youtube.com", "youtube-nocookie.com", "youtu.be", "googlevideo.com", "ytimg.com"]
    .some(domain => host === domain || host.endsWith("." + domain));
}

function classify(raw, headers = []) {
  let url;
  try { url = new URL(raw); } catch { return null; }
  if (url.protocol !== "https:" || url.username || url.password || excludedHost(url.hostname)) return null;
  let path;
  try { path = decodeURIComponent(url.pathname); } catch { path = url.pathname; }
  // Do not offer individual HLS/DASH segments as complete videos.
  if (/\.(?:ts|m4s|m4a\.part|vtt|srt|key)$/i.test(path)) return null;
  const basename = path.split("/").pop();
  if (/\.mp4$/i.test(basename) && /^(?:init(?:ialization)?[-_.]|seg(?:ment)?\d*[-_]|frag(?:ment)?\d*[-_]|chunk\d*[-_])/i.test(basename)) return null;
  const mime = (headers.find(h => h.name.toLowerCase() === "content-type")?.value || "")
    .split(";", 1)[0].trim().toLowerCase();
  if (/\.(?:m3u8|mpd)$/i.test(path) || /mpegurl|dash\+xml/.test(mime)) {
    return { kind: "stream", ext: "", mime,
      streamType: /\.mpd$/i.test(path) || mime.includes("dash+xml") ? "dash" : "hls" };
  }
  if (mime === "video/mp2t") return null;
  const suffix = path.match(/\.(mp4|webm|mov|m4v|ogv|mp3|m4a|ogg|wav|flac)$/i)?.[1]?.toLowerCase();
  if (!suffix && !/^(video|audio)\//.test(mime)) return null;
  const byMime = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
    "video/ogg": "ogv", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/ogg": "ogg",
    "audio/wav": "wav", "audio/flac": "flac"};
  // Unknown MIME/container gets no invented .mp4 extension.
  const ext = suffix || byMime[mime];
  return ext ? {kind: "file", ext, mime} : null;
}

function shortName(raw) {
  return (raw || "video").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-zA-Z0-9_-]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 38) || "video";
}

function keyFor(tabId) { return "tab_" + tabId; }
function serial(tabId, action) {
  const result = (queues.get(tabId) || Promise.resolve()).catch(() => {}).then(action);
  queues.set(tabId, result);
  result.finally(() => { if (queues.get(tabId) === result) queues.delete(tabId); }).catch(() => {});
  return result;
}

async function capture(details) {
  if (details.tabId < 0 || details.statusCode < 200 || details.statusCode >= 300) return;
  const media = classify(details.url, details.responseHeaders);
  if (!media) return;
  return serial(details.tabId, async () => {
    let tab;
    try { tab = await chrome.tabs.get(details.tabId); } catch { return; }
    const page = moodlePage(tab.url);
    if (!page) return;
    const key = keyFor(details.tabId);
    const stored = (await chrome.storage.session.get(key))[key];
    const state = stored?.page === page.key ? stored : {page: page.key, context: page.context, items: []};
    if (state.items.some(item => item.url === details.url)) return;
    const url = new URL(details.url);
    const id = crypto.randomUUID();
    state.items.push({id, url: details.url, kind: media.kind, ext: media.ext, streamType: media.streamType,
      host: url.hostname,
      label: shortName(tab.title), filename: "FFHS_Moodle/" + page.context + "/" + shortName(tab.title)
        + "_" + id.slice(0, 8) + (media.ext ? "." + media.ext : "")});
    state.items = state.items.slice(-LIMIT);
    await chrome.storage.session.set({[key]: state});
  });
}

async function handle(message, sender) {
  // Only our popup may request a download. Web pages/content scripts cannot.
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL("popup.html")) {
    throw new Error("Richiesta non consentita");
  }
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  const page = moodlePage(tab?.url);
  if (!page) throw new Error("Apri una scheda Moodle FFHS e avvia il video.");
  return serial(tab.id, async () => {
    const key = keyFor(tab.id);
    const state = (await chrome.storage.session.get(key))[key];
    const items = state?.page === page.key ? state.items : [];
    if (message.type === "list") {
      // Do not expose signed URLs to popup DOM or clipboard.
      return {items: items.map(({id, kind, ext, host, label, streamType}) => ({id, kind, ext, host, label, streamType})),
        context: page.context};
    }
    if (message.type === "clear") { await chrome.storage.session.remove(key); return {ok: true}; }
    if (message.type === "hls") {
      const item = items.find(candidate => candidate.id === message.id);
      if (!item || item.kind !== "stream" || item.streamType !== "hls") {
        throw new Error("Manifest HLS non disponibile in questa scheda");
      }
      const jobId = crypto.randomUUID();
      await chrome.storage.session.set({["job_" + jobId]: {url: item.url, context: state.context,
        label: item.label, createdAt: Date.now()}});
      await chrome.tabs.create({url: chrome.runtime.getURL("download.html") + "#" + jobId});
      return {ok: true};
    }
    if (message.type !== "download") throw new Error("Comando sconosciuto");
    const item = items.find(candidate => candidate.id === message.id);
    if (!item || item.kind !== "file" || classify(item.url)?.kind !== "file" && !item.ext) {
      throw new Error("File non disponibile in questa scheda: riproduci di nuovo il video.");
    }
    const downloadId = await chrome.downloads.download({url: item.url, filename: item.filename,
      saveAs: false, conflictAction: "uniquify"});
    return {ok: true, downloadId, filename: item.filename};
  });
}

chrome.webRequest.onHeadersReceived.addListener(details => { capture(details).catch(() => {}); },
  {urls: ["https://*/*"]}, ["responseHeaders"]);
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (change.status === "loading" || change.url) {
    serial(tabId, () => chrome.storage.session.remove(keyFor(tabId))).catch(() => {});
  }
});
chrome.tabs.onRemoved.addListener(tabId => {
  serial(tabId, () => chrome.storage.session.remove(keyFor(tabId))).catch(() => {});
});
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  handle(message, sender).then(respond, () => respond({error: "Operazione non riuscita. Apri Moodle, "
    + "riproduci il video e riprova; controlla anche i download del browser."}));
  return true;
});
