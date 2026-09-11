#!/usr/bin/env python
"""
Puente Chequeo Express -> bot oficial de WhatsApp de Veyra.

Qué hace:
  1. Lee de Supabase las solicitudes del Chequeo Express que aún NO se han
     confirmado por WhatsApp (whatsapp_sent_at IS NULL).
  2. Envía al cliente el mensaje de primer contacto del flujo, ya relleno con
     SU diagnóstico (perfil, nivel y fuga anualizada) y el enlace a su informe.
  3. Marca whatsapp_sent_at para no repetir.

El envío usa el bot oficial (Hermes `send`), el mismo número de Veyra.

Uso:
  python veyra_chequeo_bridge.py            # envía pendientes
  python veyra_chequeo_bridge.py --dry-run  # muestra lo que enviaría, sin enviar
  python veyra_chequeo_bridge.py --limit 5
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from dotenv import dotenv_values

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
ENV_PATH = os.path.join(BACKEND, '.env')
HERMES = r'C:\Users\edwin\AppData\Local\hermes\bin\hermes.exe'
SITE = 'https://veyrasoluciones.com'


def load_env():
    env = dotenv_values(ENV_PATH)
    url = (env.get('SUPABASE_URL') or '').strip().rstrip('/')
    key = (env.get('SUPABASE_SERVICE_ROLE_KEY') or '').strip()
    if not url or not key:
        sys.exit('Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en backend/.env')
    return url, key


def sb_get(url, key, path):
    req = urllib.request.Request(
        f'{url}/rest/v1/{path}',
        headers={'apikey': key, 'Authorization': f'Bearer {key}'},
    )
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def sb_patch(url, key, path, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        f'{url}/rest/v1/{path}',
        data=data,
        method='PATCH',
        headers={
            'apikey': key,
            'Authorization': f'Bearer {key}',
            'Content-Type': 'application/json',
            'Prefer': 'return=minimal',
        },
    )
    return urllib.request.urlopen(req, timeout=25).status


def first_name(name):
    return (str(name or '').strip().split() or [''])[0] or 'hola'


def build_message(row):
    payload = row.get('payload') or {}
    token = row.get('token') or ''
    name = first_name(row.get('name'))
    status = (row.get('status') or '').lower()

    # Diagnóstico empresarial (7 dimensiones).
    diagnostico = payload.get('diagnostico') or {}
    if status == 'diagnostico' and diagnostico:
        analisis = diagnostico.get('analysis') or {}
        frentes = [o.get('title') for o in (analisis.get('oportunidades') or []) if o.get('title')]
        lines = [
            f'Hola {name}, gracias por hacer el diagnóstico empresarial de Veyra.',
            '',
            'Esto encontramos:',
            f'· Madurez de tu operación: {analisis.get("madurez", "")}/100 ({analisis.get("nivel", "")})',
        ]
        if frentes:
            lines.append('· Frentes de mejora: ' + ', '.join(frentes[:4]))
        lines += [
            '',
            'Te lo mandamos por correo con el detalle. ¿Tienes 15 minutos esta semana para revisarlo juntos por videollamada?',
        ]
        return '\n'.join([l for l in lines if l is not None])

    # Solicitud de Business MRI (formulario de admisión).
    if status in ('pending_review', 'intake', 'mri'):
        company = (row.get('company_name') or '').strip()
        sufijo = f' para {company}' if company else ''
        return '\n'.join([
            f'Hola {name}, recibimos tu solicitud de Business MRI{sufijo}.',
            '',
            'Un arquitecto de Veyra ya la está revisando. ¿Tienes 15 minutos esta semana para verlo juntos por videollamada?',
        ])

    # Scorecard de madurez.
    if status == 'scorecard':
        sc = payload.get('scorecard') or {}
        overall = sc.get('overall', '')
        tier = sc.get('tier', '')
        lines = [f'Hola {name}, gracias por completar el Scorecard de Veyra.']
        if overall != '':
            lines.append(f'· Tu nivel: {overall}/100 ({tier})')
        lines += ['', '¿Tienes 15 minutos esta semana para revisarlo juntos por videollamada?']
        return '\n'.join(lines)

    # Chequeo Express (6 preguntas).
    chequeo = payload.get('chequeo') or {}
    diag = chequeo.get('diagnosis') or {}
    link = f'{SITE}/chequeo-reporte?token={token}' if token else ''
    perfil = diag.get('profileName') or 'tu cuello de botella'
    score = diag.get('overall', '')
    fuga = diag.get('anualizado') or ''
    lines = [
        f'Hola {name}, gracias por hacer el Chequeo Express de Veyra.',
        '',
        'Lo revisamos y tu diagnóstico quedó claro:',
        f'· Perfil: {perfil}',
        f'· Nivel de sistema: {score}/100',
    ]
    if fuga:
        lines.append(f'· Fuga estimada: {fuga}')
    lines += [
        '',
        'Aquí tienes tu informe con el detalle y el primer movimiento para esta semana:',
        link,
        '',
        '¿Tienes 15 minutos esta semana para revisarlo juntos por videollamada? Te muestro el recorrido de tus clientes y dónde se está fugando la plata.',
    ]
    return '\n'.join([l for l in lines if l is not None])


# Indicativos soportados: 57 (Colombia), 58 (Venezuela).
def valid_phone(phone):
    """Normaliza el numero a formato internacional sin '+' (multi-pais).

    Acepta numeros que ya traen indicativo (57/58) y los locales de Colombia
    (movil empieza por 3, fijo por 60) y Venezuela (empieza por 4 o 2, con 0).
    """
    digits = ''.join(ch for ch in str(phone or '') if ch.isdigit())
    if not digits:
        return ''
    # Rechaza marcadores de posicion / numeros falsos (ej. el default del form
    # de Business MRI: +57 300 000 0000).
    if len(set(digits)) <= 2:
        return ''
    if digits.endswith('0000000'):
        return ''
    if digits.startswith(('57', '58')) and len(digits) >= 12:
        return digits
    if len(digits) == 11 and digits.startswith('0'):
        if digits[1] in ('4', '2'):
            return '58' + digits[1:]      # Venezuela (0414..., 0212...)
        if digits[1] in ('3', '6'):
            return '57' + digits[1:]      # Colombia (03xx..., 06xx...)
    if len(digits) == 10:
        if digits[0] in ('3', '6'):
            return '57' + digits          # Colombia
        if digits[0] in ('4', '2'):
            return '58' + digits          # Venezuela
    return digits if 10 <= len(digits) <= 15 else ''


def send_whatsapp(number, message, dry_run=False):
    if dry_run:
        print(f'   [dry-run] -> whatsapp:{number}')
        print('   ' + message.replace('\n', '\n   '))
        return True
    try:
        proc = subprocess.run(
            [HERMES, 'send', '--to', f'whatsapp:{number}', message],
            capture_output=True, text=True, timeout=90,
        )
        ok = proc.returncode == 0
        if not ok:
            print(f'   error ({proc.returncode}): {(proc.stderr or proc.stdout or "").strip()[:200]}')
        return ok
    except Exception as exc:  # noqa: BLE001
        print(f'   excepción: {exc}')
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--limit', type=int, default=20)
    args = ap.parse_args()

    url, key = load_env()
    try:
        rows = sb_get(
            url, key,
            'veyra_intakes?status=in.(chequeo,diagnostico,pending_review,scorecard)&whatsapp_sent_at=is.null'
            f'&select=id,intake_id,name,phone,status,token,payload,created_at,company_name'
            f'&order=created_at.asc&limit={args.limit}',
        )
    except urllib.error.HTTPError as exc:
        sys.exit(f'No se pudo leer Supabase: {exc.code} {exc.read().decode()[:160]}')

    if not rows:
        print('Sin chequeos pendientes de confirmar por WhatsApp.')
        return

    print(f'{len(rows)} chequeo(s) pendiente(s).')
    sent = 0
    for row in rows:
        number = valid_phone(row.get('phone'))
        ref = row.get('intake_id') or row.get('id')
        if not number:
            print(f'· {ref}: teléfono inválido ({row.get("phone")!r}) — se omite')
            continue
        message = build_message(row)
        print(f'· {ref} -> {number} ({first_name(row.get("name"))})')
        if send_whatsapp(number, message, dry_run=args.dry_run):
            if not args.dry_run:
                stamp = datetime.now(timezone.utc).isoformat()
                try:
                    sb_patch(url, key, f"veyra_intakes?id=eq.{row['id']}", {'whatsapp_sent_at': stamp})
                    print('   enviado y marcado.')
                except urllib.error.HTTPError as exc:
                    print(f'   enviado, pero no se pudo marcar: {exc.code}')
            sent += 1

    print(f'Listo: {sent} enviado(s).')


if __name__ == '__main__':
    main()
