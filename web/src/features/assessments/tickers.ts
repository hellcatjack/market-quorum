export function parseTickers(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/[\s,;，；]+/)
        .map((item) => item.trim().toUpperCase())
        .filter(Boolean),
    ),
  ];
}
