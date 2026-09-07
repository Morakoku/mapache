"""Llamadas: guion correcto, avisos honestos y registro con consecuencias.

Lo que hay que blindar aquí es lo mismo que en el correo: que el CRM **no
llame a quien pidió que no le llamen** y que lo que se registra sea verdad.
Que sugiera bien el guion es útil; que respete un "no me vuelvas a llamar" es
lo que evita una denuncia.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ActivityType, CallOutcome, CallScriptType, StageType
from app.mail.guardrails import local_now
from app.models.activity import Activity
from app.models.call import DEFAULT_SCRIPTS, CallLog, CallScript
from app.models.sequence import FOLLOWUP_PENDING, FollowUp
from app.services.call_svc import CallService
from app.services.pipeline_svc import PipelineService
from tests.integration.test_email_send import _seed, _seed_settings

API = "/api/v1"


async def _scripts(db: AsyncSession) -> None:
    """Siembra los guiones de fábrica, como hace la migración."""
    db.add_all(
        [
            CallScript(
                name=s["name"],
                script_type=s["script_type"],
                opening=s["opening"],
                context=s.get("context"),
                questions=s.get("questions", []),
                value_pitch=s.get("value_pitch"),
                close=s.get("close"),
                objections=s.get("objections", []),
                is_system=True,
            )
            for s in DEFAULT_SCRIPTS
        ]
    )
    await db.flush()


# ------------------------------------------------------------------ guion


@pytest.mark.asyncio
async def test_primer_contacto_sugiere_guion_en_frio(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead)

    assert brief.suggested_type is CallScriptType.COLD_FIRST
    assert "no te conoce" in brief.suggested_reason
    assert brief.script is not None
    # El guion llega con los datos puestos, no con las llaves dobles.
    assert "{{" not in brief.script.opening
    assert "Camila" in brief.script.opening


@pytest.mark.asyncio
async def test_si_ya_respondio_el_guion_no_es_en_frio(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    lead.replied_at = datetime.now(UTC)
    await db.flush()

    brief = await CallService(db).build_brief(lead)

    assert brief.suggested_type is CallScriptType.INBOUND
    assert "ya respondió" in brief.suggested_reason


@pytest.mark.asyncio
async def test_escrito_sin_respuesta_sugiere_seguimiento(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    lead.first_contact_at = datetime.now(UTC) - timedelta(days=3)
    await db.flush()

    brief = await CallService(db).build_brief(lead)

    assert brief.suggested_type is CallScriptType.FOLLOW_UP


@pytest.mark.asyncio
async def test_se_puede_pedir_otra_situacion(db: AsyncSession) -> None:
    """La sugerencia no manda: quien llama sabe cosas que el CRM no."""
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead, script_type=CallScriptType.GATEKEEPER)

    assert brief.suggested_type is CallScriptType.COLD_FIRST
    assert brief.script is not None
    assert brief.script.script_type is CallScriptType.GATEKEEPER


# ------------------------------------------------------------------ avisos


@pytest.mark.asyncio
async def test_no_contactar_bloquea_la_llamada(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    assert lead.contact is not None
    lead.contact.do_not_contact = True
    await db.flush()

    brief = await CallService(db).build_brief(lead)

    assert brief.can_call is False
    assert brief.blocked_reason is not None
    assert "no contactar" in brief.blocked_reason


@pytest.mark.asyncio
async def test_fuera_de_horario_avisa_pero_no_bloquea(db: AsyncSession) -> None:
    """Llamar a deshora es mala idea, no un delito: se avisa y decide la persona."""
    settings = await _seed_settings(db)
    # Ventana imposible: cualquier hora queda fuera.
    settings.send_window_start = time(3, 0)
    settings.send_window_end = time(3, 1)
    await db.flush()
    await _scripts(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead)

    assert brief.can_call is True
    assert any("fuera de tu horario" in w for w in brief.warnings)


@pytest.mark.asyncio
async def test_sin_telefono_lo_dice(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead)

    assert brief.phone is None
    assert any("No hay teléfono" in w for w in brief.warnings)


@pytest.mark.asyncio
async def test_el_telefono_del_contacto_manda_sobre_el_de_la_empresa(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    lead.company.phone = "+57 604 111 1111"
    assert lead.contact is not None
    lead.contact.phone = "+57 300 222 2222"
    await db.flush()

    brief = await CallService(db).build_brief(lead)

    assert brief.phone == "+57 300 222 2222"
    assert brief.phone_source == "contacto"


# ------------------------------------------------------------------ registro


@pytest.mark.asyncio
async def test_registrar_llamada_crea_actividad_y_cuenta_el_intento(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    service = CallService(db)

    await service.log_call(lead, outcome=CallOutcome.NO_ANSWER)
    segunda = await service.log_call(lead, outcome=CallOutcome.INTERESTED, duration_seconds=180)

    assert segunda.attempt == 2

    actividades = await db.execute(
        select(Activity).where(Activity.activity_type == ActivityType.CALL_LOGGED)
    )
    titulos = [a.title for a in actividades.scalars()]
    assert "Llamada: No contestó" in titulos
    assert "Llamada: Interesado" in titulos


@pytest.mark.asyncio
async def test_que_no_contesten_no_cuenta_como_contacto(db: AsyncSession) -> None:
    """Marcar un número no es hablar con nadie.

    Si un intento fallido moviera `last_contact_at`, el CRM diría que el
    prospecto fue contactado hace un día cuando nadie ha hablado con él.
    """
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    await CallService(db).log_call(lead, outcome=CallOutcome.NO_ANSWER)

    assert lead.last_contact_at is None
    assert lead.first_contact_at is None


@pytest.mark.asyncio
async def test_hablar_si_cuenta_como_contacto(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    await CallService(db).log_call(lead, outcome=CallOutcome.CALLBACK)

    assert lead.last_contact_at is not None
    assert lead.first_contact_at is not None


@pytest.mark.asyncio
async def test_pedir_no_llamar_marca_el_contacto_sin_preguntar(db: AsyncSession) -> None:
    """Lo único que se aplica solo: es una petición expresa del prospecto."""
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    await CallService(db).log_call(lead, outcome=CallOutcome.DO_NOT_CALL)

    assert lead.contact is not None
    assert lead.contact.do_not_contact is True

    brief = await CallService(db).build_brief(lead)
    assert brief.can_call is False


@pytest.mark.asyncio
async def test_registrar_no_mueve_de_etapa_por_su_cuenta(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    etapa_inicial = lead.stage_id

    await CallService(db).log_call(lead, outcome=CallOutcome.INTERESTED)

    assert lead.stage_id == etapa_inicial


@pytest.mark.asyncio
async def test_mover_de_etapa_cuando_se_pide(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    movido = await CallService(db).apply_suggested_stage(lead, CallOutcome.MEETING_SCHEDULED)
    await db.flush()

    assert movido is True
    stage = await PipelineService(db).get_by_type(StageType.MEETING)
    assert stage is not None
    assert lead.stage_id == stage.id


# ------------------------------------------------------------------ API


@pytest.mark.asyncio
async def test_api_brief_y_registro(client: AsyncClient, db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    await db.commit()

    response = await client.get(f"{API}/calls/brief/{lead.id}")
    assert response.status_code == 200
    brief = response.json()
    assert brief["suggested_type"] == "COLD_FIRST"
    assert brief["script"]["opening"]
    assert brief["can_call"] is True

    cuando = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    response = await client.post(
        f"{API}/calls/{lead.id}",
        json={
            "outcome": "CALLBACK",
            "script_id": brief["script"]["script_id"],
            "duration_seconds": 95,
            "notes": "Pidió que le llame el jueves.",
            "follow_up_at": cuando,
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["call"]["outcome_label"] == "Pidió que le llame después"
    assert body["call"]["attempt"] == 1
    assert body["follow_up_created"] is True
    assert body["stage_moved"] is False

    pendientes = await db.execute(
        select(FollowUp).where(FollowUp.lead_id == lead.id, FollowUp.status == FOLLOWUP_PENDING)
    )
    assert len(list(pendientes.scalars())) == 1


@pytest.mark.asyncio
async def test_el_recordatorio_de_llamar_cae_en_horario(
    client: AsyncClient, db: AsyncSession
) -> None:
    """ "Recuérdame en dos días" no puede quedar a las tres de la mañana."""
    settings = await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    await db.commit()

    # Una hora imposible: 04:00 UTC son las 23:00 en Colombia.
    madrugada = datetime.now(UTC).replace(hour=4, minute=0) + timedelta(days=2)
    response = await client.post(
        f"{API}/calls/{lead.id}",
        json={"outcome": "CALLBACK", "follow_up_at": madrugada.isoformat()},
    )
    assert response.status_code == 201
    assert response.json()["follow_up_created"] is True

    pendiente = (
        (await db.execute(select(FollowUp).where(FollowUp.lead_id == lead.id))).scalars().one()
    )

    local = local_now(settings, pendiente.scheduled_at)
    assert settings.send_window_start <= local.time() <= settings.send_window_end
    # Y nunca se adelanta respecto a lo que pidió la persona.
    assert pendiente.scheduled_at >= madrugada


@pytest.mark.asyncio
async def test_el_worker_no_se_come_el_recordatorio_de_llamar(db: AsyncSession) -> None:
    """Un recordatorio para una persona no es un envío automático.

    Sin esta exclusión el worker lo marcaba «saltado: el paso no tiene
    plantilla» y el "vuelve a llamarle el jueves" desaparecía de la agenda sin
    que nadie lo hubiera atendido.
    """
    from app.workers.followup_worker import _due

    await _seed_settings(db)
    lead = await _seed(db)

    recordatorio = FollowUp(
        lead_id=lead.id,
        scheduled_at=datetime.now(UTC) - timedelta(hours=1),
        note="Volver a llamar.",
        is_manual=True,
    )
    db.add(recordatorio)
    await db.flush()

    vencidos = await _due(db, 100)

    assert recordatorio.id not in [f.id for f in vencidos]
    assert recordatorio.status == FOLLOWUP_PENDING


@pytest.mark.asyncio
async def test_api_historial_de_llamadas(client: AsyncClient, db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    await CallService(db).log_call(lead, outcome=CallOutcome.VOICEMAIL)
    await db.commit()

    response = await client.get(f"{API}/calls", params={"lead_id": str(lead.id)})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["outcome_label"] == "Buzón de voz"
    assert body["items"][0]["company_name"] == "Restaurante La Finca"


@pytest.mark.asyncio
async def test_api_guion_con_variable_inventada_se_rechaza(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Una variable mal escrita se leería en voz alta delante del prospecto."""
    await _seed_settings(db)
    await db.commit()

    response = await client.post(
        f"{API}/call-scripts",
        json={
            "name": "Guion con error",
            "script_type": "COLD_FIRST",
            "opening": "Hola, llamo de {{nombre_empresa}}",
        },
    )
    # 400 y no 422: no es un fallo de forma, es una regla de negocio.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "UNKNOWN_SCRIPT_VARIABLE"


@pytest.mark.asyncio
async def test_api_no_deja_borrar_un_guion_de_fabrica(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_settings(db)
    await _scripts(db)
    await db.commit()

    scripts = await db.execute(select(CallScript).where(CallScript.is_system.is_(True)).limit(1))
    script = scripts.scalars().one()

    response = await client.delete(f"{API}/call-scripts/{script.id}?confirm=true")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CALL_SCRIPT_IS_SYSTEM"


@pytest.mark.asyncio
async def test_api_guiones_por_defecto_cubren_las_cinco_situaciones(
    client: AsyncClient, db: AsyncSession
) -> None:
    await _seed_settings(db)
    await _scripts(db)
    await db.commit()

    response = await client.get(f"{API}/call-scripts")
    assert response.status_code == 200
    tipos = {s["script_type"] for s in response.json()}
    assert tipos == {t.value for t in CallScriptType}


@pytest.mark.asyncio
async def test_los_guiones_de_fabrica_dicen_de_donde_sale_el_numero(db: AsyncSession) -> None:
    """El guion en frío tiene que poder responder "¿de dónde sacaste mi número?".

    No es un detalle de cortesía: es la pregunta que separa una llamada
    legítima de una que parece una estafa.
    """
    frio = next(s for s in DEFAULT_SCRIPTS if s["script_type"] is CallScriptType.COLD_FIRST)
    objeciones = {o["objection"] for o in frio["objections"]}
    assert any("número" in o for o in objeciones)

    respuesta = next(o["response"] for o in frio["objections"] if "número" in o["objection"])
    assert "no te vuelva a llamar" in respuesta

    # Y la apertura avisa de que la llamada no se esperaba.
    assert "en frío" in frio["opening"]


@pytest.mark.asyncio
async def test_muchos_intentos_sin_resultado_avisan(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    service = CallService(db)

    for _ in range(5):
        await service.log_call(lead, outcome=CallOutcome.NO_ANSWER)

    brief = await service.build_brief(lead)

    assert brief.previous_calls == 5
    assert any("Insistir más" in w for w in brief.warnings)


@pytest.mark.asyncio
async def test_registrar_llamada_suma_uso_del_guion(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead)
    assert brief.script is not None
    script_id = brief.script.script_id

    await CallService(db).log_call(lead, outcome=CallOutcome.NO_ANSWER, script_id=script_id)

    script = await db.get(CallScript, script_id)
    assert script is not None
    assert script.times_used == 1


@pytest.mark.asyncio
async def test_llamadas_quedan_en_el_timeline_del_prospecto(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Seis semanas después, la ficha tiene que contar qué se habló."""
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    await CallService(db).log_call(
        lead, outcome=CallOutcome.INTERESTED, notes="Quiere ver un ejemplo antes de decidir."
    )
    await db.commit()

    response = await client.get(f"{API}/leads/{lead.id}/timeline")
    assert response.status_code == 200
    titulos = [a["title"] for a in response.json()["items"]]
    assert "Llamada: Interesado" in titulos


@pytest.mark.asyncio
async def test_una_frase_sin_dato_no_se_dice(db: AsyncSession) -> None:
    """Nunca se lee "me llamó la atención que ." en voz alta.

    Una empresa recién scrapeada no tiene señales detectadas. En un correo el
    hueco casi no se nota; dicho por teléfono delata la plantilla en el primer
    segundo. La frase entera se cae y el resto del guion sigue.
    """
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead, script_type=CallScriptType.COLD_FIRST)

    assert brief.script is not None
    # El dato que falta se reporta...
    assert "signal_summary" in brief.script.missing
    # ...y la frase que dependía de él no aparece.
    assert "me llamó la atención" not in (brief.script.context or "")
    assert " ." not in (brief.script.context or "")
    # Pero la apertura, que no dependía de nada que falte, sigue entera.
    assert "Camila" in brief.script.opening
    assert "treinta segundos" in brief.script.opening


@pytest.mark.asyncio
async def test_las_preguntas_conservan_el_signo_de_apertura(db: AsyncSession) -> None:
    """El guion se escribe en español, no en inglés.

    Al trocear el texto en frases hay que cortar por el final —«.», «!», «?»—
    y nunca por «¿» o «¡», que abren. Cortar por ellos se comía el signo.
    """
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead, script_type=CallScriptType.INBOUND)

    assert brief.script is not None
    assert "¿Tienes un minuto?" in brief.script.opening
    assert all(q.startswith("¿") for q in brief.script.questions)


@pytest.mark.asyncio
async def test_con_senal_detectada_la_frase_si_se_dice(db: AsyncSession) -> None:
    from app.core.enums import SourceType
    from app.models.company import CompanySignal

    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    db.add(
        CompanySignal(
            company_id=lead.company_id,
            signal_key="no_website",
            source=SourceType.WEBSITE,
        )
    )
    await db.flush()

    brief = await CallService(db).build_brief(lead, script_type=CallScriptType.COLD_FIRST)

    assert brief.script is not None
    assert "signal_summary" not in brief.script.missing
    assert "me llamó la atención" in (brief.script.context or "")


@pytest.mark.asyncio
async def test_lead_inexistente_da_404(client: AsyncClient, db: AsyncSession) -> None:
    await _seed_settings(db)
    await db.commit()
    response = await client.get(f"{API}/calls/brief/{'0' * 8}-0000-0000-0000-{'0' * 12}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_sin_guiones_el_brief_lo_dice_en_vez_de_fallar(db: AsyncSession) -> None:
    """Un CRM recién instalado sin guiones no puede tirar la pantalla abajo."""
    await _seed_settings(db)
    lead = await _seed(db)

    brief = await CallService(db).build_brief(lead)

    assert brief.script is None
    assert brief.can_call is True
    assert any("No hay ningún guion" in w for w in brief.warnings)


@pytest.mark.asyncio
async def test_los_intentos_fallidos_tambien_se_guardan(db: AsyncSession) -> None:
    """Sin ellos no se puede saber cuántas llamadas cuesta una conversación."""
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    service = CallService(db)

    await service.log_call(lead, outcome=CallOutcome.NO_ANSWER)
    await service.log_call(lead, outcome=CallOutcome.VOICEMAIL)
    await service.log_call(lead, outcome=CallOutcome.INTERESTED)

    registros = await db.execute(select(CallLog).where(CallLog.lead_id == lead.id))
    outcomes = [c.outcome for c in registros.scalars()]
    assert len(outcomes) == 3
    assert CallOutcome.NO_ANSWER in outcomes


@pytest.mark.asyncio
async def test_guion_de_servicio_gana_al_generico(db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)

    propio = CallScript(
        name="Frío para páginas web",
        script_type=CallScriptType.COLD_FIRST,
        service_id=lead.service_id,
        opening="Hola {{first_name}}, llamo por el sitio de {{company_name}}.",
        is_system=False,
    )
    db.add(propio)
    await db.flush()

    brief = await CallService(db).build_brief(lead)

    assert brief.script is not None
    assert brief.script.script_id == propio.id


@pytest.mark.asyncio
async def test_lead_sin_ciudad_avisa_del_hueco(db: AsyncSession) -> None:
    """El guion en frío menciona la ciudad; sin ella la frase queda coja."""
    await _seed_settings(db)
    await _scripts(db)
    lead = await _seed(db)
    lead.company.city = None
    await db.flush()

    brief = await CallService(db).build_brief(lead)

    assert brief.script is not None
    assert "city" in brief.script.missing
    assert any("quedan" in w or "cojas" in w for w in brief.warnings)


@pytest.mark.asyncio
async def test_actualizar_guion_valida_las_variables(client: AsyncClient, db: AsyncSession) -> None:
    await _seed_settings(db)
    await _scripts(db)
    await db.commit()

    scripts = await db.execute(select(CallScript).limit(1))
    script = scripts.scalars().one()

    response = await client.patch(
        f"{API}/call-scripts/{script.id}",
        json={"close": "Te llamo el {{dia_de_la_semana}}."},
    )
    assert response.status_code == 400

    response = await client.patch(
        f"{API}/call-scripts/{script.id}",
        json={"close": "¿Te llamo mañana, {{first_name}}?"},
    )
    assert response.status_code == 200
    assert "first_name" in response.json()["close"]
