import { register } from "../render/registry.js";

export function registerBuiltins() {
  register("chat", () => import("./chat/Chat.jsx"));
  register("table", () => import("./table/Table.jsx"));
  register("card", () => import("./card/Card.jsx"));
}
