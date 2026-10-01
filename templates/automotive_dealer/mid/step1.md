---
name: dealer-step1
subject: "Leads que no llegan a {{ company_name }}"
language: es
---
{{ greeting }}

{% if hook %}{{ hook }}

{% endif %}Hablo con bastantes concesionarios{% if city %} en {{ city }}{% endif %} y el problema se repite: {{ pitch.pain }}.

{{ pitch.one_liner }} {{ pitch.proof }}

{{ pitch.cta }}

Un saludo,
{{ sender.name }}
{{ sender.title }}
{% if sender.phone %}{{ sender.phone }}
{% endif %}{% if sender.website %}{{ sender.website }}
{% endif %}
Si prefieres que no te escriba más, respóndeme "baja" y lo elimino de la lista.{% if sender.address %} {{ sender.address }}{% endif %}
