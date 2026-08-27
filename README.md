# Maison Élise — dépôt d'installation HAOS

Ce dépôt public sert uniquement de canal d'installation/test pour **Maison Élise App**.

Candidate en test au 27/08/2026 :

- Maison Élise App : `0.1.0-dev.7`
- Maison Élise Bridge : `0.1.0-dev.7` — dépôt HACS séparé
- skill Maison Élise et Cloudhook : inchangés
- sélection de l’agent conversationnel : automatique via le pipeline Assist préféré (étoile)
- Maison Élise lit le `conversation_engine` du `preferred_pipeline` à chaque requête
- les anciennes valeurs `agent_id` de dev.6 sont ignorées par l’App pour éviter un couplage à un fournisseur LLM

Le transport reste indépendant du fournisseur : changer le LLM de l’assistant préféré ne doit plus nécessiter de modifier Maison Élise.

Élise Why / `InvestigateWhy` et Investigator restent séparés de cette sélection. Le choix d’utiliser l’outil causal reste une responsabilité de l’agent conversationnel ; Investigator reste le moteur causal déterministe et lecture seule.

Aucun correctif de comportement LLM ne doit être réalisé dans la skill, le Cloudhook, le Bridge ou Investigator sans preuve ciblée.

La documentation complète et la traçabilité restent dans les sources Drive Maison Cognitive. La dev.7 reste une candidate de test tant que la recette terrain n’est pas validée.
