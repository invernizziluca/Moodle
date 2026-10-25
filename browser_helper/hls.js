"use strict";

// Pure parsing/planning, also exercised by the offline regression tests.
(() => {
  function attributes(text) {
    const out = {};
    const re = /([A-Z0-9-]+)=(?:"([^"]*)"|([^,]*))/g;
    let match;
    while ((match = re.exec(text))) out[match[1]] = match[2] ?? match[3];
    return out;
  }
  function safeUrl(raw, base) {
    const url = new URL(raw, base);
    const host = url.hostname.toLowerCase();
    if (url.protocol !== "https:" || url.username || url.password || url.port && url.port !== "443"
        || host === "localhost" || host.endsWith(".localhost") || !host.includes(".")
        || /^\d+(?:\.\d+){3}$/.test(host) || host.includes(":")) {
      throw new Error("Il manifest contiene una destinazione non consentita.");
    }
    if (["youtube.com", "youtube-nocookie.com", "youtu.be", "googlevideo.com", "ytimg.com"]
      .some(domain => host === domain || host.endsWith("." + domain))) {
      throw new Error("I video YouTube vengono conservati soltanto come link.");
    }
    return url.href;
  }
  function parse(text, base) {
    if (text.length > 2 * 1024 * 1024) throw new Error("Manifest troppo grande.");
    const lines = text.replace(/^\uFEFF/, "").split(/\r?\n/).map(line => line.trim()).filter(Boolean);
    if (lines[0] !== "#EXTM3U") throw new Error("Il server non ha restituito un manifest HLS.");
    for (const line of lines) {
      if (/^#EXT-X-(?:SESSION-)?KEY:/.test(line) && attributes(line).METHOD !== "NONE") {
        throw new Error("Streaming cifrato o DRM: non viene scaricato da questo assistente.");
      }
      if (/^#EXT-X-BYTERANGE:|^#EXT-X-PART:/.test(line)) {
        throw new Error("Streaming con intervalli di byte/parti non supportato.");
      }
      if (line.includes("URI=") && !/^#EXT-X-(?:MAP|MEDIA|KEY|SESSION-KEY):/.test(line)) {
        throw new Error("Manifest con riferimenti aggiuntivi non supportati: nessuna playlist offline pubblicata.");
      }
    }
    const variants = [];
    for (let i = 0; i < lines.length; i++) {
      if (!lines[i].startsWith("#EXT-X-STREAM-INF:")) continue;
      const attrs = attributes(lines[i]);
      const next = lines[i + 1];
      if (!next || next.startsWith("#")) throw new Error("Manifest master incompleto.");
      variants.push({url: safeUrl(next, base), bandwidth: Number(attrs.BANDWIDTH) || 0, audio: attrs.AUDIO});
    }
    if (variants.length) {
      variants.sort((a, b) => b.bandwidth - a.bandwidth);
      const chosen = variants[0];
      const audio = lines.filter(line => line.startsWith("#EXT-X-MEDIA:"))
        .map(attributes).filter(a => a.TYPE === "AUDIO" && a["GROUP-ID"] === chosen.audio && a.URI);
      audio.sort((a, b) => Number(b.DEFAULT === "YES") - Number(a.DEFAULT === "YES"));
      return {kind: "master", video: chosen.url, audio: audio.length ? safeUrl(audio[0].URI, base) : null};
    }
    if (!lines.includes("#EXT-X-ENDLIST")) throw new Error("Streaming live/aperto: attendi una registrazione VOD conclusa.");
    const entries = [];
    const local = [];
    let expectsSegment = false;
    for (const line of lines) {
      if (line.includes("URI=") && !/^#EXT-X-(?:MAP|KEY):/.test(line)) {
        throw new Error("Playlist media con riferimenti aggiuntivi non supportati.");
      }
      if (line.startsWith("#EXT-X-MAP:")) {
        const attrs = attributes(line);
        if (attrs.BYTERANGE) throw new Error("Inizializzazione con intervalli di byte non supportata.");
        if (!attrs.URI) throw new Error("Inizializzazione HLS mancante.");
        const name = "init" + entries.length + ".mp4";
        entries.push({url: safeUrl(attrs.URI, base), name});
        local.push('#EXT-X-MAP:URI="' + name + '"');
      } else if (line.startsWith("#EXTINF:")) {
        expectsSegment = true; local.push(line);
      } else if (!line.startsWith("#")) {
        if (!expectsSegment) throw new Error("Segmento HLS senza durata.");
        const url = safeUrl(line, base);
        const ext = new URL(url).pathname.match(/\.(ts|m4s|mp4|aac|webm)$/i)?.[1]?.toLowerCase() || "bin";
        const name = "s" + String(entries.length).padStart(6, "0") + "." + ext;
        entries.push({url, name}); local.push(name); expectsSegment = false;
      } else if (/^#EXTM3U$|^#EXT-X-(?:VERSION|TARGETDURATION|MEDIA-SEQUENCE|DISCONTINUITY-SEQUENCE|DISCONTINUITY|ENDLIST|PLAYLIST-TYPE|INDEPENDENT-SEGMENTS|PROGRAM-DATE-TIME|GAP|BITRATE)(?::|$)/.test(line)) local.push(line);
    }
    if (expectsSegment || !entries.length || !lines.some(line => line.startsWith("#EXTINF:"))) {
      throw new Error("Manifest senza segmenti video completi.");
    }
    if (entries.length > 10000) throw new Error("Troppi segmenti nel manifest.");
    return {kind: "media", entries, playlist: local.join("\n") + "\n"};
  }
  globalThis.FFHS_HLS = {attributes, safeUrl, parse};
})();
