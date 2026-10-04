function toggleNav(){
  const el = document.getElementById('mainnav');
  el.classList.toggle('show');
}
function confirmSubmit(){
  return confirm("Kirim pengajuan tarik gaji sekarang?");
}

// Kunci scroll halaman saat panel floating apa pun sedang terbuka.
(function initFloatingPanelScrollLock(){
  function sync(){
    var open = Array.from(document.querySelectorAll('.modal, .modal-overlay, .invoice-overlay')).some(function(el){
      var style = window.getComputedStyle(el);
      return style.display !== 'none' && style.visibility !== 'hidden';
    });
    document.body.classList.toggle('modal-open', open);
  }
  sync();
  new MutationObserver(sync).observe(document.body, {subtree:true, attributes:true, attributeFilter:['class','style','aria-hidden']});
})();

(function initThemeToggle(){
  const btn = document.getElementById('theme-toggle');
  const label = document.getElementById('theme-toggle-label');
  if (!btn || !label) return;

  const storageKey = 'gajiku-theme';
  const body = document.body;

  function applyTheme(nextTheme){
    if (nextTheme === 'new') {
      body.classList.add('theme-new');
      label.textContent = 'New Tema';
      btn.setAttribute('aria-pressed', 'true');
      btn.classList.add('active');
    } else {
      body.classList.remove('theme-new');
      label.textContent = 'Legacy';
      btn.setAttribute('aria-pressed', 'false');
      btn.classList.remove('active');
    }
  }

  const saved = localStorage.getItem(storageKey);
  if (saved === 'new' || saved === 'legacy') {
    applyTheme(saved);
  } else {
    applyTheme(body.classList.contains('theme-new') ? 'new' : 'legacy');
  }

  btn.addEventListener('click', function(){
    const nextTheme = body.classList.contains('theme-new') ? 'legacy' : 'new';
    localStorage.setItem(storageKey, nextTheme);
    applyTheme(nextTheme);
  });

  btn.addEventListener('keydown', function(e){
    if (e.key !== 'Enter' && e.key !== ' ') return;
    e.preventDefault();
    btn.click();
  });
})();

(function initPasswordToggle(){
  var toggles = document.querySelectorAll('[data-toggle-password]');
  if (!toggles.length) return;

  toggles.forEach(function(btn){
    var targetId = btn.getAttribute('data-toggle-password');
    var input = document.getElementById(targetId);
    if (!input) return;

    btn.addEventListener('click', function(){
      var showing = input.type === 'text';
      input.type = showing ? 'password' : 'text';
      btn.setAttribute('aria-pressed', String(!showing));
      btn.setAttribute('aria-label', showing ? 'Tampilkan password' : 'Sembunyikan password');
      var label = btn.querySelector('.password-toggle__text');
      if (label) {
        label.textContent = showing ? 'Tampilkan' : 'Sembunyikan';
      }
    });
  });
})();

(function initResponsiveTables(){
  function textOf(el){
    return (el ? el.textContent : '').replace(/\s+/g, ' ').trim();
  }

  function isActionLabel(label){
    return /^(aksi|action)$/i.test(label || '');
  }

  function cloneCellContent(cell){
    var wrap = document.createDocumentFragment();
    if (!cell) return wrap;

    Array.from(cell.childNodes).forEach(function(node){
      wrap.appendChild(node.cloneNode(true));
    });

    if (!textOf(cell) && !cell.children.length) {
      wrap.appendChild(document.createTextNode('-'));
    }

    return wrap;
  }

  function copyResponsiveData(target, cells){
    cells.forEach(function(cell){
      if (!cell) return;
      if (cell.dataset && cell.dataset.txid) {
        target.dataset.txid = cell.dataset.txid;
      }
    });
  }

  function copyStateClasses(target, cell){
    if (!target || !cell || !cell.classList) return;
    ['ok', 'bad', 'warn'].forEach(function(cls){
      if (cell.classList.contains(cls)) {
        target.classList.add(cls);
      }
    });
  }

  function buildColumnPairs(labels){
    var pairs = [];
    for (var i = 0; i < labels.length; i += 1) {
      var primaryLabel = labels[i] || '';
      var secondaryLabel = labels[i + 1] || '';
      var shouldPair = secondaryLabel && !isActionLabel(primaryLabel) && !isActionLabel(secondaryLabel);
      pairs.push({
        primary: primaryLabel,
        secondary: shouldPair ? secondaryLabel : '',
        action: isActionLabel(primaryLabel),
        start: i,
        end: shouldPair ? i + 1 : i,
      });
      if (shouldPair) i += 1;
    }
    return pairs;
  }

  function pairSpanForRange(pairs, start, span){
    var end = start + Math.max(span, 1) - 1;
    return pairs.filter(function(pair){
      return pair.start <= end && pair.end >= start;
    }).length || 1;
  }

  function makeHeaderCell(pair){
    var th = document.createElement('th');
    th.className = 'mobile-stack-head-cell';
    if (pair.action) th.classList.add('mobile-stack-cell--action');

    var primary = document.createElement('b');
    primary.className = 'mobile-stack-label-primary';
    primary.textContent = pair.primary || '';
    th.appendChild(primary);

    if (pair.secondary) {
      var secondary = document.createElement('span');
      secondary.className = 'mobile-stack-label-secondary';
      secondary.textContent = pair.secondary;
      th.appendChild(secondary);
    }

    return th;
  }

  function makeDataCell(primaryCell, secondaryCell, primaryLabel){
    var td = document.createElement('td');
    td.className = 'mobile-stack-cell';
    if (!secondaryCell) td.classList.add('mobile-stack-cell--single');
    if (isActionLabel(primaryLabel)) td.classList.add('mobile-stack-cell--action');
    copyResponsiveData(td, [primaryCell, secondaryCell]);

    var primary = document.createElement('div');
    primary.className = 'mobile-stack-value-primary';
    copyStateClasses(primary, primaryCell);
    primary.appendChild(cloneCellContent(primaryCell));
    td.appendChild(primary);

    if (secondaryCell) {
      var secondary = document.createElement('div');
      secondary.className = 'mobile-stack-value-secondary';
      copyStateClasses(secondary, secondaryCell);
      secondary.appendChild(cloneCellContent(secondaryCell));
      td.appendChild(secondary);
    }

    return td;
  }

  function makeFullDataCell(cell, colspan){
    var td = document.createElement('td');
    td.className = 'mobile-stack-cell mobile-stack-cell--full';
    td.colSpan = Math.max(colspan, 1);
    copyResponsiveData(td, [cell]);
    td.appendChild(cloneCellContent(cell));
    return td;
  }

  function buildMobileTable(table, labels){
    var pairs = buildColumnPairs(labels);
    var mobile = document.createElement('table');
    mobile.className = 'mobile-stack-table';

    var thead = document.createElement('thead');
    var headRow = document.createElement('tr');
    pairs.forEach(function(pair){
      headRow.appendChild(makeHeaderCell(pair));
    });
    thead.appendChild(headRow);
    mobile.appendChild(thead);

    ['tbody', 'tfoot'].forEach(function(sectionName){
      var sourceSection = table.querySelector(sectionName);
      if (!sourceSection) return;

      var section = document.createElement(sectionName);
      Array.from(sourceSection.rows).forEach(function(row){
        var cells = Array.from(row.children).filter(function(cell){
          return cell.matches('td, th');
        });
        if (!cells.length) return;

        var mobileRow = document.createElement('tr');
        var labelIndex = 0;
        for (var i = 0; i < cells.length; i += 1) {
          var cell = cells[i];
          var span = Number(cell.getAttribute('colspan') || 1);
          var primaryLabel = labels[labelIndex] || '';

        if (span > 1 || cells.length === 1) {
          var fullSpan = cells.length === 1 ? pairs.length : pairSpanForRange(pairs, labelIndex, span);
          mobileRow.appendChild(makeFullDataCell(cell, fullSpan));
          labelIndex += span;
          continue;
        }

          var nextCell = cells[i + 1];
          var nextSpan = nextCell ? Number(nextCell.getAttribute('colspan') || 1) : 1;
          var secondaryLabel = labels[labelIndex + 1] || '';
          var shouldPair = nextCell && nextSpan === 1 && secondaryLabel && !isActionLabel(primaryLabel) && !isActionLabel(secondaryLabel);

          if (shouldPair) {
            mobileRow.appendChild(makeDataCell(cell, nextCell, primaryLabel));
            i += 1;
            labelIndex += 2;
          } else if (textOf(cell) || cell.children.length) {
            mobileRow.appendChild(makeDataCell(cell, null, primaryLabel));
            labelIndex += 1;
          } else {
            labelIndex += 1;
          }
        }
        section.appendChild(mobileRow);
      });

      if (section.rows.length) {
        mobile.appendChild(section);
      }
    });

    return mobile;
  }

  function decorateTable(table){
    if (!table || table.dataset.responsiveReady === '1') return;
    var headerRows = table.tHead ? Array.from(table.tHead.rows) : [];
    if (!headerRows.length) return;

    var headerCells = Array.from(headerRows[headerRows.length - 1].cells);
    var labels = [];
    headerCells.forEach(function(cell){
      var label = textOf(cell);
      var span = Number(cell.getAttribute('colspan') || 1);
      for (var i = 0; i < span; i += 1) {
        labels.push(label);
      }
    });
    if (!labels.length) return;

    Array.from(table.querySelectorAll('tbody tr, tfoot tr')).forEach(function(row){
      var cells = Array.from(row.children).filter(function(cell){
        return cell.matches('td, th');
      });
      var labelIndex = 0;

      cells.forEach(function(cell){
        var span = Number(cell.getAttribute('colspan') || 1);
        if (span > 1 || cells.length === 1) {
          cell.dataset.tableFull = '1';
        }
        if (!cell.dataset.label && span === 1) {
          cell.dataset.label = labels[labelIndex] || '';
        }
        labelIndex += span;
      });
    });

    table.insertAdjacentElement('afterend', buildMobileTable(table, labels));
    table.dataset.responsiveReady = '1';
  }

  function init(){
    Array.from(document.querySelectorAll('table')).forEach(decorateTable);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
