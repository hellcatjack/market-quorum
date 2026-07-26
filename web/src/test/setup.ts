import "@testing-library/jest-dom/vitest";

Object.defineProperty(globalThis, "crypto", {
  value: { randomUUID: () => "00000000-0000-4000-8000-000000000001" },
  configurable: true,
});
