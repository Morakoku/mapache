import io, re, asyncio, ssl, uuid
import asyncpg

env = io.open(r'C:\Users\edwin\Documents\Trinidad\mapache\ops\.env.ops', encoding='utf-8').read()
url = re.search(r'^DATABASE_URL=(.+)$', env, flags=re.M).group(1).strip().replace('postgresql+asyncpg://', 'postgresql://')


async def main():
    ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    c = await asyncpg.connect(url, ssl=ctx, statement_cache_size=0,
                              server_settings={'search_path': 'crm,public,extensions'})

    svc = str(uuid.uuid4())
    try:
        await c.execute(
            "insert into crm.services (id, name, currency) values ($1, 'Prospeccion Veyra', 'COP') on conflict do nothing;",
            svc,
        )
        print('servicio por defecto creado:', svc)
    except Exception as e:
        # si falta alguna columna NOT NULL, avisar
        print('services insert fallo:', str(e)[:160])
        row = await c.fetchrow("select id from crm.services limit 1;")
        svc = str(row['id']) if row else svc

    stage = await c.fetchval("select id from crm.pipeline_stages where is_default limit 1;")
    stage = str(stage)

    sql = f"""
    insert into crm.leads
      (id, owner_id, company_id, service_id, contact_id, stage_id, status,
       score, engagement_score, estimated_value, currency,
       first_contact_at, last_activity_at, next_follow_up_at, lost_reason,
       created_at, updated_at)
    select
      l.id, l.owner_id, l.company_id, '{svc}'::uuid,
      case when exists(select 1 from crm.contacts c where c.id=l.contact_id)
           then l.contact_id else null end,
      coalesce((select s.id from crm.pipeline_stages s where s.id=l.stage_id), '{stage}'::uuid),
      (case when l.status='WON' then 'WON' when l.status='LOST' then 'LOST'
            else 'OPEN' end)::crm.lead_status,
      coalesce(l.score,0), coalesce(l.engagement_score,0), l.estimated_value, 'COP',
      l.first_contact_at, l.last_activity_at, l.next_followup_at, l.lost_reason,
      l.created_at, l.updated_at
    from public.leads l
    where exists(select 1 from crm.companies x where x.id = l.company_id)
    on conflict do nothing;
    """
    try:
        await c.execute(sql)
        print('leads mapeados')
    except Exception as e:
        print('leads insert fallo:', str(e)[:220])

    print('crm.leads:', await c.fetchval('select count(*) from crm.leads;'))
    print('por status:', [dict(r) for r in await c.fetch("select status, count(*) n from crm.leads group by 1 order by 2 desc;")])
    await c.close()

asyncio.run(main())
