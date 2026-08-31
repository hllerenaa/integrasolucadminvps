/* Panel VPS - Integrasoluc :: lógica del tablero */
(function () {
  'use strict';

  var estado = {
    datos: [], meta: {}, resumen: {}, disco: {}, capacidades: {},
    orden: 'cliente', asc: true
  };
  var temporizador = null;

  // ------------------------------------------------------------- utilidades
  function $(sel) { return document.querySelector(sel); }
  function esc(v) {
    if (v === null || v === undefined || v === '') return '';
    return String(v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function texto(v) {
    return (v === null || v === undefined || v === '') ? '<span class="tenue">—</span>' : esc(v);
  }
  function badge(clase, etiqueta, titulo) {
    return '<span class="badge ' + clase + '"' + (titulo ? ' title="' + esc(titulo) + '"' : '') +
           '>' + esc(etiqueta) + '</span>';
  }

  function badgeServicio(inst) {
    var s = inst.servicio_estado || {};
    if (s.activo) return badge('verde', s.estado === 'activating' ? 'iniciando' : 'activo', s.uptime);
    if (s.estado === 'no-encontrado') return badge('gris', 'sin unidad', s.error);
    if (s.estado === 'failed') return badge('rojo', 'fallido', s.error);
    return badge('rojo', s.estado || 'inactivo', s.error);
  }

  function badgeBase(inst) {
    var db = inst.db || {};
    if (db.desactivado) return badge('gris', 'no consultada');
    return db.ok ? badge('verde', 'activa') : badge('rojo', 'caída', db.error);
  }

  function badgeUrl(inst) {
    var u = inst.url_estado || {};
    if (!inst.url) return badge('gris', 'sin dominio');
    if (u.responde === null || u.responde === undefined) return badge('gris', 'sin verificar');
    if (u.responde) {
      var clase = (u.codigo && u.codigo < 400) ? 'verde' : 'ambar';
      return badge(clase, 'HTTP ' + u.codigo, inst.url + ' · ' + (u.tiempo_ms || 0) + ' ms');
    }
    return badge('rojo', 'sin respuesta', u.error);
  }

  function badgeSsl(inst) {
    var c = inst.ssl || {};
    if (c.estado === 'vigente') return badge('verde', (c.dias_restantes || 0) + 'd', 'Vence ' + c.valido_hasta + ' · ' + (c.emisor || ''));
    if (c.estado === 'por-vencer') return badge('ambar', (c.dias_restantes || 0) + 'd', 'Vence ' + c.valido_hasta);
    if (c.estado === 'vencido') return badge('rojo', 'vencido', 'Venció ' + c.valido_hasta);
    return badge('gris', 'sin SSL', c.error || '');
  }

  function badgeApache(inst) {
    var a = inst.apache || {};
    if (!a.archivo) return badge('gris', 'sin vhost', a.error || '');
    return a.habilitado ? badge('verde', 'habilitado', a.nombre) : badge('rojo', 'deshabilitado', a.nombre);
  }

  function fechaConAviso(valor, dias, limite) {
    if (!valor) return '<span class="tenue">—</span>';
    var clase = (dias !== null && dias !== undefined && dias > limite) ? ' class="viejo"' : '';
    var sufijo = (dias !== null && dias !== undefined) ? ' <span class="tenue">(' + dias + 'd)</span>' : '';
    return '<span' + clase + '>' + esc(String(valor).substring(0, 16)) + '</span>' + sufijo;
  }

  // ---------------------------------------------------------------- tarjetas
  function pintarTarjetas() {
    var r = estado.resumen || {}, d = estado.disco || {}, cap = estado.capacidades || {};
    var tarjetas = [
      { rotulo: 'Instancias', valor: r.total || 0,
        extra: Object.keys(r.por_tipo || {}).map(function (k) { return k + ': ' + r.por_tipo[k]; }).join(' · ') },
      { rotulo: 'Servicios activos', valor: (r.servicios_activos || 0) + '/' + (r.total || 0),
        clase: r.servicios_inactivos ? 'mal' : 'ok' },
      { rotulo: 'Sitios Apache', valor: (r.sitios_habilitados || 0) + '/' + (r.total || 0) },
      { rotulo: 'SSL vigentes', valor: (r.ssl_vigentes || 0) + '/' + (r.total || 0),
        clase: r.ssl_alerta ? 'mal' : 'ok',
        extra: r.ssl_alerta ? (r.ssl_alerta + ' por vencer/vencidos') : '' }
    ];
    if (cap.url) {
      tarjetas.push({ rotulo: 'URLs respondiendo', valor: (r.urls_ok || 0) + '/' + (r.total || 0),
                      clase: (r.urls_ok === r.total) ? 'ok' : 'mal' });
    }
    if (cap.bd) {
      tarjetas.push({ rotulo: 'Bases activas', valor: (r.db_activas || 0) + '/' + (r.total || 0),
                      clase: r.db_caidas ? 'mal' : 'ok' });
      tarjetas.push({ rotulo: 'Tamaño total BD', valor: r.db_tamano || '-' });
    }
    if (cap.media) tarjetas.push({ rotulo: 'Tamaño total media', valor: r.media_tamano || '-' });
    tarjetas.push({ rotulo: 'Disco del servidor',
      valor: (d.porcentaje !== undefined && d.porcentaje !== null) ? d.porcentaje + '%' : '-',
      extra: d.usado ? (d.usado + ' de ' + d.total + ' · libre ' + d.libre) : '' });

    $('#tarjetas').innerHTML = tarjetas.map(function (t) {
      return '<div class="tarjeta ' + (t.clase || '') + '">' +
        '<div class="rotulo">' + esc(t.rotulo) + '</div>' +
        '<div class="valor">' + esc(t.valor) + '</div>' +
        (t.extra ? '<div class="rotulo">' + esc(t.extra) + '</div>' : '') + '</div>';
    }).join('');
  }

  function aplicarCapacidades() {
    var cap = estado.capacidades || {};
    document.querySelectorAll('.col-bd').forEach(function (el) { el.style.display = cap.bd ? '' : 'none'; });
    document.querySelectorAll('.col-media').forEach(function (el) { el.style.display = cap.media ? '' : 'none'; });
  }

  // ------------------------------------------------------------------ tabla
  function filtrar(lista) {
    var txt = ($('#filtro-texto').value || '').toLowerCase().trim();
    var tipo = $('#filtro-tipo').value;
    var est = $('#filtro-estado').value;
    return lista.filter(function (i) {
      var r = i.resumen || {};
      if (tipo && i.tipo !== tipo) return false;
      if (est === 'servicio-inactivo' && r.servicio_activo) return false;
      if (est === 'db-caida' && r.db_ok !== false) return false;
      if (est === 'sitio-desactivado' && r.apache_habilitado) return false;
      if (est === 'ssl-problema' && ['vencido', 'por-vencer'].indexOf(r.ssl_estado) === -1) return false;
      if (est === 'url-caida' && r.url_responde) return false;
      if ((est === 'ok' || est === 'alerta' || est === 'error') && r.salud !== est) return false;
      if (txt) {
        var blob = [i.cliente, i.dominio, i.servicio, r.empresa, r.apache_sitio, (i.db || {}).dbname]
          .join(' ').toLowerCase();
        if (blob.indexOf(txt) === -1) return false;
      }
      return true;
    });
  }

  function valorOrden(inst, campo) {
    var r = inst.resumen || {};
    switch (campo) {
      case 'cliente': return (inst.cliente || '').toLowerCase();
      case 'tipo': return inst.tipo || '';
      case 'servicio': return r.servicio_activo ? '0' : '1';
      case 'db': return r.db_ok ? '0' : '1';
      case 'url': return r.url_responde ? '0' : '1';
      case 'ssl': return (r.ssl_dias === null || r.ssl_dias === undefined) ? 99999 : r.ssl_dias;
      case 'apache': return r.apache_habilitado ? '0' : '1';
      case 'fecha_instalacion': return r.fecha_instalacion || '';
      case 'db_tamano_bytes': return r.db_tamano_bytes || 0;
      case 'media_bytes': return r.media_bytes || 0;
      default: return r[campo] || '';
    }
  }

  function ordenar(lista) {
    var campo = estado.orden, asc = estado.asc ? 1 : -1;
    return lista.slice().sort(function (a, b) {
      var va = valorOrden(a, campo), vb = valorOrden(b, campo);
      if (va === vb) return (a.cliente || '').localeCompare(b.cliente || '');
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * asc;
      return String(va).localeCompare(String(vb)) * asc;
    });
  }

  function pintarTabla() {
    var lista = ordenar(filtrar(estado.datos));
    var cap = estado.capacidades || {};
    var cuerpo = $('#cuerpo-tabla');
    if (!lista.length) {
      cuerpo.innerHTML = '<tr><td colspan="15" class="vacio">Sin instancias que coincidan con el filtro.</td></tr>';
    } else {
      cuerpo.innerHTML = lista.map(function (i) {
        var r = i.resumen || {}, db = i.db || {};
        var oculto = cap.bd ? '' : ' style="display:none"';
        var ocultoMedia = cap.media ? '' : ' style="display:none"';
        return '<tr data-id="' + esc(i.id) + '">' +
          '<td class="cliente">' + esc(i.cliente) +
            '<small>' + esc(r.empresa || i.dominio || i.ruta) + '</small></td>' +
          '<td><span class="chip">' + esc(i.tipo) + '</span></td>' +
          '<td>' + badgeServicio(i) + '</td>' +
          '<td>' + (i.url ? '<a href="' + esc(i.url) + '" target="_blank" rel="noopener">' +
              esc(i.dominio) + '</a><br>' : '') + badgeUrl(i) + '</td>' +
          '<td>' + badgeSsl(i) + '</td>' +
          '<td>' + badgeApache(i) + '</td>' +
          '<td><span class="tenue">' + esc((r.fecha_instalacion || '').substring(0, 10) || '—') + '</span>' +
            (r.servicio_creado ? '<br><span class="tenue" title="Fecha del archivo .service">svc ' +
              esc(r.servicio_creado.substring(0, 10)) + '</span>' : '') + '</td>' +
          '<td class="col-bd"' + oculto + '>' + badgeBase(i) +
            ' <span class="tenue">' + esc(db.dbname || '') + '</span></td>' +
          '<td class="num col-bd"' + oculto + '>' + texto(r.db_tamano) + '</td>' +
          '<td class="num col-media"' + ocultoMedia + '>' + texto(r.media_tamano) + '</td>' +
          '<td class="col-bd"' + oculto + '>' + fechaConAviso(r.auditoria_fecha, r.auditoria_dias, 7) +
            (r.auditoria_usuario ? ' <span class="tenue">' + esc(r.auditoria_usuario) + '</span>' : '') + '</td>' +
          '<td class="col-bd"' + oculto + '>' + fechaConAviso(r.ultima_sesion, null, 999) + '</td>' +
          '<td class="col-bd"' + oculto + '>' + texto(r.primera_venta) + '</td>' +
          '<td class="col-bd"' + oculto + '>' + fechaConAviso(r.ultima_venta, r.dias_sin_ventas, 15) + '</td>' +
          '<td><button class="boton mini" data-id="' + esc(i.id) + '">Ver</button></td>' +
          '</tr>';
      }).join('');
    }
    $('#pie-info').textContent = lista.length + ' de ' + estado.datos.length +
      ' instancias · último refresco: ' + (estado.meta.ultimo_refresco || '—');
  }

  // ------------------------------------------------------------------ modal
  function dl(pares) {
    return '<dl>' + pares.filter(function (p) { return p[1] !== undefined && p[1] !== null && p[1] !== ''; })
      .map(function (p) { return '<dt>' + esc(p[0]) + '</dt><dd>' + (p[2] ? p[1] : texto(p[1])) + '</dd>'; })
      .join('') + '</dl>';
  }

  function botonesAccion(inst) {
    var cap = estado.capacidades || {};
    if (!cap.acciones) return '<p class="tenue">Acciones desactivadas en config.json</p>';
    var s = inst.servicio_estado || {}, a = inst.apache || {};
    var html = '';
    if (cap.acciones_servicios && s.existe) {
      html += s.activo
        ? '<button class="boton peligro accion" data-accion="detener">Detener servicio</button> '
        : '<button class="boton accion" data-accion="iniciar">Iniciar servicio</button> ';
      html += '<button class="boton mini accion" data-accion="reiniciar">Reiniciar</button> ';
      html += (s.habilitado === 'enabled')
        ? '<button class="boton mini accion" data-accion="deshabilitar">Quitar del arranque</button> '
        : '<button class="boton mini accion" data-accion="habilitar">Activar en arranque</button> ';
    }
    if (cap.acciones_apache && a.sitio) {
      html += a.habilitado
        ? '<button class="boton peligro accion" data-accion="apache_desactivar">Desactivar sitio Apache</button> '
        : '<button class="boton accion" data-accion="apache_activar">Activar sitio Apache</button> ';
    }
    return html || '<p class="tenue">Sin acciones disponibles para esta instancia</p>';
  }

  function abrirDetalle(id) {
    var inst = estado.datos.filter(function (i) { return i.id === id; })[0];
    if (!inst) return;
    var cap = estado.capacidades || {};
    var s = inst.servicio_estado || {}, db = inst.db || {}, media = inst.media || {},
        git = inst.git || {}, aud = db.auditoria || {}, ses = db.sesiones || {},
        emp = db.empresa || {}, ap = inst.apache || {}, cert = inst.ssl || {}, u = inst.url_estado || {};

    var bloques = '';

    bloques += '<div class="bloque"><h3>Instalación</h3>' + dl([
      ['Ruta', inst.ruta], ['Empresa', emp.nombre_empresa || emp.razonsocial], ['RUC', emp.ruc],
      ['Dominio', inst.url ? '<a href="' + esc(inst.url) + '" target="_blank" rel="noopener">' + esc(inst.url) + '</a>' : null, true],
      ['Creada el', inst.fecha_instalacion],
      ['Rama git', git.rama], ['Último commit', (git.commit || '') + (git.fecha ? ' · ' + git.fecha : '')],
      ['Actualizado', inst.actualizado], ['Recolección', (inst.duracion_ms || 0) + ' ms']
    ]) + '</div>';

    bloques += '<div class="bloque"><h3>Servicio systemd</h3>' + dl([
      ['Unidad', s.unidad], ['Estado', badgeServicio(inst) + ' ' + esc(s.subestado || ''), true],
      ['Arranque', s.habilitado], ['PID', s.pid], ['Memoria', s.memoria],
      ['Archivo .service', s.archivo], ['Servicio creado el', s.creado],
      ['Activo desde', s.desde], ['Uptime', s.uptime], ['Reinicios', s.reinicios],
      ['Puerto (gunicorn)', s.puerto], ['Error', s.error]
    ]) + '</div>';

    bloques += '<div class="bloque"><h3>Apache</h3>' + dl([
      ['Estado', badgeApache(inst), true], ['Archivo', ap.archivo], ['Sitio (a2ensite)', ap.sitio],
      ['ServerName', ap.servername], ['ServerAlias', (ap.alias || []).join(', ')],
      ['DocumentRoot', ap.documentroot], ['Vhost modificado', ap.modificado],
      ['Proxy a puerto', (ap.puertos_proxy || []).join(', ')],
      ['Otros vhost', (ap.otros || []).map(function (o) {
        return o.nombre + (o.habilitado ? ' (activo)' : ' (inactivo)'); }).join('<br>'), true],
      ['Error', ap.error]
    ]) + '</div>';

    bloques += '<div class="bloque"><h3>Certificado SSL</h3>' + dl([
      ['Estado', badgeSsl(inst), true], ['Válido hasta', cert.valido_hasta],
      ['Días restantes', cert.dias_restantes], ['Emisor', cert.emisor],
      ['CN del certificado', cert.dominio_certificado], ['Archivo', cert.archivo],
      ['Error', cert.error]
    ]) + '</div>';

    bloques += '<div class="bloque"><h3>Respuesta de la URL</h3>' + dl([
      ['URL', inst.url], ['Estado', badgeUrl(inst), true], ['Código HTTP', u.codigo],
      ['Tiempo', u.tiempo_ms ? u.tiempo_ms + ' ms' : null],
      ['SSL válido en vivo', u.ssl_valido === null || u.ssl_valido === undefined ? null : (u.ssl_valido ? 'sí' : 'no')],
      ['Error', u.error]
    ]) + '</div>';

    if (cap.media) {
      bloques += '<div class="bloque"><h3>Media</h3>' + dl([
        ['Ruta', media.ruta], ['Tamaño', media.tamano], ['Calculado', media.calculado],
        ['Desde caché', media.desde_cache ? 'sí' : 'no'], ['Error', media.error]
      ]) + '</div>';
    }

    if (cap.bd) {
      bloques += '<div class="bloque"><h3>Base de datos</h3>' + dl([
        ['Estado', badgeBase(inst), true], ['Host', db.host], ['Base', db.dbname],
        ['Versión PostgreSQL', db.version], ['Tamaño', db.tamano],
        ['Latencia conexión', db.latencia_ms ? db.latencia_ms + ' ms' : null], ['Error', db.error]
      ]) + '</div>';
      bloques += '<div class="bloque"><h3>Última auditoría</h3>' + dl([
        ['Fecha', aud.fecha], ['Hora', aud.hora], ['Usuario', aud.usuario], ['Acción', aud.accion],
        ['Tabla', aud.tabla_afectada], ['Registro', aud.registro],
        ['Registros', aud.total_aprox], ['Error', aud.error]
      ]) + '</div>';
      bloques += '<div class="bloque"><h3>Sesiones</h3>' + dl([
        ['Último inicio de sesión', ses.ultimo_login], ['Usuario', ses.usuario],
        ['Hace', (ses.dias === undefined || ses.dias === null) ? null : ses.dias + ' días'],
        ['Sesiones vigentes', ses.sesiones_vigentes], ['Usuarios activos', ses.usuarios_activos],
        ['Última conexión', ses.ultima_conexion], ['Error', ses.error]
      ]) + '</div>';
    }

    var ventas = '';
    if (cap.bd) {
      var filas = (db.ventas || []).map(function (v) {
        return '<tr><td>' + esc(v.etiqueta) + '<br><span class="tenue">' + esc(v.tabla) + '</span></td>' +
          '<td>' + texto(v.primera) + '</td><td>' + texto(v.ultima) + '</td><td>' + texto(v.total) + '</td>' +
          '<td>' + (v.error ? badge('rojo', 'error', v.error) : '') + '</td></tr>';
      }).join('') || '<tr><td colspan="5" class="tenue">Sin tablas de ventas detectadas</td></tr>';
      ventas = '<div class="bloque" style="margin-top:14px"><h3>Ventas detectadas</h3>' +
        '<table class="tabla-mini"><thead><tr><th>Origen</th><th>Primera</th><th>Última</th>' +
        '<th>Registros</th><th></th></tr></thead><tbody>' + filas + '</tbody></table></div>';
    }

    $('#modal-titulo').innerHTML = esc(inst.cliente) + ' <span class="chip">' + esc(inst.tipo) + '</span>';
    $('#modal-cuerpo').innerHTML =
      '<div class="acciones" data-id="' + esc(inst.id) + '">' + botonesAccion(inst) +
        '<div id="resultado-accion"></div></div>' +
      '<div class="grid-detalle">' + bloques + '</div>' + ventas +
      '<div style="margin-top:14px;text-align:right">' +
        '<button class="boton mini" id="btn-refrescar-uno" data-id="' + esc(inst.id) + '">Refrescar esta instancia</button> ' +
        '<a class="boton mini" href="/api/instancia/' + encodeURIComponent(inst.id) + '" target="_blank" rel="noopener">Ver JSON</a>' +
      '</div>';
    $('#modal').classList.remove('oculto');
  }

  // ------------------------------------------------------------------ datos
  function cargar() {
    return fetch('/api/estado', { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401) { window.location.href = '/login'; return null; }
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        estado.datos = d.instancias || [];
        estado.meta = d.meta || {};
        estado.resumen = d.resumen || {};
        estado.disco = d.disco || {};
        estado.capacidades = d.capacidades || {};
        $('#estado-refresco').textContent = estado.meta.refrescando
          ? 'Actualizando…' : 'Actualizado ' + (estado.meta.ultimo_refresco || '');
        aplicarCapacidades();
        pintarTarjetas();
        pintarTabla();
      })
      .catch(function (e) { $('#estado-refresco').textContent = 'Error: ' + e; });
  }

  function refrescar(opciones) {
    opciones = opciones || {};
    var boton = opciones.media ? $('#btn-refrescar-media') : $('#btn-refrescar');
    boton.disabled = true;
    $('#estado-refresco').textContent = 'Actualizando…';
    fetch('/api/refrescar', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opciones)
    }).then(function () {
      var intentos = 0;
      var esperar = setInterval(function () {
        intentos++;
        cargar().then(function () {
          if (!estado.meta.refrescando || intentos > 40) {
            clearInterval(esperar);
            boton.disabled = false;
          }
        });
      }, 3000);
    }).catch(function () { boton.disabled = false; });
  }

  var TEXTO_ACCION = {
    iniciar: 'iniciar el servicio', detener: 'DETENER el servicio',
    reiniciar: 'reiniciar el servicio', habilitar: 'activar el servicio en el arranque',
    deshabilitar: 'quitar el servicio del arranque',
    apache_activar: 'activar el sitio en Apache', apache_desactivar: 'DESACTIVAR el sitio en Apache'
  };

  function ejecutarAccion(id, accion, boton) {
    var inst = estado.datos.filter(function (i) { return i.id === id; })[0] || {};
    if (!window.confirm('¿Confirmas ' + (TEXTO_ACCION[accion] || accion) + ' de "' + inst.cliente + '"?')) return;
    boton.disabled = true;
    $('#resultado-accion').innerHTML = '<p class="tenue">Ejecutando…</p>';
    fetch('/api/accion', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: id, accion: accion })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        $('#resultado-accion').innerHTML = '<div class="' + (d.ok ? 'aviso-ok' : 'aviso-error') + '">' +
          esc(d.ok ? 'Listo: ' + d.comando : (d.error || 'Falló: ' + (d.salida || d.comando))) + '</div>';
        return cargar().then(function () { abrirDetalle(id); });
      })
      .catch(function (e) {
        $('#resultado-accion').innerHTML = '<div class="aviso-error">Error: ' + esc(e) + '</div>';
        boton.disabled = false;
      });
  }

  function verHistorial() {
    var caja = $('#caja-historial');
    if (caja.style.display !== 'none') { caja.style.display = 'none'; return; }
    fetch('/api/acciones', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var filas = (d.acciones || []).map(function (a) {
          return '<tr><td>' + esc(a.fecha) + '</td><td>' + esc(a.usuario) + '</td><td>' + esc(a.accion) +
            '</td><td>' + esc(a.objetivo) + '</td><td>' + esc(a.resultado) + '</td></tr>';
        }).join('') || '<tr><td colspan="5" class="vacio">Sin acciones registradas</td></tr>';
        $('#cuerpo-historial').innerHTML = filas;
        caja.style.display = '';
      });
  }

  function programarAuto() {
    if (temporizador) clearInterval(temporizador);
    if ($('#auto-refresco').checked) temporizador = setInterval(cargar, 30000);
  }

  // ---------------------------------------------------------------- eventos
  document.addEventListener('DOMContentLoaded', function () {
    ['#filtro-texto', '#filtro-tipo', '#filtro-estado'].forEach(function (sel) {
      $(sel).addEventListener('input', pintarTabla);
    });
    $('#auto-refresco').addEventListener('change', programarAuto);
    $('#btn-refrescar').addEventListener('click', function () { refrescar({}); });
    $('#btn-refrescar-media').addEventListener('click', function () { refrescar({ media: true }); });
    $('#btn-historial').addEventListener('click', verHistorial);
    $('#modal-cerrar').addEventListener('click', function () { $('#modal').classList.add('oculto'); });
    $('#modal').addEventListener('click', function (e) {
      if (e.target === $('#modal')) $('#modal').classList.add('oculto');
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') $('#modal').classList.add('oculto');
    });
    $('#cuerpo-tabla').addEventListener('click', function (e) {
      var fila = e.target.closest('tr[data-id]');
      if (fila) abrirDetalle(fila.getAttribute('data-id'));
    });
    $('#modal-cuerpo').addEventListener('click', function (e) {
      if (e.target.id === 'btn-refrescar-uno') {
        refrescar({ solo: e.target.getAttribute('data-id') });
      } else if (e.target.classList.contains('accion')) {
        ejecutarAccion(e.target.closest('.acciones').getAttribute('data-id'),
                       e.target.getAttribute('data-accion'), e.target);
      }
    });
    document.querySelectorAll('th[data-orden]').forEach(function (th) {
      th.addEventListener('click', function () {
        var campo = th.getAttribute('data-orden');
        estado.asc = (estado.orden === campo) ? !estado.asc : true;
        estado.orden = campo;
        pintarTabla();
      });
    });
    cargar();
    programarAuto();
  });
})();
