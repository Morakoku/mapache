# Templates de Email - Mapache CRM

Estos templates siguen el enfoque Humanizer: texto que suena humano, no robotico.
Evitan los 25 patrones de AI writing (triadas forzadas, lenguaje de ventas, frases de cierre, etc.).

---

## welcome.md - Email de Bienvenida

**Asunto:** {{company_name}} — propuesta de valor

Hola {{contact_name}},

Te escribo porque pasamos por {{company_name}} hace unos días y vimos algo que nos interesó. No sé si ya tienen esto cubierto, pero quería comentarte lo que hacemos.

Trabajamos con empresas como la suya para {{value_proposition}}. El resultado típico es {{typical_result}}, aunque depende de cada caso.

Si tiene sentido, me gustaría conversar 15 minutos la próxima semana. Sin compromiso.

Saludos,
{{sender_name}}
{{sender_phone}}

---

## followup.md - Email de Seguimiento

**Asunto:** Seguimiento — conversación del {{meeting_date}}

Hola {{contact_name}},

Gracias por tu tiempo el {{meeting_date}}. Como quedamos, te envío esto que mencioné:

{{followup_item}}

Quedo atento a cualquier duda. Si necesitas algo antes de la próxima reunión, avísame.

Saludos,
{{sender_name}}

---

## proposal.md - Propuesta Comercial

**Asunto:** Propuesta — {{project_name}}

Hola {{contact_name}},

Después de lo que conversarme, armé esta propuesta. Es lo que entiendo que necesitas; si algo no cuadra, dime y lo ajustamos.

**Qué incluye:**
- {{scope_item_1}}
- {{scope_item_2}}
- {{scope_item_3}}

**Inversión:** {{price}}
**Tiempo estimado:** {{timeline}}

La propuesta es válida por 7 días. Si te parece bien, lo arrancamos el {{start_date}}.

Quedo atento,
{{sender_name}}
{{sender_phone}}

---

## notas.md - Notas sobre el tono

Qué evitamos:
- "Estimado/a" → usa el nombre directo
- "Espero que este mensaje te encuentre bien" → salta directo al punto
- Triadas ("ágil, eficiente, escalable") → habla como personas
- Cierre ("Quedo a tu entera disposición") → "Quedo atento/a" o nada
- Signos de exclamación → solo cuando sea necesario

Qué sí usamos:
- Tú (no "usted" formal a menos que el cliente sea muy corporativo)
- Frases cortas
- Datos concretos (nombres, fechas, montos)
- Preguntas directas
