# Maison Élise — dépôt d'installation HAOS

Ce dépôt public sert uniquement de canal d'installation/test pour **Maison Élise App**.

Baseline au 27/08/2026 :

- Maison Élise App : `0.1.0-dev.6`
- Maison Élise Bridge : `0.1.0-dev.7` — dépôt HACS séparé
- skill Maison Élise et Cloudhook : inchangés
- agent conversationnel Home Assistant : configurable via `agent_id`
- valeur par défaut : `conversation.openai_conversation`
- Gemini observé : `conversation.google_ai_conversation`

Le transport de l'App est indépendant du fournisseur LLM. Les essais terrain ont toutefois montré que Gemini peut utiliser Élise Why / `InvestigateWhy` correctement sans le sélectionner systématiquement sur toutes les questions causales ; OpenAI a été observé plus régulier.

Aucun correctif de ce comportement ne doit être réalisé dans la skill, le Cloudhook, le Bridge ou Investigator sans preuve ciblée.

La documentation complète et la traçabilité restent dans le dépôt privé `Maison-Cognitive` et dans les sources Drive Maison Cognitive.
