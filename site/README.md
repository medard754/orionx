# Site vitrine ORIONX

Site statique (HTML, CSS, JavaScript), **autonome** : aucune dépendance au code de l'agent, aucune bibliothèque, aucune police
ni ressource externe, aucun traceur. Pour le déployer, on envoie **ce dossier tel quel** chez un hébergeur de site statique.

## Contenu

| Fichier | Page |
|---|---|
| `index.html` | Produit (accueil) : héro 3D, fonctionnalités, étapes, téléchargement |
| `abonnement.html` | Offres Démo / Pro / Équipe, questions fréquentes |
| `a-propos.html` | Mission, principes, état du projet |
| `contact.html` | Formulaire (prépare un e-mail dans la messagerie du visiteur) |
| `connexion.html` | « Se connecter » : espace client à venir, tableau de bord local en attendant |
| `assets/` | Style (`site.css`), scripts (`site.js`, `i18n.js`, `core3d.js`), traductions (`i18n/`), logo, icône, aperçus et galerie du tableau de bord |

## Voir le site sur son PC

Ouvrir `index.html` dans le navigateur, ou servir le dossier :

```powershell
cd site
python -m http.server 8080
# puis http://127.0.0.1:8080
```

## Avant de publier : à faire OBLIGATOIREMENT

1. **Adresse de contact** : remplacer `contact@orionx.example` dans `assets/site.js` (constante `CONTACT_EMAIL`).
2. **Mentions légales** (loi française LCEN, obligatoire pour tout site publié) : ajouter une page avec l'éditeur (nom ou raison
   sociale, adresse, contact), le directeur de la publication et l'hébergeur. Si des e-mails sont collectés (liste d'attente),
   ajouter une **politique de confidentialité** (RGPD).
3. **Avis juridique** : la publicité pour des produits de trading est encadrée (AMF). Faire relire les textes et l'avertissement
   sur les risques présent en pied de chaque page.
4. **Marque** : vérifier la disponibilité du nom ORIONX (marque, domaine) avant d'acheter un nom de domaine.
5. **Tarifs** : décider des prix de l'offre Pro (aujourd'hui « Tarif à venir »).
6. **Téléchargements** : les boutons App Store et Windows mènent à la liste d'attente tant que les applications n'existent pas.
   Ne pas les remplacer par des badges officiels d'Apple ou de Microsoft sans respecter leurs règles d'utilisation.

## Déployer

**En ligne (GitHub Pages)** : https://medard754.github.io/orionx/ , servi depuis la branche `gh-pages`, qui ne contient que ce
dossier. Pour publier une mise à jour après un commit sur `feat/site-vitrine` :

```powershell
git subtree push --prefix site origin gh-pages
```

Le site est en ligne quelques minutes plus tard. `.nojekyll` demande à GitHub de servir les fichiers tels quels.

Autres hébergeurs possibles :

N'importe quel hébergement de site statique convient : il suffit de publier le contenu de `site/`.

- **Netlify** ou **Cloudflare Pages** : glisser-déposer le dossier `site/`, ou relier le dépôt GitHub avec `site` comme dossier de
  publication (aucune commande de build).
- **GitHub Pages** : publier le dossier `site/` (le dépôt est aujourd'hui public ; décider d'abord s'il doit rester public).
- **Hébergeur classique** (OVH, o2switch…) : envoyer le contenu de `site/` par FTP dans le dossier web (`www/`).

Le test `agent/tests/test_site.py` vérifie à chaque modification : liens internes valides, aucune ressource externe,
avertissement sur les risques sur chaque page, aucun champ de mot de passe.

## Langues (FR, EN, ES)

Le français est la source : c'est le texte écrit dans les pages. Chaque texte traduisible porte un `data-i18n="clé"`
(ou `data-i18n-attr="alt:clé"` pour un attribut) ; `assets/i18n/en.js` et `es.js` donnent la traduction de chaque clé.
Le visiteur choisit avec les boutons FR / EN / ES (choix mémorisé, langue du navigateur par défaut, `?lang=en` possible).

- **Modifier un texte** : changer le français dans la page ET sa traduction dans `en.js` et `es.js` (sous la même clé).
- **Ajouter une langue** : créer `assets/i18n/xx.js` avec toutes les clés, charger le script dans les 5 pages, ajouter
  `<button data-lang="xx">` dans l'en-tête et `"xx"` dans `LANGS` de `assets/i18n.js`.
- Les tests (`agent/tests/test_site.py`) vérifient que chaque clé est traduite, qu'aucune traduction n'est orpheline et que les
  balises (dégradé des titres, ✓) sont conservées. Le tableau de bord de l'agent n'est, lui, pas encore traduit (dit sur le site).

## Section 3D « cœur d'ORIONX »

`assets/core3d.js` dessine, sur un `<canvas>`, un réseau neuronal en 3D (projection en perspective écrite à la main, sans
bibliothèque) entouré d'un périmètre de contrôle du risque : des impulsions (idées de l'IA) le traversent (validées, vert) ou s'y
heurtent (refusées, orange). Illustration pure : aucune donnée de marché, aucun appel réseau. L'animation s'arrête hors écran, onglet
masqué, et reste fixe si le système demande moins de mouvement (`prefers-reduced-motion`).

## Mettre à jour les captures du tableau de bord

Les images `apercu-*.png` et `galerie-*.png` viennent de `demo-dashboard` (agent et courtier simulés) : lancer le dashboard de
démonstration, capturer avec Edge en mode sans fenêtre, puis recadrer. Refaire les captures à chaque changement visible du dashboard.
