// Layout only: measured content widths decide which footer controls fit. A viewport breakpoint cannot know that.
export type FooterMeasure = { id: string; width: number; priority: number; pinned?: boolean };

const present = (items: readonly FooterMeasure[]) =>
  items.filter((item) => Number.isFinite(item.width) && item.width > 0);
const size = (items: readonly FooterMeasure[], gap: number) => {
  const visible = present(items);
  return visible.reduce((total, item) => total + item.width, 0) + Math.max(0, visible.length - 1) * gap;
};

export function fitFooter(
  available: number,
  measured: readonly FooterMeasure[],
  gap: number,
  moreWidth: number,
): { visible: string[]; hidden: string[] } {
  const items = present(measured);
  if (size(items, gap) <= available) return { visible: items.map((item) => item.id), hidden: [] };
  const visible = items.filter((item) => item.pinned);
  const more = { id: "@more", width: moreWidth, priority: 0 };
  const byPriority = items
    .map((item, order) => ({ item, order }))
    .filter(({ item }) => !item.pinned)
    .sort((left, right) => right.item.priority - left.item.priority || left.order - right.order);
  for (const { item } of byPriority) {
    if (size([...visible, item, more], gap) <= available) visible.push(item);
  }
  const selected = new Set(visible.map((item) => item.id));
  return {
    visible: items.filter((item) => selected.has(item.id)).map((item) => item.id),
    hidden: items.filter((item) => !selected.has(item.id)).map((item) => item.id),
  };
}

/** Compact optional attribution text before moving actual controls to overflow. */
export function footerWidths(
  available: number,
  items: readonly FooterMeasure[],
  gap: number,
  credit: { id: string; expanded: number; compact: number } | null,
): { compactCredit: boolean; items: FooterMeasure[] } {
  if (!credit) return { compactCredit: false, items: [...items] };
  const expanded = items.map((item) => (item.id === credit.id ? { ...item, width: credit.expanded } : item));
  const compactCredit = size(expanded, gap) > available;
  return {
    compactCredit,
    items: compactCredit
      ? expanded.map((item) => (item.id === credit.id ? { ...item, width: credit.compact } : item))
      : expanded,
  };
}
