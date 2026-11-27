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
  const { counts, columns, tileSize } = result;
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
    regions.push({
      x: bounds.left * tileSize, y: bounds.top * tileSize,
      width: (bounds.right - bounds.left + 1) * tileSize,
      height: (bounds.bottom - bounds.top + 1) * tileSize
    });
  }
  return regions;
}

function showMarks(sides, result, highlight, selected) {
  sides.forEach(({ canvas }, side) => {
    canvas.hidden = !result || (!highlight && !selected);
    if (canvas.hidden) return;
    canvas.width = result.width;
    canvas.height = result.height;
    const context = canvas.getContext('2d');
    if (highlight) {
      context.fillStyle = side === 0 ? 'rgba(217, 48, 77, .23)' : 'rgba(30, 157, 89, .25)';
      result.counts.forEach((count, index) => {
        if (count >= 3) {
          context.fillRect((index % result.columns) * result.tileSize,
            Math.floor(index / result.columns) * result.tileSize, result.tileSize, result.tileSize);
        }
      });
    }
    if (selected) {
      const pad = 5;
      context.strokeStyle = '#1658b3';
      context.lineWidth = 4;
      context.strokeRect(Math.max(2, selected.x - pad), Math.max(2, selected.y - pad),
        Math.min(canvas.width - 4, selected.width + 2 * pad),
        Math.min(canvas.height - 4, selected.height + 2 * pad));
    }
  });
}

function lockScrolling(sides, enabled) {
  let scrollTarget = null;
  for (const [index, side] of sides.entries()) {
    const other = sides[1 - index].scroll;
    side.scroll.addEventListener('scroll', () => {
      if (!enabled.checked || scrollTarget === side.scroll) return;
      const ratio = (current, size) => size > 0 ? current / size : 0;
      const x = ratio(side.scroll.scrollLeft, side.scroll.scrollWidth - side.scroll.clientWidth);
      const y = ratio(side.scroll.scrollTop, side.scroll.scrollHeight - side.scroll.clientHeight);
      scrollTarget = other;
      other.scrollTo(x * (other.scrollWidth - other.clientWidth), y * (other.scrollHeight - other.clientHeight));
      requestAnimationFrame(() => { scrollTarget = null; });
    }, { passive: true });
  }
}

async function mountViewer(root) {
  const sides = Array.from(root.querySelectorAll('[data-preview]'), node => ({
    url: node.dataset.preview,
    canvas: node.querySelector('canvas'),
    scroll: node.querySelector('.viewer-scroll'),
    sheet: node.querySelector('.viewer-sheet'),
    image: node.querySelector('img'),
    placeholder: node.querySelector('.viewer-placeholder'),
    viewLink: node.querySelector('[data-view-link]')
  }));
  const page = root.querySelector('[data-page]');
  const previous = root.querySelector('[data-previous]');
  const next = root.querySelector('[data-next]');
  const totalLabel = root.querySelector('[data-total]');
  const previousChange = root.querySelector('[data-previous-change]');
  const nextChange = root.querySelector('[data-next-change]');
  const changePosition = root.querySelector('[data-change-position]');
  const list = root.querySelector('[data-change-list]');
  const items = root.querySelector('[data-change-items]');
  const listStatus = root.querySelector('[data-list-status]');
  const scanned = new Map();
  const status = root.querySelector('[data-status]');
  const highlight = root.querySelector('[data-highlight]');
  const zoom = root.querySelector('[data-zoom]');
  const sync = root.querySelector('[data-sync]');
  const notes = root.querySelector('[data-pdf-notes]');
  let generation = 0;
  let pageNumber = 1;
  let pixels = null;
  let regions = [];
  let activeRegion = -1;
  let navigating = false;
  let loading = false;

  const infos = await Promise.all(sides.map(async side => {
    try {
      const response = await fetch(side.url);
      const info = await response.json();
      return response.ok ? info : { error: info.error || '문서 미리보기를 열 수 없습니다.' };
    } catch {
      return { error: '문서 미리보기를 열 수 없습니다.' };
    }
  }));
  const total = Math.max(...infos.map(info => info.pages || 0));
  if (!total) {
    sides.forEach((side, index) => { side.placeholder.textContent = infos[index].error; });
    status.textContent = '미리보기를 열 수 없습니다. 원본을 다운로드해 확인할 수 있습니다.';
    return;
  }

  const comparing = sides.length === 2;
  const comparable = comparing && infos.every(info => info.pages);
  function updateControls() {
    const busy = loading || navigating;
    page.disabled = busy;
    previous.disabled = busy || pageNumber === 1;
    next.disabled = busy || pageNumber === total;
    if (comparing) {
      previousChange.disabled = busy || !comparable;
      nextChange.disabled = busy || !comparable;
    }
  }

  function updateHighlight() {
    const region = activeRegion >= 0 ? regions[activeRegion] : null;
    showMarks(sides, pixels, highlight.checked && regions.length > 0, region);
  }

  function setSheetWidths() {
    sides.forEach(({ sheet }, index) => {
      const points = infos[index].sheet_widths?.[pageNumber - 1];
      const scale = Number(zoom.value) / 100;
      if (points > 0) {
        sheet.style.setProperty('--sheet-width', `${Math.round(points * 4 / 3 * scale)}px`);
      } else {
        sheet.style.removeProperty('--sheet-width');
      }
    });
  }

  function focusRegion(index) {
    activeRegion = index;
    const current = items.querySelector('[aria-current]');
    current?.removeAttribute('aria-current');
    items.querySelector(`[data-location="${pageNumber}:${index}"]`)?.setAttribute('aria-current', 'true');
    const region = regions[index];
    changePosition.textContent = `이 페이지의 변경 ${index + 1} / ${regions.length}`;
    updateHighlight();
    if (!pixels || !region) return;
    for (const { image, scroll } of sides) {
      const doc = image.getBoundingClientRect();
      const viewport = scroll.getBoundingClientRect();
      const left = scroll.scrollLeft + doc.left - viewport.left
        + (region.x + region.width / 2) * doc.width / pixels.width - scroll.clientWidth / 2;
      const top = scroll.scrollTop + doc.top - viewport.top
        + (region.y + region.height / 2) * doc.height / pixels.height - scroll.clientHeight / 2;
      scroll.scrollTo({ left, top });
    }
  }

  async function showNotes(number, current) {
    if (!notes) return;
    try {
      const response = await fetch(`${notes.dataset.pdfNotes}/${number}`);
      if (!response.ok || current !== generation) return;
      const { notes: entries } = await response.json();
      if (current !== generation) return;
      notes.querySelector('ul').replaceChildren(...entries.map(entry => {
        const item = document.createElement('li');
        item.textContent = entry;
        return item;
      }));
      notes.hidden = !entries.length;
    } catch {
      // A missing annotation list leaves the page preview available.
    }
  }

  async function render(number) {
    if (!Number.isInteger(number)) number = pageNumber;
    pageNumber = Math.max(1, Math.min(total, number));
    const current = ++generation;
    if (notes) notes.hidden = true;
    page.value = pageNumber;
    root.dispatchEvent(new CustomEvent('gitdoc:page', { detail: { number: pageNumber, total } }));
    setSheetWidths();
    loading = true;
    updateControls();
    pixels = null;
    regions = [];
    activeRegion = -1;
    if (comparing) {
      highlight.disabled = true;
      updateHighlight();
      changePosition.textContent = '';
      items.querySelector('[aria-current]')?.removeAttribute('aria-current');
    }
    status.textContent = navigating ? `${pageNumber}쪽에서 변경을 찾는 중입니다.` : '페이지를 불러오는 중입니다.';
    const loaded = await Promise.all(sides.map(async (side, index) => {
      side.sheet.hidden = true;
      side.placeholder.hidden = false;
      const info = infos[index];
      if (side.viewLink) {
        const url = new URL(side.viewLink.href);
        if (info.pages) url.searchParams.set('page', String(Math.min(info.pages, pageNumber)));
        side.viewLink.href = url;
      }
      if (!info.pages || pageNumber > info.pages) {
        side.placeholder.textContent = info.error || '이 버전에는 해당 페이지가 없습니다.';
        return false;
      }
      side.placeholder.textContent = '페이지 준비 중';
      const label = comparing ? (index === 0 ? '기준 버전' : '비교 버전') : '문서';
      side.image.alt = `${label} ${pageNumber}쪽`;
      side.image.src = `${side.url}/${pageNumber}.png`;
      try {
        await side.image.decode();
      } catch {
        if (current === generation) side.placeholder.textContent = '페이지 이미지를 열 수 없습니다.';
        return false;
      }
      if (current !== generation) return false;
      side.placeholder.hidden = true;
      side.sheet.hidden = false;
      side.scroll.scrollTo(0, 0);
      return true;
    }));
    if (current !== generation) return;
    loading = false;
    if (!comparing) {
      status.textContent = loaded[0] ? '' : '원본을 다운로드해 확인할 수 있습니다.';
      updateControls();
      if (loaded[0]) showNotes(pageNumber, current);
      return;
    }
    if (!loaded.every(Boolean)) {
      if (comparable && infos.some(info => pageNumber > info.pages) && loaded.some(Boolean)) {
        regions = [null];
        status.textContent = '한 버전에만 있는 페이지입니다.';
      } else {
        status.textContent = '양쪽에 페이지가 있을 때 시각적 차이를 표시합니다.';
      }
    } else {
      pixels = comparePixels(sides[0].image, sides[1].image);
      if (pixels) {
        regions = changeRegions(pixels);
        highlight.disabled = !regions.length;
        status.textContent = regions.length ? '분홍색은 기준본, 초록색은 수정본의 달라진 영역입니다.'
          : '이 페이지에서 눈에 띄는 시각적 차이가 없습니다.';
      } else {
        regions = [null];
        status.textContent = '페이지 크기가 달라 변경 위치를 겹쳐 표시할 수 없습니다.';
      }
    }
    if (comparable && loaded.every((ok, index) => ok || pageNumber > infos[index].pages)) {
      scanned.set(pageNumber, Promise.resolve(regions));
    }
    if (regions.length) changePosition.textContent = `이 페이지에 변경 ${regions.length}곳`;
    updateHighlight();
    updateControls();
  }

  async function seek(direction) {
    if (navigating || loading || !comparable) return;
    const withinPage = activeRegion < 0
      ? (direction > 0 ? 0 : regions.length - 1)
      : activeRegion + direction;
    if (withinPage >= 0 && withinPage < regions.length) {
      focusRegion(withinPage);
      return;
    }
    const startPage = pageNumber;
    const startRegion = activeRegion;
    navigating = true;
    updateControls();
    try {
      for (let number = startPage + direction; number >= 1 && number <= total; number += direction) {
        await render(number);
        if (regions.length) {
          focusRegion(direction > 0 ? 0 : regions.length - 1);
          return;
        }
      }
      if (pageNumber !== startPage) await render(startPage);
      if (startRegion >= 0) focusRegion(startRegion);
      status.textContent = direction > 0 ? '이후에 발견된 변경이 없습니다.' : '이전에 발견된 변경이 없습니다.';
    } finally {
      navigating = false;
      updateControls();
    }
  }

  async function scan(number) {
    if (!scanned.has(number)) {
      const result = (async () => {
        if (infos.some(info => number > info.pages)) return [null];
        const images = await Promise.all(sides.map(async side => {
          const image = new Image();
          image.src = `${side.url}/${number}.png`;
          await image.decode();
          return image;
        }));
        const result = comparePixels(...images);
        return result ? changeRegions(result) : [null];
      })();
      scanned.set(number, result);
    }
    return scanned.get(number);
  }

  let listedThrough = 0;
  let listing = false;
  let failures = 0;
  list?.addEventListener('toggle', async () => {
    if (!list.open || listing || listedThrough === total) return;
    listing = true;
    while (list.open && listedThrough < total) {
      const number = ++listedThrough;
      listStatus.textContent = `${number} / ${total}쪽 확인 중`;
      try {
        const changes = await scan(number);
        changes.forEach((region, index) => {
          const item = document.createElement('li');
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'secondary';
          button.dataset.location = `${number}:${index}`;
          button.textContent = region ? `${number}쪽 · 변경 ${index + 1}` : `${number}쪽 · 페이지 변경`;
          if (number === pageNumber && index === activeRegion) button.setAttribute('aria-current', 'true');
          button.addEventListener('click', async () => {
            if (navigating || loading) return;
            if (number !== pageNumber) await render(number);
            if (index < regions.length) focusRegion(index);
          });
          item.append(button);
          items.append(item);
        });
      } catch {
        failures += 1;
      }
    }
    if (listedThrough === total) {
      listStatus.textContent = `변경 ${items.childElementCount}곳${failures ? ` · 읽을 수 없는 페이지 ${failures}쪽` : ''}`;
    }
    listing = false;
  });

  page.max = total;
  totalLabel.textContent = `/ ${total}`;
  previous.addEventListener('click', () => render(pageNumber - 1));
  next.addEventListener('click', () => render(pageNumber + 1));
  page.addEventListener('change', () => render(page.valueAsNumber));
  previousChange?.addEventListener('click', () => seek(-1));
  nextChange?.addEventListener('click', () => seek(1));
  highlight?.addEventListener('change', updateHighlight);
  zoom.addEventListener('change', () => {
    root.dataset.magnification = zoom.value;
    setSheetWidths();
    if (activeRegion >= 0) focusRegion(activeRegion);
  });
  if (comparing) {
    lockScrolling(sides, sync);
    list.hidden = !comparable;
  }
  const params = new URLSearchParams(location.search);
  await render(Number(root.closest('[data-document-editor]')?.dataset.page || params.get('page') || 1));
  const initialChange = Number(params.get('change'));
  if (Number.isInteger(initialChange) && initialChange > 0 && initialChange <= regions.length) {
    focusRegion(initialChange - 1);
  }
}

document.querySelectorAll('[data-visual-viewer]').forEach(root => {
  mountViewer(root).catch(() => {
    root.querySelector('[data-status]').textContent = '미리보기를 표시할 수 없습니다. 원본을 확인하세요.';
  });
});
