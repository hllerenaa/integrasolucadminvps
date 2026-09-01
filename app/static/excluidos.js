/* Página de sistemas excluidos del panel */
(function () {
  'use strict';
  function $(s) { return document.querySelector(s); }
  function esc(v) {
    return String(v === null || v === undefined ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function nombresDesdeTexto() {
    return ($('#editor-excluidos').value || '')
      .replace(/;/g, ',').split(/[,\n]/)
      .map(function (n) { return n.trim().toLowerCase(); })
      .filter(Boolean);
  }

  function sincronizarTexto() {
    var nombres = nombresDesdeTexto();
    document.querySelectorAll('input.ver').forEach(function (chk) {
      var cliente = (chk.getAttribute('data-cliente') || '').toLowerCase();
      var servicio = (chk.getAttribute('data-servicio') || '').toLowerCase();
      var oculta = nombres.indexOf(cliente) !== -1 || nombres.indexOf(servicio) !== -1;
      chk.checked = !oculta;
      var etiqueta = chk.parentNode.querySelector('.badge');
      if (etiqueta) {
        etiqueta.className = 'badge ' + (oculta ? 'gris' : 'verde');
        etiqueta.textContent = oculta ? 'oculta' : 'visible';
      }
    });
  }

  function alternar(chk) {
    var nombres = nombresDesdeTexto();
    var cliente = (chk.getAttribute('data-cliente') || '').toLowerCase();
    var servicio = (chk.getAttribute('data-servicio') || '').toLowerCase();
    if (chk.checked) {
      nombres = nombres.filter(function (n) { return n !== cliente && n !== servicio; });
    } else if (nombres.indexOf(servicio) === -1 && nombres.indexOf(cliente) === -1) {
      nombres.push(servicio || cliente);
    }
    $('#editor-excluidos').value = nombres.join(',');
    sincronizarTexto();
  }

  function guardar() {
    var boton = $('#btn-guardar');
    boton.disabled = true;
    $('#resultado').innerHTML = '<p class="tenue">Guardando…</p>';
    fetch('/api/excluidos', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nombres: nombresDesdeTexto() })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        boton.disabled = false;
        if (!d.ok) {
          $('#resultado').innerHTML = '<div class="aviso-error">' + esc(d.error) + '</div>';
          return;
        }
        $('#resultado').innerHTML = '<div class="aviso-ok">Guardado en ' + esc(d.archivo) +
          ' · ocultos: ' + (d.nombres.length ? esc(d.nombres.join(', ')) : 'ninguno') +
          '. El panel se está refrescando.</div>';
        sincronizarTexto();
      })
      .catch(function (e) {
        boton.disabled = false;
        $('#resultado').innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>';
      });
  }

  document.addEventListener('DOMContentLoaded', function () {
    $('#btn-guardar').addEventListener('click', guardar);
    $('#editor-excluidos').addEventListener('input', sincronizarTexto);
    document.addEventListener('change', function (e) {
      if (e.target.classList.contains('ver')) alternar(e.target);
    });
    $('#filtro-texto').addEventListener('input', function () {
      var txt = (this.value || '').toLowerCase().trim();
      document.querySelectorAll('#tabla-excluidos tbody tr').forEach(function (fila) {
        fila.hidden = !!txt && (fila.getAttribute('data-buscar') || '').indexOf(txt) === -1;
      });
    });
  });
})();
