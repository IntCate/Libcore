import { create } from "zustand";

export const useRenderStore = create((set) => ({
  containers: {},

  upsertContainer: (target, desc) =>
    set((s) => {
      const existing = s.containers[target];
      return {
        containers: {
          ...s.containers,
          [target]: {
            ...desc,
            data: existing?.data ?? desc.data ?? [],
          },
        },
      };
    }),

  appendData: (target, data) =>
    set((s) => {
      const c = s.containers[target];
      if (!c) return s;
      return {
        containers: {
          ...s.containers,
          [target]: { ...c, data: [...(c.data ?? []), data] },
        },
      };
    }),

  removeContainer: (target) =>
    set((s) => {
      const { [target]: _removed, ...rest } = s.containers;
      return { containers: rest };
    }),

  clearAll: () => set({ containers: {} }),
}));
