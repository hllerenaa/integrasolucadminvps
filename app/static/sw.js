/* Service worker del panel: app instalable, pantalla sin conexión y notificaciones push. */
'use strict';

var CACHE = 'panel-vps-v4';
var ESTATICOS = [
  '/static/app.css',
  '/static/app.js',
  '/static/iconos/icono-192.png',
  '/static/iconos/icono-72.png'
];

var SIN_CONEXION =
  '<!doctype html><html lang="es"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width,initial-scale=1">' +
  '<title>Sin conexión</title><style>body{margin:0;min-height:100vh;display:flex;align-items:center;' +
  'justify-content:center;background:#12325f;color:#fff;font:15px -apple-system,Segoe UI,Roboto,sans-serif;' +
  'text-align:center;padding:24px}button{margin-top:16px;padding:10px 18px;border:0;border-radius:8px;' +
  'background:#1f6feb;color:#fff;font:inherit}</style></head><body><div>' +
  '<h1 style="font-size:20px">Sin conexión con el servidor</h1>' +
  '<p>No se pudo llegar al panel. Revisa tu conexión a internet o si el servidor está caído.</p>' +
  '<button onclick="location.reload()">Reintentar</button></div></body></html>';

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) { return c.addAll(ESTATICOS); })
    .catch(function () {}));
  self.skipWaiting();
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (claves) {
    return Promise.all(claves.filter(function (k) { return k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Páginas: siempre del servidor (datos en vivo); sin red, aviso de desconexión.
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(function () {
      return new Response(SIN_CONEXION, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
    }));
    return;
  }
  // Estáticos: primero la red (para no quedarse con versiones viejas), caché si falla.
  if (url.pathname.indexOf('/static/') === 0) {
    e.respondWith(fetch(req).then(function (r) {
      if (r.ok) {
        var copia = r.clone();
        caches.open(CACHE).then(function (c) { c.put(req, copia); });
      }
      return r;
    }).catch(function () {
      return caches.match(req, { ignoreSearch: true });
    }));
  }
  // /api/ y el resto: sin tocar.
});

self.addEventListener('push', function (e) {
  var datos = {};
  try { datos = e.data ? e.data.json() : {}; } catch (ex) { datos = { body: e.data && e.data.text() }; }
  var titulo = datos.title || 'Panel VPS';
  var opciones = {
    body: datos.body || '',
    icon: '/static/iconos/icono-192.png',
    badge: '/static/iconos/icono-72.png',
    tag: datos.tag || 'panel',
    renotify: true,
    requireInteraction: datos.nivel === 'error',
    vibrate: datos.nivel === 'error' ? [200, 100, 200, 100, 400] : [150],
    data: { url: datos.url || '/notificaciones' },
    timestamp: Date.now()
  };
  e.waitUntil(self.registration.showNotification(titulo, opciones).then(function () {
    // Aviso a las pestañas abiertas para que actualicen la campana al instante.
    return self.clients.matchAll({ type: 'window' }).then(function (lista) {
      lista.forEach(function (c) { c.postMessage({ tipo: 'notificacion' }); });
    });
  }));
});

self.addEventListener('notificationclick', function (e) {
  e.notification.close();
  var destino = new URL((e.notification.data || {}).url || '/', self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(function (lista) {
    for (var i = 0; i < lista.length; i++) {
      if (lista[i].url.indexOf(self.location.origin) === 0 && 'focus' in lista[i]) {
        lista[i].navigate(destino);
        return lista[i].focus();
      }
    }
    return self.clients.openWindow(destino);
  }));
});

// El navegador puede renovar la suscripción por su cuenta: se vuelve a registrar.
self.addEventListener('pushsubscriptionchange', function (e) {
  e.waitUntil(fetch('/api/push/clave', { credentials: 'same-origin' })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      var clave = Uint8Array.from(atob(d.clave.replace(/-/g, '+').replace(/_/g, '/') +
        '==='.slice((d.clave.length + 3) % 4)), function (c) { return c.charCodeAt(0); });
      return self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: clave });
    })
    .then(function (sus) {
      return fetch('/api/push/suscribir', {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ suscripcion: sus.toJSON() })
      });
    }).catch(function () {}));
});
