---
name: construction-step1
subject: "{{ company_name }} – nuevos proyectos sin depender del boca a boca"
language: es
---
{{ greeting }}

{% if hook %}{{ hook }}

{% endif %}Al hablar con constructoras{% if city %} de {{ city }}{% endif %} nos encontramos siempre lo mismo: {{ pitch.pain }}.

{{ pitch.one_liner }} {{ pitch.proof }}

{{ pitch.cta }}

Un saludo,
{{ sender.name }}
{{ sender.title }}
{% if sender.phone %}{{ sender.phone }}
{% endif %}{% if sender.website %}{{ sender.website }}
{% endif %}
Si prefieres que no te escriba más, respóndeme "baja" y lo elimino de la lista.{% if sender.address %} {{ sender.address }}{% endif %}
