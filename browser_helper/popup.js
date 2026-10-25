"use strict";
const status = document.getElementById("status");
async function refresh() {
  const response = await chrome.runtime.sendMessage({type: "list"});
  document.getElementById("items").replaceChildren();
  if (response.error) { status.textContent = response.error; return; }
  status.textContent = response.items.length ? "File/player rilevati: " + response.items.length
    : "Nessun media rilevato. Riproduci il video e premi Aggiorna elenco.";
  for (const media of response.items) {
    const row = document.createElement("li");
    const label = document.createElement("span");
    label.textContent = media.label + " — " + media.host;
    row.append(label);
    if (media.kind === "stream") {
      const note = document.createElement("small");
      note.textContent = "Streaming: HLS VOD non cifrato può essere salvato con i segmenti per VLC. "
        + "DASH, contenuti live e protetti non sono supportati.";
      row.append(note);
      if (media.streamType === "hls") {
        const button = document.createElement("button");
        button.textContent = "Scarica streaming HLS";
        button.addEventListener("click", async () => {
          const result = await chrome.runtime.sendMessage({type: "hls", id: media.id});
          if (result.error) status.textContent = result.error;
        });
        row.append(button);
      }
    } else {
      const button = document.createElement("button");
      button.textContent = "Scarica file rilevato " + media.ext.toUpperCase();
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await chrome.runtime.sendMessage({type: "download", id: media.id});
          status.textContent = result.error || "Download avviato: " + result.filename
            + ". Verifica il completamento nei download del browser.";
        } catch { status.textContent = "Download non avviato. Riapri l’assistente e riprova."; }
        finally { button.disabled = false; }
      });
      row.append(document.createElement("br"), button);
    }
    document.getElementById("items").append(row);
  }
}
document.getElementById("refresh").addEventListener("click", () => refresh().catch(() => {
  status.textContent = "Apri una scheda Moodle e riprova.";
}));
document.getElementById("clear").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({type: "clear"}); await refresh();
});
document.getElementById("embedded").addEventListener("click", async () => {
  try {
    const granted = await chrome.permissions.request({origins: ["https://*/*"]});
    status.textContent = granted ? "Accesso concesso. Ricarica l’attività Moodle, avvia il video e aggiorna l’elenco."
      : "Accesso non concesso: restano rilevabili i file sul dominio Moodle.";
  } catch { status.textContent = "Permesso non concesso. Puoi continuare con i file sul dominio Moodle."; }
});
refresh().catch(() => { status.textContent = "Apri una scheda Moodle FFHS e riprova."; });
