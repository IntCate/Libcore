import { consumeRender } from "../render/consumer.js";

const listeners = new Set();

/** 订阅一条原始下行消息（当前仅默认连接使用；保留扩展位）。 */
export function onDownstream(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** 连接后端 WebSocket 下行通道，收到渲染消息 -> consumeRender。自动断线重连。 */
export function connectEvents() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const url = `${proto}://${window.location.host}/events`;

  let ws;
  let retry = 1000;

  const connect = () => {
    ws = new WebSocket(url);

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        consumeRender(msg);
        listeners.forEach((fn) => fn(msg));
      } catch {
        // 非渲染消息，忽略
      }
    };

    ws.onclose = () => {
      // 断线重连（退避封顶）
      setTimeout(connect, Math.min(retry, 10000));
      retry = Math.min(retry * 2, 10000);
    };

    ws.onopen = () => {
      retry = 1000; // 连接成功，重置退避
    };
  };

  connect();

  return () => {
    try {
      ws?.close();
    } catch {}
  };
}