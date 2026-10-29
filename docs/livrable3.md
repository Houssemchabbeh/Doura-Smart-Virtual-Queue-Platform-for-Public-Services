# Livrable 3 — Base de données et API REST

**Projet :** Doura — plateforme de file d'attente virtuelle pour les services publics tunisiens
**Équipe :** Houssem Ben Chabbeh, Houssem Eddine Sfaxi, Mouhamed Massous, Adem Moslah — ING-4-J-CIM-A, TEK-UP
**Contenu attendu :** modélisation DynamoDB, points de terminaison de l'API REST, fonctions Lambda, opérations CRUD,
validation des entrées, gestion des erreurs.

Toutes les données sont **fictives**. Le CIN n'est jamais stocké ni journalisé : seule son empreinte HMAC-SHA256 est conservée.

---

## 1. Vue d'ensemble

```
Navigateur / script ──HTTPS──> API Gateway (REST, stage v1)
                                   │  autorisateur Cognito (en-tête Authorization: <IdToken>)
                                   ▼
                         Lambdas Python 3.12 (sous-réseau privé, rôle LabRole)
                         health · agencies · tickets · queue · display
                                   │  endpoint de passerelle DynamoDB (gratuit, sans NAT)
                                   ▼
                         DynamoDB : agencies · tickets (+ Stream) · control
                                   │  Stream NEW_AND_OLD_IMAGES
                                   ▼
                         Lambda notifier (hors VPC) ──> SNS ──> e-mail
```

Tout est créé **par code Boto3** (`python infra/deploy.py --stage <nom>`), y compris l'API Gateway.
La console AWS sert uniquement à vérifier et à faire les captures (section 10).

---

## 2. Modélisation DynamoDB

Trois tables en mode **à la demande** (`PAY_PER_REQUEST`), préfixées `doura-<stage>-`.

### 2.1 Tables

| Table | Clé de partition | Clé de tri | Contenu |
|---|---|---|---|
| `agencies` | `agency_id` | `item` | `META` (nom, ville, adresse) ; `SERVICE#<lettre>` (code, nom, horaires, pièces requises, durée moyenne) ; `COUNTER#<id>` (services traités, ouvert/fermé) |
| `tickets` | `ticket_id` | – | un ticket ; **Stream** `NEW_AND_OLD_IMAGES` pour les notifications |
| `control` | `pk` | – | `SEQ#<agence>#<date>#<lettre>` → `last_number` (compteur atomique) ; `ACTIVE#<empreinte CIN>#<agence>#<date>#<lettre>` → verrou « un ticket actif par citoyen et service » |

Modèle « single-table » pour `agencies` : une seule requête `Query(agency_id)` renvoie l'agence, ses services et ses guichets.

**Identifiant de ticket** : `<agence>_<aaaammjj>_<numéro>`, par exemple `AG-TUN-03_20261026_A-047`.
Il ne contient pas de `#`, il peut donc être utilisé tel quel dans l'URL `/tickets/{ticket_id}`.

**Attributs d'un ticket** : `ticket_id`, `agency_id`, `agency_name`, `service` (code), `number` (ex. `A-047`), `status`,
`priority`, `queue_key` (`<agence>#<date>#<lettre>`), `waiting_since` (`<horodatage>#<numéro>`, présent uniquement en attente),
`created_at`, `called_at`, `counter_id`, `served_at`, `no_show_at`, `cancelled_at`, `updated_at`, `owner_sub` (identifiant
Cognito du citoyen), `cin_hash` (empreinte HMAC, jamais renvoyée).

### 2.2 Index secondaires globaux (table tickets)

Index **creux** : un ticket n'y figure que si l'attribut de clé existe.

| Index | Partition | Tri | Usage |
|---|---|---|---|
| `WaitingQueue` | `queue_key` | `waiting_since` | file d'attente d'un service, dans l'ordre d'arrivée. `waiting_since` est supprimé dès l'appel : le ticket sort automatiquement de l'index. |
| `AgencyCalled` | `agency_id` | `called_at` | derniers tickets appelés (écran public de la salle d'attente). |

Les index sont à cohérence éventuelle : ils servent à **trouver** les candidats, jamais à garantir la correction.
Chaque changement d'état passe par une **écriture conditionnelle** sur la table de base.

### 2.3 Modèles d'accès

| Besoin | Table / index | Opération |
|---|---|---|
| Lister les agences | agencies | `Scan` paginé (quelques dizaines d'items) |
| Détail d'une agence (services, guichets) | agencies | `Query agency_id = :a` |
| Services d'une agence | agencies | `Query agency_id = :a AND begins_with(item, "SERVICE#")` |
| Créer / modifier / supprimer un service | agencies | `PutItem` / `UpdateItem` / `DeleteItem` conditionnels |
| Numéro de ticket suivant | control | `UpdateItem ADD last_number :1` (atomique) |
| Un seul ticket actif par citoyen et service | control | `PutItem` avec `attribute_not_exists(pk)` |
| Créer un ticket | tickets | `PutItem` avec `attribute_not_exists(ticket_id)` |
| Lire un ticket | tickets | `GetItem ticket_id` |
| Position dans la file | tickets / `WaitingQueue` | `Query queue_key = :q AND waiting_since < :mien`, `Select=COUNT` |
| Prochain ticket d'un guichet | tickets / `WaitingQueue` | `Query queue_key = :q`, ordre croissant, `Limit` |
| Appeler / servir / annuler | tickets | `UpdateItem` avec condition sur `status` |
| Écran public | tickets / `AgencyCalled` | `Query agency_id = :a`, ordre décroissant, `Limit 5` |
| Notification e-mail | Stream tickets | Lambda notifier déclenchée sur `MODIFY` |

### 2.4 Cycle de vie d'un ticket

```
WAITING ──(call-next, agent)──> CALLED ──(PATCH SERVED)───> SERVED
   │                              │    └─(PATCH NO_SHOW)──> NO_SHOW
   └──────(DELETE, citoyen)───────┴──────────────────────> CANCELLED
```

Toute autre transition renvoie **409 `INVALID_TRANSITION`**. SERVED et CANCELLED déclenchent un e-mail (Stream → notifier → SNS).

---

## 3. Points de terminaison de l'API REST (stage `v1`)

URL de base : `https://<api_id>.execute-api.us-east-1.amazonaws.com/v1` (affichée à la fin de `deploy.py`).
Routes protégées : en-tête `Authorization: <IdToken Cognito>`. Le rôle vient du groupe Cognito (`cognito:groups`),
l'agence d'un agent ou responsable de l'attribut `custom:agency_id`, l'identité du citoyen de `sub`.
**Les rôles sont vérifiés dans chaque Lambda**, pas seulement dans l'interface.

| # | Méthode | Chemin | Accès | Lambda | CRUD | Succès | Erreurs principales |
|---|---|---|---|---|---|---|---|
| 1 | GET | `/health` | public | health | – | 200 | – |
| 2 | GET | `/me` | utilisateur connecté | health | – | 200 | 401 |
| 3 | GET | `/agencies` | public | agencies | Read | 200 | – |
| 4 | GET | `/agencies/{agency_id}` | public | agencies | Read | 200 | 400, 404 `AGENCY_NOT_FOUND` |
| 5 | GET | `/agencies/{agency_id}/services` | public | agencies | Read | 200 | 400, 404 |
| 6 | POST | `/agencies/{agency_id}/services` | MANAGER de l'agence | agencies | Create | 201 | 400, 401, 403, 404, 409 `SERVICE_EXISTS` |
| 7 | PUT | `/agencies/{agency_id}/services/{letter}` | MANAGER de l'agence | agencies | Update | 200 | 400, 401, 403, 404 `SERVICE_NOT_FOUND` |
| 8 | DELETE | `/agencies/{agency_id}/services/{letter}` | MANAGER de l'agence | agencies | Delete | 204 | 401, 403, 404, 409 `SERVICE_HAS_WAITING_TICKETS` |
| 9 | POST | `/tickets` | CITIZEN | tickets | Create | 201 | 400, 401, 403, 404, 409 `TICKET_ALREADY_ACTIVE` |
| 10 | GET | `/tickets/{ticket_id}` | CITIZEN propriétaire, AGENT/MANAGER de l'agence | tickets | Read | 200 | 400, 401, 403 `NOT_OWNER`/`WRONG_AGENCY`, 404 |
| 11 | DELETE | `/tickets/{ticket_id}` | CITIZEN propriétaire | tickets | Delete (logique : CANCELLED) | 200 | 401, 403, 404, 409 `INVALID_TRANSITION` |
| 12 | PATCH | `/tickets/{ticket_id}` | AGENT de l'agence | tickets | Update (SERVED / NO_SHOW) | 200 | 400, 401, 403, 404, 409 |
| 13 | POST | `/agencies/{agency_id}/counters/{counter_id}/call-next` | AGENT de l'agence | queue | Update | 200 ou 204 | 401, 403, 404 `COUNTER_NOT_FOUND`, 409 |
| 14 | GET | `/display?agency_id=` | public | display | Read | 200 | 400, 404 |

Chaque route accepte aussi `OPTIONS` (pré-vol CORS, intégration MOCK).

### 3.1 Exemples de requêtes et réponses

**POST /tickets** (citoyen)

```json
// requête
{"agency_id": "AG-TUN-03", "service": "A", "cin": "11223344"}

// 201 Created
{"ticket_id": "AG-TUN-03_20261026_A-009", "agency_id": "AG-TUN-03",
 "agency_name": "Municipalité Tunis-Centre (fictive)", "service": "LEGALISATION", "number": "A-009",
 "status": "WAITING", "priority": "NORMAL", "created_at": "2026-10-26T09:12:03Z",
 "updated_at": "2026-10-26T09:12:03Z", "position": 9, "estimated_wait_min": 18}
```

`position` = nombre de tickets arrivés avant dans la même file + 1 ;
`estimated_wait_min` = position × durée moyenne du service ÷ nombre de guichets ouverts pour ce service (arrondi au supérieur).
Le CIN, son empreinte, `owner_sub` et `queue_key` ne sont **jamais** renvoyés.

**GET /tickets/AG-TUN-03_20261026_A-009** → 200, même format avec la position à jour.

**DELETE /tickets/AG-TUN-03_20261026_A-009** (propriétaire) → 200, `"status": "CANCELLED"`, `"position": 0`.

**POST /agencies/AG-TUN-03/counters/C01/call-next** (agent)

```json
// 200 OK : ticket appelé
{"ticket_id": "AG-TUN-03_20261026_A-001", "number": "A-001", "status": "CALLED", "counter_id": "C01",
 "called_at": "2026-10-26T09:15:40Z", "...": "..."}
// 204 No Content : file vide
```

**PATCH /tickets/AG-TUN-03_20261026_A-001** (agent)

```json
{"status": "SERVED"}          // ou "NO_SHOW"
// 200 OK : {"status": "SERVED", "served_at": "2026-10-26T09:19:02Z", ...}
```

**POST /agencies/AG-TUN-03/services** (responsable)

```json
{"letter": "E", "code": "PERMIS_BATIR", "name": "Permis de bâtir", "hours": "Lun-Ven 08:00-12:00",
 "required_documents": ["Carte d'identité", "Plan du terrain"], "avg_service_min": 10}
// 201 Created : le service créé
```

**PUT /agencies/AG-TUN-03/services/E** → corps partiel, ex. `{"avg_service_min": 12}` → 200, service complet mis à jour.
**DELETE /agencies/AG-TUN-03/services/E** → 204 (corps vide).

**Erreur** (format unique pour toutes les routes) :

```json
// 400 Bad Request
{"error": {"code": "VALIDATION_ERROR", "message": "Champs invalides ou manquants : cin.",
           "request_id": "c0a8f1e2-..."}}
```

---

## 4. Fonctions Lambda

| Lambda | Routes | Réseau | Variables d'environnement |
|---|---|---|---|
| `doura-<stage>-health` | 1, 2 | sous-réseau privé | `STAGE` |
| `doura-<stage>-agencies` | 3 à 8 | sous-réseau privé | `AGENCIES_TABLE`, `TICKETS_TABLE` |
| `doura-<stage>-tickets` | 9 à 12 | sous-réseau privé | `TICKETS_TABLE`, `AGENCIES_TABLE`, `CONTROL_TABLE`, `CIN_SALT` |
| `doura-<stage>-queue` | 13 | sous-réseau privé | `TICKETS_TABLE`, `AGENCIES_TABLE` |
| `doura-<stage>-display` | 14 | sous-réseau privé | `TICKETS_TABLE`, `AGENCIES_TABLE` |
| `doura-<stage>-notifier` | Stream DynamoDB | hors VPC | `TOPIC_ARN` |

Python 3.12, boto3 uniquement, rôle existant **LabRole** (aucun rôle IAM créé, contrainte Learner Lab).
Chaque zip contient `handler.py` et les modules partagés `doura_common.py` (réponses, CORS, journaux, rôles, aiguillage)
et `doura_validation.py` (validation). Une alarme CloudWatch « Errors » est créée pour chaque Lambda.

Structure commune d'un handler :

1. aiguillage sur `(resource, httpMethod)` → route inconnue : **405** ;
2. lecture du corps JSON → **400 `INVALID_JSON`** ;
3. validation des paramètres de chemin et du corps → **400 `VALIDATION_ERROR`** ;
4. contrôle du rôle et de l'agence à partir des claims Cognito → **401 / 403** ;
5. lecture → **404** ;
6. écriture conditionnelle → **409** ;
7. réponse JSON avec en-têtes CORS. Toute exception imprévue → **500 `INTERNAL_ERROR`** avec un message générique ;
   le détail ne va que dans CloudWatch Logs (journaux JSON, champs sensibles masqués).

### 4.1 Prise de ticket sans incohérence

1. validation, existence de l'agence et du service, `cin_hash = HMAC-SHA256(CIN_SALT, cin)` ;
2. verrou `ACTIVE#…` posé avec `attribute_not_exists(pk)` → doublon : **409**, **aucun numéro consommé** ;
3. compteur atomique `ADD last_number :1` → numéro `A-009` (deux requêtes simultanées n'obtiennent jamais le même numéro) ;
4. écriture du ticket WAITING ;
5. si l'étape 3 ou 4 échoue : **compensation**, le verrou est supprimé pour que le citoyen puisse réessayer.

Le verrou est supprimé à l'annulation, au service (SERVED) ou à l'absence (NO_SHOW).
Le sel `CIN_SALT` est généré une seule fois (`secrets.token_hex(32)`) et conservé dans `infra/outputs-<stage>.json`,
fichier ignoré par Git.

### 4.2 Appel du suivant et concurrence

Pour chaque service traité par le guichet, l'index `WaitingQueue` donne les plus anciens tickets du jour ; on prend le plus
ancien de tous (FIFO entre services). Le passage WAITING → CALLED est conditionnel (`status = WAITING`) : si deux agents
appellent en même temps, un seul réussit, l'autre passe au candidat suivant (3 essais maximum, puis **409
`CALL_CONFLICT_RETRY`**).

---

## 5. Correspondance CRUD

| Ressource | Create | Read | Update | Delete |
|---|---|---|---|---|
| Agence | (données initiales `seed_data.py`) | GET `/agencies`, GET `/agencies/{agency_id}` | – | – |
| Service | POST `/agencies/{agency_id}/services` | GET `/agencies/{agency_id}/services` | PUT `/agencies/{agency_id}/services/{letter}` | DELETE `/agencies/{agency_id}/services/{letter}` |
| Ticket | POST `/tickets` | GET `/tickets/{ticket_id}`, GET `/display` | PATCH `/tickets/{ticket_id}`, POST `…/call-next` | DELETE `/tickets/{ticket_id}` (suppression logique : CANCELLED, historique conservé) |

---

## 6. Validation des entrées

Module partagé `backend/common/doura_validation.py` (fonctions pures, testées sans AWS).
Un champ inconnu est refusé ; le message liste **tous** les champs invalides.

| Champ | Règle |
|---|---|
| corps | objet JSON valide, sinon 400 `INVALID_JSON` ; champ inconnu → 400 `VALIDATION_ERROR` |
| `agency_id` | `^AG-[A-Z]{3}-\d{2}$` |
| `letter` | une lettre majuscule `^[A-Z]$` |
| `code` | `^[A-Z_]{3,30}$` |
| `name` | texte de 3 à 80 caractères (espaces de début et de fin retirés) |
| `hours` | texte de 5 à 60 caractères |
| `required_documents` | liste de 0 à 10 textes de 2 à 80 caractères |
| `avg_service_min` | entier de 1 à 120 |
| `cin` | exactement 8 chiffres ; transformé immédiatement en empreinte, jamais stocké ni journalisé |
| `service` (POST /tickets) | lettre d'un service existant de l'agence, sinon 404 `SERVICE_NOT_FOUND` |
| `status` (PATCH) | `SERVED` ou `NO_SHOW` uniquement |
| `ticket_id` | `^AG-[A-Z]{3}-\d{2}_\d{8}_[A-Z]-\d{3}$` |
| `counter_id` | `^C\d{2}$` (ex. `C01`) |

---

## 7. Codes d'erreur

| HTTP | Code | Cas |
|---|---|---|
| 400 | `VALIDATION_ERROR` | champ manquant, mal formé ou inconnu |
| 400 | `INVALID_JSON` | corps non JSON ou pas un objet |
| 401 | `UNAUTHORIZED` | jeton absent ou invalide (autorisateur Cognito ou Lambda) |
| 403 | `FORBIDDEN` | rôle non autorisé pour cette action |
| 403 | `NOT_OWNER` | le ticket appartient à un autre citoyen |
| 403 | `WRONG_AGENCY` | l'agent ou le responsable n'est pas rattaché à cette agence |
| 404 | `AGENCY_NOT_FOUND`, `SERVICE_NOT_FOUND`, `TICKET_NOT_FOUND`, `COUNTER_NOT_FOUND` | ressource inexistante |
| 405 | `METHOD_NOT_ALLOWED` | méthode non prévue sur cette ressource |
| 409 | `SERVICE_EXISTS` | service déjà créé avec cette lettre |
| 409 | `SERVICE_HAS_WAITING_TICKETS` | suppression d'un service ayant des tickets en attente aujourd'hui |
| 409 | `TICKET_ALREADY_ACTIVE` | le citoyen a déjà un ticket actif pour ce service aujourd'hui |
| 409 | `INVALID_TRANSITION` | changement de statut interdit, ou guichet fermé |
| 409 | `CALL_CONFLICT_RETRY` | 3 tickets pris par d'autres guichets pendant l'appel |
| 500 | `INTERNAL_ERROR` | erreur imprévue : message générique, détail dans CloudWatch Logs |

---

## 8. Sécurité et données personnelles

- Authentification Cognito (groupes CITIZEN, AGENT, MANAGER, ADMIN) ; autorisation vérifiée dans chaque Lambda.
- Le CIN est remplacé par une empreinte HMAC-SHA256 salée dès la validation ; il n'apparaît ni en base, ni dans les réponses,
  ni dans les journaux (masquage automatique de `cin`, `cin_hash`, `token`, `password` dans `doura_common.log`).
- Les Lambdas d'accès aux données tournent dans un sous-réseau privé sans accès Internet ; DynamoDB est atteint par un
  endpoint de passerelle.
- CORS sur toutes les réponses, y compris les erreurs renvoyées par API Gateway elle-même.

---

## 9. Tests

### 9.1 Tests automatiques (pytest + moto, aucun appel AWS réel)

```
pytest -q
69 passed
flake8      -> aucune erreur
```

| Fichier | Ce qui est vérifié |
|---|---|
| `tests/unit/test_validation.py` | chaque règle de validation, JSON invalide, champs inconnus, mise à jour partielle |
| `tests/unit/test_agencies.py` | lectures publiques, CRUD des services, 400 par champ, 401/403 (citoyen, agent, autre agence), 404, 409 doublon et tickets en attente, 405 |
| `tests/unit/test_tickets.py` | création (numéro, position, attente estimée), doublon 409 sans numéro consommé, compensation si l'écriture échoue (verrou libéré, 500 générique), **50 créations successives → 50 numéros consécutifs distincts**, droits de lecture (propriétaire, agent, autre citoyen, autre agence), annulation, SERVED / NO_SHOW et transitions interdites |
| `tests/unit/test_queue.py` | ordre FIFO, FIFO entre services, 204 file vide, conflit → candidat suivant, 409 après 3 conflits, guichet fermé, mauvaise agence, guichet inconnu |
| `tests/unit/test_demo_script.py` | script de démonstration des notifications |
| `tests/infra/test_deploy_moto.py` | déploiement complet **exécuté deux fois** sans doublon (tables, 6 Lambdas, 13 ressources API, autorisateur, alarmes, sel CIN conservé) |

Chaque test d'API vérifie aussi qu'aucun CIN ni empreinte n'apparaît dans les réponses.

### 9.2 Test sur AWS Learner Lab

```bash
python infra/deploy.py --stage <nom>        # crée ou met à jour toute la plateforme, puis smoke test
python scripts/try_api.py --stage <nom>     # scénario complet sur l'API réelle
```

Le smoke test vérifie `/health`, `/agencies`, `/agencies/AG-TUN-03`, `/display`, et que `/me` et `POST /tickets` sans jeton
renvoient 401. `scripts/try_api.py` connecte les utilisateurs fictifs (Cognito `USER_PASSWORD_AUTH`) et enchaîne une quarantaine
d'appels : création de ticket, doublon 409, position, appel du suivant, SERVED, annulation, CRUD d'un service par le
responsable, et les cas 400 / 401 / 403 / 404 / 409. Chaque ligne affiche le statut obtenu et le statut attendu.

---

## 10. Vérification dans la console AWS

Les ressources sont **créées uniquement par code** (Boto3, `infra/deploy.py`) ; la console sert à vérifier et à faire les
captures. Une modification manuelle s'écarterait du code et serait dupliquée au prochain déploiement.
Actions faites dans la console : démarrer le Learner Lab et copier les identifiants ; désactiver le blocage d'accès public S3
du compte uniquement si `deploy.py` le signale ; confirmer l'abonnement SNS reçu par e-mail.

| Capture | Vue de la console |
|---|---|
| ![](captures/dynamodb_tables.png) | DynamoDB → Tables : les 3 tables `doura-<stage>-*` |
| ![](captures/dynamodb_indexes.png) | table tickets → Index : `WaitingQueue` et `AgencyCalled` |
| ![](captures/dynamodb_items.png) | table tickets → Explorer les éléments après le scénario `try_api.py` |
| ![](captures/lambda_list.png) | Lambda → Fonctions : `doura-<stage>-*` |
| ![](captures/apigw_resources.png) | API Gateway → `doura-<stage>-api` → arborescence des ressources et méthodes |
| ![](captures/apigw_authorizer.png) | Autorisateurs : `doura-<stage>-cognito` (user pool Cognito) |
| ![](captures/apigw_test.png) | bouton Tester sur `GET /agencies/{agency_id}` avec la réponse 200 |
| ![](captures/apigw_stage.png) | Étapes → `v1` avec l'URL d'appel |
| ![](captures/cloudwatch_logs.png) | CloudWatch Logs de la Lambda tickets (journaux JSON, aucun CIN) |
| ![](captures/try_api_output.png) | sortie terminal de `scripts/try_api.py` (scénario CRUD, cas 400/403/409) |
