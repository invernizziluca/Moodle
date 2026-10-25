"use strict";
const jobId = location.hash.slice(1);
const status = document.getElementById("status");
const bar = document.getElementById("progress");
let cancelled = false;
let activeDownload = null;
let running = false;

function checkCancel() { if (cancelled) throw new Error("Download interrotto. Nessuna playlist completa pubblicata."); }
async function allowed(url) {
  const safe = FFHS_HLS.safeUrl(url);
  const origin = new URL(safe).origin + "/*";
  if (!await chrome.permissions.contains({origins: [origin]})) {
    throw new Error("Permesso mancante per il server del player. Torna nell’assistente, "
      + "premi Rileva player incorporati, autorizza e riprova.");
  }
  return safe;
}
async function manifest(url) {
  checkCancel();
  const response = await fetch(await allowed(url), {credentials: "include", cache: "no-store"});
  if (!response.ok) throw new Error("Il player ha rifiutato il manifest (HTTP " + response.status + "). Riapri il video in Moodle.");
  await allowed(response.url || url);
  return FFHS_HLS.parse(await response.text(), response.url || url);
}
async function leaf(url, seen = new Set()) {
  if (seen.has(url) || seen.size > 4) throw new Error("Manifest annidato/ciclico non supportato.");
  seen.add(url);
  const plan = await manifest(url);
  if (plan.kind === "media") return plan;
  if (plan.audio) throw new Error("Master annidato con audio esterno non supportato.");
  return leaf(plan.video, seen);
}
async function waitDownload(id) {
  return new Promise((resolve, reject) => {
    let settled = false;
    function cleanup() { chrome.downloads.onChanged.removeListener(changed); clearInterval(timer); }
    function finish(error) { if (settled) return; settled = true; cleanup(); error ? reject(error) : resolve(); }
    function changed(delta) {
      if (delta.id !== id) return;
      if (delta.state?.current === "complete") finish();
      if (delta.state?.current === "interrupted") finish(new Error("Download di un segmento fallito. Controlla Ctrl+J e riavvia il video."));
    }
    const timer = setInterval(() => { if (cancelled) finish(new Error("Download interrotto.")); }, 250);
    chrome.downloads.onChanged.addListener(changed);
    // Handles a small file completing before the listener was attached.
    chrome.downloads.search({id}).then(results => {
      if (results[0]?.state === "complete") finish();
      if (results[0]?.state === "interrupted") finish(new Error("Download di un segmento fallito."));
    }, () => finish(new Error("Impossibile verificare il download.")));
  });
}
async function save(url, filename, localBlob = false) {
  checkCancel();
  if (!localBlob) await allowed(url);
  activeDownload = await chrome.downloads.download({url, filename, saveAs: false, conflictAction: "overwrite"});
  await waitDownload(activeDownload);
  activeDownload = null;
  checkCancel();
}
async function savePlaylist(text, filename) {
  const url = URL.createObjectURL(new Blob([text], {type: "application/vnd.apple.mpegurl"}));
  try { await save(url, filename, true); } finally { URL.revokeObjectURL(url); }
}
async function run() {
  if (running) return;
  running = true; cancelled = false;
  document.getElementById("start").disabled = true;
  document.getElementById("stop").disabled = false;
  try {
    if (!/^[\da-f-]{36}$/i.test(jobId)) throw new Error("Richiesta scaduta: riapri l’assistente dal corso Moodle.");
    const key = "job_" + jobId;
    const job = (await chrome.storage.session.get(key))[key];
    if (!job || Date.now() - job.createdAt > 30 * 60 * 1000) {
      await chrome.storage.session.remove(key);
      throw new Error("Richiesta scaduta: riproduci il video e riprova.");
    }
    status.textContent = "Lettura delle qualità e delle tracce…";
    const first = await manifest(job.url);
    const video = first.kind === "media" ? first : await leaf(first.video);
    const audio = first.kind === "master" && first.audio ? await leaf(first.audio) : null;
    const folder = "FFHS_Moodle/" + job.context + "/" + job.label.slice(0, 30) + "_hls_" + jobId.slice(0, 8);
    const plans = [{plan: video, dir: audio ? "v/" : ""}];
    if (audio) plans.push({plan: audio, dir: "a/"});
    const total = plans.reduce((sum, p) => sum + p.plan.entries.length, 0);
    bar.max = total; bar.value = 0;
    for (const {plan, dir} of plans) {
      for (const segment of plan.entries) {
        status.textContent = "Download segmento " + (bar.value + 1) + " di " + total + "…";
        await save(segment.url, folder + "/" + dir + segment.name);
        bar.value += 1;
      }
    }
    // Only publish playable manifests after every segment has completed.
    if (audio) {
      await savePlaylist(video.playlist, folder + "/v/list.m3u8");
      await savePlaylist(audio.playlist, folder + "/a/list.m3u8");
      await savePlaylist('#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Audio",DEFAULT=YES,AUTOSELECT=YES,URI="a/list.m3u8"\n'
        + '#EXT-X-STREAM-INF:BANDWIDTH=1,AUDIO="audio"\nv/list.m3u8\n', folder + "/video.m3u8");
    } else await savePlaylist(video.playlist, folder + "/video.m3u8");
    await chrome.storage.session.remove(key);
    status.textContent = "Completato: Download/" + folder + "/video.m3u8. Apri con VLC o importa l’intera cartella nell’archivio.";
  } catch (error) {
    // Error text is controlled; never display URLs or provider response bodies.
    status.textContent = error instanceof TypeError || /https?:\/\/|token=|signature=|credential/i.test(error.message || "")
      ? "Connessione al player non riuscita. Riproduci di nuovo il video in Moodle."
      : error.message;
  } finally {
    running = false;
    document.getElementById("start").disabled = false;
    document.getElementById("stop").disabled = true;
  }
}
document.getElementById("start").addEventListener("click", run);
document.getElementById("stop").addEventListener("click", () => {
  cancelled = true;
  if (activeDownload !== null) chrome.downloads.cancel(activeDownload).catch(() => {});
});
