# ADR 0003 – Approbation signée entre risque et exécution

**Statut :** accepté · **Date :** 2026-10-12

**Décision.** Seul le gestionnaire de risques produit des `ApprovedOrder`, signés en HMAC-SHA256 avec une
clé générée au démarrage. `Executor.execute` vérifie signature et expiration avant tout envoi.

**Limite assumée.** La clé vit dans le même processus : on empêche le contournement accidentel, pas un code
malveillant déjà exécuté dans l'agent. La défense contre ce second cas relève des mises à jour signées et de la
revue de code.
