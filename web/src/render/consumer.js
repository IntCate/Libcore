import { useRenderStore } from "./renderStore.js";

export function consumeRender(msg) {
  const store = useRenderStore.getState();
  const { type, target, payload = {} } = msg;

  switch (type) {
    case "render":
      return handleRender(target, payload, store);
    case "code":
      return handleCode(target, payload, store);
    case "data":
      return handleData(target, payload, store);
    case "close":
      return handleClose(target, store);
    default:
      return { handled: false, action: "unknown", target };
  }
}

function handleRender(target, payload, store) {
  const { component, props = {}, children = [], events = {} } = payload;
  store.upsertContainer(target, { component, props, children, events });
  return { handled: true, action: "render", target, component };
}

function handleCode(target, payload, store) {
  const { lang, source, events = {} } = payload;
  store.upsertContainer(target, {
    component: null,
    code: { lang, source },
    events,
  });
  return { handled: true, action: "code", target, lang };
}

function handleData(target, payload, store) {
  const { data } = payload;
  store.appendData(target, data);
  return { handled: true, action: "data", target };
}

function handleClose(target, store) {
  store.removeContainer(target);
  return { handled: true, action: "close", target };
}
