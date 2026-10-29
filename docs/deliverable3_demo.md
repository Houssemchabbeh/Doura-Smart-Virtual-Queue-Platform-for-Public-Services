# Deliverable 3 – Demo walkthrough

End-to-end walkthrough of the DynamoDB data model and the REST API (about 12 minutes).
Run every command in **one PowerShell window**, from the repository root. Set your own stage name once:

```powershell
$STAGE = "houssem"   # your stage name (resources are named doura-<stage>-...)
```

## 0. Preparation

1. Start the Learner Lab, copy **AWS Details → AWS CLI** into `%USERPROFILE%\.aws\credentials` and save.
2. Check access, deploy and load the outputs:

```powershell
python -m infra.check_access                                   # must show [ok] everywhere
$env:DOURA_DEMO_EMAIL = "<your-email>"                         # receives the SNS notifications
python infra/deploy.py --stage $STAGE                          # deploys everything + smoke test
$O = Get-Content infra\outputs-$STAGE.json | ConvertFrom-Json
$API = $O.api_url
$O.display_url                                                 # open this URL in a browser tab
```

3. If an "AWS Notification – Subscription Confirmation" e-mail arrives, click **Confirm subscription**.
4. Rehearse the full scenario: `python scripts/try_api.py --stage $STAGE` – it must end with `0 écart(s)`.
5. Reset the demo data: `python infra/deploy.py --stage $STAGE --only seed`.
6. Open in advance: the display (Écran) URL, and the DynamoDB, API Gateway, Lambda and CloudWatch Logs consoles.

## 1. Architecture and data model (2 min, console)

- **DynamoDB → Tables**: the 3 `doura-<stage>-*` tables.
- **tickets → Indexes**: `WaitingQueue` and `AgencyCalled`.
- **tickets → Explore items**: open a ticket. The `ticket_id` looks like `AG-TUN-03_20261029_A-001`, and only a
  `cin_hash` is stored – never the CIN itself.
- **agencies → Explore items**: `META`, `SERVICE#A`, `COUNTER#C01` items of an agency live in the same table.

## 2. Everything is built by code (1 min)

```powershell
python infra/deploy.py --stage $STAGE --skip-seed
```

Every step reports *existe déjà* or *à jour*: re-running creates no duplicates, and the smoke test passes at the end.
Then open **API Gateway → doura-<stage>-api → Resources** to see the route tree.

## 3. Public API calls (1 min)

```powershell
Invoke-RestMethod "$API/health"
Invoke-RestMethod "$API/agencies/AG-TUN-03" | ConvertTo-Json -Depth 5
Invoke-RestMethod "$API/agencies/AG-TUN-03/services" | ConvertTo-Json -Depth 5
curl.exe -s "$API/agencies/tunis"                    # 400 VALIDATION_ERROR
curl.exe -s "$API/agencies/AG-XXX-99"                # 404 AGENCY_NOT_FOUND
curl.exe -s -X POST "$API/tickets" -d "{}"           # 401: no token, blocked by Cognito
```

Every error uses the same format: `{"error": {"code", "message", "request_id"}}`.

## 4. Full live scenario (3 min)

```powershell
python scripts/try_api.py --stage $STAGE
```

The output is grouped by role:

- **Citoyen**: ticket created (number, position, estimated wait), duplicate refused (409), invalid CIN (400),
  an agent cannot take a ticket (403).
- **Agent**: call-next, SERVED, forbidden transition (409), agent from another agency (403).
- **Responsable**: service E created, modified and deleted; deletion is refused while a ticket is waiting.

The SERVED ticket also triggers an e-mail notification.

## 5. Notifications and public display (2 min)

Keep the display (Écran) tab and the mailbox side by side:

```powershell
python scripts/demo_notification.py --stage $STAGE status            # today's queue
python scripts/demo_notification.py --stage $STAGE call --count 3    # the display updates
python scripts/demo_notification.py --stage $STAGE serve             # last called ticket -> SERVED -> e-mail
python scripts/demo_notification.py --stage $STAGE cancel --number A-007   # -> CANCELLED -> e-mail
```

Flow: ticket change → DynamoDB Stream → `notifier` Lambda → SNS → e-mail.

## 6. Monitoring and privacy (1 min, console)

- **CloudWatch → Log groups → `/aws/lambda/doura-<stage>-tickets`**: structured JSON logs (`ticket_created`,
  `ticket_closed`), with no CIN anywhere.
- **CloudWatch → Alarms**: one error alarm per Lambda.

## 7. Automated tests (1 min)

```powershell
.\.venv\Scripts\python -m pytest -q        # 69 passed
.\.venv\Scripts\python -m flake8           # no output = clean
```

Shorter alternative: `.\.venv\Scripts\python -m pytest -q tests/unit/test_tickets.py` (50 consecutive tickets,
duplicates, compensation after a failed write).
The `.venv` is created with `python -m venv .venv` then `.\.venv\Scripts\python -m pip install -r requirements-dev.txt`.

## Troubleshooting

| Problem | Fix |
|---|---|
| `ExpiredToken` | Copy fresh Learner Lab credentials, then re-run the command. |
| `status` shows no tickets / `call` says "Personne en attente" | `python infra/deploy.py --stage $STAGE --only seed` |
| No e-mail | Check spam; otherwise set `$env:DOURA_DEMO_EMAIL`, run `python infra/deploy.py --stage $STAGE --only sns` and confirm the subscription. |
| `try_api.py` shows KO on "Suppression refusée" | The index lagged by a moment; re-run the script. |
| `No module named boto3` | `python -m pip install -r requirements.txt` |
