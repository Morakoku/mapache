#!/usr/bin/env python
"""Extraccion de leads CON datos de contacto (Google Maps via Scrapling).

Corre con el venv de Hermes (tiene scrapling+playwright), desde backend/:

    python scripts/veyra_extract_leads.py --query "odontologia Medellin" --limit 20 --city "Medellin"
    python scripts/veyra_extract_leads.py --query "..." --dry-run

Persiste en Supabase (PostgREST): companies + contacts (telefono) + leads (COLD_OUTREACH).
Dedup por nombre normalizado (companies.dedupe_key). No toca WhatsApp ni correo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.request

from dotenv import load_dotenv

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
load_dotenv(os.path.join(BACKEND, '.env'))

U = (os.environ.get('SUPABASE_URL') or '').rstrip('/')
K = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or '').strip()
H = {'apikey': K, 'Authorization': 'Bearer ' + K, 'Content-Type': 'application/json',
     'Accept-Profile': 'crm', 'Content-Profile': 'crm'}
DEFAULT_STAGE = 'e77d3875-fa3d-46cb-9239-8d71f9399b1e'  # pipeline "Nuevo"
DEFAULT_SERVICE = '8bc3beb7-4b0b-4eb9-b547-14106e4534eb'  # servicio base de prospeccion


def _norm_name(name: str) -> str:
    s = unicodedata.normalize('NFKD', (name or '').strip().lower())
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s)[:40]  # companies.dedupe_key es varchar(40)


def _phone_e164(phone: str) -> str:
    d = ''.join(c for c in str(phone or '') if c.isdigit())
    if not d or len(set(d)) <= 2 or d.endswith('0000000'):
        return ''
    if d.startswith(('57', '58')) and len(d) >= 12:
        return d
    if len(d) == 11 and d.startswith('0'):
        if d[1] in ('4', '2'):
            return '58' + d[1:]
        if d[1] in ('3', '6'):
            return '57' + d[1:]
    if len(d) == 10 and d[0] in ('3', '6'):
        return '57' + d
    if len(d) == 10 and d[0] in ('4', '2'):
        return '58' + d
    return d if 10 <= len(d) <= 15 else ''


def _rest(method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'{U}/rest/v1/{path}', data=data, method=method,
                                 headers={**H, 'Prefer': 'return=representation'})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def _existing_keys() -> set[str]:
    keys, off = set(), 0
    while True:
        rows = _rest('GET', f'companies?select=dedupe_key&limit=1000&offset={off}')
        for r in rows:
            if r.get('dedupe_key'):
                keys.add(r['dedupe_key'])
        if len(rows) < 1000:
            break
        off += 1000
    return keys


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--query', action='append', required=True, help='termino de busqueda (repetible)')
    ap.add_argument('--limit', type=int, default=20)
    ap.add_argument('--city', default=None)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not U or not K:
        sys.exit('Falta SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en backend/.env')

    from app.scrapers.scrapling_scraper import ScraplingMapsScraper
    scraper = ScraplingMapsScraper(headless=True)

    existentes = _existing_keys()
    print(f'empresas existentes (dedupe): {len(existentes)}')

    creados = con_tel = sin_tel = dup = 0
    for query in args.query:
        items = scraper.search_sync(query, limit=args.limit)
        print(f'\n[{query}] {len(items)} resultados')
        for it in items:
            name = (it.get('name') or '').strip()
            dk = _norm_name(name)
            if not name or dk in existentes:
                dup += 1
                continue
            tel = _phone_e164(it.get('phone'))
            web = (it.get('website') or '').strip()
            dominio = re.sub(r'^https?://', '', web).split('/')[0] if web else None
            dq = 70 if tel else 40
            if web:
                dq += 15
            now = 'now()'
            company = {
                'name': name[:200], 'category': (it.get('category') or '')[:120] or None,
                'address': (it.get('address') or '')[:300] or None, 'city': args.city,
                'phone': tel or None, 'phone_raw': (it.get('phone') or '')[:40] or None,
                'website': web or None, 'website_domain': dominio,
                'google_maps_url': it.get('url'), 'rating': None, 'data_quality_score': min(dq, 95),
                'dedupe_key': dk, 'first_extracted_at': now, 'last_extracted_at': now,
            }
            if args.dry_run:
                print(f"  + {name[:40]:40} tel={tel or '-':14} web={dominio or '-'}")
                existentes.add(dk)
                creados += 1
                con_tel += 1 if tel else 0
                sin_tel += 0 if tel else 1
                continue
            try:
                creada = _rest('POST', 'companies', company)[0]
                contact = _rest('POST', 'contacts', {
                    'company_id': creada['id'], 'full_name': name[:160], 'phone': tel or None,
                    'whatsapp': tel or None, 'source': 'IMPORT', 'is_primary': True,
                })[0]
                _rest('POST', 'leads', {
                    'company_id': creada['id'], 'contact_id': contact['id'], 'stage_id': DEFAULT_STAGE,
                    'service_id': DEFAULT_SERVICE, 'status': 'OPEN', 'score': 0, 'engagement_score': 0,
                })
                existentes.add(dk)
                creados += 1
                con_tel += 1 if tel else 0
                sin_tel += 0 if tel else 1
            except Exception as exc:  # noqa: BLE001
                detalle = ''
                if hasattr(exc, 'read'):
                    try:
                        detalle = exc.read().decode('utf-8', 'ignore')[:200]
                    except Exception:  # noqa: BLE001
                        detalle = ''
                print(f'  ERROR {name[:40]}: {str(exc)[:80]} | {detalle}')

    print(f'\nRESUMEN -> creados: {creados} | con telefono: {con_tel} | sin telefono: {sin_tel} | duplicados: {dup}')


if __name__ == '__main__':
    main()
