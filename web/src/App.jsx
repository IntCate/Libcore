import { useState } from "react";
import { RenderHost, consumeRender } from "./render/index.js";

export function App() {
  const [log, setLog] = useState([]);

  const handleEvent = async (target, action, e) => {
    // 事件回传：前端 -> POST /api/rpc -> 后端 bus.dispatch（保留本地日志便于对照）
    const rpc = {
      rpcId: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      target: action.target,
      op: action.op,
      payload: { ...(action.payload || {}), event: e, source: target },
    };
    let upstream = { pending: true };
    try {
      const resp = await fetch("/api/rpc", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(rpc),
      });
      upstream = await resp.json();
    } catch (err) {
      upstream = { ok: false, error: String(err) };
    }
    setLog((l) => [...l, { target, action, event: e, upstream }]);
  };

  const demo = (type) => {
    const msgs = {
      render: {
        v: 1,
        type: "render",
        target: "/root/chat",
        payload: {
          component: "chat",
          props: { title: "对话" },
          events: { onSend: { target: "run", op: "run", payload: {} } },
        },
      },
      data: {
        v: 1,
        type: "data",
        target: "/root/chat",
        payload: { data: { role: "agent", delta: "你好，我是 agent" } },
      },
      code: {
        v: 1,
        type: "code",
        target: "/ext/pet",
        payload: {
          lang: "jsx",
          source: `() => <div style={{padding:8,border:'1px solid #ccc'}}>桌面宠物 🐱</div>`,
        },
      },
      close: { v: 1, type: "close", target: "/ext/pet", payload: {} },
    };
    consumeRender(msgs[type]);
  };

  return (
    <div className="app">
      <div className="toolbar">
        <button onClick={() => demo("render")}>render chat</button>
        <button onClick={() => demo("data")}>data 追加</button>
        <button onClick={() => demo("code")}>code 宠物</button>
        <button onClick={() => demo("close")}>close 宠物</button>
      </div>
      <RenderHost onEvent={handleEvent} />
      <div className="event-log">
        <h4>事件回传日志</h4>
        {log.map((l, i) => (
          <pre key={i}>{JSON.stringify(l)}</pre>
        ))}
      </div>
    </div>
  );
}
