# -*- coding: utf-8 -*-
"""Notificaciones push web (lo que llega al celular aunque el panel esté cerrado).

Implementa lo mínimo del estándar sin librerías extra más allá de
`cryptography`:

- VAPID (RFC 8292): el servidor se identifica ante el servicio push del
  navegador (FCM de Google, Mozilla, Apple) con un JWT firmado en ES256.
- Cifrado del mensaje (RFC 8291, «aes128gcm»): sólo el navegador suscrito
  puede leer el contenido.

Las claves VAPID se generan una vez y se guardan en var/vapid.json. Si se
cambian, todas las suscripciones existentes dejan de funcionar.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    CRYPTO_ERROR = None
except Exception as ex:  # pragma: no cover - servidor sin la librería
    CRYPTO_ERROR = str(ex)

_LOCK = threading.Lock()
TAMANO_REGISTRO = 4096


def b64u(datos):
    return base64.urlsafe_b64encode(datos).rstrip(b'=').decode('ascii')


def b64u_decodificar(texto):
    texto = (texto or '').strip()
    return base64.urlsafe_b64decode(texto + '=' * (-len(texto) % 4))


def disponible():
    return CRYPTO_ERROR is None


# ------------------------------------------------------------------ claves
def _ruta_claves(config):
    return os.path.join(config.var_dir, 'vapid.json')


def claves(config):
    """Par de claves VAPID del panel (se crea la primera vez)."""
    if not disponible():
        raise RuntimeError('Falta la librería cryptography: %s' % CRYPTO_ERROR)
    ruta = _ruta_claves(config)
    with _LOCK:
        if os.path.isfile(ruta):
            with open(ruta, 'r', encoding='utf-8') as fh:
                datos = json.load(fh)
            privada = serialization.load_pem_private_key(datos['privada'].encode('ascii'),
                                                         password=None)
            return privada, datos['publica']
        privada = ec.generate_private_key(ec.SECP256R1())
        publica = b64u(privada.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        pem = privada.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode('ascii')
        with open(ruta, 'w', encoding='utf-8') as fh:
            json.dump({'privada': pem, 'publica': publica,
                       'creada': time.strftime('%Y-%m-%d %H:%M:%S')}, fh)
        try:
            os.chmod(ruta, 0o600)
        except OSError:
            pass
        return privada, publica


def clave_publica(config):
    return claves(config)[1]


# ------------------------------------------------------------------- VAPID
def _jwt_vapid(privada, endpoint, sujeto):
    partes = urllib.parse.urlsplit(endpoint)
    audiencia = '%s://%s' % (partes.scheme, partes.netloc)
    cabecera = b64u(json.dumps({'typ': 'JWT', 'alg': 'ES256'}, separators=(',', ':')).encode())
    datos = b64u(json.dumps({'aud': audiencia, 'exp': int(time.time()) + 12 * 3600,
                             'sub': sujeto}, separators=(',', ':')).encode())
    firmado = ('%s.%s' % (cabecera, datos)).encode('ascii')
    r, s = decode_dss_signature(privada.sign(firmado, ec.ECDSA(hashes.SHA256())))
    firma = r.to_bytes(32, 'big') + s.to_bytes(32, 'big')
    return '%s.%s' % (firmado.decode('ascii'), b64u(firma))


# ----------------------------------------------------------------- cifrado
def _hmac(clave, datos):
    return hmac.new(clave, datos, hashlib.sha256).digest()


def cifrar(mensaje, p256dh, auth, sal=None, privada_efimera=None):
    """Cifra el contenido para una suscripción (RFC 8291 + RFC 8188).

    `sal` y `privada_efimera` sólo se pasan en pruebas; normalmente son aleatorias.
    """
    publica_ua = b64u_decodificar(p256dh)
    secreto_auth = b64u_decodificar(auth)
    privada = privada_efimera or ec.generate_private_key(ec.SECP256R1())
    publica_as = privada.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    clave_ua = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), publica_ua)
    compartido = privada.exchange(ec.ECDH(), clave_ua)

    # IKM = HKDF(sal=auth, ikm=ECDH, info="WebPush: info\0" || ua_pub || as_pub)
    prk_clave = _hmac(secreto_auth, compartido)
    ikm = _hmac(prk_clave, b'WebPush: info\x00' + publica_ua + publica_as + b'\x01')[:32]

    sal = sal or os.urandom(16)
    prk = _hmac(sal, ikm)
    cek = _hmac(prk, b'Content-Encoding: aes128gcm\x00\x01')[:16]
    nonce = _hmac(prk, b'Content-Encoding: nonce\x00\x01')[:12]

    # Un solo registro: contenido + delimitador 0x02 de «último registro».
    cifrado = AESGCM(cek).encrypt(nonce, mensaje + b'\x02', None)
    cabecera = sal + struct.pack('!IB', TAMANO_REGISTRO, len(publica_as)) + publica_as
    return cabecera + cifrado


# ------------------------------------------------------------------- envío
def enviar(config, suscripcion, contenido, sujeto='mailto:admin@localhost', ttl=86400,
           urgencia='high', timeout=15):
    """Envía una notificación. Devuelve (ok, código_http, detalle).

    Un 404 o 410 significa que la suscripción ya no existe (se desinstaló la
    app o se revocó el permiso) y hay que borrarla.
    """
    privada, publica = claves(config)
    endpoint = suscripcion.get('endpoint') or ''
    llaves = suscripcion.get('keys') or {}
    if not endpoint.startswith('https://') or not llaves.get('p256dh') or not llaves.get('auth'):
        return False, None, 'suscripción incompleta'
    if isinstance(contenido, (dict, list)):
        contenido = json.dumps(contenido, ensure_ascii=False)
    if isinstance(contenido, str):
        contenido = contenido.encode('utf-8')
    # El límite práctico del servicio push es ~4 KB por mensaje.
    if len(contenido) > 3000:
        contenido = contenido[:3000]
    cuerpo = cifrar(contenido, llaves['p256dh'], llaves['auth'])
    peticion = urllib.request.Request(endpoint, data=cuerpo, method='POST', headers={
        'Content-Type': 'application/octet-stream',
        'Content-Encoding': 'aes128gcm',
        'TTL': str(int(ttl)),
        'Urgency': urgencia,
        'Authorization': 'vapid t=%s, k=%s' % (_jwt_vapid(privada, endpoint, sujeto), publica),
    })
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return True, respuesta.getcode(), 'enviado'
    except urllib.error.HTTPError as ex:
        detalle = ex.read().decode('utf-8', 'replace')[:300]
        return False, ex.code, detalle or str(ex)
    except Exception as ex:
        return False, None, str(ex)
