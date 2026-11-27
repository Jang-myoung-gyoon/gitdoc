for (const workspace of document.querySelectorAll('[data-document-editor]')) {
  const form = workspace.querySelector('[data-edit-form]');
  if (!form) continue;

  const pages = workspace.querySelector('[data-visual-viewer]');
  if (form.elements.operation && pages) {
    const note = form.querySelector('[data-note-field]');
    const insert = form.querySelector('[data-insert-field]');
    let total = null;
    const updateOperation = () => {
      note.hidden = form.elements.operation.value !== 'note';
      form.elements.note.required = !note.hidden;
      insert.hidden = form.elements.operation.value !== 'insert';
      form.elements.file.disabled = insert.hidden;
      if (total !== null) form.elements.page.max = total + (insert.hidden ? 0 : 1);
    };
    form.elements.operation.addEventListener('change', updateOperation);
    updateOperation();
    pages.addEventListener('gitdoc:page', event => {
      total = event.detail.total;
      updateOperation();
      if (insert.hidden || Number(form.elements.page.value) !== total + 1) {
        form.elements.page.value = event.detail.number;
      }
    });
  }

  const sheets = workspace.querySelector('[data-workbook-viewer]');
  if (form.elements.sheet && sheets) {
    sheets.addEventListener('gitdoc:sheet', event => {
      const { name, address } = event.detail;
      form.elements.sheet.value = name;
      form.elements.cell.value = address;
    });
    sheets.addEventListener('gitdoc:cell-edit', event => {
      const { value, formula, kind } = event.detail;
      form.elements.value_kind.value = formula == null ? kind || 'text' : 'formula';
      form.elements.value.value = formula == null ? value ?? '' : '=' + formula;
    });
    sheets.addEventListener('gitdoc:sheets', event => {
      form.querySelector('#edit-sheets').replaceChildren(...event.detail.names.map(name => {
        const option = document.createElement('option');
        option.value = name;
        return option;
      }));
    });
  }
}
