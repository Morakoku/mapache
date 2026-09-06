"""Vista comercial BANT de VEYRA sobre el ScoreInput de Mapache (LOOP-20).

Convierte el modelo de decisión comercial (BANT ponderado 4x25, decisión del
fundador) en una función pura sobre los datos que Mapache ya produce: tamaño y
sector para Budget, contacto decisor para Authority, señales de fricción para
Need, y frescura/actividad para Timing. Igual que las dimensiones del motor,
no toca la base de datos y devuelve razones explicables por componente.

Umbrales de decisión (decisión del fundador, LOOP-19):
- SQL (agendar/contactar): total > 70.
- Cierre (propuesta/contrato): total > 85.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.enums import VerificationStatus
from app.scoring.dimensions import ScoreInput
from app.scoring.engine import ScoreResult
from app.utils.text import strip_accents

# Sectores de alto valor (sprint $2,850) — normalizados para comparar.
HIGH_VALUE_SECTORS = frozenset(
    {
        "bienes raices",
        "inmobiliaria",
        "inmobiliarias",
        "clinica",
        "clinicas",
        "medicina estetica",
        "cirugia estetica",
        "estetica",
        "logistica",
        "freight",
        "consultoria",
        "consultora",
        "educacion",
        "edtech",
        "instituto",
        "academia",
    }
)

# Rango objetivo del ICP (PYME/Mid-Market 10-150 empleados).
_ICP_MIN_EMPLOYEES = 10
_ICP_MAX_EMPLOYEES = 150

# Vida media de la extracción (mismo criterio que la dimensión timing).
_TIMING_HALF_LIFE_DAYS = 30.0

# Señales de fricción que indican dolor (Mapache Filter).
_FRICTION_SIGNALS = frozenset(
    {
        "no_website",
        "website_outdated",
        "no_booking",
        "no_online_form",
        "slow_response",
    }
)

_ROLE_EMAIL_MARKERS = ("info@", "contacto@", "ventas@", "admin@", "hola@")


def _norm(value: str | None) -> str:
    return strip_accents(value or "").lower().strip()


@dataclass(frozen=True, slots=True)
class BantScore:
    """Resultado BANT con desglose por componente y razones."""

    budget: int
    authority: int
    need: int
    timing: int
    total: int
    reasons: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_sql(self) -> bool:
        """Lead calificado para agendar/contactar."""
        return self.total > 70

    @property
    def is_close(self) -> bool:
        """Lead listo para propuesta/cierre."""
        return self.total > 85


def _budget(data: ScoreInput) -> tuple[int, list[str]]:
    """Capacidad de pago ($2,850) por tamaño + sector de alto valor."""
    score = 0
    reasons: list[str] = []

    size = _norm(data.employee_range)
    if size:
        if _band_lower(size) >= _ICP_MIN_EMPLOYEES and _band_upper(size) <= _ICP_MAX_EMPLOYEES:
            score += 60
            reasons.append(f"Tamaño en el rango ICP ({size})")
        elif _band_lower(size) < _ICP_MIN_EMPLOYEES:
            score += 25
            reasons.append(f"Empresa pequeña ({size}); puede encajar mejor en Guaki")
        else:
            score += 40
            reasons.append(f"Empresa grande ({size}); ciclo comercial más largo")
    else:
        reasons.append("Tamaño desconocido")

    categories = {_norm(c) for c in (data.company_category, *data.company_categories) if c}
    matched = any(
        any(hv in cat or cat in hv for hv in HIGH_VALUE_SECTORS) for cat in categories
    )
    if matched:
        score += 40
        reasons.append("Sector de alto valor (sprint $2,850)")
    else:
        score += 10
        reasons.append("Sector no prioritario")

    return min(100, score), reasons


def _band_lower(size: str) -> int:
    """Cota inferior del rango '10-150 empleados' textual, o 0 si no es numérico."""
    import re

    m = re.search(r"(\d+)", size)
    return int(m.group(1)) if m else 0


def _band_upper(size: str) -> int:
    """Cota superior del rango '10-150 empleados' textual (misma banda si es un solo número)."""
    import re

    nums = [int(n) for n in re.findall(r"\d+", size)]
    return nums[1] if len(nums) > 1 else (nums[0] if nums else 0)


def _authority(data: ScoreInput) -> tuple[int, list[str]]:
    """Acceso a un contacto decisor (no genérico)."""
    score = 0
    reasons: list[str] = []

    if data.contact_email and data.contact_email_status not in {
        VerificationStatus.BOUNCED,
        VerificationStatus.INVALID,
    }:
        score += 50
        reasons.append("Correo de contacto válido")
    else:
        reasons.append("Sin correo de contacto verificable")

    if data.contact_linkedin:
        score += 30
        reasons.append("Perfil de LinkedIn del decisor")
    if data.contact_phone:
        score += 20
        reasons.append("Teléfono de contacto")

    if data.contact_email and _norm(data.contact_email).startswith(_ROLE_EMAIL_MARKERS):
        reasons.append("El correo es de rol (info/ventas): autoridad parcial")

    return min(100, score), reasons


def _need(data: ScoreInput) -> tuple[int, list[str]]:
    """Dolor del prospecto: señales de fricción + encaje con señales del servicio."""
    score = 0
    reasons: list[str] = []

    friction = data.signals & _FRICTION_SIGNALS
    if "no_website" in friction:
        score += 35
        reasons.append("Sin sitio web propio (oportunidad directa)")
    elif friction:
        score += 25
        reasons.append(f"Señales de fricción: {', '.join(sorted(friction))}")
    else:
        reasons.append("Sin señales de fricción confirmadas")

    service_signals = {_norm(s) for s in data.opportunity_signals if s}
    company_signals = {_norm(s) for s in data.signals if s}
    overlap = service_signals & company_signals
    if overlap:
        score += 35
        reasons.append(f"Señales del servicio presentes ({', '.join(sorted(overlap))})")
    else:
        reasons.append("Sin señales de oportunidad del servicio")

    if data.problems_solved:
        problems = {_norm(p) for p in data.problems_solved}
        if problems & company_signals:
            score += 20
            reasons.append("El problema resuelto coincide con una señal detectada")

    return min(100, score), reasons


def _timing(data: ScoreInput, now: datetime | None = None) -> tuple[int, list[str]]:
    """Frescura del dato y actividad reciente."""
    current = now or datetime.now(UTC)
    reasons: list[str] = []

    if data.extracted_at is not None:
        age_days = max(0.0, (current - data.extracted_at).total_seconds() / 86400.0)
        freshness = 100.0 * (0.5 ** (age_days / _TIMING_HALF_LIFE_DAYS))
        score = round(freshness)
        reasons.append(f"Dato extraído hace {age_days:.0f} días")
    else:
        score = 0
        reasons.append("Sin fecha de extracción")

    if data.last_activity_at is not None:
        score = min(100, score + 20)
        reasons.append("Actividad reciente del prospecto")
    if data.engagement_score >= 40:
        score = min(100, score + 20)
        reasons.append("Engagement alto")

    return score, reasons


def bant_score(data: ScoreInput, *, now: datetime | None = None) -> BantScore:
    """Score comercial BANT (0-100) derivado del ScoreInput de Mapache."""
    budget, budget_reasons = _budget(data)
    authority, authority_reasons = _authority(data)
    need, need_reasons = _need(data)
    timing, timing_reasons = _timing(data, now)

    total = round((budget + authority + need + timing) / 4.0)
    return BantScore(
        budget=budget,
        authority=authority,
        need=need,
        timing=timing,
        total=total,
        reasons={
            "budget": budget_reasons,
            "authority": authority_reasons,
            "need": need_reasons,
            "timing": timing_reasons,
        },
    )


@dataclass(frozen=True, slots=True)
class SegmentClassification:
    """Clasificación de segmento comercial (SEGMENT_A / SEGMENT_B / UNCLASSIFIED).

    No se clasifica solo por empleados: se usan las señales disponibles (tamaño,
    sector, presencia digital, fricción) y la `confidence` refleja cuánta
    evidencia soporta la decisión. Los rangos son HYPOTHESIS hasta validar con
    datos de ventas.
    """

    segment: str
    segment_reason: list[str]
    confidence: int

    @property
    def is_a(self) -> bool:
        return self.segment == "SEGMENT_A"

    @property
    def is_b(self) -> bool:
        return self.segment == "SEGMENT_B"


def segment_classify(data: ScoreInput) -> SegmentClassification:
    """Clasifica PYME (A) vs PYME avanzada/mediana (B) con señales disponibles.

    Orden de evidencia: (1) tamaño; si es ambigua o desconocida, (2) sector de
    alto valor + presencia digital + fricción. Todo es HYPOTHESIS.
    """
    reasons: list[str] = []
    evidence = 0
    segment = ""

    size = _norm(data.employee_range)
    if size:
        low = _band_lower(size)
        high = _band_upper(size)
        evidence += 2
        if high <= 50:
            segment = "SEGMENT_A"
            reasons.append(f"Tamaño {size} (PYME)")
        elif low >= 50:
            segment = "SEGMENT_B"
            reasons.append(f"Tamaño {size} (mediana)")
        else:
            reasons.append(f"Tamaño {size} (banda ambigua, se usa señal secundaria)")
    else:
        reasons.append("Tamaño desconocido")

    categories = {_norm(c) for c in (data.company_category, *data.company_categories) if c}
    is_high_value = any(
        any(hv in cat or cat in hv for hv in HIGH_VALUE_SECTORS) for cat in categories
    )
    has_web = bool(data.company_website)
    friction = bool(data.signals & _FRICTION_SIGNALS)

    if not segment:
        if is_high_value and has_web:
            segment = "SEGMENT_A"
            reasons.append("Sector de alto valor con presencia web")
        elif friction:
            segment = "SEGMENT_A"
            reasons.append("Señales de fricción detectadas")
        else:
            segment = "UNCLASSIFIED"
            reasons.append("Sin evidencia suficiente para clasificar segmento")
    else:
        if is_high_value:
            evidence += 1
            reasons.append("Sector de alto valor")
        if has_web:
            evidence += 1
            reasons.append("Presencia web")
        if friction:
            evidence += 1
            reasons.append("Señales de fricción")

    confidence = min(100, 30 + evidence * 20)
    return SegmentClassification(segment=segment, segment_reason=reasons, confidence=confidence)


def next_action(
    score: ScoreResult, bant: BantScore, segment: SegmentClassification
) -> tuple[str, str]:
    """Próxima acción comercial recomendada + razón (para el Command Center)."""
    dq = score.breakdown.get("dimensions", {}).get("data_quality", {}).get("value", 0)
    if dq < 40:
        return (
            "Investigar contacto",
            f"Calidad del dato baja ({dq}): enriquecer antes de contactar",
        )
    if bant.is_close:
        return ("Preparar Business MRI", "BANT > 85: listo para diagnóstico y propuesta")
    if bant.is_sql:
        return ("Contactar", "BANT > 70: calificado para outreach humano")
    if segment.segment == "UNCLASSIFIED":
        return ("No contactar", "Sin evidencia de segmento ni dolor verificado")
    return (
        "Investigar señales",
        "BANT ≤ 70: buscar más señales de dolor/capacidad o pasar a nurture",
    )


def recommended_message(
    data: ScoreInput,
    segment: SegmentClassification,
    *,
    company_name: str | None = None,
    city_label: str | None = None,
) -> dict[str, str]:
    """Mensaje recomendado (outreach). Solo usa hechos observados como observación;
    las inferencias se redactan como hipótesis. Sin envío: esto es preparación.
    """
    name = company_name or "tu empresa"
    city = city_label or data.company_city or ""
    sector = (
        data.company_category
        or (data.company_categories[0] if data.company_categories else "")
    )

    observed = sorted(data.signals & _FRICTION_SIGNALS)
    if "no_website" in observed:
        obs = "sin sitio web propio"
    elif observed:
        obs = "con señales de fricción en su atención digital"
    elif data.company_website:
        obs = "con presencia web activa"
    else:
        obs = "con actividad en canales digitales"

    if segment.is_b:
        asunto = f"Analizamos dónde se pierden oportunidades en {name}"
        mensaje = (
            f"Hola,\n\n{name} ya opera con varios canales y procesos. En VEYRA analizamos "
            f"procesos, datos y canales para encontrar dónde se pierden oportunidades y qué "
            f"automatizar. La señal \"{obs}\" es un punto que solemos revisar.\n\n"
            f"¿Te gustaría un diagnóstico para {name} en {city}?"
        )
    elif segment.is_a:
        asunto = f"Dónde se están perdiendo oportunidades en {name}"
        mensaje = (
            f"Hola,\n\nVimos que {name} ({sector}) {obs}. Cuando una empresa crece, es fácil que "
            f"algunas oportunidades se queden sin seguimiento. En VEYRA revisamos ese proceso para "
            f"detectar dónde se están perdiendo y te dejamos un plan para corregirlo.\n\n"
            f"¿Te gustaría que revisemos el caso de {name}?"
        )
    else:
        asunto = f"Revisión para {name}"
        mensaje = (
            f"Hola,\n\nEn VEYRA ayudamos a empresas como {name} a encontrar dónde se escapan "
            f"oportunidades. ¿Te gustaría que revisemos tu caso?"
        )

    return {
        "asunto": asunto,
        "mensaje": mensaje,
        "cta": "¿Te gustaría que revisemos tu caso?",
    }


def _score_explain(score: ScoreResult) -> list[str]:
    """Razones legibles del score (dimensiones con mayor contribución)."""
    dims: dict[str, Any] = score.breakdown.get("dimensions", {})
    ranked = sorted(dims.items(), key=lambda item: item[1].get("contribution", 0), reverse=True)
    lines: list[str] = []
    for key, value in ranked[:4]:
        label = value.get("label", key)
        reasons = value.get("reasons") or ["sin razón registrada"]
        lines.append(f"{label} ({value.get('value', 0)}): {reasons[0]}")
    return lines


def _infer_problems(data: ScoreInput) -> list[str]:
    """Problemas potenciales INFERIDOS a partir de señales observadas. Nunca se
    presentan como hechos: la redacción lo deja claro."""
    problems: list[str] = []
    signals = data.signals
    if "no_website" in signals:
        problems.append("Captación dependiente de redes/WhatsApp sin canal propio (INFERIDO)")
    if "slow_response" in signals:
        problems.append("Respuesta lenta que puede perder clientes (INFERIDO)")
    if "no_booking" in signals or "no_online_form" in signals:
        problems.append("Reservas o cotizaciones manuales sin vía digital (INFERIDO)")
    if not problems:
        problems.append("Sin dolor verificado aún (solo hipótesis) — HYPOTHESIS")
    return problems


def _opportunity_text(data: ScoreInput) -> str:
    return (
        "Existe una posible oportunidad para mejorar la captación y el seguimiento de clientes, "
        "especialmente porque la empresa opera en un sector con demanda recurrente (OPORTUNIDAD "
        "POTENCIAL — requiere validación)."
    )


def commercial_intelligence(
    data: ScoreInput,
    score: ScoreResult,
    bant: BantScore,
    segment: SegmentClassification,
    *,
    company_name: str | None = None,
    city_label: str | None = None,
) -> dict[str, Any]:
    """Lead Intelligence Record (representación única del prospecto).

    Reúne empresa → segmento → contacto → señales → problemas → oportunidad →
    score → prioridad → mensaje → próxima acción. Los campos derivados llevan
    marca de procedencia (OBSERVED/INFERRED/HYPOTHESIS).
    """
    dims = score.breakdown.get("dimensions", {})
    priority = (
        "ALTA"
        if (bant.is_sql or bant.is_close)
        else ("MEDIA" if score.total >= 50 else "BAJA")
    )
    action, action_reason = next_action(score, bant, segment)

    return {
        "empresa": company_name or data.company_email or "N/A",
        "sector": (
            data.company_category
            or (data.company_categories[0] if data.company_categories else None)
        ),
        "ciudad": city_label or data.company_city or "",
        "segmento": segment.segment,
        "segment_reason": segment.segment_reason,
        "segment_confidence": segment.confidence,
        "contacto": {
            "email": data.contact_email or data.company_email,
            "phone": data.contact_phone or data.company_phone,
            "website": data.company_website,
        },
        "senales_observadas": sorted(data.signals),
        "problemas_potenciales": _infer_problems(data),
        "oportunidad": _opportunity_text(data),
        "score": {
            "total": score.total,
            "fit": dims.get("fit", {}).get("value"),
            "opportunity": dims.get("opportunity", {}).get("value"),
            "contactability": dims.get("contactability", {}).get("value"),
            "data_quality": dims.get("data_quality", {}).get("value"),
            "intent": dims.get("intent", {}).get("value"),
            "timing": dims.get("timing", {}).get("value"),
            "por_que": _score_explain(score),
        },
        "bant": {
            "budget": bant.budget,
            "authority": bant.authority,
            "need": bant.need,
            "timing": bant.timing,
            "total": bant.total,
            "is_sql": bant.is_sql,
            "is_close": bant.is_close,
        },
        "prioridad": priority,
        "mensaje_recomendado": recommended_message(
            data, segment, company_name=company_name, city_label=city_label
        ),
        "proxima_accion": action,
        "proxima_accion_reason": action_reason,
    }


__all__ = [
    "BantScore",
    "SegmentClassification",
    "bant_score",
    "commercial_intelligence",
    "next_action",
    "recommended_message",
    "segment_classify",
]
