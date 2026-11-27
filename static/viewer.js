function comparePixels(left, right) {
  const width = left.naturalWidth;
  const height = left.naturalHeight;
  if (width !== right.naturalWidth || height !== right.naturalHeight) return null;
  const source = document.createElement('canvas');
  source.width = width;
  source.height = height;
  const context = source.getContext('2d', { willReadFrequently: true });
  context.drawImage(left, 0, 0);
  const before = context.getImageData(0, 0, width, height).data;
  context.clearRect(0, 0, width, height);
  context.drawImage(right, 0, 0);
  const after = context.getImageData(0, 0, width, height).data;
  const tileSize = 12;
  const columns = Math.ceil(width / tileSize);
  const counts = new Uint16Array(columns * Math.ceil(height / tileSize));
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = (y * width + x) * 4;
      const difference = Math.max(
        Math.abs(before[index] - after[index]),
        Math.abs(before[index + 1] - after[index + 1]),
        Math.abs(before[index + 2] - after[index + 2])
      );
      if (difference >= 28) counts[Math.floor(y / tileSize) * columns + Math.floor(x / tileSize)] += 1;
    }
  }
  return { width, height, columns, tileSize, counts };
}

function changeRegions(result) {
  const { counts, columns, tileSize, width, height } = result;
  const visited = new Uint8Array(counts.length);
  const regions = [];
  for (let tile = 0; tile < counts.length; tile += 1) {
    if (counts[tile] < 3 || visited[tile]) continue;
    const x = tile % columns;
    const y = Math.floor(tile / columns);
    const bounds = { left: x, top: y, right: x, bottom: y };
    const queue = [tile];
    visited[tile] = 1;
    for (let index = 0; index < queue.length; index += 1) {
      const current = queue[index];
      const col = current % columns;
      const row = Math.floor(current / columns);
      bounds.left = Math.min(bounds.left, col);
      bounds.right = Math.max(bounds.right, col);
      bounds.top = Math.min(bounds.top, row);
      bounds.bottom = Math.max(bounds.bottom, row);
      for (let dy = -1; dy <= 1; dy += 1) {
        for (let dx = -1; dx <= 1; dx += 1) {
          const near = (row + dy) * columns + col + dx;
          if (col + dx >= 0 && col + dx < columns && counts[near] >= 3 && !visited[near]) {
            visited[near] = 1;
            queue.push(near);
          }
        }
      }
    }
    regions.push([bounds.left * tileSize, bounds.top * tileSize,
      Math.min((bounds.right + 1) * tileSize, width),
      Math.min((bounds.bottom + 1) * tileSize, height)]);
  }
  return compactRegions(regions, width, height);
}

function boundingBox(a, b) {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}

function area([left, top, right, bottom]) {
  return Math.max(0, right - left) * Math.max(0, bottom - top);
}

function coveredArea(regions) {
  const edges = [...new Set(regions.flatMap(r => [r[0], r[2]]))].sort((a, b) => a - b);
  let total = 0;
  for (let i = 1; i < edges.length; i += 1) {
    const spans = regions.filter(r => r[0] < edges[i] && r[2] > edges[i - 1])
      .sort((a, b) => a[1] - b[1]);
    let end = 0;
    let height = 0;
    for (const [, top, , bottom] of spans) {
      height += Math.max(0, bottom - Math.max(top, end));
      end = Math.max(end, bottom);
    }
    total += (edges[i] - edges[i - 1]) * height;
  }
  return total;
}

function compactRegions(regions, width, height) {
  const binsPerAxis = 8;
  // Limit the pair search for pages with many isolated marks.
  if (regions.length > binsPerAxis ** 2) {
    const bins = [];
    for (const r of regions) {
      const x = Math.floor((r[0] + r[2]) * binsPerAxis / (width * 2));
      const y = Math.floor((r[1] + r[3]) * binsPerAxis / (height * 2));
      const index = y * binsPerAxis + x;
      bins[index] = bins[index] ? boundingBox(bins[index], r) : r;
    }
    regions = bins.filter(Boolean);
  }
  while (regions.length > 7) {
    let best = Infinity;
    let pair;
    for (let i = 0; i < regions.length; i += 1) {
      for (let j = i + 1; j < regions.length; j += 1) {
        const a = regions[i], b = regions[j];
        const overlap = area([Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])]);
        const added = area(boundingBox(a, b)) - (area(a) + area(b) - overlap);
        if (added < best) {
          best = added;
          pair = [i, j];
        }
      }
    }
    const [i, j] = pair;
    regions[i] = boundingBox(regions[i], regions[j]);
    regions.splice(j, 1);
  }
  if (coveredArea(regions) * 5 >= width * height * 4) return [[0, 0, width, height]];
  return regions.sort((a, b) => a[1] - b[1] || a[0] - b[0] || a[3] - b[3] || a[2] - b[2]);
}

function pageChanges(pixels, footer) {
  if (!pixels) return { regions: [null], footerOnly: false };
  const regions = changeRegions(pixels);
  const footerOnly = Boolean(footer && regions.length && regions.every(([left, top, right, bottom]) =>
    left >= footer[0] * pixels.width && top >= footer[1] * pixels.height &&
    right <= footer[2] * pixels.width && bottom <= footer[3] * pixels.height));
  return { regions, footerOnly };
}

function showMarks(sides, result, regions, highlight, selected) {
  sides.forEach(({ canvas }, side) => {
    canvas.hidden = !result || (!highlight && !selected);
    if (canvas.hidden) return;
    canvas.width = result.width;
    canvas.height = result.height;
    const context = canvas.getContext('2d');
    if (highlight) {
      context.fillStyle = side === 0 ? 'rgba(217, 48, 77, .23)' : 'rgba(30, 157, 89, .25)';
      context.beginPath();
      for (const [left, top, right, bottom] of regions) {
        context.rect(left, top, right - left, bottom - top);
      }
      context.fill();
    }
    if (selected) {
      const pad = 5;
      context.strokeStyle = '#1658b3';
      context.lineWidth = 4;
      const left = Math.max(2, selected[0] - pad);
      const top = Math.max(2, selected[1] - pad);
      const right = Math.min(canvas.width - 2, selected[2] + pad);
      const bottom = Math.min(canvas.height - 2, selected[3] + pad);
      context.strokeRect(left, top, right - left, bottom - top);
    }
  });
}

async function mountViewer(root) {
  const sides = Array.from(root.querySelectorAll('[data-preview]'), node => ({
    url: node.dataset.preview,
    viewLink: node.querySelector('[data-view-link]'),
    version: versionLocation(node)
  }));
  const parameters = new URLSearchParams(location.search);
  const setEditLocation = editLocationControl(root);
  const scroll = root.querySelector('[data-paper-scroll]');
  const stack = root.querySelector('[data-paper-stack]');
  const page = root.querySelector('[data-page]');
  const previous = root.querySelector('[data-previous]');
  const next = root.querySelector('[data-next]');
  const previousChange = root.querySelector('[data-previous-change]');
  const nextChange = root.querySelector('[data-next-change]');
  const changePosition = root.querySelector('[data-change-position]');
  const changeTotal = root.querySelector('[data-change-total]');
  const list = root.querySelector('[data-change-list]');
  const items = root.querySelector('[data-change-items]');
  const status = root.querySelector('[data-status]');
  const highlight = root.querySelector('[data-highlight]');
  const zoom = root.querySelector('[data-zoom]');
  const notes = root.querySelector('[data-pdf-notes]');
  const comparing = sides.length === 2;
  const infos = await Promise.all(sides.map(async side => {
    try {
      const response = await fetch(side.url);
      const info = await response.json();
      return response.ok ? info : { error: info.error };
    } catch {
      return { error: '문서 미리보기를 열 수 없습니다.' };
    }
  }));
  const comparable = comparing && infos.every(info => info.pages);
  // Compare images from the same renderer to avoid differences caused by font rasterization.
  const renderer = comparable && infos.some(info => info.requires_poppler) ? '?renderer=poppler' : '';
  const maxPages = Math.max(...infos.map(info => info.pages || 0));
  let pairs = Array.from({ length: maxPages }, (_, index) =>
    infos.map(info => index < info.pages ? index + 1 : null));
  let sources = [];
  let footers = [];
  if (comparable) {
    try {
      const response = await fetch(root.dataset.pageAlignment);
      if (response.ok) ({ pairs, sources, footers = [] } = await response.json());
    } catch {
      // Keep the existing positional preview available if alignment cannot be loaded.
    }
  }
  const total = pairs.length;
  if (!total) {
    const errors = [...new Set(infos.map(info => info.error).filter(Boolean))];
    status.textContent = errors.join(' ') || '미리보기를 열 수 없습니다. 원본을 다운로드해 확인할 수 있습니다.';
    scroll.hidden = true;
    return;
  }

  const resident = new Map();
  const scanned = new Map();
  const textMeasure = document.createElement('canvas').getContext('2d');
  textMeasure.font = '100px sans-serif';
  const textLayers = new Map();
  let visibleText = new Set();
  let followedLocation = false;
  const textResize = new ResizeObserver(entries => {
    for (const { target, contentRect } of entries) {
      const spans = textLayers.get(target);
      if (!spans || !contentRect.width || !contentRect.height) continue;
      positionText(spans, contentRect.width, contentRect.height);
      const marked = target.querySelector('[data-location-start]');
      if (!followedLocation && marked && target.closest('.paper-row').dataset.pageNumber === String(pageNumber)) {
        followedLocation = true;
        setOffset(topOf(marked) - 36);
      }
    }
  });
  let pageNumber = 0;
  let activeRegion = -1;
  let positioning = false;
  let navigating = false;
  let scrollFrame = 0;
  let noteRequest = 0;

  const rows = pairs.map((pair, index) => {
    const number = index + 1;
    const element = document.createElement('div');
    element.className = 'viewer-grid paper-row' + (comparing ? '' : ' viewer-single');
    element.dataset.pageNumber = number;
    const gap = comparable && !pair[0] ? '삽입된 페이지'
      : comparable && !pair[1] ? '삭제된 페이지' : '';
    const change = gap ? `${gap} · ${sources[index] === 'recorded' ? 'GitDoc 편집 기록' : '자동 판정'}` : '';
    if (gap) element.classList.add('paper-row-with-gap');
    const cells = infos.map((info, side) => {
      const physicalPage = pair[side];
      const cell = document.createElement('section');
      cell.className = 'paper-page';
      const heading = document.createElement('strong');
      heading.className = 'paper-page-heading';
      const label = comparing ? (side === 0 ? '기준 버전' : '비교 버전') : '문서';
      heading.textContent = physicalPage ? `${label} ${physicalPage}쪽` : `${label}${gap ? ' · 페이지 없음' : ''}`;
      cell.append(heading);
      if (!physicalPage) {
        if (gap) cell.classList.add('paper-page-missing');
        const placeholder = document.createElement('p');
        placeholder.className = 'viewer-placeholder';
        placeholder.textContent = info.error || '이 버전에 대응하는 페이지가 없습니다.';
        cell.append(placeholder);
        element.append(cell);
        return null;
      }
      const sheet = document.createElement('div');
      sheet.className = 'viewer-sheet';
      if (change) {
        const badge = document.createElement('span');
        badge.className = side === 0 ? 'paper-page-badge removed' : 'paper-page-badge inserted';
        badge.textContent = change;
        heading.append(' · ', badge);
      }
      const [width, height] = info.dimensions?.[physicalPage - 1] || [];
      if (width > 0 && height > 0 && Number.isFinite(width / height)) {
        sheet.style.setProperty('--paper-ratio', width / height);
      }
      cell.append(sheet);
      element.append(cell);
      return { sheet, label: `${label} ${physicalPage}쪽`, physicalPage, points: info.sheet_widths?.[physicalPage - 1] };
    });
    return { element, cells, change };
  });
  stack.append(...rows.map(row => row.element));
  page.max = total;
  root.querySelector('[data-total]').textContent = `/ ${total}`;

  function updateControls() {
    page.disabled = false;
    previous.disabled = pageNumber <= 1;
    next.disabled = pageNumber >= total;
    if (comparing) {
      previousChange.disabled = navigating || !comparable;
      nextChange.disabled = navigating || !comparable;
    }
  }

  function drawMarks(number, state) {
    if (!comparable || !state?.pixels) return;
    showMarks(state.rendered, state.pixels, state.regions, highlight.checked && state.regions.length > 0,
      number === pageNumber ? state.regions[activeRegion] : null);
  }

  function updateStatus() {
    const state = resident.get(pageNumber);
    if (comparing) {
      highlight.disabled = !comparable;
      changePosition.textContent = state?.footerOnly ? '' : activeRegion >= 0 && state?.regions?.length
        ? `이 페이지의 변경 ${activeRegion + 1} / ${state.regions.length}`
        : state?.regions?.length ? `이 페이지에 변경 ${state.regions.length}곳` : '';
    }
    if (navigating) return;
    if (!state?.done) status.textContent = '페이지를 불러오는 중입니다.';
    else if (state.error) status.textContent = '페이지 이미지를 열 수 없습니다.';
    else if (!comparing) status.textContent = '';
    else if (!comparable) status.textContent = '양쪽에서 문서를 열 수 있을 때 시각적 차이를 표시합니다.';
    else if (rows[pageNumber - 1].change) status.textContent = rows[pageNumber - 1].change;
    else if (state.footerOnly) status.textContent = '본문은 동일하며 하단 표시만 달라졌습니다.';
    else if (!state.pixels) status.textContent = '페이지 크기가 달라 변경 위치를 겹쳐 표시할 수 없습니다.';
    else status.textContent = state.regions.length ? '분홍색은 기준본, 초록색은 수정본의 달라진 영역입니다.'
      : '이 페이지에서 눈에 띄는 시각적 차이가 없습니다.';
  }

  async function showNotes(number) {
    const current = ++noteRequest;
    notes.hidden = true;
    try {
      const response = await fetch(`${notes.dataset.pdfNotes}/${number}`);
      if (!response.ok || current !== noteRequest) return;
      const { notes: entries } = await response.json();
      if (current !== noteRequest) return;
      notes.querySelector('ul').replaceChildren(...entries.map(entry => {
        const item = document.createElement('li');
        item.textContent = entry;
        return item;
      }));
      notes.hidden = !entries.length;
    } catch {
      // Keep the page available if its annotations cannot be read.
    }
  }

  function activate(number, force = false) {
    if (number === pageNumber && !force) return;
    const old = pageNumber;
    pageNumber = number;
    activeRegion = -1;
    drawMarks(old, resident.get(old));
    page.value = number;
    if (old !== number) setEditLocation(null);
    sides.forEach(({ viewLink }, index) => {
      if (!viewLink || !infos[index].pages) return;
      const url = new URL(viewLink.href);
      const paired = pairs[number - 1][index];
      const nearest = paired || pairs.slice(0, number).reverse().find(pair => pair[index])?.[index] || 1;
      url.searchParams.set('page', String(nearest));
      viewLink.href = url;
    });
    if (comparing) items.querySelector('[aria-current]')?.removeAttribute('aria-current');
    if (notes) showNotes(number);
    updateControls();
    updateStatus();
  }

  async function imageFor(index, number, signal, priority = 'low') {
    // A cancellable fetch releases queued work when the reader scrolls past it.
    const response = await fetch(`${sides[index].url}/${number}.png${renderer}`, { signal, priority });
    if (!response.ok) throw new Error('페이지 이미지를 열 수 없습니다.');
    const url = URL.createObjectURL(await response.blob());
    const image = new Image();
    try {
      image.src = url;
      await image.decode();
      return image;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  function positionText(spans, width, height) {
    for (const { span, first, last, box, measured } of spans) {
      const dx = (last[0] - first[0]) * width;
      const dy = (last[1] - first[1]) * height;
      const vertical = Math.abs(dy) > Math.abs(dx);
      const flipped = (vertical ? dy : dx) < 0 && !span.textContent.match(/[\u0590-\u08ff]/u);
      const angle = vertical ? (flipped ? -90 : 90) : (flipped ? 180 : 0);
      const [x0, y0, x1, y1] = box;
      const w = (x1 - x0) * width, h = (y1 - y0) * height;
      const size = vertical ? w : h;
      if (!(size > 0)) continue;
      span.style.left = `${(angle === 90 || angle === 180 ? x1 : x0) * 100}%`;
      span.style.top = `${(angle === -90 || angle === 180 ? y1 : y0) * 100}%`;
      span.style.fontSize = `${size}px`;
      span.style.transform = `rotate(${angle}deg) scaleX(${(vertical ? h : w) / (size * measured)})`;
    }
  }

  async function showText(cell, side, state, priority) {
    try {
      const response = await fetch(`${sides[side].url}/${cell.physicalPage}/text`,
        { signal: state.controller.signal, priority });
      if (!response.ok) return;
      const { lines } = await response.json();
      if (state.controller.signal.aborted || !lines.length) return;
      const layer = document.createElement('div');
      layer.className = 'viewer-text';
      layer.dataset.side = side;
      layer.dataset.physicalPage = cell.physicalPage;
      const markStart = Number(parameters.get('text_line'));
      const markEnd = Number(parameters.get('text_end_line'));
      const markHere = markStart > 0 && markEnd >= markStart &&
        parameters.get('text_side') === String(side) &&
        parameters.get('page') === cell.sheet.closest('.paper-row').dataset.pageNumber;
      const spans = lines.map((line, index) => {
        const span = document.createElement('span');
        span.dir = 'auto';
        span.dataset.line = index + 1;
        if (markHere && index + 1 === markStart) span.dataset.locationStart = '';
        span.textContent = line.map(([value]) => value).join('');
        const box = line.reduce(([x0, y0, x1, y1], [, b]) =>
          [Math.min(x0, b[0]), Math.min(y0, b[1]), Math.max(x1, b[2]), Math.max(y1, b[3])],
        [1, 1, 0, 0]);
        layer.append(span, '\n');
        return { span, box, first: line[0][1], last: line.at(-1)[1],
          measured: Math.max(1, textMeasure.measureText(span.textContent).width) / 100 };
      });
      textLayers.set(cell.sheet, spans);
      cell.sheet.append(layer);
      if (markHere) markTextLocation(spans, markStart, markEnd);
      textResize.observe(cell.sheet);
    } catch {
      // The raster preview remains available if extracting selectable text fails.
    }
  }

  function markTextLocation(spans, start, end) {
    const first = spans[start - 1]?.span;
    const last = spans[end - 1]?.span;
    if (!first || !last || !Number.isInteger(start) || !Number.isInteger(end)) return;
    const from = Number(parameters.get('text_offset') ?? 0);
    const to = Number(parameters.get('text_end_offset') ?? last.textContent.length);
    if (globalThis.CSS?.highlights && typeof Highlight === 'function' &&
      Number.isInteger(from) && Number.isInteger(to) && from >= 0 && to >= 0 &&
      from <= first.textContent.length && to <= last.textContent.length &&
      (first !== last || from < to)) {
      const range = document.createRange();
      range.setStart(first.firstChild, from);
      range.setEnd(last.firstChild, to);
      CSS.highlights.set('gitdoc-edit-location', new Highlight(range));
    } else {
      for (const { span } of spans.slice(start - 1, end)) span.classList.add('edit-location-mark');
    }
  }

  function selectedLine(node) {
    return (node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement)
      ?.closest('.viewer-text > span');
  }

  function offsetIn(line, node, offset) {
    const prefix = document.createRange();
    prefix.selectNodeContents(line);
    prefix.setEnd(node, offset);
    return prefix.toString().length;
  }

  scroll.addEventListener('pointerdown', () => setEditLocation(null));
  document.addEventListener('selectionchange', () => {
    const selection = window.getSelection();
    if (!selection?.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0);
    const first = selectedLine(range.startContainer);
    const last = selectedLine(range.endContainer);
    if (!first || !root.contains(first)) return;
    const layer = first.parentElement;
    if (!last || last.parentElement !== layer || !selection.toString().trim()) {
      setEditLocation(null);
      return;
    }
    const side = Number(layer.dataset.side);
    const position = Number(layer.closest('.paper-row').dataset.pageNumber);
    const physicalPage = Number(layer.dataset.physicalPage);
    const start = { line: Number(first.dataset.line),
      offset_utf16: offsetIn(first, range.startContainer, range.startOffset) };
    const end = { line: Number(last.dataset.line),
      offset_utf16: offsetIn(last, range.endContainer, range.endOffset) };
    const sheet = layer.getBoundingClientRect();
    const region = range.getBoundingClientRect();
    const fraction = value => Math.round(Math.max(0, Math.min(1, value)) * 10000) / 10000;
    const box = [
      fraction((region.left - sheet.left) / sheet.width),
      fraction((region.top - sheet.top) / sheet.height),
      fraction((region.right - sheet.left) / sheet.width),
      fraction((region.bottom - sheet.top) / sheet.height)
    ];
    setEditLocation({
      kind: 'page_text', ...sides[side].version, page: physicalPage,
      ...(comparing ? { comparison_position: position, comparison_side: side === 0 ? 'base' : 'target' } : {}),
      selected_text: selection.toString(), start, end, box_normalized: box
    }, { page: position, text_side: side, text_line: start.line, text_offset: start.offset_utf16,
      text_end_line: end.line, text_end_offset: end.offset_utf16,
      ...(root.dataset.printLayout === 'true' ? { view: 'pages' } : {}) });
  });

  function loadVisibleText(number, state) {
    if (!visibleText.has(number) || !state?.done || state.requestedText) return;
    state.requestedText = true;
    for (const { side } of state.rendered) {
      showText(rows[number - 1].cells[side], side, state, number === pageNumber ? 'high' : 'low');
    }
  }

  function showRow(number, priority = 'high') {
    if (resident.has(number)) return resident.get(number).promise;
    const state = { done: false, controller: new AbortController(), rendered: [], regions: [] };
    resident.set(number, state);
    const cells = rows[number - 1].cells;
    state.promise = Promise.allSettled(cells.map((cell, side) =>
      cell && imageFor(side, cell.physicalPage, state.controller.signal, priority)))
      .then(results => {
        if (resident.get(number) !== state) return;
        state.done = true;
        state.error = results.some(result => result.status === 'rejected');
        results.forEach((result, side) => {
          const cell = cells[side];
          if (!cell) return;
          if (result.status === 'rejected') {
            const message = document.createElement('p');
            message.className = 'viewer-placeholder';
            message.textContent = '페이지 이미지를 열 수 없습니다.';
            cell.sheet.append(message);
            return;
          }
          const image = result.value;
          image.alt = cell.label;
          image.draggable = false;
          cell.sheet.append(image);
          const canvas = document.createElement('canvas');
          canvas.className = 'viewer-overlay';
          canvas.setAttribute('aria-hidden', 'true');
          canvas.hidden = true;
          if (comparing) cell.sheet.append(canvas);
          state.rendered.push({ image, canvas, side });
        });
        if (comparable && !state.error) {
          state.pixels = state.rendered.length === 2 ? comparePixels(...state.rendered.map(item => item.image)) : null;
          const result = pageChanges(state.pixels, footers[number - 1]);
          Object.assign(state, result);
          if (!scanned.has(number)) scanned.set(number, Promise.resolve(result));
          drawMarks(number, state);
        }
        loadVisibleText(number, state);
        if (number === pageNumber) updateStatus();
      });
    return state.promise;
  }

  function hideRow(number) {
    const state = resident.get(number);
    if (!state) return;
    state.controller.abort();
    resident.delete(number);
    rows[number - 1].cells.forEach(cell => {
      if (!cell) return;
      if (cell.sheet.querySelector('[data-location-start]')) globalThis.CSS?.highlights?.delete('gitdoc-edit-location');
      textResize.unobserve(cell.sheet);
      textLayers.delete(cell.sheet);
      cell.sheet.replaceChildren();
    });
  }

  function countRowsBefore(isBefore) {
    let low = 0;
    let high = total;
    while (low < high) {
      const middle = (low + high) >> 1;
      const bounds = rows[middle].element.getBoundingClientRect();
      if (isBefore(bounds)) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  function renderViewport() {
    if (!scroll.clientHeight) return;
    const top = scroll.getBoundingClientRect().top + scroll.clientTop;
    const bottom = top + scroll.clientHeight;
    const first = countRowsBefore(bounds => bounds.bottom <= top) + 1;
    const last = countRowsBefore(bounds => bounds.top < bottom);
    visibleText = new Set([pageNumber]);
    for (let number = first; number <= last; number += 1) visibleText.add(number);
    for (const number of resident.keys()) {
      if (number !== pageNumber && (number < first - 1 || number > last + 1)) hideRow(number);
    }
    showRow(pageNumber);
    for (let number = first; number <= last; number += 1) showRow(number);
    if (first > 1) showRow(first - 1, 'low');
    if (last < total) showRow(last + 1, 'low');
    for (const number of visibleText) loadVisibleText(number, resident.get(number));
  }

  function topOf(element) {
    return scroll.scrollTop + element.getBoundingClientRect().top - scroll.getBoundingClientRect().top - scroll.clientTop;
  }

  function visiblePage() {
    const bounds = scroll.getBoundingClientRect();
    if (scroll.scrollTop > 0 && scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 1) return total;
    const focus = bounds.top + Math.min(48, bounds.height / 4);
    return Math.max(1, countRowsBefore(row => row.top <= focus));
  }

  // Programmatic jumps clamp at the bottom and must keep the requested page active.
  let lastOffset = 0;
  function setOffset(top, left = scroll.scrollLeft) {
    positioning = true;
    scroll.scrollTo({ top, left });
    lastOffset = scroll.scrollTop;
    positioning = false;
    renderViewport();
  }

  function jump(number) {
    const target = Number.isInteger(number) ? Math.max(1, Math.min(total, number)) : pageNumber;
    activate(target, true);
    setOffset(topOf(rows[target - 1].element) - 8);
  }

  scroll.addEventListener('scroll', () => {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      if (positioning || Math.abs(scroll.scrollTop - lastOffset) < 0.5) return;
      lastOffset = scroll.scrollTop;
      activate(visiblePage());
      renderViewport();
    });
  }, { passive: true });

  async function focusRegion(number, index) {
    if (number !== pageNumber) jump(number);
    await showRow(number);
    if (number !== pageNumber) return;
    const state = resident.get(number);
    if (!state?.regions?.length || state.footerOnly || index >= state.regions.length) return;
    activeRegion = index;
    items.querySelector('[aria-current]')?.removeAttribute('aria-current');
    items.querySelector(`[data-location="${number}:${index}"]`)?.setAttribute('aria-current', 'true');
    drawMarks(number, state);
    updateStatus();
    const region = state.regions[index];
    if (region && state.pixels) {
      const image = state.rendered[0].image;
      const bounds = image.getBoundingClientRect();
      setOffset(topOf(image) + (region[1] + region[3]) * bounds.height / (2 * state.pixels.height) - scroll.clientHeight / 2);
    }
  }

  function scan(number) {
    if (!scanned.has(number)) {
      scanned.set(number, (async () => {
        const row = rows[number - 1];
        if (row.change) return pageChanges(null);
        const state = resident.get(number);
        if (state) {
          await state.promise;
          if (state.done && !state.error) return { regions: state.regions, footerOnly: state.footerOnly };
        }
        const images = await Promise.all(row.cells.map((cell, index) => imageFor(index, cell.physicalPage)));
        return pageChanges(comparePixels(...images), footers[number - 1]);
      })());
    }
    return scanned.get(number);
  }

  async function seek(direction) {
    if (navigating || !comparable) return;
    navigating = true;
    updateControls();
    const startPage = pageNumber;
    const startRegion = activeRegion;
    status.textContent = '변경 위치를 찾는 중입니다.';
    try {
      for (let number = startPage; number >= 1 && number <= total && pageNumber === startPage; number += direction) {
        const result = await scan(number).catch(() => null);
        const changes = result && !result.footerOnly ? result.regions : [];
        if (pageNumber !== startPage) break;
        const index = number === startPage && startRegion >= 0
          ? startRegion + direction : direction > 0 ? 0 : changes.length - 1;
        if (index >= 0 && index < changes.length) {
          await focusRegion(number, index);
          return;
        }
      }
      if (pageNumber === startPage) status.textContent = '더 이동할 변경이 없습니다.';
    } finally {
      navigating = false;
      updateControls();
      if (status.textContent === '변경 위치를 찾는 중입니다.') updateStatus();
    }
  }

  const locations = [];
  let listed = 0;
  function updateList() {
    if (!list.open) return;
    const group = locations.slice(listed, listed + 100);
    listed += group.length;
    for (const { number, index, region } of group) {
      const item = document.createElement('li');
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'secondary';
      button.dataset.location = `${number}:${index}`;
      const row = rows[number - 1];
      const shownPage = row.cells[1]?.physicalPage ?? row.cells[0]?.physicalPage;
      button.textContent = `${shownPage}쪽 · ${row.change || (region ? `변경 ${index + 1}` : '페이지 변경')}`;
      if (number === pageNumber && index === activeRegion) button.setAttribute('aria-current', 'true');
      button.addEventListener('click', () => focusRegion(number, index));
      item.append(button);
      items.append(item);
    }
    list.querySelector('[data-more-changes]').hidden = listed >= locations.length;
  }

  async function countChanges() {
    let failures = 0;
    let footerPages = 0;
    for (let number = 1; number <= total; number += 1) {
      changeTotal.textContent = `전체 변경 확인 중 · ${number} / ${total}쪽`;
      try {
        const { regions, footerOnly } = await scan(number);
        if (footerOnly) footerPages += 1;
        else regions.forEach((region, index) => locations.push({ number, index, region }));
      } catch {
        failures += 1;
      }
    }
    changeTotal.textContent = `전체 변경 ${locations.length}건${footerPages ? ` · 하단 표시만 변경 ${footerPages}쪽` : ''}${failures ? ` · 확인 실패 ${failures}쪽` : ''}`;
    updateList();
  }

  function setZoom() {
    const scale = Number(zoom.value) / 100;
    stack.style.setProperty('--paper-zoom', `${zoom.value}%`);
    stack.style.setProperty('--paper-factor', scale);
    rows.forEach(({ cells }) => cells.forEach(cell => {
      if (cell?.points > 0) cell.sheet.style.setProperty('--sheet-width', `${Math.round(cell.points * 4 / 3 * scale)}px`);
    }));
  }

  previous.addEventListener('click', () => jump(pageNumber - 1));
  next.addEventListener('click', () => jump(pageNumber + 1));
  page.addEventListener('change', () => jump(page.valueAsNumber));
  previousChange?.addEventListener('click', () => seek(-1));
  nextChange?.addEventListener('click', () => seek(1));
  highlight?.addEventListener('change', () => resident.forEach((state, number) => drawMarks(number, state)));
  zoom.addEventListener('change', () => {
    const selected = activeRegion;
    setZoom();
    jump(pageNumber);
    if (selected >= 0) focusRegion(pageNumber, selected);
  });
  if (comparable) {
    list.hidden = false;
    list.addEventListener('toggle', () => { if (listed === 0) updateList(); });
    list.querySelector('[data-more-changes]').addEventListener('click', updateList);
  }
  setZoom();
  const initialPage = Number(parameters.get('page') || 1);
  jump(Number.isInteger(initialPage) ? initialPage : 1);
  const resize = new ResizeObserver(renderViewport);
  resize.observe(scroll);
  resize.observe(stack);
  const initialChange = Number(parameters.get('change'));
  if (comparable && Number.isInteger(initialChange) && initialChange > 0) {
    await focusRegion(pageNumber, initialChange - 1);
  }
  if (comparable) countChanges();
  else if (changeTotal) changeTotal.textContent = '전체 변경을 확인할 수 없습니다.';
}

document.querySelectorAll('[data-visual-viewer]').forEach(root => {
  mountViewer(root).catch(() => {
    root.querySelector('[data-status]').textContent = '미리보기를 표시할 수 없습니다. 원본을 확인하세요.';
  });
});
