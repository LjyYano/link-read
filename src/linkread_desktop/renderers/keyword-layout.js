// Keyword cloud placement: a pure function shared by the reading page and its Node tests.
(function (root) {
  'use strict';

  /**
   * Place rectangles without overlap, spiralling out from the centre of a wide ellipse.
   *
   * items: [{ w, h }] in CSS pixels, in display order; the first one ends up in the middle.
   * width, height: the canvas every rectangle must stay inside, margins included.
   * options.margin: space kept around the canvas edge.
   * options.gap: space kept between rectangles.
   * options.squash: height of the ellipse as a share of its width; lower means a wider cloud.
   * Returns one { x, y } per item, or null when any item cannot be placed.
   */
  function placeKeywords(items, width, height, options) {
    const { margin = 4, gap = 4, squash = 0.52 } = options || {};
    const placed = [];
    const fits = (x, y, w, h) => x >= margin && y >= margin && x + w <= width - margin && y + h <= height - margin &&
      !placed.some(p => x < p.x + p.w + gap && x + w + gap > p.x && y < p.y + p.h + gap && y + h + gap > p.y);
    const cx = width / 2, cy = height / 2;
    for (let index = 0; index < items.length; index++) {
      const { w, h } = items[index];
      if (w + 2 * margin > width || h + 2 * margin > height) return null;
      let box = null;
      // Beyond the canvas width the spiral cannot offer an in-bounds position any more.
      for (let step = 0; step * 0.55 <= width / 2 + 1 && !box; step++) {
        const angle = step * 0.3 + index * 2.399963, radius = step * 0.55;
        const x = Math.round(cx + Math.cos(angle) * radius - w / 2);
        const y = Math.round(cy + Math.sin(angle) * radius * squash - h / 2);
        if (fits(x, y, w, h)) box = { x, y, w, h };
      }
      if (!box) {
        // The spiral steps over narrow gaps and never reaches the top and bottom bands,
        // so try the free edges of what is already placed, nearest to the centre first.
        const xs = [margin, width - w - margin, Math.round((width - w) / 2),
          ...placed.flatMap(p => [p.x - w - gap, p.x + p.w + gap])];
        const ys = [margin, height - h - margin, Math.round((height - h) / 2),
          ...placed.flatMap(p => [p.y - h - gap, p.y + p.h + gap])];
        let best = Infinity;
        for (const x of xs) for (const y of ys) {
          if (!fits(x, y, w, h)) continue;
          const distance = ((x + w / 2 - cx) / width) ** 2 + ((y + h / 2 - cy) / height) ** 2;
          if (distance < best) { best = distance; box = { x, y, w, h }; }
        }
      }
      if (!box) return null;
      placed.push(box);
    }
    return placed.map(({ x, y }) => ({ x, y }));
  }

  const api = { placeKeywords };
  root.LinkReadKeywordLayout = api;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window === 'undefined' ? globalThis : window);
