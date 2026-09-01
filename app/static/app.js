/* Panel VPS - Integrasoluc :: tablero de instancias */
(function () {
  'use strict';

  var estado = {
    datos: [], meta: {}, resumen: {}, disco: {}, capacidades: {},
    orden: 'cliente', asc: true, grupos: {}
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
  function guion(v) {
    return (v === null || v === undefined || v === '') ? '<span class="tenue">—</span>' : esc(v);
  }
  function badge(clase, etiqueta, titulo) {
    return '<span class="badge ' + clase + '"' + (titulo ? ' title="' + esc(titulo) + '"' : '') +
      '>' + esc(etiqueta) + '</span>';
  }
  function aviso(texto, clase) {
    var caja = $('#avisos');
    caja.innerHTML = '<div class="aviso ' + (clase || 'aviso-error') + '">' + esc(texto) +
      '<button class="cerrar-aviso" type="button">&times;</button></div>';
    if (clase === 'aviso-ok') setTimeout(function () { caja.innerHTML = ''; }, 6000);
  }

  window.addEventListener('error', function (e) {
    aviso('Error en el panel: ' + (e.message || e.error));
  });

  // ------------------------------------------------------------------ badges
  function badgeServicio(i) {
    var s = i.servicio_estado || {};
    if (s.activo) return badge('verde', s.estado === 'activating' ? 'iniciando' : 'activo',
      (s.unidad || '') + ' · uptime ' + (s.uptime || '-'));
    if (s.estado === 'no-encontrado') return badge('gris', 'sin unidad', s.error);
    if (s.estado === 'failed') return badge('rojo', 'fallido', s.error);
    return badge('rojo', s.estado || 'inactivo', s.error);
  }
  function badgeBase(i) {
    var db = i.db || {};
    if (db.desactivado) return badge('gris', 'no consultada');
    return db.ok ? badge('verde', 'activa') : badge('rojo', 'caída', db.error);
  }
  function badgeUrl(i) {
    var u = i.url_estado || {};
    if (!i.url) return badge('gris', 'sin dominio');
    if (u.responde === null || u.responde === undefined) return badge('gris', 'sin verificar');
    if (u.responde) {
      return badge(u.codigo < 400 ? 'verde' : 'ambar', 'HTTP ' + u.codigo,
        i.url + ' · ' + (u.tiempo_ms || 0) + ' ms');
    }
    return badge('rojo', 'sin respuesta', u.error);
  }
  function badgeSsl(i) {
    var c = i.ssl || {};
    var t = 'Vence ' + (c.valido_hasta || '?') + ' · ' + (c.emisor || '');
    if (c.estado === 'vigente') return badge('verde', (c.dias_restantes || 0) + 'd', t);
    if (c.estado === 'por-vencer') return badge('ambar', (c.dias_restantes || 0) + 'd', t);
    if (c.estado === 'vencido') return badge('rojo', 'vencido', t);
    if (c.estado === 'autofirmado') return badge('ambar', 'autofirmado', t + ' (certificado por defecto de Apache)');
    return badge('gris', 'sin SSL', c.error || '');
  }
  function badgeApache(i) {
    var a = i.apache || {};
    if (!a.archivo) return badge('gris', 'sin vhost', a.error || '');
    return a.habilitado ? badge('verde', 'habilitado', a.nombre)
                        : badge('rojo', 'deshabilitado', a.nombre);
  }

  function badgeServidorWeb(i) {
    var w = i.servidor_web || {};
    if (!w.tipo) return '';
    if (w.activo === null || w.activo === undefined) {
      return '<span class="chip">' + esc(w.tipo) + '</span>';
    }
    return '<span class="chip">' + esc(w.tipo) + '</span> ' +
      badge(w.activo ? 'verde' : 'rojo', w.activo ? 'activo' : (w.estado || 'inactivo'),
            (w.demonio || '') + ': ' + (w.estado || ''));
  }
  function badgeFacturas(i) {
    var f = (i.db || {}).facturacion || {};
    if (!f.disponible) return badge('gris', 'sin datos', f.error || '');
    if (f.estado === 'facturando') return badge('verde', f.mes_actual + ' este mes', 'Total: ' + f.total);
    if (f.estado === 'nunca') return badge('gris', 'nunca facturó');
    if (f.estado === 'sin-facturar-mes') return badge('ambar', '0 este mes',
      'Última facturación: ' + (f.ultimo_mes || '-'));
    return badge('rojo', (f.meses_sin_facturar || 0) + ' meses sin facturar',
      'Última facturación: ' + (f.ultimo_mes || '-'));
  }
  function barra(pct, etiqueta, titulo, limiteAmbar, limiteRojo) {
    var valor = (pct === null || pct === undefined) ? null : Math.max(0, pct);
    if (valor === null) return '<span class="tenue">—</span>';
    var ancho = Math.min(100, valor);
    var clase = valor >= (limiteRojo || 80) ? 'rojo' : (valor >= (limiteAmbar || 50) ? 'ambar' : 'verde');
    return '<div class="medidor" title="' + esc(titulo || '') + '">' +
      '<div class="medidor-barra ' + clase + '" style="width:' + ancho.toFixed(1) + '%"></div>' +
      '<span class="medidor-texto">' + esc(etiqueta) + '</span></div>';
  }

  function fechaAviso(valor, dias, limite) {
    if (!valor) return '<span class="tenue">—</span>';
    var clase = (dias !== null && dias !== undefined && dias > limite) ? ' class="viejo"' : '';
    var sufijo = (dias !== null && dias !== undefined) ? ' <span class="tenue">(' + dias + 'd)</span>' : '';
    return '<span' + clase + '>' + esc(String(valor).substring(0, 16)) + '</span>' + sufijo;
  }

  // ----------------------------------------------------------- definición de columnas
  var GRUPOS = [
    { id: 'instancia', titulo: 'Instancia' },
    { id: 'servicio', titulo: 'Servicio y web' },
    { id: 'datos', titulo: 'Base y archivos' },
    { id: 'actividad', titulo: 'Actividad' }
  ];

  var COLUMNAS = [
    { id: 'cliente', titulo: 'Cliente', grupo: 'instancia', fijo: true, sticky: true,
      valor: function (i) { return (i.cliente || '').toLowerCase(); },
      render: function (i) {
        var r = i.resumen || {};
        return '<div class="cliente">' + esc(i.cliente) + '</div>' +
          '<div class="sub">' + esc(r.empresa || '') + '</div>';
      } },
    { id: 'tipo', titulo: 'Sistema', grupo: 'instancia',
      valor: function (i) { return i.tipo; },
      render: function (i) { return '<span class="chip ' + esc(i.tipo) + '">' + esc(i.tipo) + '</span>'; } },
    { id: 'ruta', titulo: 'Ruta de instalación', grupo: 'instancia', ancho: 'ancho',
      valor: function (i) { return i.ruta || ''; },
      render: function (i) { return '<code class="ruta" title="' + esc(i.ruta) + '">' + esc(i.ruta) + '</code>'; } },
    { id: 'implementacion', titulo: 'Implementado', grupo: 'instancia',
      valor: function (i) { return (i.resumen || {}).fecha_instalacion || ''; },
      render: function (i) {
        var r = i.resumen || {};
        return guion((r.fecha_instalacion || '').substring(0, 10)) +
          (r.servicio_creado ? '<div class="sub" title="Fecha del archivo .service">svc ' +
            esc(r.servicio_creado.substring(0, 10)) + '</div>' : '');
      } },

    { id: 'servicio', titulo: 'Servicio', grupo: 'servicio',
      valor: function (i) { return (i.resumen || {}).servicio_activo ? 0 : 1; },
      render: function (i) {
        return badgeServicio(i) + '<div class="sub">' + esc(i.servicio || '') + '</div>';
      } },
    { id: 'url', titulo: 'URL', grupo: 'servicio', ancho: 'ancho',
      valor: function (i) { return (i.dominio || 'zzz').toLowerCase(); },
      render: function (i) {
        var html = i.url ? '<a href="' + esc(i.url) + '" target="_blank" rel="noopener">' +
          esc(i.dominio) + '</a>' : '<span class="tenue">sin dominio</span>';
        html += ' ' + badgeUrl(i);
        if (i.dominio_desactualizado) {
          html += '<div class="sub aviso-inline" title="credenciales.json apunta a ' +
            esc(i.dominio_credenciales) + '">credenciales: ' + esc(i.dominio_credenciales) + '</div>';
        }
        return html;
      } },
    { id: 'ssl', titulo: 'SSL (vence)', grupo: 'servicio',
      valor: function (i) {
        var d = (i.ssl || {}).dias_restantes;
        return (d === null || d === undefined) ? 999999 : d;
      },
      render: function (i) {
        var c = i.ssl || {};
        return badgeSsl(i) +
          (c.valido_hasta ? '<div class="sub">vence ' + esc(c.valido_hasta) + '</div>' : '') +
          (c.emisor ? '<div class="sub">' + esc(c.emisor) + '</div>' : '');
      } },
    { id: 'apache', titulo: 'Sitio web', grupo: 'servicio',
      valor: function (i) { return (i.apache || {}).habilitado ? 0 : 1; },
      render: function (i) {
        var a = i.apache || {};
        return badgeApache(i) +
          '<div class="sub">' + badgeServidorWeb(i) + '</div>' +
          (a.nombre ? '<div class="sub">' + esc(a.nombre) + '</div>' : '');
      } },

    { id: 'cpu', titulo: 'CPU', grupo: 'servicio', num: true,
      valor: function (i) { return (i.resumen || {}).cpu_pct || 0; },
      render: function (i) {
        var r = i.resumen || {}, s = i.servicio_estado || {};
        if (r.cpu_pct === null || r.cpu_pct === undefined) return '<span class="tenue">—</span>';
        return barra(r.cpu_pct, r.cpu_pct + '%',
          '% de un núcleo · CPU acumulada: ' + (s.cpu_segundos || 0) + ' s', 50, 90);
      } },
    { id: 'ram', titulo: 'RAM', grupo: 'servicio', num: true,
      valor: function (i) { return (i.resumen || {}).ram_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {}, s = i.servicio_estado || {};
        if (!r.ram_bytes) return '<span class="tenue">—</span>';
        return barra(r.ram_pct === null || r.ram_pct === undefined ? 0 : r.ram_pct * 4,
          r.ram_legible, (r.ram_pct || 0) + '% de la RAM del servidor' +
          (s.memoria_pico ? ' · pico ' + s.memoria_pico : ''), 50, 80);
      } },

    { id: 'base', titulo: 'Base', grupo: 'datos', requiere: 'bd',
      valor: function (i) { return (i.db || {}).ok ? 0 : 1; },
      render: function (i) {
        return badgeBase(i) + '<div class="sub">' + esc((i.db || {}).dbname || '') + '</div>';
      } },
    { id: 'db_tamano', titulo: 'Tamaño BD', grupo: 'datos', requiere: 'bd', num: true,
      valor: function (i) { return (i.resumen || {}).db_tamano_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.db_tamano_bytes) return guion(r.db_tamano);
        return barra((r.db_pct_disco || 0) * 5, r.db_tamano,
          (r.db_pct_disco || 0) + '% del disco del servidor', 50, 80);
      } },
    { id: 'media', titulo: 'Media', grupo: 'datos', requiere: 'media', num: true,
      valor: function (i) { return (i.resumen || {}).media_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.media_bytes) return guion(r.media_tamano);
        return barra((r.media_pct_disco || 0) * 5, r.media_tamano,
          (r.media_pct_disco || 0) + '% del disco del servidor', 50, 80);
      } },
    { id: 'logs', titulo: 'Logs', grupo: 'datos', requiere: 'logs', num: true,
      valor: function (i) { return (i.resumen || {}).logs_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.logs_archivos) return '<span class="tenue">—</span>';
        var clase = (r.logs_bytes > 524288000) ? ' class="viejo"' : '';   // > 500 MB
        return '<span' + clase + '>' + esc(r.logs_tamano) + '</span>' +
          '<div class="sub">' + esc(r.logs_archivos) + ' archivo(s)</div>';
      } },

    { id: 'ocupa', titulo: 'Ocupa (BD+media+logs)', grupo: 'datos', num: true,
      valor: function (i) { return (i.resumen || {}).ocupa_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.ocupa_bytes) return '<span class="tenue">—</span>';
        return barra((r.ocupa_pct_disco || 0) * 5, r.ocupa_legible +
          ' · ' + (r.ocupa_pct_disco || 0) + '%',
          'BD + media + logs frente al disco del servidor', 50, 80);
      } },
    { id: 'auditoria', titulo: 'Última auditoría', grupo: 'actividad', requiere: 'bd', ancho: 'ancho',
      valor: function (i) { return (i.resumen || {}).auditoria_fecha || ''; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.auditoria_fecha) return '<span class="tenue">—</span>';
        var acciones = { A: 'Adición', M: 'Modificación', E: 'Eliminación' };
        return fechaAviso(r.auditoria_fecha, r.auditoria_dias, 7) +
          (r.auditoria_hora ? ' <span class="tenue">' + esc(r.auditoria_hora.substring(0, 5)) + '</span>' : '') +
          '<div class="sub">' + esc(r.auditoria_usuario || '') +
          (r.auditoria_accion ? ' · ' + esc(acciones[r.auditoria_accion] || r.auditoria_accion) : '') +
          (r.auditoria_tabla ? ' · ' + esc(r.auditoria_tabla) : '') + '</div>';
      } },
    { id: 'sesion', titulo: 'Última sesión', grupo: 'actividad', requiere: 'bd',
      valor: function (i) { return (i.resumen || {}).ultima_sesion || ''; },
      render: function (i) {
        var r = i.resumen || {};
        return fechaAviso(r.ultima_sesion, r.ultima_sesion_dias, 30) +
          '<div class="sub">' + esc(r.ultima_sesion_usuario || '') + '</div>';
      } },
    { id: 'facturas', titulo: 'Facturación', grupo: 'actividad', requiere: 'bd',
      valor: function (i) {
        var m = (i.resumen || {}).facturas_meses_sin;
        return (m === null || m === undefined) ? 9999 : m;
      },
      render: function (i) {
        var r = i.resumen || {};
        return badgeFacturas(i) + '<div class="sub">' +
          (r.facturas_total !== null && r.facturas_total !== undefined
            ? 'total ' + esc(r.facturas_total) : '') +
          (r.facturas_ultimo_mes ? ' · últ. ' + esc(r.facturas_ultimo_mes) : '') + '</div>';
      } },
    { id: 'primera_venta', titulo: 'Primera venta', grupo: 'actividad', requiere: 'bd',
      valor: function (i) { return (i.resumen || {}).primera_venta || ''; },
      render: function (i) { return guion((i.resumen || {}).primera_venta); } },
    { id: 'ultima_venta', titulo: 'Última venta', grupo: 'actividad', requiere: 'bd',
      valor: function (i) { return (i.resumen || {}).ultima_venta || ''; },
      render: function (i) {
        var r = i.resumen || {};
        return fechaAviso(r.ultima_venta, r.dias_sin_ventas, 15) +
          (r.venta_tabla ? '<div class="sub">' + esc(r.venta_tabla) + '</div>' : '');
      } }
  ];

  function columnasVisibles() {
    var cap = estado.capacidades || {};
    return COLUMNAS.filter(function (c) {
      if (c.requiere && cap[c.requiere] === false) return false;
      return estado.grupos[c.grupo] !== false;
    });
  }

  // ------------------------------------------------------------------ tabla
  function pintarCabecera() {
    var cols = columnasVisibles();
    var grupos = [];
    cols.forEach(function (c) {
      var ultimo = grupos[grupos.length - 1];
      if (ultimo && ultimo.id === c.grupo) ultimo.n++;
      else grupos.push({ id: c.grupo, n: 1 });
    });
    $('#fila-grupos').innerHTML = grupos.map(function (g) {
      var titulo = (GRUPOS.filter(function (x) { return x.id === g.id; })[0] || {}).titulo || '';
      return '<th colspan="' + g.n + '" class="grupo grupo-' + g.id + '">' + esc(titulo) + '</th>';
    }).join('') + '<th class="grupo"></th>';

    $('#fila-cabecera').innerHTML = cols.map(function (c) {
      var flecha = (estado.orden === c.id) ? (estado.asc ? ' ▲' : ' ▼') : '';
      return '<th data-col="' + c.id + '" class="' + (c.num ? 'num ' : '') +
        (c.sticky ? 'sticky ' : '') + (estado.orden === c.id ? 'ordenada' : '') + '">' +
        esc(c.titulo) + flecha + '</th>';
    }).join('') + '<th></th>';
  }

  function filtrar(lista) {
    var txt = ($('#filtro-texto').value || '').toLowerCase().trim();
    var tipo = $('#filtro-tipo').value;
    var est = $('#filtro-estado').value;
    return lista.filter(function (i) {
      var r = i.resumen || {};
      if (tipo && i.tipo !== tipo) return false;
      if (est === 'servicio-inactivo' && r.servicio_activo) return false;
      if (est === 'db-caida' && r.db_ok !== false) return false;
      if (est === 'sitio-desactivado' && r.apache_habilitado !== false) return false;
      if (est === 'ssl-problema' &&
          ['vencido', 'por-vencer', 'autofirmado'].indexOf(r.ssl_estado) === -1) return false;
      if (est === 'url-caida' && r.url_responde !== false) return false;
      if (est === 'dominio-viejo' && !r.dominio_desactualizado) return false;
      if (est === 'sin-facturar' && ['detenido', 'sin-facturar-mes'].indexOf(r.facturas_estado) === -1) return false;
      if ((est === 'ok' || est === 'alerta' || est === 'error') && r.salud !== est) return false;
      if (txt) {
        var blob = [i.cliente, i.dominio, i.dominio_credenciales, i.servicio, i.ruta,
                    r.empresa, (i.db || {}).dbname, (i.apache || {}).nombre].join(' ').toLowerCase();
        if (blob.indexOf(txt) === -1) return false;
      }
      return true;
    });
  }

  function ordenar(lista) {
    var col = COLUMNAS.filter(function (c) { return c.id === estado.orden; })[0] || COLUMNAS[0];
    var dir = estado.asc ? 1 : -1;
    return lista.slice().sort(function (a, b) {
      var va = col.valor(a), vb = col.valor(b);
      if (va === vb) return (a.cliente || '').localeCompare(b.cliente || '');
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
      if (va === '' || va === null) return 1;
      if (vb === '' || vb === null) return -1;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }

  function pintarTabla() {
    var cols = columnasVisibles();
    var lista = ordenar(filtrar(estado.datos));
    pintarCabecera();
    var cuerpo = $('#cuerpo-tabla');
    if (!lista.length) {
      var m = estado.meta || {};
      var mensaje = m.refrescando
        ? ('Recolectando datos de ' + (m.esperadas || 0) + ' instancias… las filas van apareciendo ' +
           'conforme se completan (la primera vuelta puede tardar varios minutos).')
        : (estado.datos.length ? 'Sin instancias que coincidan con el filtro.'
                              : 'No se encontraron instalaciones. Revisa base_dirs en config.json.');
      cuerpo.innerHTML = '<tr><td class="vacio" colspan="' + (cols.length + 1) + '">' +
        esc(mensaje) + '</td></tr>';
    } else {
      cuerpo.innerHTML = lista.map(function (i) {
        var r = i.resumen || {};
        return '<tr data-id="' + esc(i.id) + '" class="salud-' + esc(r.salud || '') + '">' +
          cols.map(function (c) {
            return '<td class="' + (c.num ? 'num ' : '') + (c.sticky ? 'sticky ' : '') +
              (c.ancho ? 'ancho' : '') + '">' + c.render(i) + '</td>';
          }).join('') +
          '<td><button class="boton mini ver" type="button" data-ver="' + esc(i.id) + '">Ver</button></td></tr>';
      }).join('');
    }
    $('#contador').textContent = lista.length + ' de ' + estado.datos.length + ' instancias';
    $('#pie-info').textContent = 'Último refresco: ' + (estado.meta.ultimo_refresco || '—') +
      ' · los datos se recargan solos cada 30 s';
  }

  function pintarTarjetas() {
    var r = estado.resumen || {}, d = estado.disco || {}, cap = estado.capacidades || {};
    var t = [
      { rotulo: 'Instancias', valor: r.total || 0,
        extra: Object.keys(r.por_tipo || {}).map(function (k) { return k + ': ' + r.por_tipo[k]; }).join(' · ') },
      { rotulo: 'Servicios activos', valor: (r.servicios_activos || 0) + '/' + (r.total || 0),
        clase: r.servicios_inactivos ? 'mal' : 'ok', filtro: 'servicio-inactivo' },
      { rotulo: 'Sitios Apache', valor: (r.sitios_habilitados || 0) + '/' + (r.total || 0),
        filtro: 'sitio-desactivado' },
      { rotulo: 'SSL vigentes', valor: (r.ssl_vigentes || 0) + '/' + (r.total || 0),
        clase: r.ssl_alerta ? 'mal' : 'ok', filtro: 'ssl-problema',
        extra: r.ssl_alerta ? r.ssl_alerta + ' con problema' : '' }
    ];
    if (cap.url) t.push({ rotulo: 'URLs respondiendo', valor: (r.urls_ok || 0) + '/' + (r.total || 0),
      clase: (r.urls_ok === r.total) ? 'ok' : 'mal', filtro: 'url-caida' });
    if (r.dominios_desactualizados) t.push({ rotulo: 'Dominios desactualizados',
      valor: r.dominios_desactualizados, clase: 'mal', filtro: 'dominio-viejo',
      extra: 'credenciales.json vs Apache' });
    if (cap.bd) {
      t.push({ rotulo: 'Bases activas', valor: (r.db_activas || 0) + '/' + (r.total || 0),
        clase: r.db_caidas ? 'mal' : 'ok', filtro: 'db-caida' });
      t.push({ rotulo: 'Tamaño total BD', valor: r.db_tamano || '-' });
    }
    var web = estado.servidoresWeb || {};
    var demonios = Object.keys(web);
    if (demonios.length) {
      t.push({ rotulo: 'Servidor web',
        valor: demonios.map(function (d) { return d + ': ' + (web[d].activo ? 'activo' : 'CAÍDO'); }).join(' · '),
        clase: demonios.every(function (d) { return web[d].activo; }) ? 'ok' : 'mal' });
    }
    if (cap.media) t.push({ rotulo: 'Tamaño total media', valor: r.media_tamano || '-' });
    if (cap.logs) t.push({ rotulo: 'Tamaño total logs', valor: r.logs_tamano || '-' });
    var rec = estado.recursos || {};
    if (rec.ram_total) {
      t.push({ rotulo: 'RAM del servidor', valor: (rec.ram_pct || 0) + '%',
        clase: (rec.ram_pct || 0) > 85 ? 'mal' : 'ok',
        extra: rec.ram_usada_legible + ' de ' + rec.ram_total_legible +
               ' · instancias: ' + (r.ram_tamano || '-') });
    }
    if (rec.carga !== null && rec.carga !== undefined) {
      t.push({ rotulo: 'CPU del servidor', valor: (rec.carga_pct || 0) + '%',
        clase: (rec.carga_pct || 0) > 85 ? 'mal' : 'ok',
        extra: 'carga ' + rec.carga + ' en ' + (rec.nucleos || '?') + ' núcleos · instancias: ' +
               (r.cpu_pct || 0) + '%' });
    }
    t.push({ rotulo: 'Disco del servidor',
      valor: (d.porcentaje === undefined || d.porcentaje === null) ? '-' : d.porcentaje + '%',
      extra: d.usado ? (d.usado + ' de ' + d.total + ' · libre ' + d.libre +
                        (r.ocupa_tamano ? ' · instancias ' + r.ocupa_tamano : '')) : '' });

    $('#tarjetas').innerHTML = t.map(function (x) {
      return '<div class="tarjeta ' + (x.clase || '') + (x.filtro ? ' clicable" data-filtro="' + x.filtro : '') + '">' +
        '<div class="rotulo">' + esc(x.rotulo) + '</div>' +
        '<div class="valor">' + esc(x.valor) + '</div>' +
        (x.extra ? '<div class="rotulo">' + esc(x.extra) + '</div>' : '') + '</div>';
    }).join('');
  }

  function pintarControles() {
    var select = $('#orden');
    if (select.options.length === 0) {
      select.innerHTML = COLUMNAS.map(function (c) {
        return '<option value="' + c.id + '">' + esc(c.titulo) + '</option>';
      }).join('');
      select.value = estado.orden;
    }
    $('#menu-columnas').innerHTML = GRUPOS.map(function (g) {
      return '<label><input type="checkbox" data-grupo="' + g.id + '"' +
        (estado.grupos[g.id] === false ? '' : ' checked') + '> ' + esc(g.titulo) + '</label>';
    }).join('');
  }

  // ------------------------------------------------------------------ modal
  function dl(pares) {
    return '<dl>' + pares.filter(function (p) {
      return p[1] !== undefined && p[1] !== null && p[1] !== '';
    }).map(function (p) {
      return '<dt>' + esc(p[0]) + '</dt><dd>' + (p[2] ? p[1] : esc(p[1])) + '</dd>';
    }).join('') + '</dl>';
  }

  function botonesAccion(inst) {
    var cap = estado.capacidades || {};
    if (!cap.acciones) return '<p class="tenue">Acciones desactivadas en config.json</p>';
    var s = inst.servicio_estado || {}, a = inst.apache || {}, html = '';
    if (cap.acciones_servicios && s.existe) {
      html += s.activo
        ? '<button class="boton peligro accion" type="button" data-accion="detener">Detener servicio</button> '
        : '<button class="boton accion" type="button" data-accion="iniciar">Iniciar servicio</button> ';
      html += '<button class="boton mini accion" type="button" data-accion="reiniciar">Reiniciar</button> ';
      html += (s.habilitado === 'enabled')
        ? '<button class="boton mini accion" type="button" data-accion="deshabilitar">Quitar del arranque</button> '
        : '<button class="boton mini accion" type="button" data-accion="habilitar">Activar en arranque</button> ';
    }
    if (cap.acciones_apache && a.sitio) {
      var nombreServidor = (a.servidor === 'nginx') ? 'nginx' : 'Apache';
      html += a.habilitado
        ? '<button class="boton peligro accion" type="button" data-accion="apache_desactivar">Desactivar sitio en ' + nombreServidor + '</button> '
        : '<button class="boton accion" type="button" data-accion="apache_activar">Activar sitio en ' + nombreServidor + '</button> ';
    }
    return html || '<p class="tenue">Sin acciones disponibles para esta instancia</p>';
  }

  function abrirDetalle(id) {
    var inst = estado.datos.filter(function (i) { return i.id === id; })[0];
    if (!inst) return;
    var cap = estado.capacidades || {};
    var s = inst.servicio_estado || {}, db = inst.db || {}, media = inst.media || {},
        git = inst.git || {}, aud = db.auditoria || {}, ses = db.sesiones || {},
        emp = db.empresa || {}, ap = inst.apache || {}, cert = inst.ssl || {},
        u = inst.url_estado || {}, fac = db.facturacion || {};
    var b = '';

    b += '<div class="bloque"><h3>Instancia</h3>' + dl([
      ['Cliente', inst.cliente], ['Sistema', inst.tipo],
      ['Empresa', emp.nombre_empresa || emp.razonsocial], ['RUC', emp.ruc],
      ['Ruta de instalación', inst.ruta], ['Implementada el', inst.fecha_instalacion],
      ['Rama git', git.rama], ['Último commit', (git.commit || '') + (git.fecha ? ' · ' + git.fecha : '')],
      ['Actualizado', inst.actualizado]
    ]) + '</div>';

    b += '<div class="bloque"><h3>Dominio y URL</h3>' + dl([
      ['URL', inst.url ? '<a href="' + esc(inst.url) + '" target="_blank" rel="noopener">' + esc(inst.url) + '</a>' : null, true],
      ['Estado', badgeUrl(inst), true],
      ['Dominio según Apache', inst.dominio_apache], ['DOMINIO_GENERAL (credenciales.json)', inst.dominio_credenciales],
      ['Aviso', inst.dominio_desactualizado
        ? '<span class="badge ambar">credenciales.json desactualizado</span>' : null, true],
      ['Código HTTP', u.codigo], ['Tiempo', u.tiempo_ms ? u.tiempo_ms + ' ms' : null], ['Error', u.error]
    ]) + '</div>';

    b += '<div class="bloque"><h3>Servicio systemd</h3>' + dl([
      ['Unidad', s.unidad], ['Detectada por', inst.servicio_origen],
      ['Estado', badgeServicio(inst) + ' ' + esc(s.subestado || ''), true],
      ['Arranque', s.habilitado], ['PID', s.pid],
      ['Memoria', s.memoria + ((inst.resumen || {}).ram_pct ? ' (' + (inst.resumen || {}).ram_pct +
        '% de la RAM del servidor)' : '')],
      ['Pico de memoria', s.memoria_pico], ['Procesos', s.tareas],
      ['CPU ahora', (s.cpu_pct === null || s.cpu_pct === undefined) ? null : s.cpu_pct + '% de un núcleo'],
      ['CPU acumulada', s.cpu_segundos ? s.cpu_segundos + ' s' : null],
      ['Activo desde', s.desde], ['Uptime', s.uptime], ['Reinicios', s.reinicios],
      ['Puerto (gunicorn)', inst.puerto], ['Archivo .service', s.archivo],
      ['Servicio creado el', s.creado], ['Error', s.error]
    ]) + '</div>';

    b += '<div class="bloque"><h3>Sitio web (' + esc((ap.servidor || 'sin detectar')) + ')</h3>' + dl([
      ['Estado', badgeApache(inst), true],
      ['Servidor', badgeServidorWeb(inst) || (ap.servidor || null), true],
      ['Archivo', ap.archivo], ['Sitio', ap.sitio],
      ['ServerName', ap.servername], ['ServerAlias', (ap.alias || []).join(', ')],
      ['DocumentRoot', ap.documentroot], ['Proxy a puerto', (ap.puertos_proxy || []).join(', ')],
      ['Vhost modificado', ap.modificado],
      ['Emparejado por', (ap.motivos || []).join(' + ')],
      ['Otros vhost', (ap.otros || []).map(function (o) {
        return esc(o.nombre) + ' → ' + esc(o.servername || '?') + (o.habilitado ? ' (activo)' : ' (inactivo)');
      }).join('<br>'), true],
      ['Error', ap.error]
    ]) + '</div>';

    b += '<div class="bloque"><h3>Certificado SSL</h3>' + dl([
      ['Estado', badgeSsl(inst), true], ['Válido hasta', cert.valido_hasta],
      ['Días restantes', cert.dias_restantes], ['Emisor', cert.emisor],
      ['CN del certificado', cert.dominio_certificado],
      ['Coincide con el dominio', cert.coincide_dominio === null || cert.coincide_dominio === undefined
        ? null : (cert.coincide_dominio ? 'sí' : 'NO')],
      ['Archivo', cert.archivo], ['Error', cert.error]
    ]) + '</div>';

    if (cap.media) {
      b += '<div class="bloque"><h3>Media</h3>' + dl([
        ['Ruta', media.ruta], ['Tamaño', media.tamano], ['Calculado', media.calculado],
        ['Desde caché', media.desde_cache ? 'sí' : 'no'], ['Error', media.error]
      ]) + '</div>';
    }

    if (cap.bd) {
      b += '<div class="bloque"><h3>Base de datos</h3>' + dl([
        ['Estado', badgeBase(inst), true], ['Host', db.host], ['Base', db.dbname],
        ['Versión PostgreSQL', db.version], ['Tamaño', db.tamano],
        ['Latencia', db.latencia_ms ? db.latencia_ms + ' ms' : null],
        ['% del disco del servidor', (inst.resumen || {}).db_pct_disco
          ? (inst.resumen || {}).db_pct_disco + ' %' : null],
        ['Error', db.error]
      ]) + '</div>';
      b += '<div class="bloque"><h3>Última auditoría</h3>' + dl([
        ['Fecha', aud.fecha], ['Hora', aud.hora], ['Usuario', aud.usuario], ['Acción', aud.accion],
        ['Tabla', aud.tabla_afectada], ['Registro', aud.registro], ['Registros', aud.total_aprox],
        ['Error', aud.error]
      ]) + '</div>';
      b += '<div class="bloque"><h3>Sesiones</h3>' + dl([
        ['Último inicio de sesión', ses.ultimo_login], ['Usuario', ses.usuario],
        ['Hace', (ses.dias === undefined || ses.dias === null) ? null : ses.dias + ' días'],
        ['Sesiones vigentes', ses.sesiones_vigentes], ['Usuarios activos', ses.usuarios_activos],
        ['Última conexión', ses.ultima_conexion], ['Error', ses.error]
      ]) + '</div>';
      b += '<div class="bloque"><h3>Facturación electrónica</h3>' + dl([
        ['Estado', badgeFacturas(inst), true], ['Facturas totales', fac.total],
        ['Este mes', fac.mes_actual], ['Mes anterior', fac.mes_anterior],
        ['Primera factura', fac.primera], ['Última factura', fac.ultima],
        ['Último mes facturado', fac.ultimo_mes],
        ['Meses sin facturar', fac.meses_sin_facturar], ['Error', fac.error]
      ]) + '</div>';
    }

    if (cap.logs) {
      var lg = inst.logs || {};
      var filasLogs = (lg.archivos || []).map(function (a) {
        return '<tr><td><code class="ruta" title="' + esc(a.archivo) + '">' + esc(a.archivo) +
          '</code><div class="sub">' + esc(a.origen) + (a.rotado ? ' · rotado' : '') + '</div></td>' +
          '<td class="num">' + esc(a.tamano) + '</td><td>' + esc(a.modificado) + '</td></tr>';
      }).join('');
      b += '<div class="bloque"><h3>Archivos de log</h3>' + dl([
        ['Total', lg.tamano], ['Archivos', lg.total_archivos],
        ['Por origen', Object.keys(lg.por_origen_legible || {}).map(function (k) {
          return k + ': ' + lg.por_origen_legible[k]; }).join(' · ')],
        ['Error', lg.error]
      ]) + (filasLogs ? '<table class="tabla-mini"><thead><tr><th>Archivo</th><th>Tamaño</th>' +
        '<th>Modificado</th></tr></thead><tbody>' + filasLogs + '</tbody></table>' : '') + '</div>';
    }

    var ventas = '';
    if (cap.bd) {
      var filas = (db.ventas || []).map(function (v) {
        return '<tr><td>' + esc(v.etiqueta) + '<div class="sub">' + esc(v.tabla) + '</div></td>' +
          '<td>' + guion(v.primera) + '</td><td>' + guion(v.ultima) + '</td><td>' + guion(v.total) + '</td>' +
          '<td>' + (v.error ? badge('rojo', 'error', v.error) : '') + '</td></tr>';
      }).join('') || '<tr><td colspan="5" class="tenue">Sin tablas de ventas detectadas</td></tr>';
      ventas = '<div class="bloque" style="margin-top:14px"><h3>Ventas por origen</h3>' +
        '<table class="tabla-mini"><thead><tr><th>Origen</th><th>Primera</th><th>Última</th>' +
        '<th>Registros</th><th></th></tr></thead><tbody>' + filas + '</tbody></table></div>';
    }

    $('#modal-titulo').innerHTML = esc(inst.cliente) + ' <span class="chip ' + esc(inst.tipo) + '">' +
      esc(inst.tipo) + '</span>';
    $('#modal-cuerpo').innerHTML =
      '<div class="acciones" data-id="' + esc(inst.id) + '">' + botonesAccion(inst) +
        '<div id="resultado-accion"></div></div>' +
      '<div class="grid-detalle">' + b + '</div>' + ventas +
      '<div id="caja-credenciales"></div>' +
      '<div style="margin-top:14px;text-align:right">' +
        (cap.credenciales ? '<button class="boton mini" type="button" id="btn-credenciales" data-id="' +
          esc(inst.id) + '">Ver credenciales.json</button> ' : '') +
        '<button class="boton mini" type="button" id="btn-refrescar-uno" data-id="' + esc(inst.id) + '">Refrescar esta instancia</button> ' +
        '<a class="boton mini" href="/api/instancia/' + encodeURIComponent(inst.id) + '" target="_blank" rel="noopener">Ver JSON</a>' +
      '</div>';
    $('#modal').classList.remove('oculto');
  }

  // ------------------------------------------------------------------ datos
  function cargar() {
    return fetch('/api/estado', { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401) { window.location.href = '/login'; return null; }
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d) return;
        estado.datos = d.instancias || [];
        estado.meta = d.meta || {};
        estado.resumen = d.resumen || {};
        estado.disco = d.disco || {};
        estado.capacidades = d.capacidades || {};
        estado.servidoresWeb = d.servidores_web || {};
        estado.recursos = d.recursos || {};
        var m = estado.meta;
        $('#estado-refresco').textContent = m.refrescando
          ? ('Recolectando ' + (m.recolectadas || 0) + ' de ' + (m.esperadas || 0) + ' instancias…')
          : 'Actualizado ' + (m.ultimo_refresco || '');
        pintarControles();
        pintarTarjetas();
        pintarTabla();
      })
      .catch(function (e) { aviso('No se pudo leer el estado: ' + e.message); });
  }

  function refrescar(opciones, boton) {
    opciones = opciones || {};
    if (boton) boton.disabled = true;
    $('#estado-refresco').textContent = 'Actualizando datos…';
    aviso(opciones.media ? 'Recalculando el tamaño de las carpetas media, puede tardar varios minutos…'
                         : 'Refrescando todas las instancias…', 'aviso-info');
    fetch('/api/refrescar', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(opciones)
    }).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      var intentos = 0;
      var esperar = setInterval(function () {
        intentos++;
        cargar().then(function () {
          if (!estado.meta.refrescando || intentos > 120) {
            clearInterval(esperar);
            if (boton) boton.disabled = false;
            aviso('Datos actualizados', 'aviso-ok');
          }
        });
      }, 2000);
    }).catch(function (e) {
      if (boton) boton.disabled = false;
      aviso('No se pudo iniciar el refresco: ' + e.message);
    });
  }

  function descargar(ruta, nombre, boton) {
    if (boton) boton.disabled = true;
    aviso('Generando ' + nombre + '…', 'aviso-info');
    fetch(ruta, { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) return r.text().then(function (t) { throw new Error(t || ('HTTP ' + r.status)); });
        return r.blob();
      })
      .then(function (blob) {
        var url = URL.createObjectURL(blob);
        var a = document.createElement('a');
        a.href = url; a.download = nombre;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
        aviso('Descarga lista: ' + nombre, 'aviso-ok');
      })
      .catch(function (e) { aviso('No se pudo generar ' + nombre + ': ' + e.message); })
      .then(function () { if (boton) boton.disabled = false; });
  }

  var TEXTO_ACCION = {
    iniciar: 'INICIAR el servicio', detener: 'DETENER el servicio',
    reiniciar: 'REINICIAR el servicio', habilitar: 'activar el servicio en el arranque',
    deshabilitar: 'quitar el servicio del arranque',
    apache_activar: 'ACTIVAR el sitio web', apache_desactivar: 'DESACTIVAR el sitio web'
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
        $('#resultado-accion').innerHTML = '<div class="aviso-error">Error: ' + esc(e.message) + '</div>';
        boton.disabled = false;
      });
  }

  function verCredenciales(id, conSecretos) {
    var caja = $('#caja-credenciales');
    caja.innerHTML = '<p class="tenue">Leyendo credenciales.json…</p>';
    fetch('/api/credenciales/' + encodeURIComponent(id) + (conSecretos ? '?secretos=1' : ''),
          { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) {
          caja.innerHTML = '<div class="aviso-error">' + esc(d.error || 'No se pudo leer') +
            '</div>' + (d.contenido_crudo ? '<pre class="codigo">' + esc(d.contenido_crudo) + '</pre>' : '');
          return;
        }
        var cap = estado.capacidades || {};
        caja.innerHTML =
          '<div class="bloque" style="margin-top:14px" data-id="' + esc(id) + '">' +
            '<h3>credenciales.json</h3>' +
            '<div class="sub">' + esc(d.archivo) + ' · modificado ' + esc(d.modificado || '?') +
              ' · permisos ' + esc(d.permisos || '?') +
              (d.respaldos && d.respaldos.length ? ' · ' + d.respaldos.length + ' respaldo(s)' : '') + '</div>' +
            (d.con_secretos ? '<div class="aviso-error" style="margin:8px 0">Las contraseñas están visibles en pantalla</div>'
                            : '<div class="sub" style="margin:6px 0">Las claves aparecen ocultas (••••). ' +
                              'Si editas sin revelarlas, se conservan tal cual.</div>') +
            '<textarea id="editor-credenciales" spellcheck="false" rows="16"' +
              (d.editable ? '' : ' readonly') + '>' + esc(d.texto) + '</textarea>' +
            '<div style="margin-top:8px;text-align:right">' +
              (cap.credenciales_secretos && !d.con_secretos
                ? '<button class="boton mini" type="button" id="btn-credenciales-secretos" data-id="' +
                  esc(id) + '">Mostrar contraseñas</button> ' : '') +
              (d.editable ? '<button class="boton" type="button" id="btn-credenciales-guardar" data-id="' +
                esc(id) + '">Guardar cambios</button>' : '') +
            '</div>' +
            '<div id="resultado-credenciales"></div>' +
          '</div>';
      })
      .catch(function (e) { caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>'; });
  }

  function guardarCredenciales(id) {
    if (!window.confirm('Se va a sobrescribir credenciales.json (se guarda un respaldo). ' +
                        'Recuerda reiniciar el servicio para que tome los cambios. ¿Continuar?')) return;
    var texto = $('#editor-credenciales').value;
    $('#resultado-credenciales').innerHTML = '<p class="tenue">Guardando…</p>';
    fetch('/api/credenciales/' + encodeURIComponent(id), {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texto: texto })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) {
          $('#resultado-credenciales').innerHTML = '<div class="aviso-error">' + esc(d.error) + '</div>';
          return;
        }
        $('#resultado-credenciales').innerHTML = '<div class="aviso-ok">Guardado. Cambios: ' +
          esc((d.claves_modificadas || []).join(', ') || 'ninguno') + '. Respaldo: ' +
          esc(d.respaldo) + '. ' + esc(d.aviso) + '</div>';
        cargar();
      })
      .catch(function (e) {
        $('#resultado-credenciales').innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>';
      });
  }


  // -------------------------------------------------- asistente de instancias
  var opcionesAlta = null;
  var tareaActual = null, tareaPoll = null, tareaLineas = 0;

  function abrirAsistente() {
    var caja = $('#nueva-cuerpo');
    caja.innerHTML = '<p class="tenue">Cargando opciones…</p>';
    $('#modal-nueva').classList.remove('oculto');
    fetch('/api/aprovisionar/opciones', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.habilitado === false) {
          caja.innerHTML = '<div class="aviso-error">La creación de instancias está desactivada en config.json</div>';
          return;
        }
        opcionesAlta = d;
        pintarFormularioAlta();
      })
      .catch(function (e) { caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>'; });
  }

  function pintarFormularioAlta() {
    var d = opcionesAlta;
    var tipos = Object.keys(d.templates || {});
    var modelos = function (tipo) {
      return (d.modelos || []).filter(function (m) { return !tipo || m.tipo === tipo; });
    };
    var opcionesModelo = function (clave) {
      return '<option value="">Usar la plantilla del panel</option>' +
        (d.modelos || []).map(function (m) {
          return '<option value="' + esc(m.id) + '">' + esc(m.cliente) + ' (' + esc(m.tipo) + ')</option>';
        }).join('');
    };
    $('#nueva-cuerpo').innerHTML =
      '<div class="bloque"><h3>Datos de la instancia</h3>' +
        '<div class="formulario">' +
          '<label>Sistema<select id="f-tipo">' + tipos.map(function (t) {
            return '<option value="' + esc(t) + '"' + (d.templates[t].existe ? '' : ' disabled') + '>' +
              esc(t) + (d.templates[t].existe ? '' : ' (template no encontrado)') + '</option>';
          }).join('') + '</select></label>' +
          '<label>Nombre de la instancia<input id="f-cliente" placeholder="ej: gigis" autocomplete="off"></label>' +
          '<label>Base de datos<input id="f-base" placeholder="db_gigis" autocomplete="off"></label>' +
          '<label>Dominio<input id="f-dominio" placeholder="gigis' +
            (d.dominio_base ? '.' + esc(d.dominio_base) : '') + '" autocomplete="off"></label>' +
          '<label>Puerto gunicorn<input id="f-puerto" type="number" value="' +
            esc(d.puerto_sugerido || '') + '"></label>' +
          '<label>Modelo del .service<select id="f-modelo-servicio">' + opcionesModelo() + '</select></label>' +
          '<label>Modelo del vhost<select id="f-modelo-vhost">' + opcionesModelo() + '</select></label>' +
        '</div>' +
        '<div class="opciones">' +
          '<label class="check"><input type="checkbox" id="f-actualizar"' +
            (d.actualizar_template ? ' checked' : '') + '> Actualizar el template desde git antes de copiar</label>' +
          '<label class="check"><input type="checkbox" id="f-servicio" checked> Crear e iniciar el servicio systemd</label>' +
          '<label class="check"><input type="checkbox" id="f-vhost" checked> Crear y activar el vhost de Apache</label>' +
          '<label class="check"><input type="checkbox" id="f-certbot"' +
            (d.certbot ? ' checked' : '') + '> Emitir certificado con certbot</label>' +
          '<label class="check"><input type="checkbox" id="f-debug"> DEBUG = true en credenciales.json</label>' +
        '</div>' +
        '<div id="resultado-validacion"></div>' +
        '<div style="margin-top:12px;text-align:right">' +
          '<button class="boton mini" type="button" id="btn-validar">Validar</button> ' +
          '<button class="boton secundario" type="button" id="btn-simular" style="background:#eef2fa;color:#12325f">Simular</button> ' +
          '<button class="boton" type="button" id="btn-crear">Crear instancia</button>' +
        '</div>' +
      '</div>' +
      '<p class="tenue">La simulación valida todo y muestra los comandos exactos que se ejecutarían, sin tocar nada.</p>';

    var cliente = $('#f-cliente');
    cliente.addEventListener('input', function () {
      var v = (cliente.value || '').toLowerCase().replace(/[^a-z0-9_-]/g, '');
      if (!$('#f-base').dataset.tocado) $('#f-base').value = v ? 'db_' + v : '';
      if (!$('#f-dominio').dataset.tocado) {
        $('#f-dominio').value = v && opcionesAlta.dominio_base ? v + '.' + opcionesAlta.dominio_base : v;
      }
    });
    ['f-base', 'f-dominio'].forEach(function (id) {
      $('#' + id).addEventListener('input', function () { this.dataset.tocado = '1'; });
    });
    cliente.focus();
  }

  function datosAlta() {
    return {
      tipo: $('#f-tipo').value,
      cliente: ($('#f-cliente').value || '').trim().toLowerCase(),
      base: ($('#f-base').value || '').trim().toLowerCase(),
      dominio: ($('#f-dominio').value || '').trim().toLowerCase(),
      puerto: parseInt($('#f-puerto').value, 10),
      modelo_servicio: $('#f-modelo-servicio').value,
      modelo_vhost: $('#f-modelo-vhost').value,
      actualizar_template: $('#f-actualizar').checked,
      crear_servicio: $('#f-servicio').checked,
      crear_vhost: $('#f-vhost').checked,
      certbot: $('#f-certbot').checked,
      debug: $('#f-debug').checked
    };
  }

  function validarAlta() {
    var caja = $('#resultado-validacion');
    caja.innerHTML = '<p class="tenue">Validando…</p>';
    return fetch('/api/aprovisionar/validar', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(datosAlta())
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        caja.innerHTML = '<ul class="revisiones">' + (d.revisiones || []).map(function (r) {
          var icono = r.ok ? '✔' : (r.critico ? '✖' : '!');
          var clase = r.ok ? 'ok' : (r.critico ? 'error' : 'aviso');
          return '<li class="rev-' + clase + '">' + icono + ' ' + esc(r.mensaje) +
            (r.detalle ? ' <span class="tenue">— ' + esc(r.detalle) + '</span>' : '') + '</li>';
        }).join('') + '</ul>';
        return d.ok;
      });
  }

  function crearInstancia(simular) {
    validarAlta().then(function (ok) {
      if (!ok) return;
      var datos = datosAlta();
      datos.simular = !!simular;
      if (!simular && !window.confirm('Se va a crear la instancia "' + datos.cliente +
          '": carpeta /home/' + datos.cliente + ', base ' + datos.base +
          ', servicio y vhost. ¿Continuar?')) return;
      fetch('/api/aprovisionar/crear', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(datos)
      }).then(function (r) { return r.json(); })
        .then(function (d) {
          if (!d.ok) { aviso(d.error || 'No se pudo iniciar'); return; }
          $('#modal-nueva').classList.add('oculto');
          verTarea(d.tarea);
        })
        .catch(function (e) { aviso(e.message); });
    });
  }

  // ------------------------------------------------------------------ tareas
  function verTarea(id) {
    tareaActual = id; tareaLineas = 0;
    $('#tarea-titulo').textContent = 'Tarea en curso';
    $('#tarea-cuerpo').innerHTML = '<div id="tarea-pasos"></div>' +
      '<pre class="log" id="tarea-log"></pre><div id="tarea-pie"></div>';
    $('#modal-tarea').classList.remove('oculto');
    if (tareaPoll) clearInterval(tareaPoll);
    var tick = function () {
      fetch('/api/tarea/' + encodeURIComponent(id) + '?desde=' + tareaLineas,
            { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          if (d.error) { clearInterval(tareaPoll); return; }
          $('#tarea-titulo').innerHTML = esc(d.titulo) + ' ' +
            badge(d.estado === 'ok' ? 'verde' : (d.estado === 'error' ? 'rojo' : 'ambar'), d.estado);
          $('#tarea-pasos').innerHTML = (d.pasos || []).map(function (p) {
            var icono = p.estado === 'ok' ? '✔' : (p.estado === 'error' ? '✖' : '…');
            return '<div class="paso paso-' + esc(p.estado) + '">' + icono + ' ' + esc(p.nombre) +
              (p.detalle ? ' <span class="tenue">' + esc(p.detalle) + '</span>' : '') + '</div>';
          }).join('');
          var log = $('#tarea-log');
          (d.lineas || []).forEach(function (l) {
            var linea = document.createElement('div');
            linea.className = 'log-' + l.nivel;
            linea.textContent = l.t + '  ' + l.texto;
            log.appendChild(linea);
          });
          tareaLineas = d.total_lineas;
          log.scrollTop = log.scrollHeight;
          if (d.estado === 'ok' || d.estado === 'error') {
            clearInterval(tareaPoll); tareaPoll = null;
            $('#tarea-pie').innerHTML =
              (d.estado === 'error' && (d.deshacer || []).length
                ? '<div class="aviso-error" style="margin-top:10px">La tarea falló después de crear cosas. ' +
                  'Puedes revertir lo creado: ' +
                  (d.deshacer || []).map(function (x) { return esc(x.tipo) + ' ' + esc(x.valor); }).join(', ') +
                  '<button class="boton peligro" type="button" id="btn-deshacer" data-id="' + esc(d.id) +
                  '" style="margin-left:10px">Deshacer</button></div>'
                : '') +
              (d.estado === 'ok' && d.datos && d.datos.instancia_id
                ? '<div class="aviso-ok" style="margin-top:10px">Instancia creada. ' +
                  '<button class="boton mini" type="button" id="btn-ver-nueva" data-id="' +
                  esc(d.datos.instancia_id) + '">Ver en el panel</button></div>'
                : '');
            cargar();
          }
        })
        .catch(function () {});
    };
    tick();
    tareaPoll = setInterval(tick, 1500);
  }

  function listarTareas() {
    fetch('/api/tareas', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $('#tarea-titulo').textContent = 'Tareas ejecutadas';
        $('#tarea-cuerpo').innerHTML = '<table class="tabla-mini"><thead><tr><th>Inicio</th>' +
          '<th>Tarea</th><th>Usuario</th><th>Estado</th><th></th></tr></thead><tbody>' +
          ((d.tareas || []).map(function (t) {
            return '<tr><td>' + esc(t.creado) + '</td><td>' + esc(t.titulo) + '</td><td>' +
              esc(t.creado_por || '') + '</td><td>' +
              badge(t.estado === 'ok' ? 'verde' : (t.estado === 'error' ? 'rojo' : 'ambar'), t.estado) +
              '</td><td><button class="boton mini" type="button" data-tarea="' + esc(t.id) +
              '">Ver log</button></td></tr>';
          }).join('') || '<tr><td colspan="5" class="vacio">Sin tareas todavía</td></tr>') +
          '</tbody></table>';
        $('#modal-tarea').classList.remove('oculto');
      })
      .catch(function (e) { aviso(e.message); });
  }

  function deshacerTarea(id) {
    if (!window.confirm('Se va a revertir lo que creó esa tarea (carpeta, base de datos, ' +
                        'servicio y vhost). Esta acción borra datos. ¿Continuar?')) return;
    fetch('/api/tarea/' + encodeURIComponent(id) + '/deshacer',
          { method: 'POST', credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo deshacer'); return; }
        verTarea(d.tarea);
      })
      .catch(function (e) { aviso(e.message); });
  }

  function verHistorial() {
    var caja = $('#caja-historial');
    if (!caja.hidden) { caja.hidden = true; return; }
    fetch('/api/acciones', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $('#cuerpo-historial').innerHTML = (d.acciones || []).map(function (a) {
          return '<tr><td>' + esc(a.fecha) + '</td><td>' + esc(a.usuario) + '</td><td>' + esc(a.accion) +
            '</td><td>' + esc(a.objetivo) + '</td><td>' + esc(a.resultado) + '</td><td class="sub">' +
            esc(a.salida) + '</td></tr>';
        }).join('') || '<tr><td colspan="6" class="vacio">Sin acciones registradas</td></tr>';
        caja.hidden = false;
      })
      .catch(function (e) { aviso('No se pudo leer el historial: ' + e.message); });
  }

  function programarAuto() {
    if (temporizador) clearInterval(temporizador);
    if ($('#auto-refresco').checked) temporizador = setInterval(cargar, 30000);
  }

  // --------------------------------------------------------------- eventos
  function iniciar() {
    // Un solo manejador delegado: si algo falla, no se cae el resto del panel.
    document.addEventListener('click', function (e) {
      var el = e.target;
      try {
        if (el.id === 'btn-refrescar') return refrescar({}, el);
        if (el.id === 'btn-media') return refrescar({ media: true }, el);
        if (el.id === 'btn-excel') return descargar('/export.xlsx', 'instancias.xlsx', el);
        if (el.id === 'btn-csv') return descargar('/export.csv', 'instancias.csv', el);
        if (el.id === 'btn-historial') return verHistorial();
        if (el.id === 'btn-excluidos') { window.location.href = '/excluidos'; return; }
        if (el.id === 'btn-nueva') return abrirAsistente();
        if (el.id === 'btn-tareas') return listarTareas();
        if (el.id === 'nueva-cerrar' || el.id === 'modal-nueva') return $('#modal-nueva').classList.add('oculto');
        if (el.id === 'tarea-cerrar' || el.id === 'modal-tarea') {
          if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
          return $('#modal-tarea').classList.add('oculto');
        }
        if (el.id === 'btn-validar') return validarAlta();
        if (el.id === 'btn-simular') return crearInstancia(true);
        if (el.id === 'btn-crear') return crearInstancia(false);
        if (el.id === 'btn-deshacer') return deshacerTarea(el.getAttribute('data-id'));
        if (el.hasAttribute && el.hasAttribute('data-tarea')) return verTarea(el.getAttribute('data-tarea'));
        if (el.id === 'btn-ver-nueva') {
          $('#modal-tarea').classList.add('oculto');
          return abrirDetalle(el.getAttribute('data-id'));
        }
        if (el.id === 'btn-columnas') return $('#menu-columnas').classList.toggle('oculto');
        if (el.id === 'btn-orden-dir') {
          estado.asc = !estado.asc;
          el.textContent = estado.asc ? '▲' : '▼';
          return pintarTabla();
        }
        if (el.id === 'modal-cerrar' || el.id === 'modal') return $('#modal').classList.add('oculto');
        if (el.classList.contains('cerrar-aviso')) return ($('#avisos').innerHTML = '');
        if (el.id === 'btn-refrescar-uno') return refrescar({ solo: el.getAttribute('data-id') }, el);
        if (el.id === 'btn-credenciales') return verCredenciales(el.getAttribute('data-id'), false);
        if (el.id === 'btn-credenciales-secretos') return verCredenciales(el.getAttribute('data-id'), true);
        if (el.id === 'btn-credenciales-guardar') return guardarCredenciales(el.getAttribute('data-id'));
        if (el.classList.contains('accion')) {
          return ejecutarAccion(el.closest('.acciones').getAttribute('data-id'),
                                el.getAttribute('data-accion'), el);
        }
        var tarjeta = el.closest('.tarjeta.clicable');
        if (tarjeta) {
          $('#filtro-estado').value = tarjeta.getAttribute('data-filtro');
          return pintarTabla();
        }
        var th = el.closest('th[data-col]');
        if (th) {
          var col = th.getAttribute('data-col');
          estado.asc = (estado.orden === col) ? !estado.asc : true;
          estado.orden = col;
          $('#orden').value = col;
          $('#btn-orden-dir').textContent = estado.asc ? '▲' : '▼';
          return pintarTabla();
        }
        var fila = el.closest('tr[data-id]');
        if (fila && !el.closest('a')) return abrirDetalle(fila.getAttribute('data-id'));
      } catch (ex) {
        aviso('Error procesando la acción: ' + ex.message);
      }
    });

    document.addEventListener('change', function (e) {
      var el = e.target;
      if (el.id === 'auto-refresco') return programarAuto();
      if (el.id === 'orden') { estado.orden = el.value; return pintarTabla(); }
      if (el.hasAttribute && el.hasAttribute('data-grupo')) {
        estado.grupos[el.getAttribute('data-grupo')] = el.checked;
        return pintarTabla();
      }
    });

    document.addEventListener('input', function (e) {
      if (['filtro-texto', 'filtro-tipo', 'filtro-estado'].indexOf(e.target.id) !== -1) pintarTabla();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        $('#modal').classList.add('oculto');
        $('#modal-nueva').classList.add('oculto');
        if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
        $('#modal-tarea').classList.add('oculto');
      }
    });

    cargar();
    programarAuto();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
