# Verdity — Systeme d'Analyse Automatisee de Pull Requests

> **Verdity** — contraction de *verdict* + *fidelite/integrite*. Chaque constatation est un **verdict** (structure, justifie, note de confiance), livre avec **integrite** (calibre, valide, auditable, jamais publie automatiquement sans l'avoir merite).

---

## Sommaire

- [Presentation](#presentation)
- [Architecture du systeme](#architecture-du-systeme)
- [Composants](#composants)
- [Contraintes non-negociables](#contraintes-non-negociables)
- [Structure du projet](#structure-du-projet)
- [Demarrage rapide](#demarrage-rapide)
- [Securite](#securite)
- [Tests](#tests)
- [Configuration](#configuration)
- [Phases de developpement](#phases-de-developpement)
- [Integrations](#integrations)
- [An concurrentielle](#analyse-concurrentielle)
- [Licence](#licence)

---

## Presentation

Verdity est un systeme d'analyse automatisee de pull requests base sur l'intelligence artificielle. Il est concu pour reviewer le code de maniere structuree, avec un systeme de notation de confiance deterministe, une verification independante, et un modele de securite robuste.

### Caracteristiques principales

- **4 agents specialises** : Securite, Qualite de code, Tests, Documentation
- **Verification independante** : chaque constatation passe par une porte de verification avant publication
- **Mode de correction agentic** : generation automatisee de correctifs avec creation de PR
- **Integration MCP** : serveur Model Context Protocol expose 8 outils pour Claude Desktop, Cursor et VS Code
- **Index semantique complet** : indexation du code complet avec extraction de symboles (Python, JS, Go, Rust)
- **Regles de review personnalisees** : configuration YAML via `.verdity/rules.yml`
- **Economie de tokens** : comptage par appel avec quotas et signaux de degradation
- **Journal d'audit** : log append-only avec checksums SHA-256 d'integrite

---

## Architecture du systeme

```
[GitHub] ──(HTTPS)──▶ [Passerelle d'entree] ──▶ [File d'evenements] ──▶ [Orchestrateur]
                                                │                       │
                                                │               ┌───────┴───────┐
                                                │               ▼               ▼
                                                │         [Index semantique] [Specialistes]
                                                │               │           (parallele)
                                                │               │              │
                                                │               ▼              ▼
                                                │         [Aggregateur]  [Agent de codage]
                                                │               │              │
                                                │               ▼              ▼
                                                │         [Routeur]      [Porte de verification]
                                                │               │              │
                                                │               ▼              ▼
                                                │         [File d'approbation] [Tests de regression]
                                                │               │
                                                └───────────────┼──▶ [Magasin d'audit]
                                                                ▼
                                                          [Economie de tokens]
```

### Flux de traitement

1. **Reception** : La passerelle recoit un webhook GitHub, verifie la signature HMAC-SHA256, et place l'evenement dans la file.
2. **Mise en file** : La file d'evenements SQLite durable garantit un traitement au moins une fois,/partitionnee par depot.
3. **Orchestration** : L'orchestrateur lance les 4 agents specialises en parallele avec isolation des delais.
4. **Analyse semantique** : L'index semantique fournit le contexte du code complet a tous les agents.
5. **Agregation** : L'aggregateur dedoublonne, resout les conflits, et classe les constatations.
6. **Routage** : Le routeur calcule un score de confiance et decide (approbation automatique / revue manuelle / rejet).
7. **Verification** : La porte de verification valide que le code compile, passe le lint, ne contient pas de nouveaux secrets.
8. **Audit** : Chaque constatation et decision est enregistree dans le magasin d'audit append-only.

---

## Composants

| Composant | Fichier | Responsabilite |
|-----------|---------|----------------|
| **Passerelle d'entree** | `gateway/app.py` | Verification HMAC → mise en file → audit. Sans etat, reponse <1s. |
| **File d'evenements** | `event_queue.py` | File durable au-moins-une-fois, partitionnee par depot. |
| **Orchestrateur** | `orchestrator.py` | Workflow durable : dispersion/recolte des specialistes en parallele. |
| **Index semantique** | `semantic_index.py` | Embeddings partages + graphe de symboles + re-indexation incremental. |
| **Agent Securite** | `agents/security.py` | Analyse en 3 passes : secrets, recherche semantique, vulnerabilites de diff. |
| **Agent Qualite** | `agents/code_quality.py` | Detection de patterns de style/maintenabilite. |
| **Agent Tests** | `agents/testing.py` | Detection des lacunes de couverture de tests. |
| **Agent Documentation** | `agents/documentation.py` | Detection de docstrings/CHANGELOG/briseurs d'API. |
| **Aggregateur** | `aggregator.py` | Dedoublonnage, resolution de conflits, classement (deterministe). |
| **Routeur** | `router.py` | Notation de confiance multi-signaux → routage. |
| **File d'approbation** | `approval_queue.py` | Magasin persistant pour les constatations sous le seuil. |
| **Agent de codage** | `coding_agent.py` | Generation de correctifs regles + mode agentic. |
| **Porte de verification** | `verification_gate.py` | Porte : compile → lint → pas de nouveaux secrets → verificateur independant. |
| **Economie de tokens** | `token_economics.py` | Comptage par appel + quotas + signaux de degradation. |
| **Application de budget** | `budget_enforcer.py` | Surveillance des depenses en temps reel ; abandonne les specialistes optionnels avant la securite. |
| **Verification HMAC** | `hmac_verify.py` | Verification de signature a temps constant avec rotation a double secret. |
| **Normaliseur de webhook** | `webhook_normalizer.py` | Evenement GitHub → schema `VerdityEvent`. |
| **Magasin d'audit** | `audit_store.py` | Journal append-only avec checksums SHA-256 par enregistrement. |
| **Client GitHub** | `github_client.py` | Client async pour GitHub API (webhooks, PRs, comments). |
| **Regles de review** | `review_rules.py` | Regles personnalisees via `.verdity/rules.yml`. |
| **Serveur MCP** | `mcp_server.py` | Serveur Model Context Protocol exposant 8 outils. |
| **Agent multi-modele** | `model_fallback.py` | Fallback automatique entre modeles LLM avec cooldown et backoff exponentiel. |
| **Limiteur de debit** | `rate_limiter.py` | Limitation de debit adaptative avec etat par cible. |

---

## Contraintes non-negociables

La violation de l'une de ces regles est un **echec de build**, pas un choix de style.

| # | Contrainte | Verification |
|---|-----------|-------------|
| 1 | Chaque webhook verifie HMAC-SHA256 avec comparaison a temps constant avant analyse | `hmac_verify.py`, tests de la passerelle |
| 2 | L'entree est decouplee du traitement via une file durable | La passerelle n'appelle que `queue.publish()` |
| 3 | Les specialistes s'executent en parallele ; un delai/echec ne bloque jamais les autres | `asyncio.gather`, tests d'isolation des delais |
| 4 | Un seul index semantique partage — pas de magasins prives par agent | Tous les agents recoivent un `SemanticIndex` injecte |
| 5 | Les scores de confiance calcules par code deterministe, jamais par auto-rapport LLM | `_compute_secret_confidence()`, tests du routeur |
| 6 | Les changements de code passent : porte → verificateur independant → regression, dans cet ordre | `VerificationGate`, `VerifierSubagent` sont des classes separees |
| 7 | Les constatations sous le seuil ne sont jamais publiees automatiquement ; vont toujours a la file d'approbation | Le routeur routage par seuil de score |
| 8 | Chaque appel de modele est mesure via `TokenEconomicsService` | Tous les agents appellent `record_call()` |
| 9 | Chaque constatation et decision d'approbation est enregistree dans le magasin d'audit | Tous les agents ecrivent dans `AuditStore` |

---

## Structure du projet

```
verdity/
├── src/verdity/
│   ├── __init__.py                 # Racine du package, version 0.3.0
│   ├── config.py                   # pydantic-settings ; tous les secrets depuis env/KMS
│   ├── async_sqlite.py             # sqlite3 stdl emballe avec asyncio
│   ├── schemas/
│   │   ├── __init__.py             # Re-exports (Severity, Finding, etc.)
│   │   └── _models.py              # Tous les modeles de donnees Pydantic
│   ├── hmac_verify.py              # HMAC-SHA256 + rotation a double secret
│   ├── webhook_normalizer.py       # Evenement GitHub → TriggerType
│   ├── event_queue.py              # File SQLite durable
│   ├── audit_store.py              # Journal d'audit append-only avec checksums sha256
│   ├── token_economics.py          # Comptage par appel + application de budget
│   ├── semantic_index.py           # Embeddings partages + graphe de symboles + re-indexation
│   ├── orchestrator.py             # Workflow durable avec dispersion/recolte
│   ├── aggregator.py               # Dedoublonnage, resolution de conflits, classement
│   ├── router.py                   # Notation de confiance + decisions de routage
│   ├── approval_queue.py           # Magasin de file d'approbation persistant
│   ├── coding_agent.py             # Generation de correctifs deterministes + mode agentic
│   ├── review_rules.py             # Regles de review personnalisees (.verdity/rules.yml)
│   ├── mcp_server.py               # Serveur Model Context Protocol (8 outils)
│   ├── verification_gate.py        # Porte de verification + verificateur independant + regression
│   ├── budget_enforcer.py          # Surveillance des depenses + signaux de degradation
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── security.py             # Specialiste securite (analyse en 3 passes)
│   │   ├── code_quality.py         # Specialiste qualite de code
│   │   ├── testing.py              # Specialiste tests
│   │   └── documentation.py        # Specialiste documentation
│   └── gateway/
│       └── app.py                  # Passerelle d'entree FastAPI
├── tests/                          # 409 tests, couverture 100% enforcee
├── dev-notes/                      # Notes de developpement et modeles menaces
├── .env.example                    # Modele — JAMAIS commiter de vrais secrets
├── pyproject.toml                  # Configuration du projet, parametres pytest
└── README.md                       # Ce fichier
```

---

## Demarrage rapide

```bash
# Creer un environnement virtuel
python -m venv .venv
source .venv/bin/activate  # ou .venv\Scripts\activate sur Windows

# Installer les dependances
pip install -e ".[dev]"

# Definir les variables d'environnement requises
cp .env.example .env
# Modifier .env — JAMAIS commiter le vrai fichier

# Lancer la suite de tests complete (100% de couverture requise)
pytest -v

# Demarrer la passerelle (developpement)
uvicorn verdity.gateway.app:app --reload --port 8000
```

### Variables d'environnement requises

| Variable | Requise | Description |
|----------|---------|-------------|
| `WEBHOOK_HMAC_SECRET` | Oui | Secret HMAC-SHA256 pour la verification des webhooks GitHub |
| `WEBHOOK_HMAC_SECRET_PREVIOUS` | Non | Secret precedent pendant la rotation (vide hors rotation) |
| `GITHUB_APP_ID` | Oui | ID numerique de l'application GitHub |
| `GITHUB_APP_INSTALLATION_ID` | Oui | ID d'installation pour le depot cible |
| `GITHUB_APP_PRIVATE_KEY` | Oui | Cle privee PEM pour l'application GitHub |

---

## Securite

Voir **[SECURITY.md](SECURITY.md)** pour le modele de menaces complet, le guide de durcissement, et l'analyse STRIDE.

### Controles de securite mis en oeuvre

| Controle | Implementation |
|----------|---------------|
| Verification HMAC | `hmac.compare_digest` — temps constant, pas de contournement |
| Protection contre les rejeux | Cache de dedoublonnage par ID de livraison avec TTL de 24h |
| Gestion des secrets | pydantic `SecretStr` ; tous les secrets depuis env/KMS |
| Integrite de l'audit | Checksum SHA-256 par enregistrement d'audit |
| Validation de sortie | Toutes les constatations passent la validation de schema Pydantic |
| Isolation des locataires | Tous les magasins partitionnes par `repo_id` |
| Application du budget | Surveillance des depenses en temps reel avec signaux de degradation |
| Verification independante | `VerifierSubagent` est separe de `CodingAgent` |
| En-tetes de securite | HSTS, CSP, X-Content-Type-Options sur toutes les reponses |
| Assainissement des chemins | Rejette les parcours, chemins absolus, octets nuls |

### Signalement de problemes de securite

Veuillez signaler les vulnerabilites de securite via GitHub Security Advisory, pas via les issues publiques.

---

## Tests

```bash
# Lancer tous les tests avec couverture (echec si <100%)
pytest -v

# Lancer un seul fichier de test
pytest tests/test_hmac_verify.py -v

# Lancer avec rapport de couverture
pytest --cov=src/verdity --cov-report=html
open htmlcov/index.html
```

### Couverture de tests

**100% de couverture enforcee** sur les 409 tests dans 15 fichiers de test.

| Module | Couverture |
|--------|------------|
| Tous les modules | 100% |
| Instructions totales | 2 445 / 2 445 |
| Tests | 409 en succes |

---

## Configuration

Toute la configuration se fait via des variables d'environnement (voir `.env.example`). En production, les secrets doivent etre injectes depuis un magasin de secrets gere (AWS Secrets Manager, Azure Key Vault, HashiCorp Vault) — jamais depuis des fichiers `.env`.

### Chemins de base de donnees

Toutes les bases SQLite sont par defaut a `:memory:` pour les tests. Pour un stockage persistant :

```bash
QUEUE_SQLITE_PATH=/var/lib/verdity/queue.db
AUDIT_SQLITE_PATH=/var/lib/verdity/audit.db
```

Les bases de test sont automatiquement creees dans un repertoire temporaire et nettoyees apres chaque execution.

---

## Phases de developpement

| Phase | Composant | Statut |
|-------|-----------|--------|
| 1 | Passerelle d'entree, File d'evenements, Schemas, Magasin d'audit, Economie de tokens | Termine |
| 2 | Index semantique (embeddings + graphe de symboles + re-indexation incremental) | Termine |
| 3 | Orchestrateur + Agent specialiste Securite | Termine |
| 4 | Agents Qualite, Tests, Documentation + Aggregateur | Termine |
| 5 | Routeur de confiance + File d'approbation | Termine |
| 6 | Agent de codage + Porte de verification + Verificateur independant | Termine |
| 7 | Application de budget + Signaux de degradation | Termine |
| 8 | Durcissement + Validation du modele de menaces STRIDE | Termine |

**Total : 409 tests en succes, 100% de couverture enforcee.**

---

## Integrations

### MCP (Model Context Protocol)

Verdity expose un serveur MCP avec 8 outils utilisables depuis Claude Desktop, Cursor, ou VS Code :

| Outil | Description |
|-------|-------------|
| `review_security` | Analyse de securite d'un diff |
| `review_quality` | Analyse de qualite de code |
| `review_testing` | Analyse de couverture de tests |
| `review_documentation` | Analyse de documentation |
| `review_full` | Analyse complete multi-agents |
| `generate_fix` | Generation de correctif pour une constatation |
| `apply_fix` | Application de correctif et creation de PR |
| `get_review_rules` | Recuperation des regles de review actives |

### GitHub App

Verdity s'integre comme application GitHub, recevant les webhooks de pull request et y repondant avec des commentaires structures et des notes de confiance.

### Modes d'execution

```bash
# Mode worker (traitement en arriere-plan)
verdity-worker --queue sqlite:///queue.db --audit audit.db

# Mode passerelle (API HTTP)
uvicorn verdity.gateway.app:app --host 0.0.0.0 --port 8000
```

---

## Analyse concurrentielle

Verdity se positionne sur le marche des outils de revue de code IA (2026) :

| Critere | Verdity | CodeRabbit | Copilot Review | Greptile |
|---------|---------|------------|----------------|----------|
| Verification independante | Oui | Non | Non | Non |
| Audit SHA-256 | Oui | Non | Non | Non |
| Economie de tokens | Oui | Non | Non | Non |
| Contexte code complet | Oui | Partiel | Non | Oui |
| Integration MCP | Oui | Non | Non | Non |
| Mode agentic | Oui | Partiel | Non | Non |
| Agents specialises | 4 | 1 | 1 | 1 |
| Modeles multiples | Oui | Non | Non | Non |

### Points forts de Verdity

- **Seul outil** combinant economie de tokens, integrite d'audit SHA-256, porte de verification independante, et architecture a agents specialises
- **Contexte du code complet** avec extraction de symboles et graphe de dependances
- **Integration MCP** pour Claude Desktop, Cursor, VS Code
- **Mode agentic** avec generation de correctifs et creation automatique de PR

### Lacunes identifiees (vs. leaders du marche)

- Metriques d'ingenierie (deploiement,MTTR, etc.) — prevu v0.4.x
- Multi-plateforme (GitLab, Bitbucket) — prevu v0.4.x
- Resolution de confiance avant publication — prevu v0.5.x

---

## Version

- **Version actuelle** : 0.3.1 (2 septembre 2026)
- **Precedente** : 0.3.0 (1 septembre 2026)
- **Licence** : MIT

---

## Licence

MIT License — voir [LICENSE](LICENSE).
