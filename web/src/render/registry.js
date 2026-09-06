import { lazy } from "react";

const registry = new Map();
const listeners = new Set();

export function register(name, loader) {
  registry.set(name, loader);
  notifyChange();
}

export function unregister(name) {
  registry.delete(name);
  notifyChange();
}

export function resolve(name) {
  const loader = registry.get(name);
  return loader ? lazy(loader) : null;
}

export function list() {
  return Array.from(registry.keys());
}

export function onChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notifyChange() {
  const manifest = list();
  listeners.forEach((fn) => fn(manifest));
}
