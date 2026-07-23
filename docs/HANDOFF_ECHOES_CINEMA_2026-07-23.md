# Handoff opérationnel — EchoesEngine / Echoes Cinema / K-Core

Date : 2026-07-23  
Projet : `kamdridi-lgtm/EchoesEngine`  
Branche active : `feature/k-core-canonical-audit-2026-07-21`  
Pull request : `#1 — Build verified EchoesEngine / K-Core / Cinema foundation`  
État global : **PARTIAL — fondation d’exécution vérifiée, premier rendu vidéo IA réel encore non prouvé**

---

## 1. Mission réelle

Nous ne construisons pas seulement un générateur de quatre secondes. Nous construisons une **usine autonome de production vidéo musicale** pour Kam Dridi et, plus tard, pour le service commercial Echoes Cinema.

Le système cible doit pouvoir :

1. recevoir une chanson, ses sections, une direction artistique et des références visuelles ;
2. produire un plan de réalisation cohérent ;
3. choisir le bon moteur local ou distant selon la VRAM, la licence et l’usage commercial ;
4. générer les plans vidéo ;
5. reprendre uniquement les plans manquants ou endommagés après une interruption ;
6. assembler les plans avec l’audio ;
7. contrôler le codec, le format, la durée et la synchronisation audio/vidéo ;
8. conserver les preuves SHA-256 et les rapports d’exécution ;
9. nettoyer automatiquement le stockage jetable sans supprimer les modèles ni les résultats utiles ;
10. fonctionner longtemps avec un minimum de babysitting.

La première vidéo de quatre secondes est une **preuve technique obligatoire**, pas le produit final.

---

## 2. Architecture canonique

```text
K-Core — contrôle opérationnel
    |
    | health / submit / status / kill switch
    v
Echoes Cinema Job Service :8090
    |
    | authentification, file de priorité, stockage, ledger durable
    v
Cinema Job Runner
    |
    | manifest -> rendu par plan -> reprise -> assemblage -> QC -> preuves
    v
Render Provider :8081
    |
    | modèle IA local ou fournisseur commercial distant
    v
MP4 H.264 / yuv420p + audio + job-result.json + SHA-256
```

### Responsabilités

- **EchoesEngine** : moteur natif DSP, traitement audio, contrats fondamentaux.
- **Echoes Cinema** : planification, génération vidéo, assemblage, QC et preuves.
- **K-Core** : contrôle, soumission, surveillance et sécurité opérationnelle.
- **DreamGirl / DreamVerse / EchoesStudio** : futurs clients des contrats, pas propriétaires du moteur canonique.

---

## 3. Emplacements obligatoires sur la machine de Karim

```text
D:\A.I\EchoesEngine
D:\A.I\EchoesCinema
D:\A.I\Python310
D:\A.I\EchoesCinema\.venv-cinema
D:\A.I\EchoesCinema\cache
D:\A.I\EchoesCinema\jobs
D:\A.I\EchoesCinema\proofs
```

### Règle absolue

Les modèles, caches, environnements Python, installateurs temporaires, résultats et preuves doivent rester sur **D:**.

Ne jamais proposer de déplacer le projet ou les modèles sur C:. Windows peut encore écrire de petites métadonnées système sur C:, mais le pipeline ne doit pas y placer volontairement ses données lourdes.

---

## 4. Protocole de vérité

Ordre obligatoire :

```text
CODE -> BUILD -> RUN -> OUTPUT -> QC -> PROOF -> REAL
```

Vocabulaire autorisé :

- `REAL` : exécuté et prouvé avec artefacts et contrôle qualité.
- `PARTIAL` : une partie fonctionne, mais la preuve finale manque.
- `MOCK` : fixture ou sortie synthétique de test.
- `DORMANT` : code présent mais non actif.
- `BROKEN` : chemin exécuté et cassé.
- `MISSING` : non construit ou non vérifié.

Un contrat compilé ne prouve pas qu’un modèle vidéo a réellement généré une image.

---

## 5. État présent vérifié

### REAL

- Build Windows d’EchoesEngine.
- Traitement audio WAV réel et preuve audio.
- PromptDirector, ShotPlanner et génération de manifest.
- Générateur de manifest Python sans Visual Studio.
- Contrat HTTP authentifié du fournisseur de rendu.
- Téléchargement et validation des clips fournisseur.
- Assemblage MP4 H.264 / `yuv420p`.
- Ajout audio et contrôle de dérive audio/vidéo.
- SHA-256 des clips, manifests, états, audio et MP4 final.
- Reprise par plan avec validation SHA-256 + FFprobe.
- Régénération uniquement des plans manquants, modifiés ou cassés.
- Ledger atomique persistant.
- Transformation des travaux interrompus en `RECOVERABLE` après redémarrage.
- File de priorité `0–100` avec FIFO à priorité égale.
- Limitation du nombre de travaux simultanés.
- Réservation d’espace disque avant admission.
- Refus fail-closed lorsque la réserve de sécurité de D: serait violée.
- Libération automatique de la réservation après le travail.
- Nettoyage automatique avant et après exécution.
- Protection des modèles Hugging Face, de l’environnement Python et des preuves actuelles.
- Python 3.10.11 installé sur D: sur la machine de Karim.
- Environnement virtuel Cinema créé sur D:.
- PyTorch CUDA et dépendances Diffusers installés sur la machine de Karim.
- GPU détecté : NVIDIA GeForce RTX 2060, 6 Go de VRAM.

### PARTIAL

- Fournisseur ModelScope faible VRAM : code et contrats validés, chargement réel local non encore prouvé.
- Sélection automatique local / distant : contrat validé, exploitation commerciale non encore déployée.
- Fournisseur CogVideoX-2B commercial : contrat et verrouillage de révision présents, exécution de production non prouvée.
- Service Cinema durable : orchestration validée par tests, exploitation continue sur la machine de Karim non encore prouvée.
- K-Core vers Cinema : contrat de contrôle présent, chaîne de production complète non encore prouvée avec un vrai modèle.

### MOCK

- Preuves synthétiques MP4 utilisées par les workflows GitHub.
- Fournisseur HTTP fixture utilisé pour tester le protocole et la reprise.

### MISSING

- Premier clip réellement généré par un modèle IA sur la RTX 2060.
- `realModelLoaded=true` prouvé sur la machine cible.
- `job-result.json` avec `backendStatus=REAL` provenant du vrai modèle.
- Validation visuelle humaine du premier clip.
- Séquence artistique cohérente de 20 à 30 secondes.
- Continuité fiable du visage et de l’identité de Kam Dridi.
- Clip musical complet.
- Déploiement commercial autonome.

---

## 6. Protection du disque D

### Nettoyage autorisé

- dossiers temporaires ;
- cache Python compilé ;
- cache CUDA temporaire ;
- cache Numba ;
- ancien cache pip ;
- anciens installateurs Python ;
- anciens dossiers de build Visual Studio devenus inutiles ;
- anciennes archives de preuves au-delà de la limite conservée.

### Ne jamais supprimer automatiquement

- `cache\huggingface` contenant les modèles ;
- `.venv-cinema` ;
- `D:\A.I\Python310` ;
- la preuve actuelle ;
- les deux archives de preuve les plus récentes ;
- les travaux `RUNNING`, `QUEUED` ou `RECOVERABLE` ;
- les fichiers nécessaires à une reprise validée.

### Politique actuelle

```text
Réserve de sécurité D:      20 GiB
Réservation par travail      8 GiB
Maximum par travail         200 GiB
Workers GPU simultanés        1
Archives de preuve gardées     2
```

Ces valeurs sont réglables, mais la RTX 2060 doit rester à un seul worker GPU local.

---

## 7. Preuves et fichiers à conserver

Après chaque vrai essai, conserver au minimum :

```text
provider-health.json
provider.log
provider-error.log
gpu-report.json
render-manifest.json
render-state.json
resume-plan.json
video-qc.json
job-result.json
service-run.log
cleanup-before-run.json
cleanup-after-run.json
*.mp4
```

Un succès n’est pas accepté sans :

```text
realModelLoaded=true
render-state status=PASS
video-qc status=PASS
job-result status=PASS
backendStatus=REAL
MP4 non vide
SHA-256 enregistré
validation visuelle humaine
```

---

## 8. Prochaine étape prioritaire — P0

### Objectif

Obtenir le **premier vrai clip IA local de 3 à 5 secondes** sur la RTX 2060 sans réinstaller Python, PyTorch ou Diffusers.

### Lanceur disponible

```text
START_ECHOES_CINEMA_REFRESH_D_ONLY_V5.cmd
```

Le lanceur doit :

1. mettre à jour la branche active ;
2. nettoyer uniquement les données jetables ;
3. réutiliser Python et l’environnement virtuel existants ;
4. démarrer le fournisseur faible VRAM ;
5. télécharger ou réutiliser le modèle sur D: ;
6. générer la preuve ;
7. assembler le MP4 ;
8. exécuter le QC ;
9. écrire les rapports ;
10. nettoyer de nouveau les éléments temporaires.

### Profil RTX 2060 actuel

```text
384 x 216
4 images/seconde
16 images
15 étapes
sequential CPU offload
VAE slicing + tiling
attention slicing
forward chunking
repli OOM : 320 x 184
```

### Réaction au prochain test

- Ne pas recommencer l’architecture.
- Lire la dernière erreur exacte.
- Corriger uniquement le bloqueur réel.
- Pousser le correctif sur la même branche.
- Faire passer les workflows.
- Relancer sans réinstaller ce qui fonctionne déjà.

---

## 9. Ordre de poursuite après la preuve locale

### P1 — Stabiliser le moteur local

- vérifier le chargement complet du modèle ;
- mesurer VRAM, RAM et temps de génération ;
- adapter le profil OOM ;
- garantir la reprise après interruption ;
- produire trois preuves consécutives sans corruption.

### P2 — Route commerciale

- ne jamais utiliser le modèle de preuve non commercial pour une vente ;
- activer un fournisseur dont la licence commerciale et la révision sont explicitement autorisées ;
- utiliser une machine distante plus puissante lorsque 6 Go de VRAM sont insuffisants ;
- conserver les mêmes contrats HTTP, QC, preuves et reprise.

### P3 — Références et identité

- ajouter une image de référence de Kam Dridi ;
- exiger la capacité `referenceImage` ;
- ajouter une stratégie de continuité de sujet ;
- mesurer les dérives du visage, des vêtements, de la guitare et du décor ;
- refuser de déclarer la continuité `REAL` avant validation visuelle.

### P4 — Première vraie scène artistique

Créer une séquence de 20 à 30 secondes :

```text
rock industriel cinématique
mégapole nocturne sous la pluie
lumières ambre et rouge sombre
performance de Kam Dridi
mouvements de caméra contrôlés
montage synchronisé à la musique
```

Elle doit utiliser la vraie chanson, plusieurs plans cohérents, une reprise par plan et un MP4 final contrôlé.

### P5 — Clip musical complet

- découpage complet de la chanson ;
- génération par lots ;
- continuité artistique ;
- réutilisation des plans validés ;
- montage final ;
- versions YouTube, réseaux sociaux et page web ;
- archive maîtresse et preuves de production.

### P6 — Usine autonome et revenus

- connecter K-Core à la file durable ;
- tableau de suivi ;
- soumission client ;
- quotas et coûts ;
- fournisseur commercial distant ;
- livraison automatisée ;
- publication contrôlée vers le site et les plateformes.

---

## 10. Directives impératives pour le prochain assistant ou agent

1. Lire ce fichier avant toute action.
2. Utiliser le dépôt `kamdridi-lgtm/EchoesEngine` et la branche `feature/k-core-canonical-audit-2026-07-21`.
3. Inspecter le dernier commit et les workflows avant de modifier du code.
4. Ne jamais prétendre que le premier clip IA est réussi sans artefacts locaux.
5. Ne pas répéter des plans déjà construits ; poursuivre à partir du premier point `MISSING`.
6. Ne pas réinstaller Python, PyTorch ou Diffusers sans preuve qu’ils sont réellement cassés.
7. Garder toutes les données lourdes sur D:.
8. Ne pas supprimer les modèles ni les preuves utiles pendant le nettoyage.
9. Corriger le bloqueur exact montré par le journal, puis relancer.
10. Exécuter les workflows GitHub après chaque changement important.
11. Toute erreur doit rester visible dans les JSON et les logs ; ne jamais convertir une erreur en faux `PASS`.
12. Toute sortie commerciale doit être bloquée si la licence n’est pas explicitement autorisée.
13. La RTX 2060 locale reste un moteur de preuve et de petits plans ; prévoir le distant pour la production lourde.
14. Conserver les SHA-256, les rapports FFprobe et les états de reprise.
15. Ne pas fusionner ou annoncer la phase vidéo IA comme terminée avant la preuve P0.

---

## 11. Définition de « P0 terminé »

P0 est terminé uniquement lorsque les éléments suivants existent sur la machine de Karim :

```text
un vrai modèle chargé
realModelLoaded=true
un MP4 réellement généré par ce modèle
H.264
pixel format yuv420p
durée positive
audio présent lorsque demandé
A/V drift dans la tolérance
job-result status=PASS
backendStatus=REAL
SHA-256 du MP4 final
validation visuelle par Karim
```

Avant cela, l’état global reste :

```text
Echoes Cinema : PARTIAL
Premier clip IA local : MISSING
```

---

## 12. Résumé en une phrase

Nous avons construit et vérifié toute la **colonne vertébrale sécurisée, durable, reprenable et économe en stockage** d’Echoes Cinema ; la prochaine action est de faire passer un vrai modèle sur la RTX 2060, puis de transformer cette preuve en scènes commerciales cohérentes et enfin en clips musicaux complets automatisés.
