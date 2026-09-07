"""Templates de email renderizados con la estetica de Veyra Soluciones."""

from __future__ import annotations

from typing import Any

VEYRA_BASE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1.0'>
    <style>
        body {{ margin: 0; padding: 0; background-color: #f4f4f4; font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; }}
        .header {{ background-color: #1a1a1a; padding: 20px; text-align: center; }}
        .header h1 {{ margin: 0; color: #ffffff; font-size: 24px; font-weight: bold; letter-spacing: 1px; }}
        .header .tagline {{ color: #ff6b35; font-size: 11px; margin-top: 5px; text-transform: uppercase; letter-spacing: 2px; }}
        .separator {{ height: 4px; background-color: #ff6b35; }}
        .body-content {{ padding: 30px 30px 20px; }}
        .section-label {{ color: #ff6b35; font-size: 11px; font-weight: bold; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 15px; }}
        .greeting {{ font-size: 22px; color: #1a1a1a; font-weight: bold; margin-bottom: 20px; }}
        .question-box {{ background-color: #f9f9f9; border-left: 4px solid #ff6b35; padding: 20px 25px; margin: 25px 0; }}
        .question-text {{ font-size: 18px; color: #1a1a1a; font-weight: bold; line-height: 1.5; }}
        .cta-text {{ color: #555555; font-size: 14px; line-height: 1.6; margin-top: 20px; }}
        .btn {{ padding: 12px 24px; text-decoration: none; font-size: 13px; font-weight: bold; text-align: center; border-radius: 4px; display: inline-block; }}
        .btn-black {{ background-color: #1a1a1a; color: #ffffff; }}
        .btn-orange {{ background-color: #ff6b35; color: #ffffff; }}
        .footer {{ background-color: #f4f4f4; padding: 20px 30px; text-align: center; font-size: 12px; color: #888888; }}
        .footer a {{ color: #ff6b35; text-decoration: none; }}
        .footer .unsubscribe {{ margin-top: 10px; }}
    </style>
</head>
<body>
    <div class='container'>
        <div class='header'>
            <h1>VEYRA SOLUCIONES</h1>
            <div class='tagline'>BUSINESS MRI™ - CLARIDAD PARA DECIDIR</div>
        </div>
        <div class='separator'></div>
        <div class='body-content'>
            {body_content}
        </div>
        <div class='footer'>
            <p><strong>{sender_name}</strong><br>
            Veyra Soluciones<br>
            <a href='mailto:{sender_email}'>{sender_email}</a> ·
            <a href='https://veyrasoluciones.com'>veyrasoluciones.com</a></p>
            <p class='unsubscribe'>Si no desea recibir mensajes, responda con "no contactar".</p>
        </div>
    </div>
</body>
</html>"""


def render_welcome(data: dict[str, Any]) -> str:
    """Template de bienvenida / primer contacto."""
    body = f"""
        <div class='section-label'>Una pregunta sobre su operación</div>
        <div class='greeting'>Hola equipo de {data.get('company_name', 'su empresa')},</div>
        <p>Soy {data.get('sender_name', 'Edwin Alexander')}, de Veyra Soluciones.</p>
        <p>Ayudamos a empresas a identificar dónde se está generando fricción operativa antes de que se convierta en retrasos, tareas repetidas o decisiones a ciegas.</p>
        <p>No quiero asumir un diagnóstico a partir de información pública. Por eso les hago una pregunta directa:</p>
        <div class='question-box'>
            <div class='question-text'>{data.get('question', '¿Qué proceso les gustaría que funcionara mejor hoy?')}</div>
        </div>
        <p class='cta-text'>Si tiene sentido, respondan este correo con una frase. Les diré honestamente si un Business MRI™ puede ayudarles y cuál sería el siguiente paso.</p>
    """
    return VEYRA_BASE_TEMPLATE.format(
        body_content=body,
        sender_name=data.get('sender_name', 'Edwin Alexander'),
        sender_email=data.get('sender_email', 'edwin@veyrasoluciones.com'),
    )


def render_followup(data: dict[str, Any]) -> str:
    """Template de seguimiento."""
    body = f"""
        <div class='section-label'>Seguimiento</div>
        <div class='greeting'>Hola {data.get('contact_name', 'equipo')},</div>
        <p>{data.get('message', 'Gracias por su tiempo en nuestra conversación.')}</p>
        <p>{data.get('followup_item', '')}</p>
        <p class='cta-text'>Quedo atento a cualquier duda.</p>
    """
    return VEYRA_BASE_TEMPLATE.format(
        body_content=body,
        sender_name=data.get('sender_name', 'Edwin Alexander'),
        sender_email=data.get('sender_email', 'edwin@veyrasoluciones.com'),
    )


def render_proposal(data: dict[str, Any]) -> str:
    """Template de propuesta comercial."""
    scope_items = data.get('scope_items', [])
    scope_html = ''.join(f'<li>{item}</li>' for item in scope_items)

    body = f"""
        <div class='section-label'>Propuesta Comercial</div>
        <div class='greeting'>Hola {data.get('contact_name', 'equipo')},</div>
        <p>Después de lo que conversamos, armé esta propuesta:</p>
        <div class='question-box'>
            <div class='question-text'>{data.get('project_name', 'Propuesta')}</div>
        </div>
        <p><strong>Qué incluye:</strong></p>
        <ul>{scope_html}</ul>
        <p><strong>Inversión:</strong> {data.get('price', 'A convenir')}</p>
        <p><strong>Tiempo estimado:</strong> {data.get('timeline', '14-30 días')}</p>
        <p class='cta-text'>La propuesta es válida por 7 días.</p>
    """
    return VEYRA_BASE_TEMPLATE.format(
        body_content=body,
        sender_name=data.get('sender_name', 'Edwin Alexander'),
        sender_email=data.get('sender_email', 'edwin@veyrasoluciones.com'),
    )


def render_text_only(data: dict[str, Any]) -> str:
    """Template de texto simple (para seguimientos cortos)."""
    return f"""

{data.get('message', '')}

--
{data.get('sender_name', 'Edwin Alexander')}
Veyra Soluciones
{data.get('sender_email', 'edwin@veyrasoluciones.com')}
"""
