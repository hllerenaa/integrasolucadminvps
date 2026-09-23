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
    if (f.estado === 'facturando') {
      var mes = dinero(f.monto_mes_actual);
      return badge('verde', f.mes_actual + ' este mes' + (mes ? ' · ' + mes : ''),
        'Total histórico: ' + f.total + (dinero(f.monto_total) ? ' · ' + dinero(f.monto_total) : ''));
    }
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


  // Importes en dólares al estilo que usan el SRI y las facturas: 1,234.56.
  var FORMATO_MONEDA = (typeof Intl !== 'undefined' && Intl.NumberFormat)
    ? new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
    : null;
  function dinero(valor) {
    if (valor === null || valor === undefined || isNaN(valor)) return null;
    return '$' + (FORMATO_MONEDA ? FORMATO_MONEDA.format(valor) : Number(valor).toFixed(2));
  }

  function porAnio(lista, limite, conMonto) {
    if (!lista || !lista.length) return '<span class="tenue">—</span>';
    var completo = lista.map(function (a) {
      return a.anio + ': ' + a.total + (conMonto && a.monto ? ' (' + dinero(a.monto) + ')' : '');
    }).join(' · ');
    var visibles = lista.slice(0, limite || 5);
    return '<span class="anios" title="' + esc(completo) + '">' +
      visibles.map(function (a, i) {
        var monto = (conMonto && a.monto) ? '<em>' + esc(dinero(a.monto)) + '</em>' : '';
        return '<span class="chip-anio' + (i === 0 ? ' actual' : '') + '">' +
          esc(a.anio) + ': <strong>' + esc(a.total) + '</strong>' + monto + '</span>';
      }).join(' ') +
      (lista.length > visibles.length
        ? ' <span class="tenue">+' + (lista.length - visibles.length) + ' años</span>' : '') +
      '</span>';
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
        var impl = (r.fecha_instalacion || '').substring(0, 10);
        return '<div class="cliente">' + esc(i.cliente) +
            ' <span class="chip ' + esc(i.tipo) + '">' + esc(i.tipo) + '</span></div>' +
          '<div class="sub">' + esc(r.empresa || '') + '</div>' +
          '<div style="margin:3px 0">' + badgeServicio(i) +
            ' <span class="tenue">' + esc(i.servicio || '') + '</span></div>' +
          '<div class="sub">' +
            (impl ? 'impl. ' + esc(impl) : '') +
            (r.servicio_creado ? ' · svc ' + esc(r.servicio_creado.substring(0, 10)) : '') +
          '</div>';
      } },
    { id: 'tipo', titulo: 'Sistema', grupo: 'instancia', oculta: true,
      valor: function (i) { return i.tipo; },
      render: function (i) { return '<span class="chip ' + esc(i.tipo) + '">' + esc(i.tipo) + '</span>'; } },
    { id: 'ruta', titulo: 'Ruta de instalación', grupo: 'instancia', oculta: true, ancho: 'ancho',
      valor: function (i) { return i.ruta || ''; },
      render: function (i) { return '<code class="ruta" title="' + esc(i.ruta) + '">' + esc(i.ruta) + '</code>'; } },
    { id: 'ruc_proveedor', titulo: 'RUC facturador', grupo: 'instancia', requiere: 'bd',
      valor: function (i) { return ((i.resumen || {}).ruc_proveedor || 'zzz').toLowerCase(); },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.ruc_proveedor_disponible) {
          return badge('gris', 'sin columna',
            'Esta base no tiene la columna del RUC facturador ' +
            '(rucproveedor / ruc_proveedor): falta aplicar la migración');
        }
        var etiqueta = r.ruc_proveedor
          ? '<span>' + esc(r.ruc_proveedor) + '</span>'
          : badge('ambar', 'vacío', 'Sin RUC de facturador cargado');
        if (!(estado.capacidades || {}).acciones_datos) return etiqueta;
        return '<button class="celda-editable editar-campo" type="button" data-id="' + esc(i.id) +
          '" data-campo="rucproveedor" data-etiqueta="RUC facturador"' +
          ' title="Clic para actualizarlo (campo rucproveedor)">' + etiqueta + ' ✎</button>';
      } },
    { id: 'api_cedula', titulo: 'API cédula', grupo: 'instancia', requiere: 'bd',
      valor: function (i) {
        var r = i.resumen || {};
        return !r.api_cedula_disponible ? 2 : (r.api_cedula ? 0 : 1);
      },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.api_cedula_disponible || !(estado.capacidades || {}).acciones_datos) {
          return badgeApiCedula(i);
        }
        return '<button class="celda-editable alternar-api" type="button" data-id="' + esc(i.id) +
          '" data-activar="' + (r.api_cedula ? '0' : '1') +
          '" title="Clic para ' + (r.api_cedula ? 'desactivar' : 'activar') +
          ' la búsqueda de personas por cédula (' + esc(r.api_cedula_columna || '') + ')">' +
          badgeApiCedula(i) + ' ✎</button>';
      } },
    { id: 'implementacion', titulo: 'Implementado', grupo: 'instancia', oculta: true,
      valor: function (i) { return (i.resumen || {}).fecha_instalacion || ''; },
      render: function (i) {
        var r = i.resumen || {};
        return guion((r.fecha_instalacion || '').substring(0, 10)) +
          (r.servicio_creado ? '<div class="sub" title="Fecha del archivo .service">svc ' +
            esc(r.servicio_creado.substring(0, 10)) + '</div>' : '');
      } },

    { id: 'servicio', titulo: 'Servicio', grupo: 'servicio', oculta: true,
      valor: function (i) { return (i.resumen || {}).servicio_activo ? 0 : 1; },
      render: function (i) {
        return badgeServicio(i) + '<div class="sub">' + esc(i.servicio || '') + '</div>';
      } },
    { id: 'url', titulo: 'URL y ruta', grupo: 'servicio', ancho: 'ancho',
      valor: function (i) { return (i.dominio || 'zzz').toLowerCase(); },
      render: function (i) {
        var html = '<div class="linea-url">' +
          (i.url ? '<a href="' + esc(i.url) + '" target="_blank" rel="noopener">' +
            esc(i.dominio) + '</a>' : '<span class="tenue">sin dominio</span>') +
          ' ' + badgeUrl(i) + '</div>';
        html += '<div class="sub ruta-linea" title="' + esc(i.ruta) + '">' +
          '<span class="tenue">ruta</span> <code>' + esc(i.ruta) + '</code></div>';
        if (i.dominio_desactualizado) {
          html += '<div class="sub aviso-inline" title="credenciales.json apunta a ' +
            esc(i.dominio_credenciales) + '">credenciales: ' + esc(i.dominio_credenciales) + '</div>';
        }
        if (i.dominio_compartido) {
          html += '<div class="sub aviso-inline" title="Varias instalaciones tienen este mismo ' +
            'DOMINIO_GENERAL en credenciales.json (suele ser el del template): por eso no se ' +
            'puede deducir su sitio web">dominio compartido en credenciales.json</div>';
        }
        return html;
      } },
    { id: 'base_ssl', titulo: 'Base / SSL', grupo: 'servicio', requiere: 'bd',
      valor: function (i) { return (i.db || {}).ok ? 0 : 1; },
      render: function (i) {
        var db = i.db || {}, c = i.ssl || {};
        return '<div class="linea-dato">' + badgeBase(i) +
            ' <span class="tenue">' + esc(db.dbname || '') + '</span></div>' +
          '<div class="linea-dato">' + badgeSsl(i) +
            (c.valido_hasta ? ' <span class="tenue">vence ' + esc(c.valido_hasta) + '</span>' : '') +
          '</div>' +
          (c.emisor ? '<div class="sub">' + esc(c.emisor) + '</div>' : '');
      } },
    { id: 'ssl', titulo: 'SSL (vence)', grupo: 'servicio', oculta: true,
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

    { id: 'consumo', titulo: 'CPU / RAM', grupo: 'servicio', num: true,
      valor: function (i) { return (i.resumen || {}).cpu_pct || 0; },
      render: function (i) {
        var r = i.resumen || {}, s = i.servicio_estado || {};
        if ((r.cpu_pct === null || r.cpu_pct === undefined) && !r.ram_bytes) {
          return '<span class="tenue">—</span>';
        }
        var cpu = (r.cpu_pct === null || r.cpu_pct === undefined)
          ? '<span class="tenue">CPU —</span>'
          : barra(r.cpu_pct, 'CPU ' + r.cpu_pct + '%',
                  '% de un núcleo · CPU acumulada: ' + (s.cpu_segundos || 0) + ' s', 50, 90);
        var ram = !r.ram_bytes
          ? '<span class="tenue">RAM —</span>'
          : barra((r.ram_pct || 0) * 4, 'RAM ' + r.ram_legible,
                  (r.ram_pct || 0) + '% de la RAM del servidor' +
                  (s.memoria_pico ? ' · pico ' + s.memoria_pico : ''), 50, 80);
        return cpu + '<div style="height:3px"></div>' + ram;
      } },
    { id: 'cpu', titulo: 'CPU', grupo: 'servicio', num: true, oculta: true,
      valor: function (i) { return (i.resumen || {}).cpu_pct || 0; },
      render: function (i) {
        var r = i.resumen || {}, s = i.servicio_estado || {};
        if (r.cpu_pct === null || r.cpu_pct === undefined) return '<span class="tenue">—</span>';
        return barra(r.cpu_pct, r.cpu_pct + '%',
          '% de un núcleo · CPU acumulada: ' + (s.cpu_segundos || 0) + ' s', 50, 90);
      } },
    { id: 'ram', titulo: 'RAM', grupo: 'servicio', num: true, oculta: true,
      valor: function (i) { return (i.resumen || {}).ram_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {}, s = i.servicio_estado || {};
        if (!r.ram_bytes) return '<span class="tenue">—</span>';
        return barra(r.ram_pct === null || r.ram_pct === undefined ? 0 : r.ram_pct * 4,
          r.ram_legible, (r.ram_pct || 0) + '% de la RAM del servidor' +
          (s.memoria_pico ? ' · pico ' + s.memoria_pico : ''), 50, 80);
      } },

    { id: 'base', titulo: 'Base', grupo: 'datos', requiere: 'bd', oculta: true,
      valor: function (i) { return (i.db || {}).ok ? 0 : 1; },
      render: function (i) {
        return badgeBase(i) + '<div class="sub">' + esc((i.db || {}).dbname || '') + '</div>';
      } },
    { id: 'almacenamiento', titulo: 'BD / media / logs / total', grupo: 'datos', num: true,
      valor: function (i) { return (i.resumen || {}).db_tamano_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {}, cap = estado.capacidades || {};
        var lineas = [];
        if (cap.bd !== false) {
          lineas.push('<div class="linea-dato"><span class="tenue">BD</span> ' +
            (r.db_tamano_bytes
              ? barra((r.db_pct_disco || 0) * 5, r.db_tamano,
                      (r.db_pct_disco || 0) + '% del disco', 50, 80)
              : '<span class="tenue">—</span>') + '</div>');
        }
        if (cap.media !== false) {
          lineas.push('<div class="linea-dato"><span class="tenue">Media</span> ' +
            (r.media_bytes
              ? barra((r.media_pct_disco || 0) * 5, r.media_tamano,
                      (r.media_pct_disco || 0) + '% del disco', 50, 80)
              : '<span class="tenue">—</span>') + '</div>');
        }
        if (cap.logs !== false) {
          lineas.push('<div class="linea-dato"><span class="tenue">Logs</span> ' +
            (r.logs_archivos
              ? '<span' + (r.logs_bytes > 524288000 ? ' class="viejo"' : '') + '>' +
                esc(r.logs_tamano) + '</span> <span class="tenue">(' +
                esc(r.logs_archivos) + ')</span>'
              : '<span class="tenue">—</span>') + '</div>');
        }
        // El total va al final, para leer de un vistazo cuánto ocupa en el disco.
        if (r.ocupa_bytes) {
          lineas.push('<div class="linea-dato linea-total"><span class="tenue">Total</span> ' +
            barra((r.ocupa_pct_disco || 0) * 5,
                  r.ocupa_legible + ' · ' + (r.ocupa_pct_disco || 0) + '%',
                  'BD + media + logs frente al disco del servidor', 50, 80) + '</div>');
        }
        return lineas.join('');
      } },
    { id: 'db_tamano', titulo: 'Tamaño BD', grupo: 'datos', requiere: 'bd', num: true, oculta: true,
      valor: function (i) { return (i.resumen || {}).db_tamano_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.db_tamano_bytes) return guion(r.db_tamano);
        return barra((r.db_pct_disco || 0) * 5, r.db_tamano,
          (r.db_pct_disco || 0) + '% del disco del servidor', 50, 80);
      } },
    { id: 'media', titulo: 'Media', grupo: 'datos', requiere: 'media', num: true, oculta: true,
      valor: function (i) { return (i.resumen || {}).media_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.media_bytes) return guion(r.media_tamano);
        return barra((r.media_pct_disco || 0) * 5, r.media_tamano,
          (r.media_pct_disco || 0) + '% del disco del servidor', 50, 80);
      } },
    { id: 'logs', titulo: 'Logs', grupo: 'datos', requiere: 'logs', num: true, oculta: true,
      valor: function (i) { return (i.resumen || {}).logs_bytes || 0; },
      render: function (i) {
        var r = i.resumen || {};
        if (!r.logs_archivos) return '<span class="tenue">—</span>';
        var clase = (r.logs_bytes > 524288000) ? ' class="viejo"' : '';   // > 500 MB
        return '<span' + clase + '>' + esc(r.logs_tamano) + '</span>' +
          '<div class="sub">' + esc(r.logs_archivos) + ' archivo(s)</div>';
      } },

    { id: 'ocupa', titulo: 'Ocupa (BD+media+logs)', grupo: 'datos', num: true, oculta: true,
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
    { id: 'sin_uso', titulo: 'Sin uso', grupo: 'actividad', requiere: 'bd', num: true,
      valor: function (i) {
        var d = (i.resumen || {}).dias_sin_uso;
        return (d === null || d === undefined) ? -1 : d;
      },
      render: function (i) {
        var d = (i.resumen || {}).dias_sin_uso;
        if (d === null || d === undefined) return '<span class="tenue">—</span>';
        var clase = d > 90 ? 'rojo' : (d > 30 ? 'ambar' : 'verde');
        return badge(clase, d + ' d',
          'Días desde la última auditoría, sesión o venta (la más reciente)');
      } },
    { id: 'facturas', titulo: 'Facturación', grupo: 'actividad', requiere: 'bd',
      valor: function (i) {
        var m = (i.resumen || {}).facturas_meses_sin;
        return (m === null || m === undefined) ? 9999 : m;
      },
      render: function (i) {
        var r = i.resumen || {};
        var lineas = [];
        if (r.facturas_total !== null && r.facturas_total !== undefined) {
          lineas.push('total ' + esc(r.facturas_total) +
            (dinero(r.facturas_monto_total) ? ' · ' + esc(dinero(r.facturas_monto_total)) : ''));
        }
        if (r.facturas_anio) {
          lineas.push(esc(r.facturas_anio) + ' este año' +
            (dinero(r.facturas_monto_anio) ? ' · ' + esc(dinero(r.facturas_monto_anio)) : ''));
        }
        if (r.facturas_ultimo_mes) lineas.push('últ. ' + esc(r.facturas_ultimo_mes));
        return badgeFacturas(i) +
          lineas.map(function (t) { return '<div class="sub">' + t + '</div>'; }).join('');
      } },
    { id: 'facturas_anios', titulo: 'Facturas por año', grupo: 'actividad', requiere: 'bd',
      valor: function (i) { return (i.resumen || {}).facturas_anio || 0; },
      render: function (i) {
        var fac = (i.db || {}).facturacion || {};
        if (!fac.disponible) return '<span class="tenue">—</span>';
        return porAnio(fac.por_anio, 4, true);
      } },
    { id: 'primera_venta', titulo: 'Primera venta', grupo: 'actividad', requiere: 'bd',
      valor: function (i) { return (i.resumen || {}).primera_venta || ''; },
      render: function (i) { return guion((i.resumen || {}).primera_venta); } },
    { id: 'ultima_venta', titulo: 'Última venta', grupo: 'actividad', requiere: 'bd',
      valor: function (i) { return (i.resumen || {}).ultima_venta || ''; },
      render: function (i) {
        var r = i.resumen || {};
        return fechaAviso(r.ultima_venta, r.dias_sin_ventas, 15) +
          (r.venta_tabla ? '<div class="sub">' + esc(r.venta_tabla) + '</div>' : '') +
          ((r.ventas_anio !== null && r.ventas_anio !== undefined)
            ? '<div class="sub">' + esc(r.ventas_anio) + ' este año · ' +
              esc(r.ventas_anio_anterior || 0) + ' el anterior</div>' : '');
      } }
  ];

  function columnasVisibles() {
    var cap = estado.capacidades || {};
    return COLUMNAS.filter(function (c) {
      if (c.oculta) return false;               // su dato ya sale en otra columna
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
    $('#fila-grupos').innerHTML = grupos.map(function (g, idx) {
      var titulo = (GRUPOS.filter(function (x) { return x.id === g.id; })[0] || {}).titulo || '';
      // La primera columna queda fija al desplazar en horizontal, así que su
      // casilla de grupo se parte en dos: la fija (opaca, sin texto cuando el
      // grupo abarca más columnas) y el resto con el título centrado.
      if (idx === 0 && cols.length && cols[0].sticky) {
        return '<th class="grupo grupo-' + g.id + ' sticky">' +
          (g.n === 1 ? esc(titulo) : '') + '</th>' +
          (g.n > 1 ? '<th colspan="' + (g.n - 1) + '" class="grupo grupo-' + g.id + '">' +
            esc(titulo) + '</th>' : '');
      }
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
    var api = $('#filtro-api') ? $('#filtro-api').value : '';
    var uso = $('#filtro-uso') ? parseInt($('#filtro-uso').value, 10) : 0;
    var serv = $('#filtro-servicio') ? $('#filtro-servicio').value : '';
    var ocultos = $('#filtro-ocultos') ? $('#filtro-ocultos').value : '';
    return lista.filter(function (i) {
      var r = i.resumen || {};
      if (tipo && i.tipo !== tipo) return false;
      if (ocultos === 'si' && !i.oculta) return false;
      if (ocultos === 'no' && i.oculta) return false;
      if (serv) {
        var se = (i.servicio_estado || {});
        var sk = (i.socket || {});
        if (serv === 'activo' && !se.activo) return false;
        if (serv === 'inactivo' && (se.activo || se.estado === 'failed' || !se.existe)) return false;
        if (serv === 'fallido' && se.estado !== 'failed') return false;
        if (serv === 'sin-unidad' && se.existe) return false;
        if (serv === 'socket' && !(sk.activo && !se.activo)) return false;
      }
      if (api === 'si' && !r.api_cedula) return false;
      if (api === 'no' && r.api_cedula !== false) return false;
      if (api === 'nd' && r.api_cedula_disponible) return false;
      if (uso) {
        if (r.dias_sin_uso === null || r.dias_sin_uso === undefined) return false;
        if (r.dias_sin_uso <= uso) return false;
      }
      if (est === 'oculta' && !i.oculta) return false;
      if (est === 'visible' && i.oculta) return false;
      if (est === 'servicio-inactivo' && r.servicio_activo) return false;
      if (est === 'db-caida' && r.db_ok !== false) return false;
      if (est === 'sitio-desactivado' && r.apache_habilitado !== false) return false;
      if (est === 'ssl-problema' &&
          ['vencido', 'por-vencer', 'autofirmado'].indexOf(r.ssl_estado) === -1) return false;
      if (est === 'url-caida' && r.url_responde !== false) return false;
      if (est === 'dominio-viejo' && !r.dominio_desactualizado) return false;
      if (est === 'dominio-compartido' && !r.dominio_compartido) return false;
      if (est === 'sin-ruc-proveedor' &&
          !(r.ruc_proveedor_disponible && !r.ruc_proveedor)) return false;
      if (est === 'sin-columna-ruc' && r.ruc_proveedor_disponible) return false;
      if (est === 'sin-facturar' && ['detenido', 'sin-facturar-mes'].indexOf(r.facturas_estado) === -1) return false;
      if (est === 'api-activa' && !r.api_cedula) return false;
      if (est === 'api-inactiva' && (r.api_cedula !== false)) return false;
      if (est === 'socket-huerfano' && !(r.socket_activo && !r.servicio_activo)) return false;
      if ((est === 'ok' || est === 'alerta' || est === 'error') && r.salud !== est) return false;
      if (txt) {
        var blob = [i.cliente, i.dominio, i.dominio_credenciales, i.servicio, i.ruta,
                    r.empresa, r.ruc, r.ruc_proveedor, (i.db || {}).dbname,
                    (i.apache || {}).nombre].join(' ').toLowerCase();
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
        // El servidor ya filtra según el permiso: quien no puede ver las
        // ocultas tampoco las recibe, así que nunca entran en este total.
        extra: Object.keys(r.por_tipo || {}).map(function (k) { return k + ': ' + r.por_tipo[k]; })
          .concat(r.ocultas ? [r.ocultas + ' oculta' + (r.ocultas === 1 ? '' : 's')] : [])
          .join(' · ') },
      { rotulo: 'Servicios activos', valor: (r.servicios_activos || 0) + '/' + (r.total || 0),
        clase: r.servicios_inactivos ? 'mal' : 'ok', filtroServicio: 'inactivo' },
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
    if (r.dominios_compartidos) t.push({ rotulo: 'Dominio compartido',
      valor: r.dominios_compartidos, clase: 'mal', filtro: 'dominio-compartido',
      extra: 'mismo DOMINIO_GENERAL en varias instalaciones' });
    if (cap.bd && r.sin_uso_90) {
      t.push({ rotulo: 'Sin uso > 90 días', valor: r.sin_uso_90, clase: 'mal',
        filtroUso: '90', extra: 'sin auditoría, sesión ni ventas' });
    }
    if (cap.bd && (r.sin_ruc_proveedor || r.sin_columna_ruc_proveedor)) {
      t.push({ rotulo: 'RUC proveedor', clase: 'mal',
        valor: (r.sin_ruc_proveedor || 0) + ' sin cargar',
        filtro: 'sin-ruc-proveedor',
        extra: (r.sin_columna_ruc_proveedor || 0) + ' bases sin el campo (falta migrar)' });
    }
    if (cap.bd && (r.api_cedula_activas || r.api_cedula_inactivas)) {
      t.push({ rotulo: 'API cédula activa',
        valor: (r.api_cedula_activas || 0) + '/' +
               ((r.api_cedula_activas || 0) + (r.api_cedula_inactivas || 0)),
        filtroApi: 'no',
        extra: (r.api_cedula_inactivas || 0) + ' con la búsqueda desactivada' });
    }
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
      var extraAttr = x.filtro ? ' clicable" data-filtro="' + x.filtro
                    : (x.filtroApi ? ' clicable" data-filtro-api="' + x.filtroApi
                    : (x.filtroUso ? ' clicable" data-filtro-uso="' + x.filtroUso
                    : (x.filtroServicio ? ' clicable" data-filtro-servicio="' + x.filtroServicio : '')));
      return '<div class="tarjeta ' + (x.clase || '') + extraAttr + '">' +
        '<div class="rotulo">' + esc(x.rotulo) + '</div>' +
        '<div class="valor">' + esc(x.valor) + '</div>' +
        (x.extra ? '<div class="rotulo">' + esc(x.extra) + '</div>' : '') + '</div>';
    }).join('');
  }

  function pintarControles() {
    var selectOcultas = $('#filtro-ocultos');
    if (selectOcultas) {
      // Sólo tiene sentido para quien ve las instancias ocultas.
      selectOcultas.hidden = !((estado.capacidades || {}).ver_excluidos ||
                               MODO === 'excluidos');
    }
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

    var puedeEditar = (estado.capacidades || {}).acciones_datos;
    var rucProv = (inst.resumen || {}).ruc_proveedor;
    var rucProvOk = (inst.resumen || {}).ruc_proveedor_disponible;
    b += '<div class="bloque"><h3>Instancia</h3>' + dl([
      ['Cliente', inst.cliente], ['Sistema', inst.tipo],
      ['Empresa', emp.nombre_empresa || emp.razonsocial], ['RUC', emp.ruc],
      ['RUC facturador', rucProvOk
        ? (puedeEditar
            ? '<span class="campo-editable">' +
              '<input id="campo-rucproveedor" value="' + esc(rucProv || '') +
              '" placeholder="sin cargar" maxlength="20">' +
              '<button class="boton mini guardar-config" type="button" data-id="' + esc(inst.id) +
              '" data-campo="rucproveedor">Guardar</button>' +
              '<span id="resultado-config"></span></span>'
            : esc(rucProv || '(vacío)'))
        : '<span class="badge gris">esta base no tiene el campo (falta migrar)</span>', true],
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

      // Número de facturas e importe van juntos: "12 · $3.450,00".
      var conMonto = function (cantidad, monto) {
        if (cantidad === null || cantidad === undefined) return null;
        var m = dinero(monto);
        return m ? cantidad + ' · ' + m : String(cantidad);
      };
      b += '<div class="bloque"><h3>Facturación electrónica</h3>' + dl([
        ['Estado', badgeFacturas(inst), true],
        ['Facturas totales', conMonto(fac.total, fac.monto_total)],
        ['Este año', conMonto(fac.anio_actual, fac.monto_anio_actual)],
        ['Este mes', conMonto(fac.mes_actual, fac.monto_mes_actual)],
        ['Mes anterior', conMonto(fac.mes_anterior, fac.monto_mes_anterior)],
        ['Primera factura', fac.primera], ['Última factura', fac.ultima],
        ['Emitidas por año', porAnio(fac.por_anio, 12, true), true],
        ['Último mes facturado', fac.ultimo_mes],
        ['Meses sin facturar', fac.meses_sin_facturar],
        ['Columna de importe', fac.columna_monto ||
          (fac.disponible ? 'sin columna de importe en esta base' : null)],
        ['Filtros aplicados', (fac.filtros || []).length
          ? (fac.filtros || []).join(' y ') + ' en verdadero' : 'ninguno'],
        ['Error', fac.error]
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
          '<td>' + guion(v.primera) + '</td><td>' + guion(v.ultima) + '</td>' +
          '<td class="num">' + guion(v.total) + '</td>' +
          '<td>' + porAnio(v.por_anio, 6) + '</td>' +
          '<td>' + (v.error ? badge('rojo', 'error', v.error) : '') + '</td></tr>';
      }).join('') || '<tr><td colspan="6" class="tenue">Sin tablas de ventas detectadas</td></tr>';
      ventas = '<div class="bloque" style="margin-top:14px"><h3>Ventas por origen</h3>' +
        '<table class="tabla-mini"><thead><tr><th>Origen</th><th>Primera</th><th>Última</th>' +
        '<th class="num">Registros</th><th>Por año</th><th></th></tr></thead><tbody>' +
        filas + '</tbody></table></div>';
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

  // Identificadores de las filas que están en pantalla, en el orden en que se
  // ven: la exportación reproduce exactamente eso (filtros y orden incluidos).
  function idsVisibles() {
    return Array.prototype.map.call(
      document.querySelectorAll('#cuerpo-tabla tr[data-id]'),
      function (tr) { return tr.getAttribute('data-id'); });
  }

  function descargar(ruta, nombre, boton, ids) {
    if (boton) boton.disabled = true;
    aviso('Generando ' + nombre + '…', 'aviso-info');
    var opciones = { credentials: 'same-origin' };
    if (ids && ids.length) {
      opciones.method = 'POST';
      opciones.headers = { 'Content-Type': 'application/json' };
      opciones.body = JSON.stringify({ ids: ids });
    }
    fetch(ruta, opciones)
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
        caja.innerHTML = listaRevisiones(d.revisiones);
        return d.ok;
      });
  }

  function listaRevisiones(revisiones) {
    return '<ul class="revisiones">' + (revisiones || []).map(function (r) {
      var icono = r.ok ? '✔' : (r.critico ? '✖' : '!');
      var clase = r.ok ? 'ok' : (r.critico ? 'error' : 'aviso');
      return '<li class="rev-' + clase + '">' + icono + ' ' + esc(r.mensaje) +
        (r.detalle ? ' <span class="tenue">— ' + esc(r.detalle) + '</span>' : '') + '</li>';
    }).join('') + '</ul>';
  }

  function diagnosticarEntorno() {
    var caja = $('#diagnostico-cuerpo');
    caja.innerHTML = '<p class="tenue">Revisando el servidor (puede tardar unos segundos)…</p>';
    fetch('/api/aprovisionar/diagnostico', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) { caja.innerHTML = '<div class="aviso-error">' + esc(d.error) + '</div>'; return; }
        caja.innerHTML =
          '<div class="aviso ' + (d.ok ? 'aviso-ok' : 'aviso-error') + '" style="margin:0 0 10px">' +
            (d.ok ? 'El entorno está listo para crear instancias'
                  : d.errores + ' problema(s) impiden crear instancias') +
            (d.avisos ? ' · ' + d.avisos + ' aviso(s)' : '') + '</div>' +
          '<div class="grid-detalle">' + (d.grupos || []).map(function (g) {
            return '<div class="bloque"><h3>' + esc(g.titulo) + '</h3>' + listaRevisiones(g.revisiones) + '</div>';
          }).join('') + '</div>';
      })
      .catch(function (e) { caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>'; });
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
              enlacesDescarga(d) +
              (d.estado === 'ok' && d.datos && d.datos.instancia_id
                ? ((d.datos.problemas || []).length
                    ? '<div class="aviso-error" style="margin-top:10px">La instancia se creó, pero la ' +
                      'verificación encontró problemas: ' + esc(d.datos.problemas.join('; ')) + '</div>'
                    : '') +
                  '<div class="aviso-ok" style="margin-top:10px">Instancia creada. ' +
                  '<button class="boton mini" type="button" id="btn-ver-nueva" data-id="' +
                  esc(d.datos.instancia_id) + '">Ver en el panel</button></div>'
                : '');
            refrescarPaginaTrasTarea(d);
          }
        })
        .catch(function () {});
    };
    tick();
    tareaPoll = setInterval(tick, 1500);
  }

  // Backups recién generados: se pueden bajar desde la misma ventana de la tarea.
  function enlacesDescarga(d) {
    if (d.tipo !== 'backup') return '';
    var listos = ((d.datos || {}).resultados || []).filter(function (r) { return r.ok && r.archivo; });
    if (!listos.length) return '';
    return '<div class="aviso-ok" style="margin-top:10px">Backups listos para descargar: ' +
      listos.map(function (r) {
        return '<a class="boton mini" style="margin:4px 4px 0 0" href="/backups/descargar?archivo=' +
          encodeURIComponent(r.archivo) + '">⬇ ' + esc(r.nombre || r.archivo) +
          (r.tamano ? ' (' + esc(r.tamano) + ')' : '') + '</a>';
      }).join('') + '</div>';
  }

  // Cada página refresca lo suyo: cargar() pinta la tabla de instancias,
  // que sólo existe en el panel principal y en /excluidos.
  function refrescarPaginaTrasTarea(d) {
    if (MODO === 'backups') { verBackups(); return verBases(); }
    if (MODO === 'certificados') return verCertificados('#contenido');
    if (MODO === 'principal' || MODO === 'excluidos') return cargar();
    return cargarCapacidades();
  }

  function tablaCron(d) {
    var entradas = d.entradas || [];
    var filas = entradas.map(function (c) {
      return '<tr><td>' + esc(c.descripcion || c.expresion || '') +
        (c.expresion && c.expresion !== c.descripcion
          ? '<div class="sub"><code>' + esc(c.expresion) + '</code></div>' : '') +
        '</td><td>' + esc(c.usuario || '') + '</td>' +
        '<td class="celda-larga"><code>' + esc(c.comando) + '</code></td>' +
        '<td class="sub">' + esc(c.origen) + '</td></tr>';
    }).join('');
    var timers = (d.timers || []).map(function (t) {
      return '<tr><td>' + (t.proxima ? 'próxima: ' + esc(t.proxima) : '<span class="tenue">—</span>') +
        (t.ultima ? '<div class="sub">última: ' + esc(t.ultima) + '</div>' : '') +
        '</td><td>root</td><td class="celda-larga"><code>' + esc(t.unidad) + '</code>' +
        (t.descripcion ? '<div class="sub">' + esc(t.descripcion) + '</div>' : '') +
        (t.activa ? '<div class="sub">dispara ' + esc(t.activa) + '</div>' : '') +
        '</td><td class="sub">systemd</td></tr>';
    }).join('');
    return '<h3 class="titulo-seccion">Programadas en el servidor</h3>' +
      '<p class="tenue">Crontabs de los usuarios, /etc/crontab, /etc/cron.d y los ' +
      'temporizadores de systemd. Es sólo lectura: para cambiarlas se edita el crontab ' +
      'en el servidor.</p>' +
      ((d.errores || []).length
        ? '<p class="aviso-inline">' + esc((d.errores || []).join(' · ')) + '</p>' : '') +
      '<table class="tabla-mini"><thead><tr><th>Cuándo</th><th>Usuario</th><th>Comando</th>' +
      '<th>Origen</th></tr></thead><tbody>' +
      (filas + timers || '<tr><td colspan="4" class="vacio">Sin tareas programadas</td></tr>') +
      '</tbody></table>';
  }

  function tablaTareas(d) {
    return '<h3 class="titulo-seccion">Ejecutadas desde el panel</h3>' +
      '<table class="tabla-mini"><thead><tr><th>Inicio</th>' +
      '<th>Tarea</th><th>Usuario</th><th>Estado</th><th></th></tr></thead><tbody>' +
      ((d.tareas || []).map(function (t) {
        return '<tr><td>' + esc(t.creado) + '</td><td>' + esc(t.titulo) + '</td><td>' +
          esc(t.creado_por || '') + '</td><td>' +
          badge(t.estado === 'ok' ? 'verde' : (t.estado === 'error' ? 'rojo' : 'ambar'), t.estado) +
          '</td><td><button class="boton mini" type="button" data-tarea="' + esc(t.id) +
          '">Ver log</button></td></tr>';
      }).join('') || '<tr><td colspan="5" class="vacio">Sin tareas todavía</td></tr>') +
      '</tbody></table>';
  }

  // Dos cosas distintas en la misma ventana: lo que el servidor tiene
  // programado (cron) y lo que el panel ha lanzado en esta instalación.
  function listarTareas() {
    $('#tarea-titulo').textContent = 'Tareas programadas y ejecutadas';
    $('#tarea-cuerpo').innerHTML = '<p class="tenue">Cargando…</p>';
    $('#modal-tarea').classList.remove('oculto');
    Promise.all([
      fetch('/api/cron', { credentials: 'same-origin' }).then(function (r) { return r.json(); })
        .catch(function (e) { return { errores: ['no se pudo leer el cron: ' + e.message] }; }),
      fetch('/api/tareas', { credentials: 'same-origin' }).then(function (r) { return r.json(); })
        .catch(function () { return {}; })
    ]).then(function (respuestas) {
      $('#tarea-cuerpo').innerHTML = tablaCron(respuestas[0]) + tablaTareas(respuestas[1]);
    }).catch(function (e) { aviso(e.message); });
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


  function abrirModalCampo(id, campo, etiqueta) {
    var inst = estado.datos.filter(function (i) { return i.id === id; })[0] || {};
    var valor = (inst.resumen || {})[campo === 'rucproveedor' ? 'ruc_proveedor' : campo] || '';
    var caja = $('#modal-campo');
    if (!caja) {
      caja = document.createElement('div');
      caja.id = 'modal-campo';
      caja.className = 'modal oculto';
      caja.innerHTML = '<div class="modal-caja" style="max-width:460px">' +
        '<div class="modal-cabecera"><h2 id="campo-titulo"></h2>' +
        '<button class="cerrar" id="campo-cerrar" type="button">&times;</button></div>' +
        '<div class="modal-cuerpo" id="campo-cuerpo"></div></div>';
      document.body.appendChild(caja);
    }
    $('#campo-titulo').textContent = etiqueta + ' · ' + inst.cliente;
    $('#campo-cuerpo').innerHTML =
      '<p class="tenue">' + esc((inst.resumen || {}).empresa || '') +
        ' · columna <code>' + esc((inst.resumen || {}).ruc_proveedor_columna || campo) +
        '</code> de seguridad_configuracion</p>' +
      '<input id="campo-valor" value="' + esc(valor) + '" maxlength="20" ' +
        'placeholder="sin cargar" style="width:100%;padding:9px 10px;border:1px solid var(--borde);' +
        'border-radius:7px;font:inherit">' +
      '<div style="margin-top:12px;text-align:right">' +
        '<button class="boton mini" type="button" id="campo-cancelar">Cancelar</button> ' +
        '<button class="boton" type="button" id="campo-guardar" data-id="' + esc(id) +
        '" data-campo="' + esc(campo) + '">Guardar</button></div>' +
      '<div id="campo-resultado"></div>';
    caja.classList.remove('oculto');
    setTimeout(function () { var e = $('#campo-valor'); if (e) { e.focus(); e.select(); } }, 30);
  }

  function guardarModalCampo(id, campo, boton) {
    var valor = ($('#campo-valor').value || '').trim();
    boton.disabled = true;
    $('#campo-resultado').innerHTML = '<p class="tenue">Guardando…</p>';
    fetch('/api/instancia/' + encodeURIComponent(id) + '/configuracion', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ campo: campo, valor: valor })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        boton.disabled = false;
        if (!d.ok) {
          $('#campo-resultado').innerHTML = '<div class="aviso-error">' + esc(d.error) + '</div>';
          return;
        }
        $('#campo-resultado').innerHTML = '<div class="aviso-ok">Guardado</div>';
        cargar().then(function () {
          setTimeout(function () { $('#modal-campo').classList.add('oculto'); }, 600);
        });
      })
      .catch(function (e) {
        boton.disabled = false;
        $('#campo-resultado').innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>';
      });
  }

  function guardarConfiguracion(id, campo, boton) {
    var entrada = $('#campo-' + campo);
    if (!entrada) return;
    var valor = (entrada.value || '').trim();
    boton.disabled = true;
    $('#resultado-config').innerHTML = ' <span class="tenue">Guardando…</span>';
    fetch('/api/instancia/' + encodeURIComponent(id) + '/configuracion', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ campo: campo, valor: valor })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        boton.disabled = false;
        if (!d.ok) {
          $('#resultado-config').innerHTML = ' <span class="aviso-error">' + esc(d.error) + '</span>';
          return;
        }
        $('#resultado-config').innerHTML = ' <span class="aviso-ok">guardado</span>';
        cargar();
      })
      .catch(function (e) {
        boton.disabled = false;
        $('#resultado-config').innerHTML = ' <span class="aviso-error">' + esc(e.message) + '</span>';
      });
  }

  function cambiarApiCedula(id, activar, boton) {
    var inst = estado.datos.filter(function (i) { return i.id === id; })[0] || {};
    if (!window.confirm((activar ? 'Activar' : 'Desactivar') +
        ' la búsqueda de personas por cédula en "' + inst.cliente +
        '"? Se actualiza seguridad_configuracion en su base.')) return;
    boton.disabled = true;
    if ($('#resultado-api')) $('#resultado-api').innerHTML = ' <span class="tenue">Aplicando…</span>';
    fetch('/api/instancia/' + encodeURIComponent(id) + '/api-cedula', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activar: activar })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        boton.disabled = false;
        if (!d.ok) {
          if ($('#resultado-api')) {
            $('#resultado-api').innerHTML = ' <span class="aviso-error">' + esc(d.error) + '</span>';
          } else { aviso(d.error); }
          return;
        }
        aviso('Búsqueda por cédula ' + (activar ? 'activada' : 'desactivada') +
              ' en ' + inst.cliente + ' (' + d.columna + ')', 'aviso-ok');
        var enDetalle = !$('#modal').classList.contains('oculto');
        cargar().then(function () { if (enDetalle) abrirDetalle(id); });
      })
      .catch(function (e) {
        boton.disabled = false;
        if ($('#resultado-api')) {
          $('#resultado-api').innerHTML = ' <span class="aviso-error">' + esc(e.message) + '</span>';
        } else { aviso(e.message); }
      });
  }

  var estadoCert = { datos: [], orden: 'dias', asc: true, destino: null };

  var COLUMNAS_CERT = [
    { id: 'nombre', titulo: 'Certificado', valor: function (c) { return (c.nombre || '').toLowerCase(); } },
    { id: 'cliente', titulo: 'Instancia', valor: function (c) { return (c.cliente || 'zzz').toLowerCase(); } },
    { id: 'dominios', titulo: 'Dominios', valor: function (c) { return (c.dominios || []).join(','); } },
    { id: 'emitido', titulo: 'Emitido', valor: function (c) { return c.emitido || ''; } },
    { id: 'vence', titulo: 'Vence', valor: function (c) { return c.vence || ''; } },
    { id: 'dias', titulo: 'Días', num: true,
      valor: function (c) { return (c.dias === null || c.dias === undefined) ? 999999 : c.dias; } },
    { id: 'estado', titulo: 'Estado', valor: function (c) { return c.estado || ''; } }
  ];

  function verCertificados(destino) {
    estadoCert.destino = destino || '#tarea-cuerpo';
    var caja = $(estadoCert.destino);
    if (!destino) {
      $('#tarea-titulo').textContent = 'Certificados SSL';
      $('#modal-tarea').classList.remove('oculto');
      if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
    }
    caja.innerHTML = '<p class="tenue">Consultando certbot…</p>';

    return fetch('/api/certificados', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error && !(d.certificados || []).length) {
          caja.innerHTML = '<div class="aviso-error">' + esc(d.error) + '</div>';
          return;
        }
        estadoCert.datos = d.certificados || [];
        estadoCert.meta = d;
        pintarCertificados();
      })
      .catch(function (e) {
        caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>';
      });
  }

  function pintarCertificados() {
    var d = estadoCert.meta || {}, cap = estado.capacidades || {};
    var caja = $(estadoCert.destino);
    caja.innerHTML =
      '<div class="barra" style="padding-left:0;padding-right:0">' +
        '<input type="search" id="filtro-cert" placeholder="Buscar certificado, dominio o cliente…"' +
          ' value="' + esc(estadoCert.filtro || '') + '">' +
        '<select id="filtro-cert-estado">' +
          ['', 'vigente', 'renovable', 'por-vencer', 'vencido', 'renovacion-pausada', 'sin-instancia']
            .map(function (v) {
              var etiquetas = { '': 'Todos los estados', 'sin-instancia': 'Sin instancia asociada',
                                'renovacion-pausada': 'Renovación pausada' };
              return '<option value="' + esc(v) + '"' +
                (estadoCert.estado === v ? ' selected' : '') + '>' +
                esc(etiquetas[v] || v) + '</option>';
            }).join('') +
        '</select>' +
        '<span class="crece"></span>' +
        '<span class="tenue" id="contador-cert"></span>' +
        (cap.acciones_certbot
          ? ' <button class="boton mini renovar-cert" type="button" data-simular="1">Probar renovación</button>' +
            ' <button class="boton renovar-cert" type="button">Renovar los que toquen</button>'
          : '') +
      '</div>' +
      avisoRenovacionAutomatica(d.automatica) +
      '<p class="tenue">Fuente: ' + (d.certbot ? 'certbot certificates' : '/etc/letsencrypt/live') +
        ' · ' + esc(d.total || 0) + ' certificado(s)' +
        (cap.acciones_apache
          ? ' · <button class="boton mini servidor-web" type="button" data-modo="recargar">' +
              'Recargar Apache/nginx</button> ' +
            '<button class="boton mini servidor-web" type="button" data-modo="reiniciar">' +
              'Reiniciar Apache/nginx</button>'
          : '') + '</p>' +
      '<div class="tabla-envoltura"><table class="tabla" id="tabla-cert" style="min-width:auto">' +
        '<thead><tr id="cabecera-cert"></tr></thead>' +
        '<tbody id="cuerpo-cert"></tbody></table></div>' +
      tablaSinCertificado(d.sin_certificado || [], cap);
    pintarFilasCert();
  }

  function avisoRenovacionAutomatica(a) {
    if (!a) return '';
    if (a.activa) {
      return '<div class="aviso aviso-ok" style="margin:0 0 8px">Renovación automática activa (' +
        esc(a.timer_activo ? a.timer : a.cron) + ')' +
        (a.proxima ? ' · próxima ejecución: ' + esc(a.proxima) : '') +
        (a.ultima ? ' · última: ' + esc(a.ultima) : '') + '</div>';
    }
    return '<div class="aviso aviso-error" style="margin:0 0 8px">No hay renovación automática: ' +
      'ni certbot.timer ni /etc/cron.d/certbot están activos. Los certificados vencerán si ' +
      'nadie los renueva desde aquí (o activa el timer con: systemctl enable --now certbot.timer).</div>';
  }

  function tablaSinCertificado(lista, cap) {
    if (!lista.length) return '';
    return '<h3 style="margin:18px 0 6px">Instancias sin certificado de Let\'s Encrypt (' +
        lista.length + ')</h3>' +
      '<p class="tenue">Tienen dominio y sitio web, pero ningún certificado lo cubre. ' +
        'Antes de emitir, el dominio debe apuntar por DNS a este servidor.</p>' +
      '<div class="tabla-envoltura"><table class="tabla" style="min-width:auto"><thead><tr>' +
        '<th>Instancia</th><th>Dominio</th><th>Servidor web</th><th>SSL actual</th><th></th>' +
      '</tr></thead><tbody>' +
      lista.map(function (f) {
        return '<tr><td class="cliente">' + esc(f.cliente) +
          (f.oculta ? ' <span class="badge gris">oculta</span>' : '') +
          '<div class="sub">' + esc(f.tipo || '') + '</div></td>' +
          '<td>' + esc(f.dominio) + '</td><td>' + esc(f.servidor) + '</td>' +
          '<td>' + guion(f.ssl) + '</td>' +
          '<td>' + (cap.acciones_certbot
            ? '<button class="boton mini emitir-cert" type="button" data-dominio="' +
              esc(f.dominio) + '" data-servidor="' + esc(f.servidor) + '">Emitir certificado</button>'
            : '') + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }

  function pintarFilasCert() {
    var cap = estado.capacidades || {};
    var txt = (estadoCert.filtro || '').toLowerCase().trim();
    var filtroEstado = estadoCert.estado || '';

    var lista = (estadoCert.datos || []).filter(function (c) {
      if (filtroEstado === 'sin-instancia' && c.cliente) return false;
      if (filtroEstado && filtroEstado !== 'sin-instancia' && c.estado !== filtroEstado) return false;
      if (txt) {
        var blob = [c.nombre, (c.dominios || []).join(' '), c.cliente, c.emisor, c.estado]
          .join(' ').toLowerCase();
        if (blob.indexOf(txt) === -1) return false;
      }
      return true;
    });

    var col = COLUMNAS_CERT.filter(function (c) { return c.id === estadoCert.orden; })[0]
              || COLUMNAS_CERT[5];
    var dir = estadoCert.asc ? 1 : -1;
    lista = lista.slice().sort(function (a, b) {
      var va = col.valor(a), vb = col.valor(b);
      if (va === vb) return (a.nombre || '').localeCompare(b.nombre || '');
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });

    $('#cabecera-cert').innerHTML = COLUMNAS_CERT.map(function (c) {
      return '<th data-cert="' + c.id + '" class="' + (c.num ? 'num ' : '') +
        (estadoCert.orden === c.id ? 'ordenada' : '') + '">' + esc(c.titulo) +
        (estadoCert.orden === c.id ? (estadoCert.asc ? ' ▲' : ' ▼') : '') + '</th>';
    }).join('') + '<th></th>';

    $('#cuerpo-cert').innerHTML = lista.map(function (c) {
      var clase = c.estado === 'vigente' ? 'verde'
                : (['renovable', 'por-vencer', 'renovacion-pausada'].indexOf(c.estado) !== -1
                   ? 'ambar' : 'rojo');
      var pausada = c.renovacion === 'pausada';
      var acciones = '';
      if (cap.acciones_certbot) {
        acciones =
          '<button class="boton mini renovar-cert" type="button" data-nombre="' +
            esc(c.nombre) + '">Renovar</button> ' +
          '<button class="boton mini renovar-cert" type="button" data-forzar="1" ' +
            'title="Renueva ya, aunque falten más de 30 días para el vencimiento" data-nombre="' +
            esc(c.nombre) + '">Forzar</button> ' +
          '<button class="boton mini cert-accion" type="button" data-nombre="' + esc(c.nombre) +
            '" data-accion="' + (pausada ? 'reanudar' : 'pausar') + '">' +
            (pausada ? 'Reanudar' : 'Pausar') + '</button> ' +
          '<button class="boton mini peligro cert-accion" type="button" style="color:#fff" ' +
            'data-nombre="' + esc(c.nombre) + '" data-accion="eliminar">Eliminar</button>';
      }
      return '<tr><td><strong>' + esc(c.nombre) + '</strong>' +
        (c.emisor ? '<div class="sub">' + esc(c.emisor) + '</div>' : '') + '</td>' +
        '<td>' + (c.cliente ? esc(c.cliente) : '<span class="tenue">sin instancia</span>') + '</td>' +
        '<td class="sub">' + esc((c.dominios || []).join(', ')) + '</td>' +
        '<td>' + guion(c.emitido) + '</td>' +
        '<td>' + guion(c.vence) + '</td>' +
        '<td class="num">' + badge(clase, (c.dias === null || c.dias === undefined ? '?' : c.dias + 'd')) + '</td>' +
        '<td>' + esc(c.estado) +
          (pausada ? '<div class="sub aviso-inline">sin renovación automática</div>' : '') + '</td>' +
        '<td>' + acciones + '</td></tr>';
    }).join('') || '<tr><td colspan="8" class="vacio">Sin certificados que coincidan</td></tr>';

    if ($('#contador-cert')) {
      $('#contador-cert').textContent = lista.length + ' de ' + (estadoCert.datos || []).length;
    }
  }

  function accionCertificado(nombre, accion, forzar) {
    var cuerpo = { nombre: nombre, accion: accion, forzar: !!forzar };
    if (accion === 'eliminar' && !forzar) {
      if (!window.confirm('¿Eliminar el certificado "' + nombre + '"?\n\n' +
          'Se borran sus archivos y su configuración de renovación. Si algún vhost lo usa, ' +
          'el servidor web no arrancará hasta corregirlo o emitir uno nuevo.')) return;
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
        if (!d.ok) {
          if (d.puede_forzar && window.confirm(
              (d.error || '') + '\n\nQuedaron en disco:\n' + (d.restos || []).join('\n') +
              '\n\n¿Borrar esos restos a mano?')) {
            return accionCertificado(nombre, 'eliminar', true);
          }
          aviso((d.error || 'No se pudo aplicar') + (d.salida ? ' · ' + d.salida : ''));
          return;
        }
        aviso((d.mensaje || 'Listo') + (d.aviso ? ' · ' + d.aviso : ''), 'aviso-ok');
        verCertificados(estadoCert.destino === '#contenido' ? '#contenido' : null);
      })
      .catch(function (e) { aviso(e.message); });
  }

  function renovarCertificado(nombre, simular, forzar) {
    var texto = nombre ? ('el certificado ' + nombre) : 'los certificados que lo necesiten';
    if (forzar && !window.confirm('¿Forzar la renovación de ' + texto + ' ahora?\n\n' +
        'Let\'s Encrypt limita a 5 renovaciones del mismo certificado por semana: úsalo ' +
        'sólo si el certificado está dañado o cambió de dominio.')) return;
    if (!simular && !forzar && !window.confirm('¿Renovar ' + texto + ' con certbot?')) return;
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

  function lanzarTarea(url, cuerpo) {
    return fetch(url, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(cuerpo || {})
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo iniciar'); return; }
        verTarea(d.tarea);
      })
      .catch(function (e) { aviso(e.message); });
  }

  function emitirCertificado(dominio, servidor) {
    if (!window.confirm('¿Emitir un certificado de Let\'s Encrypt para ' + dominio + ' (' +
        servidor + ')?\n\nSe comprueba antes el DNS, certbot ajusta el sitio web para HTTPS ' +
        'con redirección y después se recarga el servidor web.')) return;
    lanzarTarea('/api/certificados/emitir', { dominio: dominio, servidor: servidor });
  }

  function servidorWeb(modo) {
    var texto = modo === 'reiniciar'
      ? '¿Reiniciar Apache/nginx? Las conexiones en curso se cortan unos segundos.'
      : '¿Recargar Apache/nginx? No corta conexiones; aplica certificados y vhosts nuevos.';
    if (!window.confirm(texto + '\n\nAntes se valida la configuración: si tiene errores no se toca.')) return;
    lanzarTarea('/api/servidor-web', { modo: modo });
  }

  // ----------------------------------------------------------------- consumo
  var datosConsumo = null, consumoAuto = null, consumoMidiendo = false;

  function medirConsumo() {
    if (consumoMidiendo) return;
    consumoMidiendo = true;
    $('#consumo-medido').textContent = 'Midiendo…';
    fetch('/api/consumo', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var primera = !datosConsumo;
        datosConsumo = d;
        pintarConsumo();
        if (primera) medirCarpetas(false);   // necesita el tamaño del disco para los %
        $('#consumo-medido').textContent = 'Medido: ' + (d.medido || '');
      })
      .catch(function (e) { aviso('No se pudo medir el consumo: ' + e.message); })
      .then(function () { consumoMidiendo = false; });
  }

  function pctDe(parte, total) {
    return total ? Math.round(parte * 1000 / total) / 10 : null;
  }

  function pintarConsumo() {
    var d = datosConsumo;
    if (!d) return;
    var s = d.sistema || {}, sv = d.servicios || {}, tot = sv.totales || {};
    var raiz = (s.discos || []).filter(function (x) { return x.montaje === '/'; })[0] ||
               (s.discos || [])[0] || {};
    var tarjeta = function (rotulo, valor, extra, pct, ambar, rojo) {
      var clase = pct === null || pct === undefined ? ''
        : (pct >= (rojo || 85) ? 'mal' : (pct >= (ambar || 70) ? '' : 'ok'));
      return '<div class="tarjeta ' + clase + '"><div class="rotulo">' + esc(rotulo) + '</div>' +
        '<div class="valor">' + esc(valor) + '</div>' +
        (extra ? '<div class="rotulo">' + esc(extra) + '</div>' : '') + '</div>';
    };
    var infra = tot.infraestructura || {}, inst = tot.instancia || {};
    $('#tarjetas-consumo').innerHTML = [
      tarjeta('CPU (carga 1 min)', (s.carga_pct !== null && s.carga_pct !== undefined ? s.carga_pct + ' %' : '-'),
              (s.nucleos || '?') + ' núcleos · carga ' + ((s.carga_1_5_15 || []).join(' / ') || '-'),
              s.carga_pct),
      tarjeta('RAM usada', (s.ram_pct !== undefined ? s.ram_pct + ' %' : '-'),
              (s.ram_usada_legible || '-') + ' de ' + (s.ram_total_legible || '-'), s.ram_pct),
      tarjeta('Swap', (s.swap || {}).porcentaje !== null && (s.swap || {}).porcentaje !== undefined
              ? s.swap.porcentaje + ' %' : 'sin swap',
              ((s.swap || {}).usado || '-') + ' de ' + ((s.swap || {}).total || '-'),
              (s.swap || {}).porcentaje, 30, 60),
      tarjeta('Disco /', (raiz.porcentaje !== undefined ? raiz.porcentaje + ' %' : '-'),
              (raiz.libre || '-') + ' libres de ' + (raiz.total || '-'), raiz.porcentaje),
      tarjeta('RAM instancias', inst.memoria || '-',
              (inst.servicios || 0) + ' servicios · CPU ' + (inst.cpu_pct || 0) + ' %',
              pctDe(inst.memoria_bytes || 0, s.ram_total)),
      tarjeta('RAM infraestructura', infra.memoria || '-',
              'PostgreSQL, Apache, nginx… · CPU ' + (infra.cpu_pct || 0) + ' %',
              pctDe(infra.memoria_bytes || 0, s.ram_total)),
      tarjeta('Encendido hace', s.uptime || '-', null, null)
    ].join('');

    $('#cuerpo-discos').innerHTML = (s.discos || []).map(function (x) {
      return '<tr><td><strong>' + esc(x.montaje) + '</strong></td><td class="sub">' +
        esc(x.dispositivo) + ' · ' + esc(x.tipo) + '</td>' +
        '<td class="num">' + esc(x.total) + '</td><td class="num">' + esc(x.usado) + '</td>' +
        '<td class="num">' + esc(x.libre) + '</td>' +
        '<td>' + barra(x.porcentaje, x.porcentaje + ' %', '', 70, 85) + '</td>' +
        '<td class="num">' + guion(x.inodos_pct === null ? null : x.inodos_pct + ' %') + '</td></tr>';
    }).join('') || '<tr><td class="vacio" colspan="7">Sin datos de discos</td></tr>';

    pintarConsumoInstancias();
    pintarServicios();
    pintarProcesos();
  }

  function pintarConsumoInstancias() {
    var d = datosConsumo;
    if (!d) return;
    var ramTotal = (d.sistema || {}).ram_total || 0;
    var raiz = ((d.sistema || {}).discos || []).filter(function (x) { return x.montaje === '/'; })[0] || {};
    var txt = ($('#filtro-consumo-inst').value || '').toLowerCase().trim();
    var orden = $('#orden-consumo-inst').value || 'ram_bytes';
    var filas = (d.instancias || []).filter(function (f) {
      return !txt || (f.cliente || '').toLowerCase().indexOf(txt) !== -1;
    }).sort(function (a, b) { return (b[orden] || 0) - (a[orden] || 0); });
    $('#cuerpo-consumo-inst').innerHTML = filas.map(function (f) {
      var pctRam = pctDe(f.ram_bytes, ramTotal);
      var pctDisco = pctDe(f.disco_bytes, raiz.total_bytes);
      return '<tr><td class="cliente">' + esc(f.cliente) +
          (f.oculta ? ' <span class="badge gris">oculta</span>' : '') +
          ' <span class="chip ' + esc(f.tipo) + '">' + esc(f.tipo) + '</span>' +
          (f.activo ? '' : ' ' + badge('rojo', 'detenida')) + '</td>' +
        '<td>' + (f.ram_bytes ? barra(pctRam, f.ram + ' · ' + pctRam + ' %', 'del total de RAM', 10, 25)
                              : '<span class="tenue">—</span>') + '</td>' +
        '<td class="num">' + (f.cpu_pct === null || f.cpu_pct === undefined ? '—' : f.cpu_pct + ' %') + '</td>' +
        '<td class="num">' + esc(f.db) + '</td><td class="num">' + esc(f.media) + '</td>' +
        '<td class="num">' + esc(f.logs) + '</td>' +
        '<td>' + barra(pctDisco, f.disco + ' · ' + (pctDisco || 0) + ' %', 'del disco /', 5, 15) + '</td></tr>';
    }).join('') || '<tr><td class="vacio" colspan="7">Sin instancias</td></tr>';
  }

  function pintarServicios() {
    var d = datosConsumo;
    if (!d) return;
    var sv = d.servicios || {};
    if (sv.ok === false) {
      $('#cuerpo-servicios').innerHTML = '<tr><td class="vacio" colspan="6">' +
        esc(sv.error || 'No se pudo consultar systemd') + '</td></tr>';
      return;
    }
    var ramTotal = (d.sistema || {}).ram_total || 0;
    var cat = $('#filtro-servicio-cat').value;
    var etiquetas = { instancia: ['azul', 'instancia'], infraestructura: ['ambar', 'infraestructura'],
                      sistema: ['gris', 'sistema'] };
    var filas = (sv.servicios || []).filter(function (f) { return !cat || f.categoria === cat; });
    $('#cuerpo-servicios').innerHTML = filas.map(function (f) {
      var pct = pctDe(f.memoria_bytes || 0, ramTotal);
      var e = etiquetas[f.categoria] || ['gris', f.categoria];
      return '<tr><td><strong>' + esc(f.nombre) + '</strong>' +
          (f.cliente ? ' <span class="tenue">(' + esc(f.cliente) + ')</span>' : '') +
          '<div class="sub">' + esc(f.descripcion || '') + '</div></td>' +
        '<td>' + badge(e[0], e[1]) + '</td>' +
        '<td>' + (f.memoria_bytes ? barra(pct, f.memoria + ' · ' + pct + ' %', '', 10, 25)
                                  : '<span class="tenue">—</span>') + '</td>' +
        '<td class="num">' + (f.cpu_pct === null ? '—' : f.cpu_pct + ' %') + '</td>' +
        '<td class="num">' + guion(f.tareas) + '</td><td class="num">' + guion(f.pid) + '</td></tr>';
    }).join('') || '<tr><td class="vacio" colspan="6">Sin servicios</td></tr>';
    var t = sv.totales || {};
    $('#resumen-servicios').textContent = Object.keys(t).map(function (k) {
      return k + ': ' + t[k].servicios + ' · ' + t[k].memoria + ' · CPU ' + t[k].cpu_pct + ' %';
    }).join('  |  ');
  }

  function pintarProcesos() {
    var d = datosConsumo;
    if (!d) return;
    var p = d.procesos || {};
    if (p.ok === false) {
      $('#cuerpo-procesos').innerHTML = '<tr><td class="vacio" colspan="7">' +
        esc(p.error || 'No se pudieron leer los procesos') + '</td></tr>';
      return;
    }
    var lista = p[$('#procesos-por').value] || [];
    $('#cuerpo-procesos').innerHTML = lista.map(function (f) {
      return '<tr><td class="num">' + esc(f.pid) + '</td><td>' + esc(f.usuario || '') + '</td>' +
        '<td><strong>' + esc(f.nombre || '') + '</strong><div class="sub"><code class="ruta" title="' +
          esc(f.comando) + '">' + esc(f.comando) + '</code></div></td>' +
        '<td>' + (f.cliente ? esc(f.cliente) : '<span class="tenue">—</span>') + '</td>' +
        '<td class="num">' + esc(f.rss) + '</td>' +
        '<td class="num">' + guion(f.ram_pct === null ? null : f.ram_pct + ' %') + '</td>' +
        '<td class="num">' + esc(f.cpu_pct) + ' %</td></tr>';
    }).join('') || '<tr><td class="vacio" colspan="7">Ningún proceso usó CPU en el último segundo</td></tr>';
    $('#resumen-procesos').textContent = (p.total || 0) + ' procesos en el servidor';
  }

  function medirCarpetas(forzar) {
    $('#cuerpo-carpetas').innerHTML = '<tr><td class="vacio" colspan="3">Midiendo con du…</td></tr>';
    fetch('/api/consumo/carpetas' + (forzar ? '?forzar=1' : ''), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var raiz = (((datosConsumo || {}).sistema || {}).discos || [])
          .filter(function (x) { return x.montaje === '/'; })[0] || {};
        $('#cuerpo-carpetas').innerHTML = (d.carpetas || []).map(function (c) {
          var pct = pctDe(c.bytes || 0, raiz.total_bytes);
          return '<tr><td>' + esc(c.etiqueta) + '</td><td><code class="ruta">' + esc(c.ruta) + '</code></td>' +
            '<td>' + (c.bytes === null ? '<span class="tenue">' + esc(c.error || '—') + '</span>'
                     : (pct === null ? esc(c.tamano)
                        : barra(pct, c.tamano + ' · ' + pct + ' %', 'del disco /', 10, 25))) + '</td></tr>';
        }).join('') || '<tr><td class="vacio" colspan="3">Sin carpetas que medir</td></tr>';
        $('#carpetas-medido').textContent = ' · medido ' + (d.medido || '') + (d.cache ? ' (guardado)' : '');
      })
      .catch(function (e) {
        $('#cuerpo-carpetas').innerHTML = '<tr><td class="vacio" colspan="3">' + esc(e.message) + '</td></tr>';
      });
  }

  function consumoBases() {
    fetch('/api/bases', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok === false) {
          $('#cuerpo-consumo-bases').innerHTML = '<tr><td class="vacio" colspan="4">' +
            esc(d.error || 'No se pudo consultar PostgreSQL') + '</td></tr>';
          return;
        }
        var total = (d.bases || []).reduce(function (a, b) { return a + (b.bytes || 0); }, 0);
        $('#cuerpo-consumo-bases').innerHTML = (d.bases || []).slice(0, 15).map(function (b) {
          var pct = pctDe(b.bytes, total);
          return '<tr><td class="cliente">' + esc(b.nombre) + '</td>' +
            '<td>' + (b.instancia ? esc(b.instancia.cliente)
                     : (b.sistema ? badge('gris', 'del motor') : badge('ambar', 'sin instancia'))) + '</td>' +
            '<td>' + barra(pct, b.tamano + ' · ' + pct + ' %', 'del total de bases', 25, 50) + '</td>' +
            '<td class="num">' + esc(b.conexiones) + '</td></tr>';
        }).join('') || '<tr><td class="vacio" colspan="4">Sin bases</td></tr>';
      })
      .catch(function () {});
  }

  function programarConsumo() {
    if (consumoAuto) { clearInterval(consumoAuto); consumoAuto = null; }
    if ($('#consumo-auto').checked) consumoAuto = setInterval(medirConsumo, 15000);
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
          '<button class="boton mini backup-verificar" type="button" data-archivo="' +
            esc(a.archivo) + '" title="Comprueba que el dump esté completo y se pueda leer">Verificar</button> ' +
          (a.solo_lectura ? '' :
            '<button class="boton mini peligro backup-borrar" type="button" style="color:#fff" ' +
              'data-archivo="' + esc(a.archivo) + '">Eliminar</button>') + '</td></tr>';
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
        '<td style="white-space:nowrap"><button class="boton mini base-detalle" type="button" data-base="' +
          esc(b.nombre) + '">Detalle</button> ' +
          '<button class="boton mini backup-base" type="button" data-base="' +
          esc(b.nombre) + '">Respaldar</button>' +
          (b.ultimo_backup_archivo
            ? ' <a class="boton mini" title="Descargar el último backup" href="/backups/descargar?archivo=' +
              encodeURIComponent(b.ultimo_backup_archivo) + '">⬇ Último</a>'
            : '') + '</td></tr>';
    }).join('') || '<tr><td class="vacio" colspan="6">Sin bases</td></tr>';
    var sv = d.servidor || {};
    $('#resumen-bases').textContent = (d.bases || []).length + ' bases · ' + (sv.total || '') + ' · ' +
      (d.sin_uso || 0) + ' sin instancia (' + (d.sin_uso_tamano || '0 B') + ')' +
      (d.host ? ' · ' + d.host : '') +
      (sv.version ? ' · PostgreSQL ' + sv.version : '') +
      (sv.max_conexiones ? ' · conexiones ' + sv.conexiones + '/' + sv.max_conexiones +
        ' (' + sv.conexiones_pct + ' %)' : '') +
      (sv.activo ? ' · activo hace ' + sv.activo : '');
  }

  function verDetalleBase(nombre) {
    if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
    $('#tarea-titulo').textContent = 'Base ' + nombre;
    $('#modal-tarea').classList.remove('oculto');
    var caja = $('#tarea-cuerpo');
    caja.innerHTML = '<p class="tenue">Consultando PostgreSQL…</p>';
    fetch('/api/bases/' + encodeURIComponent(nombre), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { caja.innerHTML = '<div class="aviso-error">' + esc(d.error || 'Error') + '</div>'; return; }
        var e = d.estadisticas || {};
        var cache = e.cache_pct;
        var inactivas = (d.conexiones || []).filter(function (c) { return c.estado === 'idle'; }).length;
        var largas = (d.conexiones || []).filter(function (c) {
          return c.estado === 'active' && c.segundos > 60; }).length;
        caja.innerHTML =
          '<div class="grid-detalle">' +
            '<div class="bloque"><h3>General</h3>' + dl([
              ['Instancia', d.instancia ? d.instancia.cliente + ' (' + d.instancia.tipo + ')' : 'sin instancia'],
              ['Tamaño', d.tamano], ['Dueño', d.dueno], ['Tablas', d.tablas_total],
              ['Codificación', d.codificacion + ' · ' + (d.collate || '')]
            ]) + '</div>' +
            '<div class="bloque"><h3>Actividad</h3>' + dl([
              ['Conexiones', (d.conexiones || []).length + ' (' + inactivas + ' inactivas' +
                (largas ? ', ' + largas + ' consultas de más de 1 min' : '') + ')'],
              ['Aciertos de caché', cache === null || cache === undefined ? '-' : cache + ' %' +
                (cache < 95 ? ' (bajo: falta RAM para shared_buffers)' : '')],
              ['Commits / rollbacks', (e.commits || 0) + ' / ' + (e.rollbacks || 0)],
              ['Filas insertadas / actualizadas / borradas',
                (e.insertadas || 0) + ' / ' + (e.actualizadas || 0) + ' / ' + (e.borradas || 0)],
              ['Deadlocks', e.deadlocks], ['Archivos temporales', e.temporales],
              ['Estadísticas desde', e.desde || 'inicio del servidor']
            ]) + '</div>' +
          '</div>' +
          '<div style="margin:10px 0">' +
            '<button class="boton mini backup-base" type="button" data-base="' + esc(d.nombre) +
              '">Respaldar ahora</button></div>' +
          '<div class="bloque"><h3>Tablas más pesadas</h3><div class="tabla-envoltura" style="margin:0">' +
            '<table class="tabla-mini"><thead><tr><th>Tabla</th><th class="num">Total</th>' +
            '<th class="num">Datos</th><th class="num">Índices</th><th class="num">Filas</th>' +
            '<th class="num">Muertas</th><th>Último vacuum</th></tr></thead><tbody>' +
            (d.tablas || []).map(function (t) {
              return '<tr><td>' + esc(t.tabla) + '</td><td class="num">' + esc(t.total) + '</td>' +
                '<td class="num">' + esc(t.datos) + '</td><td class="num">' + esc(t.indices) + '</td>' +
                '<td class="num">' + esc(t.filas) + '</td>' +
                '<td class="num">' + (t.muertas_pct > 20 ? badge('ambar', t.muertas_pct + ' %')
                                     : esc(t.muertas_pct + ' %')) + '</td>' +
                '<td>' + guion(t.vacuum) + '</td></tr>';
            }).join('') + '</tbody></table></div></div>' +
          '<div class="bloque" style="margin-top:10px"><h3>Conexiones abiertas</h3>' +
            ((d.conexiones || []).length
              ? '<div class="tabla-envoltura" style="margin:0"><table class="tabla-mini"><thead><tr>' +
                '<th class="num">PID</th><th>Usuario</th><th>Aplicación</th><th>Origen</th><th>Estado</th>' +
                '<th>Duración</th><th>Consulta</th></tr></thead><tbody>' +
                d.conexiones.map(function (c) {
                  return '<tr><td class="num">' + esc(c.pid) + '</td><td>' + esc(c.usuario) + '</td>' +
                    '<td>' + esc(c.aplicacion) + '</td><td>' + esc(c.cliente) + '</td>' +
                    '<td>' + esc(c.estado) + (c.espera ? ' <span class="tenue">(' + esc(c.espera) + ')</span>' : '') + '</td>' +
                    '<td>' + guion(c.duracion) + '</td>' +
                    '<td><code class="ruta" title="' + esc(c.consulta) + '">' + esc(c.consulta) + '</code></td></tr>';
                }).join('') + '</tbody></table></div>'
              : '<p class="tenue">Nadie está conectado a esta base.</p>') +
          '</div>';
      })
      .catch(function (e) { caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>'; });
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

  // El historial se muestra en el mismo modal que las tareas: antes se abría
  // como una caja al final de la página, fuera de la pantalla, y parecía que
  // el botón no hacía nada.
  function verHistorial() {
    fetch('/api/acciones', { credentials: 'same-origin' })
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        var filas = (d.acciones || []).map(function (a) {
          var ok = /rc=0/.test(a.resultado || '');
          return '<tr><td>' + esc(a.fecha) + '</td><td>' + esc(a.usuario || '') + '</td><td>' +
            esc(a.accion) + '</td><td>' + esc(a.objetivo || '') + '</td><td>' +
            badge(ok ? 'verde' : 'rojo', a.resultado || '') + '</td>' +
            '<td class="sub celda-larga">' + esc(a.salida || '') + '</td></tr>';
        }).join('');
        $('#tarea-titulo').textContent = 'Historial de acciones del panel';
        $('#tarea-cuerpo').innerHTML =
          '<p class="tenue">Todo lo que se ha hecho desde el panel: servicios, sitios de ' +
          'Apache, credenciales, certificados y cambios de configuración. ' +
          ((d.acciones || []).length === 1 ? '1 acción registrada'
            : (d.acciones || []).length + ' acciones registradas') +
          ', la más reciente arriba.</p>' +
          '<table class="tabla-mini"><thead><tr><th>Fecha</th><th>Usuario</th><th>Acción</th>' +
          '<th>Objetivo</th><th>Resultado</th><th>Salida</th></tr></thead><tbody>' +
          (filas || '<tr><td colspan="6" class="vacio">Sin acciones registradas</td></tr>') +
          '</tbody></table>';
        $('#modal-tarea').classList.remove('oculto');
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

  // ---------------------------------------------------------- notificaciones
  var notif = { datos: null, ultimaVista: null, poll: null, instalar: null, primera: true };
  var ICONO_NIVEL = { error: '🔴', aviso: '🟠', ok: '🟢', info: '🔵' };

  function hace(fecha) {
    if (!fecha) return '';
    var t = new Date(fecha.replace(' ', 'T'));
    if (isNaN(t)) return fecha;
    var s = Math.round((Date.now() - t.getTime()) / 1000);
    if (s < 60) return 'hace un momento';
    if (s < 3600) return 'hace ' + Math.round(s / 60) + ' min';
    if (s < 86400) return 'hace ' + Math.round(s / 3600) + ' h';
    return fecha.slice(0, 16);
  }

  function itemNotif(n) {
    return '<a class="item-notif nivel-' + esc(n.nivel) + (n.leida ? '' : ' no-leida') + '" href="' +
      esc(n.ruta || '/notificaciones') + '" data-notif="' + esc(n.id) + '">' +
      '<span class="icono-notif">' + (ICONO_NIVEL[n.nivel] || '🔵') + '</span>' +
      '<span class="texto-notif"><strong>' + esc(n.titulo) + '</strong>' +
      '<span class="sub">' + esc(n.mensaje || '') + '</span>' +
      '<span class="fecha-notif" title="' + esc(n.fecha) + '">' + esc(hace(n.fecha)) + '</span></span></a>';
  }

  function cargarNotificaciones() {
    if (!$('#btn-campana')) return Promise.resolve();
    var limite = MODO === 'notificaciones' ? 300 : 20;
    return fetch('/api/notificaciones?limite=' + limite, { credentials: 'same-origin' })
      .then(function (r) { return r.status === 401 ? null : r.json(); })
      .then(function (d) {
        if (!d) return;
        var anterior = notif.datos;
        notif.datos = d;
        var c = $('#campana-contador');
        c.textContent = d.no_leidas > 99 ? '99+' : d.no_leidas;
        c.classList.toggle('oculto', !d.no_leidas);
        document.title = document.title.replace(/^\(\d+\+?\) /, '');
        if (d.no_leidas) document.title = '(' + (d.no_leidas > 99 ? '99+' : d.no_leidas) + ') ' + document.title;
        // Con el panel abierto se avisa de lo nuevo aunque no haya push.
        if (anterior && !notif.primera) {
          var vistos = {};
          (anterior.notificaciones || []).forEach(function (n) { vistos[n.id] = 1; });
          var nuevas = (d.notificaciones || []).filter(function (n) { return !vistos[n.id]; });
          if (nuevas.length) {
            aviso((ICONO_NIVEL[nuevas[0].nivel] || '') + ' ' + nuevas[0].titulo +
                  (nuevas.length > 1 ? ' (y ' + (nuevas.length - 1) + ' más)' : ''),
                  nuevas[0].nivel === 'ok' ? 'aviso-ok' : (nuevas[0].nivel === 'error' ? 'aviso-error' : 'aviso-info'));
          }
        }
        notif.primera = false;
        pintarPanelNotif();
        if (MODO === 'notificaciones') pintarPaginaNotif();
      })
      .catch(function () {});
  }

  function pintarPanelNotif() {
    var d = notif.datos;
    if (!d || !$('#lista-notif')) return;
    $('#lista-notif').innerHTML = (d.notificaciones || []).slice(0, 20).map(itemNotif).join('') ||
      '<p class="tenue" style="padding:14px">Sin notificaciones todavía.</p>';
  }

  function marcarLeidas(ids) {
    return fetch('/api/notificaciones/leidas', {
      method: 'POST', credentials: 'same-origin', keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(ids ? { ids: ids } : {})
    }).then(cargarNotificaciones);
  }

  function alternarPanelNotif(forzarCerrar) {
    var panel = $('#panel-notif');
    if (!panel) return;
    var abrir = !forzarCerrar && panel.classList.contains('oculto');
    panel.classList.toggle('oculto', !abrir);
    if (abrir) { cargarNotificaciones(); estadoPush(); }
  }

  // ------------------------------------------------------------- push
  function pushSoportado() {
    return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
  }

  function claveAplicacion(texto) {
    var b = atob(texto.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((texto.length + 3) % 4));
    var arr = new Uint8Array(b.length);
    for (var i = 0; i < b.length; i++) arr[i] = b.charCodeAt(i);
    return arr;
  }

  function suscripcionActual() {
    if (!pushSoportado()) return Promise.resolve(null);
    return navigator.serviceWorker.getRegistration('/').then(function (reg) {
      return reg ? reg.pushManager.getSubscription() : null;
    });
  }

  function esIOS() { return /iphone|ipad|ipod/i.test(navigator.userAgent); }
  function instalada() {
    return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
      window.navigator.standalone === true;
  }

  function motivoSinPush() {
    if (location.protocol !== 'https:' && location.hostname !== 'localhost') {
      return 'Las notificaciones push y la app instalable necesitan HTTPS. Entra al panel por su ' +
        'dominio con certificado (deploy/admin_dominio.sh) en vez de http://IP:puerto.';
    }
    if (esIOS() && !instalada()) {
      return 'En iPhone primero instala la app: botón Compartir → «Agregar a pantalla de inicio», ' +
        'ábrela desde el ícono y activa las notificaciones ahí (iOS 16.4 o superior).';
    }
    if (!pushSoportado()) return 'Este navegador no admite notificaciones push.';
    return null;
  }

  function activarPush() {
    var motivo = motivoSinPush();
    if (motivo) { aviso(motivo); return Promise.resolve(); }
    return Notification.requestPermission().then(function (permiso) {
      if (permiso !== 'granted') {
        throw new Error('No diste permiso para notificaciones. Actívalo en los ajustes del ' +
          'navegador para este sitio y vuelve a intentar.');
      }
      return Promise.all([
        navigator.serviceWorker.register('/sw.js', { scope: '/' }).then(function () {
          return navigator.serviceWorker.ready;
        }),
        fetch('/api/push/clave', { credentials: 'same-origin' }).then(function (r) { return r.json(); })
      ]);
    }).then(function (res) {
      var reg = res[0], d = res[1];
      if (!d.ok) throw new Error(d.error || 'El servidor no tiene clave VAPID');
      return reg.pushManager.getSubscription().then(function (previa) {
        return previa || reg.pushManager.subscribe({
          userVisibleOnly: true, applicationServerKey: claveAplicacion(d.clave) });
      });
    }).then(function (sus) {
      return fetch('/api/push/suscribir', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ suscripcion: sus.toJSON() })
      }).then(function (r) { return r.json(); });
    }).then(function (d) {
      if (!d.ok) throw new Error(d.error || 'No se pudo registrar el dispositivo');
      aviso('Listo: este dispositivo recibirá las notificaciones. Envía una prueba para comprobarlo.', 'aviso-ok');
      estadoPush();
    }).catch(function (e) { aviso(e.message); });
  }

  function desactivarPush() {
    return suscripcionActual().then(function (sus) {
      if (!sus) return;
      var endpoint = sus.endpoint;
      return sus.unsubscribe().then(function () {
        return fetch('/api/push/desuscribir', {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: endpoint })
        });
      });
    }).then(function () { aviso('Este dispositivo ya no recibirá notificaciones push.', 'aviso-ok'); estadoPush(); });
  }

  function probarCanal(canal, boton) {
    if (boton) boton.disabled = true;
    return fetch('/api/notificaciones/probar', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ canal: canal })
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.ok) {
          aviso('Prueba de ' + canal + ' enviada' + (d.enviados !== undefined ? ' (' + d.enviados +
                ' destino/s)' : '') + '. Revisa que haya llegado.', 'aviso-ok');
        } else {
          aviso('La prueba de ' + canal + ' falló: ' + (d.error || 'error desconocido'));
        }
      })
      .catch(function (e) { aviso(e.message); })
      .then(function () { if (boton) boton.disabled = false; });
  }

  function estadoPush() {
    var rapido = $('#btn-push-rapido');
    var caja = $('#estado-push');
    return suscripcionActual().then(function (sus) {
      var motivo = motivoSinPush();
      if (rapido) rapido.classList.toggle('oculto', !!sus || !!motivo);
      if (!caja) return;
      if (motivo) {
        caja.innerHTML = '<p class="aviso-inline">' + esc(motivo) + '</p>';
        return;
      }
      var permiso = Notification.permission;
      caja.innerHTML = sus
        ? '<p>' + badge('verde', 'activas') + ' Este dispositivo recibe las notificaciones.</p>' +
          '<p><button class="boton mini" type="button" id="btn-push-probar">Enviar prueba</button> ' +
          '<button class="boton mini" type="button" id="btn-push-desactivar">Desactivar aquí</button></p>'
        : '<p>' + (permiso === 'denied'
            ? badge('rojo', 'bloqueadas') + ' El navegador bloqueó las notificaciones de este sitio: ' +
              'actívalas en los ajustes del sitio y recarga.'
            : badge('gris', 'inactivas') + ' Este dispositivo todavía no recibe notificaciones.') + '</p>' +
          (permiso === 'denied' ? '' :
            '<p><button class="boton" type="button" id="btn-push-activar">Activar notificaciones aquí</button></p>');
      caja.innerHTML += '<div id="dispositivos-push" class="sub"></div>';
      fetch('/api/push/dispositivos', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
          var lista = d.dispositivos || [];
          $('#dispositivos-push').innerHTML = lista.length
            ? 'Dispositivos registrados (' + lista.length + '):<br>' + lista.map(function (x) {
                var nombre = /android/i.test(x.agente) ? 'Android' : /iphone|ipad/i.test(x.agente) ? 'iPhone/iPad'
                  : /windows/i.test(x.agente) ? 'Windows' : /mac os/i.test(x.agente) ? 'Mac'
                  : /linux/i.test(x.agente) ? 'Linux' : 'Navegador';
                var nav = /edg\//i.test(x.agente) ? 'Edge' : /chrome|crios/i.test(x.agente) ? 'Chrome'
                  : /firefox|fxios/i.test(x.agente) ? 'Firefox' : /safari/i.test(x.agente) ? 'Safari' : '';
                return '• ' + esc(nombre + (nav ? ' · ' + nav : '')) + ' de ' + esc(x.usuario) +
                  ' <span class="tenue">(' + esc(x.desde) + ')</span>' +
                  (sus && sus.endpoint === x.endpoint ? ' ' + badge('azul', 'este') : '');
              }).join('<br>')
            : 'Ningún dispositivo registrado todavía.';
        }).catch(function () {});
    });
  }

  function estadoInstalar() {
    var caja = $('#estado-instalar');
    if (!caja) return;
    if (instalada()) {
      caja.innerHTML = '<p>' + badge('verde', 'instalada') + ' Estás usando la app instalada.</p>';
    } else if (notif.instalar) {
      caja.innerHTML = '<p>Instálala para abrirla desde el escritorio del celular, a pantalla completa.</p>' +
        '<p><button class="boton" type="button" id="btn-instalar-app">Instalar la app</button></p>';
    } else if (esIOS()) {
      caja.innerHTML = '<p>En iPhone/iPad (Safari): toca <strong>Compartir</strong> → ' +
        '<strong>Agregar a pantalla de inicio</strong>. Luego ábrela desde el ícono.</p>';
    } else if (location.protocol !== 'https:' && location.hostname !== 'localhost') {
      caja.innerHTML = '<p class="aviso-inline">Para instalarla hace falta entrar por HTTPS (dominio con certificado).</p>';
    } else {
      caja.innerHTML = '<p>En Android (Chrome): menú <strong>⋮</strong> → <strong>Instalar aplicación</strong> ' +
        '(o «Agregar a la pantalla principal»). En la computadora, el ícono de instalar en la barra de direcciones.</p>';
    }
  }

  window.addEventListener('beforeinstallprompt', function (e) {
    e.preventDefault();
    notif.instalar = e;
    estadoInstalar();
  });
  window.addEventListener('appinstalled', function () { notif.instalar = null; estadoInstalar(); });

  // ----------------------------------------------------- página de notificaciones
  function pintarPaginaNotif() {
    var d = notif.datos;
    if (!d || !$('#historial-notif')) return;
    var activas = d.activas || [];
    $('#alertas-activas').innerHTML = activas.length
      ? '<div class="lista-notif lista-notif-pagina">' + activas.map(function (a) {
          return itemNotif({ id: a.clave, nivel: a.nivel, titulo: a.titulo, leida: true,
                             mensaje: a.mensaje, ruta: a.ruta, fecha: a.desde });
        }).join('') + '</div>'
      : '<p class="aviso aviso-ok" style="margin:0">' + '🟢 Todo en orden: no hay alertas activas.</p>';
    $('#resumen-revision').textContent = 'Última revisión automática: ' + (d.ultima_revision || 'todavía no') +
      '. Una alerta se envía cuando se confirma en varias revisiones seguidas.';
    var filtro = $('#filtro-notif-nivel').value;
    var lista = (d.notificaciones || []).filter(function (n) {
      if (filtro === 'no-leidas') return !n.leida;
      return !filtro || n.nivel === filtro;
    });
    $('#historial-notif').innerHTML = lista.map(itemNotif).join('') ||
      '<p class="tenue" style="padding:14px">Sin notificaciones.</p>';
  }

  function cargarConfigNotif() {
    var caja = $('#config-notif');
    if (!caja) return;
    fetch('/api/notificaciones/config', { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (c) {
        if (c.ok === false) { caja.innerHTML = '<div class="aviso-error">' + esc(c.error) + '</div>'; return; }
        pintarConfigNotif(c);
      })
      .catch(function (e) { caja.innerHTML = '<div class="aviso-error">' + esc(e.message) + '</div>'; });
  }

  function campo(etiqueta, id, valor, tipo, ayuda, extra) {
    return '<label>' + esc(etiqueta) + '<input id="' + id + '" type="' + (tipo || 'text') + '" value="' +
      esc(valor === null || valor === undefined ? '' : valor) + '"' + (extra || '') + '>' +
      (ayuda ? '<span class="ayuda">' + ayuda + '</span>' : '') + '</label>';
  }
  function casilla(etiqueta, id, marcado) {
    return '<label class="check"><input type="checkbox" id="' + id + '"' + (marcado ? ' checked' : '') + '> ' +
      esc(etiqueta) + '</label>';
  }

  function pintarConfigNotif(c) {
    var tg = c.telegram || {}, co = c.correo || {}, pu = c.push || {}, r = c.reglas || {};
    $('#config-notif').innerHTML =
      '<div class="grid-detalle">' +
      '<div class="bloque"><h3>General</h3><div class="formulario formulario-1">' +
        casilla('Enviar notificaciones', 'n-enabled', c.enabled) +
        campo('Revisar cada (segundos)', 'n-intervalo', c.intervalo, 'number', 'Mínimo 60.', ' min="60"') +
        campo('Confirmar en N revisiones seguidas', 'n-confirmaciones', c.confirmaciones, 'number',
              'Evita avisos por un reinicio de segundos. Con 2 y 300 s: se avisa a los ~5-10 min.', ' min="1"') +
        campo('Recordar alertas activas cada (horas)', 'n-repetir', c.repetir_horas, 'number', '0 = no recordar.', ' min="0"') +
        casilla('Avisar también cuando se resuelve', 'n-recuperacion', c.avisar_recuperacion) +
        campo('Dirección pública del panel', 'n-url', c.url_panel, 'url',
              'Para los enlaces de Telegram y correo. Vacío = la que usas ahora' +
              (c.url_detectada ? ' (' + esc(c.url_detectada) + ')' : '') + '.') +
      '</div></div>' +

      '<div class="bloque"><h3>Qué avisar</h3><div class="formulario formulario-1">' +
        casilla('Servicio de una instancia caído', 'r-servicio', r.servicio_caido) +
        casilla('La web de una instancia no responde', 'r-url', r.url_caida) +
        casilla('Base de datos inaccesible', 'r-base', r.base_caida) +
        casilla('Apache / nginx caído', 'r-web', r.servidor_web_caido) +
        casilla('Sin renovación automática de certificados', 'r-auto', r.renovacion_automatica) +
        casilla('Backups atrasados', 'r-backup', r.backup_atrasado) +
        casilla('Fin de tareas (backups, altas, certbot)', 'r-tareas', r.tareas) +
        campo('Certificado que vence en (días) o menos', 'r-ssl', r.ssl_dias, 'number', '0 = no avisar.') +
        campo('Disco lleno a partir del %', 'r-disco', r.disco_pct, 'number', '0 = no avisar.') +
        campo('RAM a partir del %', 'r-ram', r.ram_pct, 'number', '0 = no avisar.') +
      '</div></div>' +

      '<div class="bloque"><h3>Telegram</h3><div class="formulario formulario-1">' +
        casilla('Enviar por Telegram', 't-enabled', tg.enabled) +
        campo('Token del bot', 't-token', tg.bot_token, 'text',
              'Créalo con <a href="https://t.me/BotFather" target="_blank" rel="noopener">@BotFather</a> → /newbot.',
              ' autocomplete="off"') +
        campo('Chat ID (varios separados por coma)', 't-chats', (tg.chat_ids || []).join(', '), 'text',
              'Escríbele cualquier mensaje a tu bot (o agrégalo a un grupo), guarda el token y pulsa «Detectar».') +
        '<div><button class="boton mini" type="button" id="btn-tg-detectar">Detectar chat</button> ' +
        '<button class="boton mini" type="button" data-probar="telegram">Enviar prueba</button></div>' +
        '<div id="tg-chats"></div>' +
      '</div></div>' +

      '<div class="bloque"><h3>Correo (SMTP)</h3><div class="formulario formulario-1">' +
        casilla('Enviar por correo', 'c-enabled', co.enabled) +
        campo('Servidor SMTP', 'c-servidor', co.servidor, 'text', 'Gmail: smtp.gmail.com · Outlook: smtp.office365.com') +
        '<div class="formulario">' +
          campo('Puerto', 'c-puerto', co.puerto, 'number') +
          '<label>Seguridad<select id="c-seguridad">' + ['starttls', 'ssl', 'ninguna'].map(function (v) {
            return '<option value="' + v + '"' + (co.seguridad === v ? ' selected' : '') + '>' +
              { starttls: 'STARTTLS (587)', ssl: 'SSL/TLS (465)', ninguna: 'Sin cifrado (25)' }[v] + '</option>';
          }).join('') + '</select></label>' +
        '</div>' +
        campo('Usuario', 'c-usuario', co.usuario, 'text', null, ' autocomplete="off"') +
        campo('Contraseña', 'c-clave', co.clave, 'password',
              'En Gmail usa una «contraseña de aplicación» (con verificación en 2 pasos activa).',
              ' autocomplete="new-password"') +
        campo('Remitente', 'c-remitente', co.remitente, 'text', 'Vacío = el usuario.') +
        campo('Destinatarios (separados por coma)', 'c-destinatarios', (co.destinatarios || []).join(', ')) +
        '<div><button class="boton mini" type="button" data-probar="correo">Enviar prueba</button></div>' +
      '</div></div>' +

      '<div class="bloque"><h3>Push (app en el celular)</h3><div class="formulario formulario-1">' +
        casilla('Enviar notificaciones push', 'p-enabled', pu.enabled) +
        campo('Contacto (correo o URL)', 'p-sujeto', pu.sujeto, 'text',
              'Lo exige el estándar para identificar al remitente. Vacío = la dirección del panel.') +
        (pu.disponible ? '' : '<p class="aviso-inline">Falta la librería cryptography en el servidor ' +
          '(pip install -r requirements.txt).</p>') +
        '<p class="sub">Cada celular se activa desde «Este dispositivo», arriba.</p>' +
      '</div></div>' +
      '</div>' +
      '<div style="margin-top:12px;text-align:right"><button class="boton" type="button" id="btn-guardar-notif">' +
        'Guardar configuración</button></div>';
  }

  function valorNum(id) { var v = parseInt($('#' + id).value, 10); return isNaN(v) ? 0 : v; }

  function guardarConfigNotif(boton) {
    var datos = {
      enabled: $('#n-enabled').checked, intervalo: valorNum('n-intervalo'),
      confirmaciones: valorNum('n-confirmaciones'), repetir_horas: valorNum('n-repetir'),
      avisar_recuperacion: $('#n-recuperacion').checked, url_panel: $('#n-url').value.trim(),
      reglas: {
        servicio_caido: $('#r-servicio').checked, url_caida: $('#r-url').checked,
        base_caida: $('#r-base').checked, servidor_web_caido: $('#r-web').checked,
        renovacion_automatica: $('#r-auto').checked, backup_atrasado: $('#r-backup').checked,
        tareas: $('#r-tareas').checked, ssl_dias: valorNum('r-ssl'), disco_pct: valorNum('r-disco'),
        ram_pct: valorNum('r-ram')
      },
      telegram: { enabled: $('#t-enabled').checked, bot_token: $('#t-token').value.trim(),
                  chat_ids: $('#t-chats').value },
      correo: { enabled: $('#c-enabled').checked, servidor: $('#c-servidor').value.trim(),
                puerto: valorNum('c-puerto'), seguridad: $('#c-seguridad').value,
                usuario: $('#c-usuario').value.trim(), clave: $('#c-clave').value,
                remitente: $('#c-remitente').value.trim(), destinatarios: $('#c-destinatarios').value },
      push: { enabled: $('#p-enabled').checked, sujeto: $('#p-sujeto').value.trim() }
    };
    if (boton) boton.disabled = true;
    return fetch('/api/notificaciones/config', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(datos)
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { aviso(d.error || 'No se pudo guardar'); return; }
        aviso('Configuración de notificaciones guardada', 'aviso-ok');
        pintarConfigNotif(d);
      })
      .catch(function (e) { aviso(e.message); })
      .then(function () { if (boton) boton.disabled = false; });
  }

  function detectarChats() {
    var caja = $('#tg-chats');
    caja.innerHTML = '<p class="tenue">Consultando a Telegram…</p>';
    // Primero se guarda (por si se acaba de pegar el token).
    guardarConfigNotif().then(function () {
      return fetch('/api/notificaciones/telegram-chats', { credentials: 'same-origin' });
    }).then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) { caja.innerHTML = '<p class="aviso-inline">' + esc(d.error) + '</p>'; return; }
        if (!(d.chats || []).length) {
          caja.innerHTML = '<p class="aviso-inline">El bot no tiene mensajes: escríbele algo (por ejemplo ' +
            '/start) desde tu Telegram y vuelve a pulsar «Detectar».</p>';
          return;
        }
        caja.innerHTML = '<p class="sub">Toca uno para agregarlo:</p>' + d.chats.map(function (c) {
          return '<button class="boton mini" type="button" data-chat="' + esc(c.id) + '">' +
            esc(c.nombre || c.id) + ' · ' + esc(c.tipo) + ' · ' + esc(c.id) + '</button>';
        }).join(' ');
      })
      .catch(function (e) { caja.innerHTML = '<p class="aviso-inline">' + esc(e.message) + '</p>'; });
  }

  function revisarAhora(boton) {
    if (boton) boton.disabled = true;
    fetch('/api/notificaciones/revisar', { method: 'POST', credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function () {
        aviso('Revisión hecha. Las alertas nuevas se envían cuando se confirman en revisiones seguidas.', 'aviso-ok');
        cargarNotificaciones();
      })
      .catch(function (e) { aviso(e.message); })
      .then(function () { if (boton) boton.disabled = false; });
  }

  // ------------------------------------------------------ menú en el celular
  function prepararCabeceraMovil() {
    var der = document.querySelector('.cabecera-der');
    if (!der || $('#btn-menu-movil')) return;
    var boton = document.createElement('button');
    boton.id = 'btn-menu-movil';
    boton.type = 'button';
    boton.className = 'boton secundario boton-menu-movil';
    boton.setAttribute('aria-label', 'Menú');
    boton.innerHTML = '&#9776;';
    der.appendChild(boton);
  }

  function iniciarNotificaciones() {
    prepararCabeceraMovil();
    if (!$('#btn-campana')) return;
    cargarNotificaciones();
    estadoPush();
    if (notif.poll) clearInterval(notif.poll);
    notif.poll = setInterval(function () {
      if (!document.hidden) cargarNotificaciones();
    }, 60000);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) cargarNotificaciones();
    });
    if (navigator.serviceWorker) {
      navigator.serviceWorker.addEventListener('message', function (e) {
        if (e.data && e.data.tipo === 'notificacion') cargarNotificaciones();
      });
    }
    if (MODO === 'notificaciones') {
      estadoInstalar();
      cargarConfigNotif();
      var tarea = (location.search.match(/[?&]tarea=([^&]+)/) || [])[1];
      if (tarea) verTarea(decodeURIComponent(tarea));
    }
  }

  function iniciar() {
    // Un solo manejador delegado: si algo falla, no se cae el resto del panel.
    document.addEventListener('click', function (e) {
      var el = e.target;
      try {
        // Controles con ícono o texto dentro: se toma el botón o enlace que lo contiene.
        var contenedor = el.closest && el.closest('button, a');
        if (contenedor && contenedor !== el && (contenedor.id || contenedor.hasAttribute('data-probar') ||
            contenedor.hasAttribute('data-chat') || contenedor.hasAttribute('data-notif'))) {
          el = contenedor;
        }
        if (el.id === 'btn-campana') return alternarPanelNotif();
        if (!el.closest('.campana') && $('#panel-notif') && !$('#panel-notif').classList.contains('oculto')) {
          alternarPanelNotif(true);
        }
        if (el.id === 'btn-menu-movil') {
          return document.querySelector('.cabecera').classList.toggle('abierta');
        }
        if (el.hasAttribute && el.hasAttribute('data-notif')) {
          marcarLeidas([el.getAttribute('data-notif')]);
          return;   // el enlace sigue su curso
        }
        if (el.id === 'btn-notif-leidas' || el.id === 'btn-notif-leidas-todas') return marcarLeidas(null);
        if (el.id === 'btn-notif-borrar') {
          if (!window.confirm('¿Vaciar todo el historial de notificaciones?')) return;
          return fetch('/api/notificaciones/borrar', { method: 'POST', credentials: 'same-origin' })
            .then(cargarNotificaciones);
        }
        if (el.id === 'btn-push-rapido' || el.id === 'btn-push-activar') return activarPush();
        if (el.id === 'btn-push-desactivar') return desactivarPush();
        if (el.id === 'btn-push-probar') return probarCanal('push', el);
        if (el.hasAttribute && el.hasAttribute('data-probar')) {
          // Se guarda antes para probar con lo que está escrito en el formulario.
          var canal = el.getAttribute('data-probar');
          return guardarConfigNotif(el).then(function () { return probarCanal(canal, el); });
        }
        if (el.id === 'btn-instalar-app' && notif.instalar) {
          notif.instalar.prompt();
          return notif.instalar.userChoice.then(function () { notif.instalar = null; estadoInstalar(); });
        }
        if (el.id === 'btn-guardar-notif') return guardarConfigNotif(el);
        if (el.id === 'btn-tg-detectar') return detectarChats();
        if (el.hasAttribute && el.hasAttribute('data-chat')) {
          var chats = $('#t-chats').value.split(',').map(function (x) { return x.trim(); }).filter(Boolean);
          if (chats.indexOf(el.getAttribute('data-chat')) === -1) chats.push(el.getAttribute('data-chat'));
          $('#t-chats').value = chats.join(', ');
          aviso('Chat agregado: pulsa «Guardar configuración» o «Enviar prueba».', 'aviso-info');
          return;
        }
        if (el.id === 'btn-revisar-ahora') return revisarAhora(el);
        if (el.id === 'btn-refrescar') return refrescar({}, el);
        if (el.id === 'btn-media') return refrescar({ media: true }, el);
        var sufijo = (MODO === 'excluidos') ? '?ocultas=1' : '';
        if (el.id === 'btn-excel') {
          return descargar('/export.xlsx' + sufijo, 'instancias.xlsx', el, idsVisibles());
        }
        if (el.id === 'btn-csv') {
          return descargar('/export.csv' + sufijo, 'instancias.csv', el, idsVisibles());
        }
        if (el.id === 'btn-historial') return verHistorial();
        if (el.id === 'btn-excluidos') { window.location.href = '/excluidos'; return; }
        if (el.id === 'btn-guardar-excluidos') return guardarListaExcluidos();
        if (el.id === 'btn-nueva') return abrirAsistente();
        if (el.id === 'btn-tareas') return listarTareas();
        if (el.id === 'btn-certificados') return verCertificados();
        if (el.id === 'btn-cert-recargar') return verCertificados('#contenido');
        var thCert = el.closest('th[data-cert]');
        if (thCert) {
          var campo = thCert.getAttribute('data-cert');
          estadoCert.asc = (estadoCert.orden === campo) ? !estadoCert.asc : true;
          estadoCert.orden = campo;
          return pintarFilasCert();
        }
        if (el.id === 'btn-backups-recargar') { verBackups(); return verBases(); }
        if (el.id === 'btn-consumo-recargar') return medirConsumo();
        if (el.id === 'btn-carpetas-medir') return medirCarpetas(true);
        if (el.id === 'btn-backup-todos') return crearBackup(null);
        if (el.classList.contains('backup-crear')) return crearBackup([el.getAttribute('data-id')]);
        if (el.classList.contains('backup-ver')) return verCopias(el.getAttribute('data-cliente'));
        if (el.classList.contains('backup-borrar')) return borrarBackup(el.getAttribute('data-archivo'));
        if (el.classList.contains('backup-verificar')) {
          return lanzarTarea('/api/backups/verificar', { archivo: el.getAttribute('data-archivo') });
        }
        if (el.classList.contains('base-detalle')) return verDetalleBase(el.getAttribute('data-base'));
        if (el.classList.contains('backup-base')) return crearBackupBase(el.getAttribute('data-base'));
        if (el.classList.contains('cert-accion')) {
          return accionCertificado(el.getAttribute('data-nombre'), el.getAttribute('data-accion'));
        }
        if (el.classList.contains('renovar-cert')) {
          return renovarCertificado(el.getAttribute('data-nombre'),
                                    el.getAttribute('data-simular') === '1',
                                    el.getAttribute('data-forzar') === '1');
        }
        if (el.classList.contains('emitir-cert')) {
          return emitirCertificado(el.getAttribute('data-dominio'), el.getAttribute('data-servidor'));
        }
        if (el.classList.contains('servidor-web')) return servidorWeb(el.getAttribute('data-modo'));
        if (el.id === 'nueva-cerrar' || el.id === 'modal-nueva') return $('#modal-nueva').classList.add('oculto');
        if (el.id === 'tarea-cerrar' || el.id === 'modal-tarea') {
          if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
          return $('#modal-tarea').classList.add('oculto');
        }
        if (el.id === 'btn-validar') return validarAlta();
        if (el.id === 'btn-diagnostico') return diagnosticarEntorno();
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
        var alternar = el.closest('.alternar-api');
        if (alternar) {
          return cambiarApiCedula(alternar.getAttribute('data-id'),
                                  alternar.getAttribute('data-activar') === '1', alternar);
        }
        var editable = el.closest('.editar-campo');
        if (editable) {
          return abrirModalCampo(editable.getAttribute('data-id'),
                                 editable.getAttribute('data-campo'),
                                 editable.getAttribute('data-etiqueta'));
        }
        if (el.id === 'campo-guardar') {
          return guardarModalCampo(el.getAttribute('data-id'), el.getAttribute('data-campo'), el);
        }
        if (el.id === 'campo-cancelar' || el.id === 'campo-cerrar' || el.id === 'modal-campo') {
          return $('#modal-campo').classList.add('oculto');
        }
        if (el.classList.contains('guardar-config')) {
          return guardarConfiguracion(el.getAttribute('data-id'),
                                      el.getAttribute('data-campo'), el);
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
          if (tarjeta.getAttribute('data-filtro-api') && $('#filtro-api')) {
            $('#filtro-api').value = tarjeta.getAttribute('data-filtro-api');
          } else if (tarjeta.getAttribute('data-filtro-servicio') && $('#filtro-servicio')) {
            $('#filtro-servicio').value = tarjeta.getAttribute('data-filtro-servicio');
          } else if (tarjeta.getAttribute('data-filtro-uso') && $('#filtro-uso')) {
            $('#filtro-uso').value = tarjeta.getAttribute('data-filtro-uso');
          } else if (tarjeta.getAttribute('data-filtro')) {
            $('#filtro-estado').value = tarjeta.getAttribute('data-filtro');
          }
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
        if (fila && !el.closest('a') && !el.closest('label.interruptor') &&
            !el.closest('.editar-campo') && !el.closest('.alternar-api')) {
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
      if (['filtro-api', 'filtro-uso', 'filtro-servicio',
           'filtro-ocultos'].indexOf(el.id) !== -1) return pintarTabla();
      if (el.id === 'filtro-backup-estado') return pintarBackups();
      if (el.id === 'solo-sin-uso') return pintarBases();
      if (el.id === 'consumo-auto') return programarConsumo();
      if (el.id === 'filtro-notif-nivel') return pintarPaginaNotif();
      if (el.id === 'orden-consumo-inst') return pintarConsumoInstancias();
      if (el.id === 'filtro-servicio-cat') return pintarServicios();
      if (el.id === 'procesos-por') return pintarProcesos();
      if (el.id === 'filtro-cert-estado') { estadoCert.estado = el.value; return pintarFilasCert(); }
      if (el.classList.contains('ver-en-panel')) return alternarVisibilidad(el);
      if (el.hasAttribute && el.hasAttribute('data-grupo')) {
        estado.grupos[el.getAttribute('data-grupo')] = el.checked;
        return pintarTabla();
      }
    });

    document.addEventListener('input', function (e) {
      if (['filtro-texto', 'filtro-tipo', 'filtro-estado', 'filtro-api', 'filtro-servicio',
           'filtro-uso', 'filtro-ocultos'].indexOf(e.target.id) !== -1) {
        pintarTabla();
      }
      if (['filtro-backups', 'filtro-backup-estado'].indexOf(e.target.id) !== -1) pintarBackups();
      if (e.target.id === 'filtro-cert') { estadoCert.filtro = e.target.value; pintarFilasCert(); }
      if (e.target.id === 'filtro-consumo-inst') pintarConsumoInstancias();
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        if (document.querySelector('.cabecera')) document.querySelector('.cabecera').classList.remove('abierta');
        ['#modal', '#modal-campo', '#modal-nueva', '#modal-tarea', '#panel-notif'].forEach(function (sel) {
          if ($(sel)) $(sel).classList.add('oculto');
        });
        if (tareaPoll) { clearInterval(tareaPoll); tareaPoll = null; }
      }
    });

    iniciarNotificaciones();
    if (MODO === 'notificaciones') {
      cargarCapacidades();
      return;
    }
    if (MODO === 'certificados') {
      cargarCapacidades().then(function () { verCertificados('#contenido'); });
      return;
    }
    if (MODO === 'backups') {
      cargarCapacidades().then(function () { verBackups(); verBases(); });
      return;
    }
    if (MODO === 'consumo') {
      cargarCapacidades().then(function () { medirConsumo(); consumoBases(); });
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
