import * as React from "react";
import { jsx as _jsx } from "react/jsx-runtime";

const DANGEROUS_PATTERNS = [
  /\beval\s*\(/,
  /\bdocument\./,
  /\bwindow\./,
  /\bfetch\s*\(/,
  /\bimport\s*\(/,
  /\brequire\s*\(/,
  /\blocalStorage\b/,
  /\bsessionStorage\b/,
  /\bXMLHttpRequest\b/,
  /\bWebSocket\b/,
];

export function validateSource(source) {
  const hits = DANGEROUS_PATTERNS.filter((re) => re.test(source));
  if (hits.length > 0) {
    throw new Error(`源码包含危险模式: ${hits.map((r) => r.source).join(", ")}`);
  }
}

export async function compileComponent(source) {
  validateSource(source);

  const Babel = await import("@babel/standalone");

  const wrapped = `(${source})`;
  const transformed = Babel.transform(wrapped, {
    presets: [["react", { runtime: "classic" }]],
  }).code;

  const factory = new Function("React", `return ${transformed}`);
  const component = factory(React);

  if (typeof component !== "function") {
    throw new Error("编译结果不是组件函数");
  }
  return component;
}
