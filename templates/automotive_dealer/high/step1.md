---
name: dealer-high-step1
subject: "{{ company_name }} – clientes premium que comparan"
language: es
---
{{ greeting }}

{% if hook %}{{ hook }}

{% endif %}Hablando con concesionarios premium, nos repiten el mismo problema: {{ pitch.pain }}.

{{ pitch.one_liner }} {{ pitch.proof }}

{{ pitch.cta }}

Un saludo,
{{ sender.name }}
{{ sender.title }}
{% if sender.phone %}{{ sender.phone }}
{% endif %}{% if sender.website %}{{ sender.website }}
{% endif %}
Si prefieres que no te escriba más, respóndeme "baja" y lo elimino de la lista.{% if sender.address %} {{ sender.address }}{% endif %}
