import { list, onChange } from "../render/registry.js";

let timer = null;

export function reportManifest() {
  const manifest = { components: list() };
  fetch("/api/manifest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(manifest),
  }).catch(() => {});
}

export function watchManifest() {
  onChange(() => {
    clearTimeout(timer);
    timer = setTimeout(reportManifest, 300);
  });
}
