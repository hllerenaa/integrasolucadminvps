/* Panel VPS - Integrasoluc :: tablero de instancias */
(function () {
  'use strict';

  // 'principal' = panel normal · 'excluidos' = mismo panel con todas las
  // instalaciones y el interruptor para ocultarlas o mostrarlas.
  var MODO = window.PANEL_MODO || 'principal';

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
    var s = i.servicio_estado || {}, sk = i.socket || {};
    if (s.activo) return badge('verde', s.estado === 'activating' ? 'iniciando' : 'activo',
      (s.unidad || '') + ' · uptime ' + (s.uptime || '-'));
    // Con activación por socket el sistema responde aunque el .service esté
    // parado: systemd lo levanta en la primera petición.
    if (sk.activo) return badge('ambar', 'socket escuchando',
      'El .service está ' + (s.estado || 'inactivo') + ' pero ' + (sk.unidad || 'el socket') +
      ' sigue escuchando: el sitio responde. Para bajarlo hay que detener ambos.');
    if (s.estado === 'no-encontrado') return badge('gris', 'sin unidad', s.error);
    if (s.estado === 'failed') return badge('rojo', 'fallido', s.error);
    return badge('rojo', s.estado || 'inactivo', s.error);
  }

  function badgeApiCedula(i) {
    var r = i.resumen || {};
    if (!r.api_cedula_disponible) return badge('gris', 'n/d',
      ((i.db || {}).api_cedula || {}).error || 'Esta versión no tiene la opción');
    return r.api_cedula ? badge('verde', 'activa', r.api_cedula_columna)
                        : badge('rojo', 'inactiva', r.api_cedula_columna);
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
    var etiqueta = a.habilitado ? 'habilitado' : 'deshabilitado';
    if (a.dudoso) return badge('ambar', etiqueta + ' (?)',
      'Coincidencia dudosa con ' + a.nombre + ': revisa con "¿Por qué este vhost?"');
    return a.habilitado ? badge('verde', etiqueta, a.nombre) : badge('rojo', etiqueta, a.nombre);
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

  var COLUMNA_VISIBILIDAD = {
    id: 'visibilidad', titulo: 'En el panel', grupo: 'instancia', soloExcluidos: true,
    valor: function (i) { return i.oculta ? 1 : 0; },
    render: function (i) {
      return '<label class="interruptor" title="' +
        (i.oculta ? 'Oculta en el panel principal' : 'Visible en el panel principal') + '">' +
        '<input type="checkbox" class="ver-en-panel" data-id="' + esc(i.id) + '"' +
        ' data-cliente="' + esc(i.cliente) + '" data-servicio="' + esc(i.servicio || '') + '"' +
        (i.oculta ? '' : ' checked') + '><span class="pista"></span></label>' +
        '<div class="sub">' + (i.oculta ? 'oculta' : 'visible') + '</div>';
    }
  };

  var COLUMNAS = [
    COLUMNA_VISIBILIDAD,
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
    { id: 'api_cedula', titulo: 'API cédula', grupo: 'actividad', requiere: 'bd',
      valor: function (i) {
        var r = i.resumen || {};
        return !r.api_cedula_disponible ? 2 : (r.api_cedula ? 0 : 1);
      },
      render: badgeApiCedula },
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
      if (c.soloExcluidos && MODO !== 'excluidos') return false;
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
      if (est === 'oculta' && !i.oculta) return false;
      if (est === 'visible' && i.oculta) return false;
      if (est === 'servicio-inactivo' && r.servicio_activo) return false;
      if (est === 'db-caida' && r.db_ok !== false) return false;
      if (est === 'sitio-desactivado' && r.apache_habilitado !== false) return false;
      if (est === 'ssl-problema' &&
          ['vencido', 'por-vencer', 'autofirmado'].indexOf(r.ssl_estado) === -1) return false;
      if (est === 'url-caida' && r.url_responde !== false) return false;
      if (est === 'dominio-viejo' && !r.dominio_desactualizado) return false;
      if (est === 'sin-facturar' && ['detenido', 'sin-facturar-mes'].indexOf(r.facturas_estado) === -1) return false;
      if (est === 'api-activa' && !r.api_cedula) return false;
      if (est === 'api-inactiva' && (r.api_cedula !== false)) return false;
      if (est === 'socket-huerfano' && !(r.socket_activo && !r.servicio_activo)) return false;
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
        return '<tr data-id="' + esc(i.id) + '" class="salud-' + esc(r.salud || '') +
          (i.oculta ? ' fila-oculta' : '') + '">' +
          cols.map(function (c) {
            return '<td class="' + (c.num ? 'num ' : '') + (c.sticky ? 'sticky ' : '') +
              (c.ancho ? 'ancho' : '') + '">' + c.render(i) + '</td>';
          }).join('') +
          '<td><button class="boton mini ver" type="button" data-ver="' + esc(i.id) + '">Ver</button></td></tr>';
      }).join('');
    }
    $('#contador').textContent = lista.length + ' de ' + estado.datos.length + ' instancias';
    if (estado.ajustarScroll) estado.ajustarScroll();
    if ($('#pie-info')) $('#pie-info').textContent = 'Último refresco: ' + (estado.meta.ultimo_refresco || '—') +
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
      ['Puerto (gunicorn)', inst.puerto],
      ['Socket', (inst.socket || {}).existe
        ? ((inst.socket.unidad || '') + ' · ' + ((inst.socket.activo) ? 'escuchando' : 'detenido') +
           ((inst.socket.escucha || (inst.socket.escuchas || []).join(', '))
             ? ' · ' + esc(inst.socket.escucha || (inst.socket.escuchas || []).join(', ')) : ''))
        : null],
      ['Archivo .service', s.archivo],
      ['Servicio creado el', s.creado], ['Error', s.error]
    ]) + '</div>';

    b += '<div class="bloque"><h3>Sitio web (' + esc((ap.servidor || 'sin detectar')) + ')</h3>' + dl([
      ['Estado', badgeApache(inst), true],
      ['Servidor', badgeServidorWeb(inst) || (ap.servidor || null), true],
      ['Archivo', ap.archivo], ['Sitio', ap.sitio],
      ['ServerName', ap.servername], ['ServerAlias', (ap.alias || []).join(', ')],
      ['DocumentRoot', ap.documentroot], ['Proxy a puerto', (ap.puertos_proxy || []).join(', ')],
      ['Vhost modificado', ap.modificado],
      ['Emparejado por', (ap.motivos || []).join(' + ') +
        (ap.dudoso ? ' (coincidencia dudosa)' : '')],
      ['Otros vhost', (ap.otros || []).map(function (o) {
        return esc(o.nombre) + ' → ' + esc(o.servername || '?') + (o.habilitado ? ' (activo)' : ' (inactivo)');
      }).join('<br>'), true],
      ['Error', ap.error]
    ]) +
      '<div style="margin-top:8px"><button class="boton mini diagnostico-vhost" type="button" ' +
      'data-id="' + esc(inst.id) + '">¿Por qué este vhost?</button>' +
      '<div id="resultado-vhost"></div></div></div>';

    b += '<div class="bloque"><h3>Certificado SSL</h3>' + dl([
      ['Estado', badgeSsl(inst), true], ['Emitido el', cert.emitido],
      ['Válido hasta', cert.valido_hasta],
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
      var api = db.api_cedula || {};
      b += '<div class="bloque"><h3>Búsqueda de cédula por API</h3>' + dl([
        ['Estado', badgeApiCedula(inst), true], ['Columna', api.columna],
        ['Error', api.error]
      ]) + (api.disponible && (estado.capacidades || {}).acciones_datos
        ? '<div style="margin-top:8px">' +
          '<button class="boton mini api-cedula" type="button" data-id="' + esc(inst.id) +
          '" data-activar="' + (api.activa ? '0' : '1') + '">' +
          (api.activa ? 'Desactivar búsqueda por cédula' : 'Activar búsqueda por cédula') +
          '</button><span id="resultado-api"></span></div>' : '') + '</div>';

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
    return fetch('/api/estado' + (MODO === 'excluidos' ? '?ocultas=1' : ''),
                 { credentials: 'same-origin' })
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

  function abrirAsistente(enPagina) {
    var caja = $('#nueva-cuerpo');
    caja.innerHTML = '<p class="tenue">Cargando opciones…</p>';
    if (!enPagina && $('#modal-nueva')) $('#modal-nueva').classList.remove('oculto');
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
          if ($('#modal-nueva')) $('#modal-nueva').classList.add('oculto');
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

  function alternarVisibilidad(chk) {
    var mostrar = chk.checked;
    var cliente = chk.getAttribute('data-cliente');
    var servicio = chk.getAttribute('data-servicio');
    chk.disabled = true;
    fetch('/api/excluidos/alternar', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cliente: cliente, servicio: servicio, ocultar: !mostrar })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        chk.disabled = false;
        if (!d.ok) { aviso(d.error || 'No se pudo guardar'); chk.checked = !mostrar; return; }
        aviso((mostrar ? 'Se mostrará ' : 'Se ocultará ') + (servicio || cliente) +
              ' · lista: ' + (d.nombres.length ? d.nombres.join(', ') : 'vacía'), 'aviso-ok');
        var editor = $('#editor-excluidos');
        if (editor) editor.value = d.nombres.join(',');
        cargar();
      })
      .catch(function (e) { chk.disabled = false; chk.checked = !mostrar; aviso(e.message); });
  }

  function guardarListaExcluidos() {
    var boton = $('#btn-guardar-excluidos');
    boton.disabled = true;
    $('#resultado-excluidos').innerHTML = '<p class="tenue">Guardando…</p>';
    fetch('/api/excluidos', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ texto: $('#editor-excluidos').value })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        boton.disabled = false;
        $('#resultado-excluidos').innerHTML = d.ok
          ? '<div class="aviso-ok">Guardado en ' + esc(d.archivo) + ' · ocultos: ' +
            esc(d.nombres.join(', ') || 'ninguno') + '</div>'
          : '<div class="aviso-error">' + esc(d.error) + '</div>';
        if (d.ok) cargar();
      })
      .catch(function (e) {
        boton.disabled = false;
        $('#resultado-excluidos').innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>';
      });
  }

  function cambiarApiCedula(id, activar, boton) {
    var inst = estado.datos.filter(function (i) { return i.id === id; })[0] || {};
    if (!window.confirm((activar ? 'Activar' : 'Desactivar') +
        ' la búsqueda de personas por cédula en "' + inst.cliente +
        '"? Se actualiza seguridad_configuracion en su base.')) return;
    boton.disabled = true;
    $('#resultado-api').innerHTML = ' <span class="tenue">Aplicando…</span>';
    fetch('/api/instancia/' + encodeURIComponent(id) + '/api-cedula', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activar: activar })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        boton.disabled = false;
        if (!d.ok) { $('#resultado-api').innerHTML = ' <span class="aviso-error">' +
          esc(d.error) + '</span>'; return; }
        aviso('Búsqueda por cédula ' + (activar ? 'activada' : 'desactivada') +
              ' en ' + inst.cliente + ' (' + d.columna + ')', 'aviso-ok');
        cargar().then(function () { abrirDetalle(id); });
      })
      .catch(function (e) {
        boton.disabled = false;
        $('#resultado-api').innerHTML = ' <span class="aviso-error">' + esc(e.message) + '</span>';
      });
  }

  function verCertificados(destino) {
    var caja = $(destino || '#tarea-cuerpo');
    if (!destino) {
      $('#tarea-titulo').textContent = 'Certificados SSL';
      $('#modal-tarea').classList.remove('oculto');
      if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
    }
    caja.innerHTML = '<p class="tenue">Consultando certbot…</p>';

    fetch('/api/certificados', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var cap = estado.capacidades || {};
        if (d.error && !(d.certificados || []).length) {
          caja.innerHTML = '<div class="aviso-error">' + esc(d.error) + '</div>';
          return;
        }
        var filas = (d.certificados || []).map(function (c) {
          var clase = c.estado === 'vigente' ? 'verde'
                    : (['renovable', 'por-vencer', 'renovacion-pausada'].indexOf(c.estado) !== -1
                       ? 'ambar' : 'rojo');
          var pausada = c.renovacion === 'pausada';
          var acciones = '';
          if (cap.acciones_certbot) {
            acciones =
              '<button class="boton mini renovar-cert" type="button" data-nombre="' +
                esc(c.nombre) + '">Renovar</button> ' +
              '<button class="boton mini cert-accion" type="button" data-nombre="' + esc(c.nombre) +
                '" data-accion="' + (pausada ? 'reanudar' : 'pausar') + '">' +
                (pausada ? 'Reanudar renovación' : 'Pausar renovación') + '</button> ' +
              '<button class="boton mini peligro cert-accion" type="button" data-nombre="' +
                esc(c.nombre) + '" data-accion="eliminar" style="color:#fff">Eliminar</button>';
          }
          return '<tr><td><strong>' + esc(c.nombre) + '</strong>' +
            (c.cliente ? '<div class="sub">' + esc(c.cliente) + '</div>' : '') + '</td>' +
            '<td>' + esc((c.dominios || []).join(', ')) + '</td>' +
            '<td>' + guion(c.emitido) + '</td>' +
            '<td>' + guion(c.vence) + '</td>' +
            '<td>' + badge(clase, (c.dias === null || c.dias === undefined ? '?' : c.dias + 'd')) +
              '<div class="sub">' + esc(c.estado) + '</div>' +
              (pausada ? '<div class="sub aviso-inline">sin renovación automática</div>' : '') +
            '</td>' +
            '<td>' + acciones + '</td></tr>';
        }).join('') || '<tr><td colspan="6" class="vacio">No hay certificados</td></tr>';

        caja.innerHTML =
          '<p class="tenue">' + esc(d.total) + ' certificado(s) · fuente: ' +
            (d.certbot ? 'certbot certificates' : 'lectura de /etc/letsencrypt/live') + '</p>' +
          '<table class="tabla-mini"><thead><tr><th>Certificado</th><th>Dominios</th>' +
            '<th>Emitido</th><th>Vence</th><th>Estado</th><th></th></tr></thead>' +
            '<tbody>' + filas + '</tbody></table>' +
          (cap.acciones_certbot
            ? '<div style="margin-top:14px;text-align:right">' +
              '<button class="boton mini renovar-cert" type="button" data-simular="1">Probar renovación (dry-run)</button> ' +
              '<button class="boton renovar-cert" type="button">Renovar los que toquen</button>' +
              '</div>'
            : '<p class="tenue">La renovación está desactivada en config.json</p>');
      })
      .catch(function (e) {
        caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>';
      });
  }

  function accionCertificado(nombre, accion) {
    var cuerpo = { nombre: nombre, accion: accion };
    if (accion === 'eliminar') {
      var escrito = window.prompt('Eliminar el certificado "' + nombre +
        '" borra sus archivos y su configuración de renovación. Si algún vhost lo usa, ' +
        'el servidor web no arrancará hasta corregirlo.\n\nEscribe el nombre exacto para confirmar:');
      if (!escrito) return;
      cuerpo.confirmacion = escrito.trim();
    } else if (accion === 'pausar' &&
               !window.confirm('Pausar la renovación automática de "' + nombre +
                 '"? El certificado sigue funcionando hasta que venza, pero certbot dejará ' +
                 'de renovarlo.')) {
      return;
    }
    fetch('/api/certificados/accion', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cuerpo)
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo aplicar'); return; }
        aviso((d.mensaje || 'Listo') + (d.aviso ? ' · ' + d.aviso : ''), 'aviso-ok');
        verCertificados(MODO === 'certificados' ? '#contenido' : null);
      })
      .catch(function (e) { aviso(e.message); });
  }

  function renovarCertificado(nombre, simular, forzar) {
    var texto = nombre ? ('el certificado ' + nombre) : 'los certificados que lo necesiten';
    if (!simular && !window.confirm('¿Renovar ' + texto + ' con certbot?')) return;
    fetch('/api/certificados/renovar', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nombre: nombre || null, simular: !!simular, forzar: !!forzar })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo iniciar'); return; }
        verTarea(d.tarea);
      })
      .catch(function (e) { aviso(e.message); });
  }

  var datosBackups = null;

  function verBackups() {
    var cuerpo = $('#cuerpo-backups');
    cuerpo.innerHTML = '<tr><td class="vacio" colspan="8">Cargando…</td></tr>';
    fetch('/api/backups', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) {
          cuerpo.innerHTML = '<tr><td class="vacio" colspan="8">' + esc(d.error) + '</td></tr>';
          return;
        }
        datosBackups = d;
        pintarBackups();
      })
      .catch(function (e) { aviso(e.message); });
  }

  function estadoBackup(fila, alerta) {
    if (!fila.ultimo) return 'sin';
    return (fila.dias === null || fila.dias > alerta) ? 'viejo' : 'ok';
  }

  function pintarBackups() {
    var d = datosBackups;
    if (!d) return;
    var alerta = d.alerta_dias || 3;
    var txt = ($('#filtro-backups').value || '').toLowerCase().trim();
    var filtro = $('#filtro-backup-estado').value;

    var filas = (d.instancias || []).filter(function (f) {
      if (filtro && estadoBackup(f, alerta) !== filtro) return false;
      if (txt && [f.cliente, f.base].join(' ').toLowerCase().indexOf(txt) === -1) return false;
      return true;
    });

    var sin = (d.instancias || []).filter(function (f) { return !f.ultimo; }).length;
    var viejos = (d.instancias || []).filter(function (f) {
      return f.ultimo && f.dias > alerta; }).length;
    $('#tarjetas-backups').innerHTML = [
      { rotulo: 'Instancias', valor: (d.instancias || []).length },
      { rotulo: 'Sin backup', valor: sin, clase: sin ? 'mal' : 'ok' },
      { rotulo: 'Atrasados (> ' + alerta + ' días)', valor: viejos, clase: viejos ? 'mal' : 'ok' },
      { rotulo: 'Archivos', valor: d.total_archivos || 0 },
      { rotulo: 'Espacio usado', valor: d.total_tamano || '-', extra: d.carpeta },
      { rotulo: 'Retención', valor: (d.retencion || 0) + ' copias' }
    ].map(function (t) {
      return '<div class="tarjeta ' + (t.clase || '') + '">' +
        '<div class="rotulo">' + esc(t.rotulo) + '</div>' +
        '<div class="valor">' + esc(t.valor) + '</div>' +
        (t.extra ? '<div class="rotulo">' + esc(t.extra) + '</div>' : '') + '</div>';
    }).join('');

    $('#cuerpo-backups').innerHTML = filas.map(function (f) {
      var est = estadoBackup(f, alerta);
      var badgeEstado = est === 'sin' ? badge('rojo', 'sin backup')
        : (est === 'viejo' ? badge('ambar', f.dias + ' días') : badge('verde', f.dias + ' días'));
      return '<tr data-cliente="' + esc(f.cliente) + '">' +
        '<td class="cliente">' + esc(f.cliente) +
          (f.oculta ? ' <span class="badge gris">oculta</span>' : '') +
          '<div class="sub">' + esc(f.tipo || '') + '</div></td>' +
        '<td>' + guion(f.base) + '</td>' +
        '<td>' + guion(f.base_tamano) + '</td>' +
        '<td>' + (f.ultimo ? esc(f.ultimo.fecha) + '<div class="sub">' +
          esc(f.ultimo.tamano) + (f.ultimo.sospechoso ? ' · muy pequeño' : '') + '</div>'
          : '<span class="tenue">—</span>') + '</td>' +
        '<td>' + badgeEstado + '</td>' +
        '<td class="num">' + esc(f.total) + '</td>' +
        '<td class="num">' + esc(f.ocupado) + '</td>' +
        '<td>' + (f.id ? '<button class="boton mini backup-crear" type="button" data-id="' +
            esc(f.id) + '">Respaldar</button> ' : '') +
          (f.total ? '<button class="boton mini backup-ver" type="button" data-cliente="' +
            esc(f.cliente) + '">Ver copias</button>' : '') + '</td></tr>';
    }).join('') || '<tr><td class="vacio" colspan="8">Sin resultados</td></tr>';

    $('#contador-backups').textContent = filas.length + ' de ' + (d.instancias || []).length +
      ' instancias · ' + (d.total_tamano || '');
  }

  function verCopias(cliente) {
    var fila = (datosBackups.instancias || []).filter(function (f) {
      return f.cliente === cliente; })[0];
    if (!fila) return;
    $('#detalle-backups').innerHTML = '<div class="bloque"><h3>Copias de ' + esc(cliente) + '</h3>' +
      '<table class="tabla-mini"><thead><tr><th>Archivo</th><th>Fecha</th><th>Tamaño</th><th></th></tr></thead><tbody>' +
      (fila.archivos || []).map(function (a) {
        return '<tr><td><code class="ruta" title="' + esc(a.archivo) + '">' + esc(a.nombre) +
          '</code>' + (a.sospechoso ? ' <span class="badge ambar">muy pequeño</span>' : '') + '</td>' +
          '<td>' + esc(a.fecha) + ' <span class="tenue">(' + esc(a.dias) + 'd)</span></td>' +
          '<td>' + esc(a.tamano) + '</td>' +
          '<td><a class="boton mini" href="/backups/descargar?archivo=' +
            encodeURIComponent(a.archivo) + '">Descargar</a> ' +
          '<button class="boton mini peligro backup-borrar" type="button" style="color:#fff" ' +
            'data-archivo="' + esc(a.archivo) + '">Eliminar</button></td></tr>';
      }).join('') + '</tbody></table></div>';
    $('#detalle-backups').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function crearBackup(ids) {
    var texto = ids ? ('la instancia ' + ids.join(', ')) : 'TODAS las instancias';
    if (!window.confirm('¿Generar backup de ' + texto + '? Puede tardar según el tamaño.')) return;
    fetch('/api/backups/crear', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: ids })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo iniciar'); return; }
        verTarea(d.tarea);
      })
      .catch(function (e) { aviso(e.message); });
  }

  function crearBackupBase(base) {
    if (!window.confirm('¿Generar backup de la base "' + base + '"?')) return;
    fetch('/api/backups/crear', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bases: [base] })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo iniciar'); return; }
        verTarea(d.tarea);
      })
      .catch(function (e) { aviso(e.message); });
  }

  function borrarBackup(archivo) {
    if (!window.confirm('¿Eliminar definitivamente ' + archivo + '?')) return;
    fetch('/api/backups/eliminar', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ archivo: archivo })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo eliminar'); return; }
        aviso('Backup eliminado', 'aviso-ok');
        verBackups();
        $('#detalle-backups').innerHTML = '';
      })
      .catch(function (e) { aviso(e.message); });
  }

  var datosBases = null;

  function verBases() {
    fetch('/api/bases', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        datosBases = d;
        pintarBases();
      })
      .catch(function (e) {
        $('#cuerpo-bases').innerHTML = '<tr><td class="vacio" colspan="6">' +
          esc(e.message) + '</td></tr>';
      });
  }

  function pintarBases() {
    var d = datosBases;
    if (!d) return;
    if (d.ok === false) {
      $('#cuerpo-bases').innerHTML = '<tr><td class="vacio" colspan="6">' +
        esc(d.error || 'No se pudo consultar PostgreSQL') + '</td></tr>';
      return;
    }
    var soloSinUso = $('#solo-sin-uso') && $('#solo-sin-uso').checked;
    var bases = (d.bases || []).filter(function (b) {
      return !soloSinUso || (!b.en_uso && !b.sistema);
    });
    $('#cuerpo-bases').innerHTML = bases.map(function (b) {
      return '<tr><td class="cliente">' + esc(b.nombre) + '</td>' +
        '<td>' + (b.en_uso
          ? esc(b.instancia.cliente) + ' <span class="chip ' + esc(b.instancia.tipo) + '">' +
            esc(b.instancia.tipo) + '</span>' +
            (b.instancia.oculta ? ' <span class="badge gris">oculta</span>' : '')
          : (b.sistema ? badge('gris', 'del motor') : badge('ambar', 'sin instancia'))) + '</td>' +
        '<td class="num">' + esc(b.tamano) + '</td>' +
        '<td class="num">' + esc(b.conexiones) + '</td>' +
        '<td>' + guion(b.ultimo_backup) + '</td>' +
        '<td><button class="boton mini backup-base" type="button" data-base="' +
          esc(b.nombre) + '">Respaldar</button></td></tr>';
    }).join('') || '<tr><td class="vacio" colspan="6">Sin bases</td></tr>';
    $('#resumen-bases').textContent = (d.bases || []).length + ' bases · ' +
      (d.sin_uso || 0) + ' sin instancia (' + (d.sin_uso_tamano || '0 B') + ')' +
      (d.host ? ' · ' + d.host : '');
  }

  function diagnosticoVhost(id) {
    var caja = $('#resultado-vhost');
    caja.innerHTML = '<p class="tenue">Analizando los sitios del servidor…</p>';
    fetch('/api/diagnostico/vhosts?id=' + encodeURIComponent(id), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var info = d.instancia || {};
        caja.innerHTML =
          '<p class="tenue" style="margin:8px 0 4px">Se leyeron ' + esc(d.total) +
            ' sitios · dominio en credenciales: ' + esc(info.dominio_credenciales || '—') +
            ' · puerto: ' + esc(info.puerto_servicio || '—') +
            ' · socket: ' + esc(info.socket_unix || '—') +
            ' · umbral: ' + esc(info.umbral) + '</p>' +
          '<table class="tabla-mini"><thead><tr><th>Archivo</th><th>ServerName</th>' +
            '<th>Proxy</th><th class="num">Puntaje</th><th>Señales</th></tr></thead><tbody>' +
          (info.candidatos || []).map(function (c) {
            return '<tr><td><code class="ruta" title="' + esc(c.archivo) + '">' +
              esc(c.archivo.split('/').pop()) + '</code>' +
              (c.habilitado ? '' : ' <span class="badge gris">no habilitado</span>') + '</td>' +
              '<td>' + esc(c.servername || '—') + '</td>' +
              '<td>' + esc((c.puertos || []).join(', ') || (c.sockets || []).join(', ') || '—') + '</td>' +
              '<td class="num">' + esc(c.puntaje) + '</td>' +
              '<td class="sub">' + esc((c.motivos || []).join(' · ')) + '</td></tr>';
          }).join('') +
          '</tbody></table>';
      })
      .catch(function (e) { caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>'; });
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
  function prepararScroll() {
    var caja = document.querySelector('.tabla-envoltura');
    var tabla = $('#tabla');
    if (!caja || !tabla) return;

    // Barra de desplazamiento arriba, sincronizada con la tabla.
    var barra = $('#scroll-superior');
    if (!barra) {
      barra = document.createElement('div');
      barra.id = 'scroll-superior';
      barra.innerHTML = '<div></div>';
      caja.parentNode.insertBefore(barra, caja);
      barra.addEventListener('scroll', function () { caja.scrollLeft = barra.scrollLeft; });
      caja.addEventListener('scroll', function () { barra.scrollLeft = caja.scrollLeft; });
    }
    var ajustar = function () {
      barra.firstChild.style.width = tabla.scrollWidth + 'px';
      barra.style.display = (tabla.scrollWidth > caja.clientWidth) ? '' : 'none';
    };
    ajustar();
    window.addEventListener('resize', ajustar);
    estado.ajustarScroll = ajustar;

    // Arrastrar con el ratón para desplazar la tabla.
    var arrastrando = false, inicioX = 0, inicioScroll = 0, movido = false;
    caja.addEventListener('mousedown', function (e) {
      if (e.button !== 0 || e.target.closest('a, button, input, label, select, textarea')) return;
      arrastrando = true; movido = false;
      inicioX = e.pageX; inicioScroll = caja.scrollLeft;
      caja.classList.add('arrastrando');
    });
    window.addEventListener('mousemove', function (e) {
      if (!arrastrando) return;
      var delta = e.pageX - inicioX;
      if (Math.abs(delta) > 4) movido = true;
      caja.scrollLeft = inicioScroll - delta;
      if (movido) e.preventDefault();
    });
    window.addEventListener('mouseup', function () {
      if (!arrastrando) return;
      arrastrando = false;
      caja.classList.remove('arrastrando');
      // Si hubo arrastre no se abre el detalle de la fila.
      if (movido) caja.dataset.arrastre = '1';
      setTimeout(function () { delete caja.dataset.arrastre; }, 50);
    });
  }

  function iniciar() {
    // Un solo manejador delegado: si algo falla, no se cae el resto del panel.
    document.addEventListener('click', function (e) {
      var el = e.target;
      try {
        if (el.id === 'btn-refrescar') return refrescar({}, el);
        if (el.id === 'btn-media') return refrescar({ media: true }, el);
        var sufijo = (MODO === 'excluidos') ? '?ocultas=1' : '';
        if (el.id === 'btn-excel') return descargar('/export.xlsx' + sufijo, 'instancias.xlsx', el);
        if (el.id === 'btn-csv') return descargar('/export.csv' + sufijo, 'instancias.csv', el);
        if (el.id === 'btn-historial') return verHistorial();
        if (el.id === 'btn-excluidos') { window.location.href = '/excluidos'; return; }
        if (el.id === 'btn-guardar-excluidos') return guardarListaExcluidos();
        if (el.id === 'btn-nueva') return abrirAsistente();
        if (el.id === 'btn-tareas') return listarTareas();
        if (el.id === 'btn-certificados') return verCertificados();
        if (el.id === 'btn-cert-recargar') return verCertificados('#contenido');
        if (el.id === 'btn-backups-recargar') { verBackups(); return verBases(); }
        if (el.id === 'btn-backup-todos') return crearBackup(null);
        if (el.classList.contains('backup-crear')) return crearBackup([el.getAttribute('data-id')]);
        if (el.classList.contains('backup-ver')) return verCopias(el.getAttribute('data-cliente'));
        if (el.classList.contains('backup-borrar')) return borrarBackup(el.getAttribute('data-archivo'));
        if (el.classList.contains('backup-base')) return crearBackupBase(el.getAttribute('data-base'));
        if (el.classList.contains('cert-accion')) {
          return accionCertificado(el.getAttribute('data-nombre'), el.getAttribute('data-accion'));
        }
        if (el.classList.contains('renovar-cert')) {
          return renovarCertificado(el.getAttribute('data-nombre'),
                                    el.getAttribute('data-simular') === '1', false);
        }
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
        if (el.classList.contains('diagnostico-vhost')) {
          return diagnosticoVhost(el.getAttribute('data-id'));
        }
        if (el.classList.contains('api-cedula')) {
          return cambiarApiCedula(el.getAttribute('data-id'),
                                  el.getAttribute('data-activar') === '1', el);
        }
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
        var envoltura = el.closest('.tabla-envoltura');
        if (envoltura && envoltura.dataset.arrastre) return;   // venía de arrastrar
        if (fila && !el.closest('a') && !el.closest('label.interruptor')) {
          return abrirDetalle(fila.getAttribute('data-id'));
        }
      } catch (ex) {
        aviso('Error procesando la acción: ' + ex.message);
      }
    });

    document.addEventListener('change', function (e) {
      var el = e.target;
      if (el.id === 'auto-refresco') return programarAuto();
      if (el.id === 'orden') { estado.orden = el.value; return pintarTabla(); }
      if (el.id === 'filtro-backup-estado') return pintarBackups();
      if (el.id === 'solo-sin-uso') return pintarBases();
      if (el.classList.contains('ver-en-panel')) return alternarVisibilidad(el);
      if (el.hasAttribute && el.hasAttribute('data-grupo')) {
        estado.grupos[el.getAttribute('data-grupo')] = el.checked;
        return pintarTabla();
      }
    });

    document.addEventListener('input', function (e) {
      if (['filtro-texto', 'filtro-tipo', 'filtro-estado'].indexOf(e.target.id) !== -1) pintarTabla();
      if (['filtro-backups', 'filtro-backup-estado'].indexOf(e.target.id) !== -1) pintarBackups();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        $('#modal').classList.add('oculto');
        $('#modal-nueva').classList.add('oculto');
        if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
        $('#modal-tarea').classList.add('oculto');
      }
    });

    if (MODO === 'certificados') {
      cargarCapacidades().then(function () { verCertificados('#contenido'); });
      return;
    }
    if (MODO === 'backups') {
      cargarCapacidades().then(function () { verBackups(); verBases(); });
      return;
    }
    if (MODO === 'nueva') {
      cargarCapacidades().then(function () { abrirAsistente(true); });
      return;
    }
    prepararScroll();
    cargar();
    programarAuto();
  }

  // En las páginas sin tabla igual hacen falta las capacidades del panel.
  function cargarCapacidades() {
    return fetch('/api/estado', { credentials: 'same-origin' })
      .then(function (r) { return r.status === 401 ? null : r.json(); })
      .then(function (d) {
        if (!d) { window.location.href = '/login'; return; }
        estado.capacidades = d.capacidades || {};
        estado.datos = d.instancias || [];
      })
      .catch(function () {});
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', iniciar);
  } else {
    iniciar();
  }
})();
