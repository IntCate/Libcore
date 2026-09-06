import { Suspense, useEffect, useState } from "react";
import { useRenderStore } from "./renderStore.js";
import { resolve } from "./registry.js";
import { compileComponent } from "./compiler.js";

export function RenderHost({ onEvent }) {
  const containers = useRenderStore((s) => s.containers);

  return (
    <div className="render-host">
      {Object.entries(containers).map(([target, c]) => (
        <ContainerView key={target} target={target} desc={c} onEvent={onEvent} />
      ))}
    </div>
  );
}

function ContainerView({ target, desc, onEvent }) {
  const { component, props = {}, children = [], events = {}, code } = desc;

  const emit = (name, e) => {
    const action = events[name];
    if (action && onEvent) onEvent(target, action, e);
  };

  if (code) {
    return <CodeView target={target} code={code} emit={emit} />;
  }

  const Comp = resolve(component);
  if (!Comp) {
    return <div className="render-missing">[未注册组件: {component}]</div>;
  }

  return (
    <Suspense fallback={<div className="render-loading">加载中...</div>}>
      <Comp {...props} data={desc.data} emit={emit} />
      {children.length > 0 && (
        <div className="render-children">
          {children.map((child, i) => (
            <ContainerView
              key={i}
              target={`${target}/${i}`}
              desc={{ ...child, data: [] }}
              onEvent={onEvent}
            />
          ))}
        </div>
      )}
    </Suspense>
  );
}

function CodeView({ target, code, emit }) {
  const [Comp, setComp] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    compileComponent(code.source)
      .then((c) => {
        if (!cancelled) setComp(() => c);
      })
      .catch((err) => {
        if (!cancelled) setError(err);
      });
    return () => {
      cancelled = true;
    };
  }, [code.source]);

  if (error) return <div className="render-error">编译失败: {error.message}</div>;
  if (!Comp) return <div className="render-loading">编译中...</div>;
  return <Comp emit={emit} />;
}
