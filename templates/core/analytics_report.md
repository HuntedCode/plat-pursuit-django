{% spaceless %}{% endspaceless %}{% autoescape off %}# PlatPursuit Analytics: {{ window.label }}

Generated {{ generated_at|date:"Y-m-d H:i" }} UTC | Bots: {% if include_bots %}**INCLUDED**{% else %}excluded ({{ totals.bot_session_count|default:0 }} filtered out){% endif %}{% if page_type_filter %} | Page-type filter: `{{ page_type_filter }}`{% endif %}{% if from_cache %} | (served from 5-min cache){% endif %}

## Headline metrics

| Metric | Value | vs prior period |
| --- | --- | --- |
| Sessions | {{ totals.sessions }} | {% if deltas.sessions != None %}{% if deltas.sessions > 0 %}+{% endif %}{{ deltas.sessions }}%{% else %}n/a{% endif %} |
| Page views | {{ totals.pageviews }} | {% if deltas.pageviews != None %}{% if deltas.pageviews > 0 %}+{% endif %}{{ deltas.pageviews }}%{% else %}n/a{% endif %} |
| Bounce rate | {{ totals.bounce_rate_pct }}% | {% if deltas.bounce_rate_pct != None %}{% if deltas.bounce_rate_pct > 0 %}+{% endif %}{{ deltas.bounce_rate_pct }}%{% else %}n/a{% endif %} |
| Pages / session | {{ totals.avg_pages_per_session }} | {% if deltas.avg_pages_per_session != None %}{% if deltas.avg_pages_per_session > 0 %}+{% endif %}{{ deltas.avg_pages_per_session }}%{% else %}n/a{% endif %} |
| Authed sessions | {{ totals.authed_pct }}% ({{ totals.authed_count }}) | {% if deltas.authed_pct != None %}{% if deltas.authed_pct > 0 %}+{% endif %}{{ deltas.authed_pct }}%{% else %}n/a{% endif %} |
| Anonymous sessions | {{ totals.anon_pct }}% ({{ totals.anon_count }}) | n/a |
| Bot sessions in window | {{ totals.bot_session_count }} | n/a |

## Daily trend

| Date | Sessions | Page views |
| --- | --- | --- |
{% for row in trend %}| {{ row.date }} | {{ row.sessions }} | {{ row.pageviews }} |
{% endfor %}
## Top pages (by views)

| Type | Page | Views |
| --- | --- | --- |
{% for row in top_pages %}| {{ row.page_type }} | {{ row.object_label }} | {{ row.views }} |
{% endfor %}
## Top entry pages (first page of each session)

| Type | Page | Sessions |
| --- | --- | --- |
{% for row in top_entry_pages %}| {{ row.page_type }} | {{ row.object_label }} | {{ row.views }} |
{% endfor %}
## Top exit pages (last page of each session)

| Type | Page | Sessions |
| --- | --- | --- |
{% for row in top_exit_pages %}| {{ row.page_type }} | {{ row.object_label }} | {{ row.views }} |
{% endfor %}
## Top referrers

| Source | Sessions | Share |
| --- | --- | --- |
{% for row in top_referrers %}| {{ row.host }} | {{ row.sessions }} | {{ row.pct }}% |
{% endfor %}
## Bounce rate by entry section

| Section | Sessions | Bounced | Bounce rate |
| --- | --- | --- | --- |
{% for row in bounce_by_section %}| {{ row.page_type }} | {{ row.sessions }} | {{ row.bounced }} | {{ row.bounce_rate_pct }}% |
{% endfor %}
## Devices

| Device | Sessions | Share |
| --- | --- | --- |
{% for d in device_browser.devices %}| {{ d.name }} | {{ d.count }} | {{ d.pct }}% |
{% endfor %}
## Browsers

| Browser | Sessions | Share |
| --- | --- | --- |
{% for b in device_browser.browsers %}| {{ b.name }} | {{ b.count }} | {{ b.pct }}% |
{% endfor %}
## Site events

| Event | Count |
| --- | --- |
{% for ev in site_events %}| {{ ev.event_type }} | {{ ev.c }} |
{% endfor %}
## Monthly recap engagement

These three counts are independent SiteEvent totals, not strict funnel stages.
`recap_share_generate` fires every time the share-card slide is rendered, so it
can exceed `recap_page_view` when users revisit the slide. Read the percentages
as relative engagement signal, not literal conversion.

- Recap page views: {{ recap_funnel.page_views }}
- Share card slide impressions: {{ recap_funnel.share_generate }}{% if recap_funnel.pv_to_share_pct != None %} ({{ recap_funnel.pv_to_share_pct }}% of page views){% endif %}
- Image downloads: {{ recap_funnel.image_download }}{% if recap_funnel.share_to_dl_pct != None %} ({{ recap_funnel.share_to_dl_pct }}% of share-card impressions){% endif %}
- Downloads / page view: {% if recap_funnel.pv_to_dl_pct != None %}{{ recap_funnel.pv_to_dl_pct }}%{% else %}n/a{% endif %}

---

*Bounce = sessions with <=1 page view. Sessions time out after 30 minutes of inactivity. UA-spoofing bots are detected by behavioral rules in `core.services.bot_behavioral`.*
{% endautoescape %}
