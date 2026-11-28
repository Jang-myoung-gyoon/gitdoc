function parseCell(address) {
  const parts = /^([A-Z]{1,3})([1-9]\d{0,6})$/.exec(address);
  if (!parts) return null;
  const col = Array.from(parts[1]).reduce((value, letter) => value * 26 + letter.charCodeAt(0) - 64, 0);
  const row = Number(parts[2]);
  return col <= 16384 && row <= 1048576 ? { row, col } : null;
}

function columnName(col) {
  let name = '';
  while (col > 0) {
    col -= 1;
    name = String.fromCharCode(65 + col % 26) + name;
    col = Math.floor(col / 26);
  }
  return name;
}

function styleCell(node, style) {
  if (!style) return;
  if (style.bold) node.style.fontWeight = '700';
  if (style.italic) node.style.fontStyle = 'italic';
  if (/^[0-9a-f]{6}$/i.test(style.color)) node.style.color = '#' + style.color;
  if (/^[0-9a-f]{6}$/i.test(style.fill)) node.style.backgroundColor = '#' + style.fill;
  if (['left', 'center', 'right'].includes(style.align)) node.style.textAlign = style.align;
}

function grid(sheet, row, col, changed, selected) {
  if (!sheet) {
    const placeholder = document.createElement('p');
    placeholder.className = 'viewer-placeholder';
    placeholder.textContent = '이 버전에는 해당 시트가 없습니다.';
    return placeholder;
  }
  const table = document.createElement('table');
  table.setAttribute('aria-label', sheet.name);
  table.className = 'sheet-table';
  const header = table.createTHead().insertRow();
  const columnEnd = Math.min(col + 15, 16384);
  const rowEnd = Math.min(row + 79, 1048576);
  for (let j = col - 1; j <= columnEnd; j += 1) {
    const th = document.createElement('th');
    th.scope = 'col';
    th.textContent = j < col ? '' : columnName(j);
    header.append(th);
  }
  const spans = new Map();
  const covered = new Set();
  for (const range of sheet.merges) {
    const [start, end] = range.split(':').map(parseCell);
    if (!start || !end || end.row < row || end.col < col || start.row > rowEnd || start.col > columnEnd) continue;
    const fromRow = Math.max(start.row, row);
    const fromCol = Math.max(start.col, col);
    const toRow = Math.min(end.row, rowEnd);
    const toCol = Math.min(end.col, columnEnd);
    const origin = columnName(start.col) + start.row;
    spans.set(columnName(fromCol) + fromRow, {
      rows: toRow - fromRow + 1, cols: toCol - fromCol + 1, origin,
      selected: selected && selected.row >= start.row && selected.row <= end.row &&
        selected.col >= start.col && selected.col <= end.col,
    });
    for (let i = fromRow; i <= toRow; i += 1) {
      for (let j = fromCol; j <= toCol; j += 1) {
        if (i !== fromRow || j !== fromCol) covered.add(columnName(j) + i);
      }
    }
  }
  const body = table.createTBody();
  for (let i = row; i <= rowEnd; i += 1) {
    const tr = body.insertRow();
    const th = document.createElement('th');
    th.scope = 'row';
    th.textContent = i;
    tr.append(th);
    for (let j = col; j <= columnEnd; j += 1) {
      const address = columnName(j) + i;
      if (covered.has(address)) continue;
      const td = tr.insertCell();
      const span = spans.get(address);
      const origin = span?.origin ?? address;
      td.dataset.address = origin;
      const [value, formula, style] = sheet.cells[origin] || [];
      if (span) {
        td.rowSpan = span.rows;
        td.colSpan = span.cols;
      }
      td.textContent = value ?? (formula == null ? '' : '=' + formula);
      td.title = origin + (formula == null ? '' : ' · =' + formula) + (value == null ? '' : ' · ' + value);
      styleCell(td, style);
      if (changed.has(origin)) td.classList.add('changed-cell');
      if (span?.selected || (selected?.row === i && selected.col === j)) {
        td.classList.add('selected-cell');
        td.setAttribute('aria-current', 'true');
      }
    }
  }
  return table;
}

function changesFor(name, left, right) {
  if (!left || !right) return [{ name, address: '', label: left ? '삭제된 시트' : '추가된 시트' }];
  const cells = new Set([...Object.keys(left.cells), ...Object.keys(right.cells)]);
  const all = new Map();
  for (const address of cells) {
    const oldCell = left.cells[address];
    const newCell = right.cells[address];
    if (JSON.stringify(oldCell) === JSON.stringify(newCell) || !parseCell(address)) continue;
    const styleOnly = oldCell && newCell && oldCell[0] === newCell[0] && oldCell[1] === newCell[1] && oldCell[3] === newCell[3];
    all.set(address, styleOnly ? '서식 변경' : '셀 변경');
  }
  for (const range of new Set([...left.merges, ...right.merges])) {
    if (left.merges.includes(range) === right.merges.includes(range)) continue;
    const address = range.split(':')[0];
    if (parseCell(address)) all.set(address, range + ' 병합 변경');
  }
  return Array.from(all, ([address, label]) => ({ name, address, label })).sort((a, b) => {
    const first = parseCell(a.address);
    const second = parseCell(b.address);
    return first.row - second.row || first.col - second.col;
  });
}

async function mountWorkbook(root) {
  const scroll = root.querySelector('[data-sheet-scroll]');
  const panes = scroll.querySelector('.sheet-panes');
  const links = root.querySelectorAll('[data-sheet-view-link]');
  const sides = Array.from(root.querySelectorAll('[data-workbook]'), (node, index) => ({
    node, url: node.dataset.workbook,
    viewLink: links[index], version: versionLocation(node),
  }));
  const setEditLocation = editLocationControl(root);
  const status = root.querySelector('[data-status]');
  const position = root.querySelector('[data-change-position]');
  const tabs = root.querySelector('[data-sheet-tabs]');
  const list = root.querySelector('[data-change-list]');
  const listItems = root.querySelector('[data-change-items]');
  const prev = root.querySelector('[data-previous-change]');
  const next = root.querySelector('[data-next-change]');
  const form = root.querySelector('[data-cell-form]');
  const books = await Promise.all(sides.map(async side => {
    const response = await fetch(side.url);
    const info = await response.json();
    if (!response.ok) throw new Error(info.error);
    return new Map(info.sheets.map(sheet => [sheet.name, sheet]));
  }));
  const comparing = sides.length === 2;
  const names = Array.from(new Set(books.flatMap(book => Array.from(book.keys()))));
  if (!names.length) throw new Error('표시할 시트가 없습니다.');
  const sheetChanges = new Map(names.map(name => [
    name, comparing ? changesFor(name, ...books.map(book => book.get(name))) : [],
  ]));
  const changes = names.flatMap(name => sheetChanges.get(name));
  let activeSheet;
  let current = -1;

  function updateHorizontalOffset() {
    panes.style.setProperty('--sheet-offset', scroll.scrollLeft + 'px');
  }

  function layoutSheets() {
    // A shared track drives both tables while their panes remain side by side.
    panes.style.setProperty('--sheet-width', scroll.clientWidth + 'px');
    const overflow = Math.max(0, ...sides.map(({ node }) =>
      (node.querySelector('table')?.offsetWidth || 0) - node.clientWidth));
    scroll.style.setProperty('--sheet-overflow', overflow + 'px');
    updateHorizontalOffset();
  }

  function identifyCell(side, name, displayedAddress) {
    const sheet = books[side].get(name);
    const address = parseCell(displayedAddress);
    if (!sheet || !address) {
      setEditLocation(null);
      return;
    }
    const sourceRow = sheet.source_rows ? sheet.source_rows[address.row - 1] : address.row;
    const sourceAddress = sourceRow ? columnName(address.col) + sourceRow : null;
    const [value = null, formula = null] = sheet.cells[displayedAddress] || [];
    setEditLocation({
      kind: 'worksheet_cell', ...sides[side].version, sheet: name, cell: sourceAddress,
      ...(comparing ? { comparison_side: side === 0 ? 'base' : 'target' } : {}),
      ...(sheet.source_rows ? { displayed_cell: displayedAddress,
        row_key: new URL(sides[side].url, location.href).searchParams.get('row_key') } : {}),
      value, formula
    }, { sheet: name, cell: displayedAddress, cell_side: side });
  }

  function show(name, address = '', index = -1, selectedSide = null) {
    activeSheet = name;
    current = index;
    const cell = parseCell(address);
    const row = Math.max(1, (cell?.row || 1) - 5);
    const col = Math.max(1, (cell?.col || 1) - 2);
    const changed = new Set(sheetChanges.get(name).map(change => change.address));
    sides.forEach(({ node }, side) => {
      node.replaceChildren(grid(books[side].get(name), row, col, changed,
        selectedSide === null || selectedSide === side ? cell : null));
      const link = sides[side].viewLink;
      if (link) {
        const url = new URL(link.href);
        url.searchParams.set('sheet', name);
        if (address) url.searchParams.set('cell', address);
        else url.searchParams.delete('cell');
        link.href = url;
      }
    });
    scroll.scrollTo(0, 0);
    layoutSheets();
    const selected = root.querySelector('.selected-cell');
    if (selected) {
      const cell = selected.getBoundingClientRect();
      const pane = selected.closest('[data-workbook]').getBoundingClientRect();
      const viewport = scroll.getBoundingClientRect();
      scroll.scrollTo(Math.max(0, cell.right - pane.right),
        Math.max(0, cell.bottom - viewport.top - scroll.clientHeight));
      updateHorizontalOffset();
    }
    tabs.value = name;
    form.elements.cell.value = address;
    identifyCell(selectedSide ?? (books.at(-1).has(name) ? sides.length - 1 : 0), name, address);
    const label = address ? `${name} · ${address}` : name;
    status.textContent = label;
    if (comparing) {
      prev.disabled = !changes.length || current === 0;
      next.disabled = !changes.length || current === changes.length - 1;
      listItems.querySelector('[aria-current]')?.removeAttribute('aria-current');
      listItems.querySelector(`[data-index="${current}"]`)?.setAttribute('aria-current', 'true');
      status.textContent = `${label} · ${sheetChanges.get(name).length}곳 변경`;
      position.textContent = current >= 0 ? `변경 ${current + 1} / ${changes.length}` : '';
    }
  }

  function jump(index) {
    if (index < 0 || index >= changes.length) return;
    const { name, address } = changes[index];
    show(name, address, index);
  }

  for (const name of names) {
    tabs.add(new Option(name, name));
  }
  tabs.addEventListener('change', () => show(tabs.value));
  scroll.addEventListener('scroll', updateHorizontalOffset, { passive: true });
  new ResizeObserver(layoutSheets).observe(scroll);
  if (comparing) {
    let shown = 0;
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'secondary';
    more.textContent = '변경 더 보기';
    function showMore() {
      const until = Math.min(shown + 150, changes.length);
      for (; shown < until; shown += 1) {
        const { name, address, label } = changes[shown];
        const item = document.createElement('li');
        const button = document.createElement('button');
        const index = shown;
        button.type = 'button';
        button.className = 'secondary';
        button.dataset.index = index;
        button.textContent = name + (address ? ' · ' + address : '') + ' · ' + label;
        if (index === current) button.setAttribute('aria-current', 'true');
        button.addEventListener('click', () => jump(index));
        item.append(button);
        listItems.append(item);
      }
      more.hidden = shown >= changes.length;
    }
    more.addEventListener('click', showMore);
    list.append(more);
    list.querySelector('[data-list-status]').textContent = `전체 변경 ${changes.length}곳`;
    showMore();
    list.hidden = false;
  }
  form.querySelector('button').disabled = false;
  form.addEventListener('submit', event => {
    event.preventDefault();
    const address = form.elements.cell.value.trim().toUpperCase();
    if (!parseCell(address)) {
      status.textContent = 'A1과 같은 셀 주소를 입력하세요.';
      return;
    }
    show(activeSheet, address, changes.findIndex(change => change.name === activeSheet && change.address === address));
  });
  if (comparing) {
    prev.addEventListener('click', () => jump(current < 0 ? changes.length - 1 : current - 1));
    next.addEventListener('click', () => jump(current + 1));
  }
  sides.forEach(({ node }, side) => {
    node.addEventListener('click', event => {
      const target = event.target.closest('td[data-address]');
      if (!target) return;
      const address = target.dataset.address;
      for (const old of root.querySelectorAll('.selected-cell')) {
        old.classList.remove('selected-cell');
        old.removeAttribute('aria-current');
      }
      target.classList.add('selected-cell');
      target.setAttribute('aria-current', 'true');
      form.elements.cell.value = address;
      identifyCell(side, activeSheet, address);
    });
  });
  const params = new URLSearchParams(location.search);
  const requestedSheet = params.get('sheet');
  const sheet = names.includes(requestedSheet) ? requestedSheet : names[0];
  const address = (params.get('cell') || '').toUpperCase();
  const requestedSide = params.get('cell_side');
  const side = requestedSide === '0' ? 0 : requestedSide === '1' && comparing ? 1 : null;
  show(sheet, parseCell(address) ? address : '',
    changes.findIndex(change => change.name === sheet && address && change.address === address), side);
}

for (const root of document.querySelectorAll('[data-workbook-viewer]')) {
  mountWorkbook(root).catch(error => {
    root.querySelector('[data-reader-alert]').textContent = error.message || '시트를 읽을 수 없습니다.';
  });
}
