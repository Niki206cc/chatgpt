document.addEventListener('DOMContentLoaded', function () {
  // Quando viene scelta manualmente una foto, seleziona automaticamente
  // la modalità "Carica immagine" per evitare l'invio involontario della default.
  const imageUpload = document.querySelector('input[name="image_upload"]');
  const uploadMode = document.querySelector('input[name="image_mode"][value="upload"]');
  if (imageUpload && uploadMode) {
    imageUpload.addEventListener('change', function () {
      if (imageUpload.files && imageUpload.files.length > 0) {
        uploadMode.checked = true;
      }
    });
  }

  const selectAll = document.getElementById('select-all-mails');
  const mailCheckboxes = Array.from(document.querySelectorAll('.mail-checkbox'));
  if (selectAll) {
    selectAll.addEventListener('change', function () {
      mailCheckboxes.forEach(function (checkbox) {
        const row = checkbox.closest('tr');
        if (!row || row.style.display !== 'none') checkbox.checked = selectAll.checked;
      });
    });
  }

  const table = document.getElementById('mails-table');
  if (!table) return;
  const tbody = table.querySelector('tbody');
  const sortButtons = Array.from(table.querySelectorAll('.sort-btn'));
  const sortState = {};
  sortButtons.forEach(function (button) {
    button.addEventListener('click', function () {
      const key = button.dataset.sort;
      const direction = sortState[key] === 'asc' ? 'desc' : 'asc';
      sortState[key] = direction;
      sortButtons.forEach(function (btn) { btn.classList.remove('active', 'asc', 'desc'); });
      button.classList.add('active', direction);
      const rows = Array.from(tbody.querySelectorAll('tr')).filter(function (row) { return row.dataset && Object.prototype.hasOwnProperty.call(row.dataset, key); });
      rows.sort(function (a, b) {
        let av = a.dataset[key] || ''; let bv = b.dataset[key] || '';
        if (key === 'size') { av = Number(av || 0); bv = Number(bv || 0); }
        else { av = av.toLocaleLowerCase('it-IT'); bv = bv.toLocaleLowerCase('it-IT'); }
        if (av < bv) return direction === 'asc' ? -1 : 1;
        if (av > bv) return direction === 'asc' ? 1 : -1;
        return 0;
      });
      rows.forEach(function (row) { tbody.appendChild(row); });
    });
  });
});
