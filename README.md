<div align="center">

# 🗺️ CodeMap

**An Agentic RAG Platform for Deep Codebase Intelligence & Graph Reasoning**

*Ingest entire repositories. Traverse their architecture with graph algorithms. Act on connected code.*

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev/)
[![FalkorDB](https://img.shields.io/badge/FalkorDB-GraphBLAS-FF4500?style=flat-square)](https://www.falkordb.com/)
[![Qdrant](https://img.shields.io/badge/Qdrant-Vector%20DB-DC2626?style=flat-square&logo=qdrant&logoColor=white)](https://qdrant.tech/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Caddy](https://img.shields.io/badge/Caddy-Reverse%20Proxy-1F88C0?style=flat-square&logo=caddy&logoColor=white)](https://caddyserver.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

**🏆 Graph Hacks: Context for AI Agents (Oct 15–18, 2026)**
* **Primary Track**: **Agents That Act on Connected Data**
* **Secondary Track**: **Company Brain**

*Submitted to [Graph Hacks](https://www.wemakedevs.org/hackathons/falkordb) by WeMakeDevs × FalkorDB*

</div>

---

## 🎥 Demo Video

> 📺 **Watch the Working Demo**: [CodeMap Walkthrough Video](https://drive.google.com/file/d/17Na4SIqYuEcexcC9wgRTmF8899aPkcfr/view?usp=sharing) *(Replace with your YouTube/Loom demo link)*

---

## 📖 Overview

**CodeMap** is a production-grade agentic Codebase Intelligence platform designed to understand and act on complex software architectures. Standard code RAG pipelines treat source code like plain text — naive chunking splits functions arbitrarily, destroys lexical scoping, and blinds models to call hierarchies and dependency flows.

**CodeMap** solves this by combining **AST / SCIP compiler indexing**, a **FalkorDB knowledge graph**, and **Qdrant vector retrieval** with an autonomous **LangGraph agent**:

* **Structural Graph Traversal**: Maps classes, functions, callers, callees, and modules into an isolated graph per repository.
* **Graph Algorithms as Agent Tools**: Employs FalkorDB's native GraphBLAS algorithms (PageRank, Betweenness Centrality, WCC, Shortest Path) so the agent can trace execution paths, pinpoint single-points-of-failure, and assess refactor blast radius in sub-20ms.
* **Explainable Reasoning**: Automatically visualizes call chains and dependency flows with interactive Mermaid diagrams in chat.
* **Graph-to-Action with MCP**: Bridges graph insights with real-world developer actions — identifying dead code or architectural risks and autonomously opening tracking issues or branches via the Model Context Protocol (MCP).

---

## ✨ Key Features

| Feature | Description |
|---|---|
| 🕸️ **FalkorDB Multi-Tenant Graph** | Strict physical graph isolation per repository (`select_graph(kb_id)`). GraphBLAS sparse matrix linear algebra delivers sub-20ms multi-hop traversals. |
| 🧮 **Native Graph Algorithms** | Built-in agent tools powered by PageRank, Betweenness Centrality, Weakly Connected Components (WCC), and Shortest Path. |
| 🔍 **Dual Hybrid RAG** | Combines topological graph traversal (FalkorDB) with high-density semantic vector search (Qdrant + `voyage-code-3`). |
| 🌳 **SCIP & Tree-Sitter Indexing** | Compiler-level symbol resolution (SCIP) and multi-language AST chunking (Python, TypeScript, JavaScript, Java, Go, Rust). |
| 🔌 **On-Demand MCP Tool Gating** | Model Context Protocol integration with granular server-level toggles to prevent context bloat and token consumption. |
| 📊 **Explainable Visual Output** | Structured Mermaid flowcharts generated dynamically in chat along with a real-time `ToolTraceTimeline` in the UI. |
| 🤖 **Multi-LLM Provider** | Decoupled model runtime supporting Mistral, Groq, Ollama, OpenAI, and Anthropic. |
| ⚡ **Streaming & Active Middleware** | Token-by-token Server-Sent Events (SSE), automatic context summarization, and robust tool validation middleware. |
| 🚀 **Asynchronous Ingestion** | Celery background workers handle cloning, AST parsing, and graph construction without blocking the web event loop. |

---

## 🏗️ Architecture

```
                                      ┌────────────────────────────────────────────────────────┐
                                      │                     React 18 UI                        │
                                      │   (ToolTraceTimeline, Mermaid Graphs, SSE Stream)      │
                                      └───────────────────────────┬────────────────────────────┘
                                                                  │ HTTP / SSE
                                                                  ▼
                                      ┌────────────────────────────────────────────────────────┐
                                      │                    Caddy Reverse Proxy                 │
                                      │             (Automatic TLS, SSE Passthrough)           │
                                      └───────────────────────────┬────────────────────────────┘
                                                                  │
                                                                  ▼
                                      ┌────────────────────────────────────────────────────────┐
                                      │                   FastAPI Application                  │
                                      │     - JWT Auth (Redis Whitelist)                       │
                                      │     - Active Tool Error & Sanitization Middleware      │
                                      │     - LangGraph Agentic Workflow                       │
                                      └─────────────┬────────────────────────────┬─────────────┘
                                                    │                            │
                     ┌──────────────────────────────┘                            └──────────────────────────────┐
                     ▼                                                                                          ▼
       ┌───────────────────────────┐                                                              ┌───────────────────────────┐
       │     LangGraph Agent       │                                                              │      Celery Workers       │
       │   - Prompt Engine (XML)   │                                                              │   - Async Repo Cloner     │
       │   - Context Summarizer    │                                                              │   - Tree-Sitter & SCIP    │
       │   - MCP Dynamic Registry  │                                                              │   - Batch Chunk Embedder  │
       └─────────────┬─────────────┘                                                              └─────────────┬─────────────┘
                     │                                                                                          │
        ┌────────────┴───────────────────────────┐                                                 ┌────────────┴─────────────┐
        ▼                                        ▼                                                 ▼                          ▼
┌───────────────┐                        ┌───────────────┐                                 ┌───────────────┐          ┌───────────────┐
│   FalkorDB    │                        │    Qdrant     │                                 │  PostgreSQL   │          │     Redis     │
│  Multigraph   │                        │ Vector Store  │                                 │ SQLModel /    │          │ Broker, Cache │
│ (GraphBLAS)   │                        │ (voyage-code) │                                 │ Metadata DB   │          │ & JWT List    │
└───────────────┘                        └───────────────┘                                 └───────────────┘          └───────────────┘
```

---

## 🧠 FalkorDB Graph Tools

CodeMap packages native FalkorDB algorithms as first-class agent tools:

### 1. `trace_call_graph` (Directional Traversal)
Traces upstream callers or downstream callees across multi-hop dependencies with configurable depth, returning execution paths and generating Mermaid dependency diagrams.

### 2. `trace_execution_path` (Shortest Path)
Finds the shortest execution chain between any two symbols using FalkorDB's native path search (`RETURN shortestPath(...)`).
> *"How does the API route `POST /checkout` eventually invoke `stripe.charge`?"*

### 3. `assess_refactor_risk` (Composite Blast Radius)
Combines multi-hop BFS caller reachability with PageRank scores to calculate a concrete risk card:
* Direct callers vs. transitive downstream consumers
* Peak PageRank authority impacted
* Risk classification (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`)

### 4. `search_syntax` & `get_project_context` (Structural Discovery)
Combines RediSearch full-text indexes over symbol definitions with topological centrality (Betweenness and In-Degree) to surface architectural pillars rather than trivial utilities.

### 5. `ask_architecture` (Turn Natural Language to Grpah Query Language)
It Can be used to turn natural language queries into graph query language queries.
Agent get instructed in prompt what can be used graph query and falkor native alogrithm such  as detection of dead code via WCC
Leverages Weakly Connected Components (`algo.WCC`) to isolate clusters of code disconnected from application entrypoints, powering automated refactoring proposals.
And any other combination it can 


---

## 🛠️ Tech Stack

### Frontend
* **React 18 (Vite)**: High-performance SPA with fast HMR.
* **Shadcn UI / Tailwind CSS**: Accessible, clean interface design.
* **Zustand**: Lightweight client-side state management.
* **Shiki**: Syntax highlighting for code snippets.
* **Mermaid**: Real-time rendering of graph traversal diagrams.

### Backend
* **FastAPI**: Async ASGI web framework with OpenAPI documentation and SSE streaming.
* **LangChain & LangGraph**: Agentic runtime, middleware pipelines, and stateful checkpointing.
* **Celery**: Distributed task queue handling repository ingestion.
* **SQLModel / SQLAlchemy**: Async ORM persistence for user accounts, sessions, and KB metadata.
* **SlowAPI**: Rate limiting and endpoint abuse protection.

### Databases & Ingestion
* **FalkorDB**: In-memory graph database running GraphBLAS linear algebra for ultra-low latency graph queries.
* **Qdrant**: Vector search engine for dense code embeddings (`voyage-code-3`).
* **PostgreSQL**: Relational storage for users, sessions, and configurations.
* **Redis**: Celery broker, token whitelisting, and session caching.
* **SCIP & Tree-Sitter**: Dual code indexing supporting compiler-accurate symbol resolution and AST grammar chunking.

---

## 📂 Project Structure

```
CodeMap/
├── backend/
│   ├── src/
│   │   ├── config/          # Pydantic application settings
│   │   ├── db/              # FalkorDB, Qdrant, Postgres, and Redis drivers
│   │   ├── models/          # SQLModel table definitions & schemas
│   │   ├── router/          # FastAPI API routes (auth, chat, setup, rag)
│   │   └── service/
│   │       ├── agent/       # LangGraph agent, system prompts, middleware & tools
│   │       │   ├── tools/   # FalkorDB code tools, document tools, MCP client
│   │       │   └── prompts.py
│   │       ├── background/  # Celery worker configuration & ingestion tasks
│   │       └── pipelines/   # SCIP & Tree-sitter ingestion pipelines
│   ├── docker-compose.dev.yml
│   ├── Dockerfile
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── components/      # Chat, ToolTraceTimeline, MCP config dialogs
│   │   ├── pages/           # Chat & Knowledge Base views
│   │   ├── store/           # Zustand state stores
│   │   └── api/             # Axios and SSE client utilities
│   ├── index.html
│   └── vite.config.ts
│
├── backend.sh               # Starts FastAPI dev server
├── celery.sh                # Starts Celery background worker
├── frontend.sh              # Starts Vite dev server
├── Caddyfile                # Reverse proxy configuration
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites
* [Docker & Docker Compose](https://docs.docker.com/get-docker/)
* Python 3.11+ / [uv](https://docs.astral.sh/uv/)
* Node.js 18+ & npm

### 1. Clone the Repository
```bash
git clone https://github.com/DineshThumma9/FalkorBlaze.git
cd FalkorBlaze
```

### 2. Configure Environment Variables
Create your configuration file in `backend/.env.dev`:

```env
# ── Databases ──────────────────────────────────────────
DATABASE_URL=postgresql://postgres:password@localhost:5432/codemap
REDIS_URL=redis://localhost:6379/0
FALKORDB_URL=redis://localhost:6380
FALKORDB_USER=falkordb

QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=

# ── Security & Authentication ──────────────────────────
SECRET_KEY=generate_a_secure_random_hex_string
FERNET_KEY=generate_a_valid_fernet_key
ALGORITHM=HS256

# ── LLM & Embeddings ───────────────────────────────────
GROQ_API_KEY=your_groq_key
MISTRALAI_API_KEY=your_mistral_key
OPENAI_API_KEY=your_openai_key

VOYAGE_API_KEY=your_voyage_key
VOYAGE_EMBEDDING_MODEL=voyage-code-3

# ── GitHub (For repository ingestion) ──────────────────
GITHUB_TOKEN=your_github_token
```

### 3. Start Database Services
Spin up FalkorDB, Qdrant, PostgreSQL, and Redis:

```bash
cd backend
docker compose -f docker-compose.dev.yml up -d falkordb qdrantdb postgres redis
cd ..
```

* FalkorDB will be available on port `6380` (with FalkorDB Browser on port `3000`).
* Qdrant will be available on port `6333`.

### 4. Run the Application
Open three terminal tabs to launch the stack:

```bash
# Terminal 1 — Backend API
./backend.sh

# Terminal 2 — Background Ingestion Worker
./celery.sh

# Terminal 3 — Frontend Web Application
./frontend.sh
```

Navigate to **`http://localhost:5173`** in your browser.

---

## 🔒 Security & Performance

* **CPU Bottleneck Mitigation**: Replaced CPU-heavy repeated bcrypt checks with high-speed Redis token whitelisting.
* **Context Control**: MCP tools can be toggled on-demand per server from the UI to protect the LLM context window from token saturation.
* **Strict Physical Multitenancy**: Each repository is partitioned into its own named FalkorDB graph namespace (`kb_id`), preventing cross-repository leakage.
* **Rate Limiting**: Endpoint abuse protection enforced via SlowAPI.

---

## 🗺️ FalkorDB Graph Data Model

Each ingested repository gets its own isolated FalkorDB graph (namespace = `kb_id`). The schema is:

### Nodes

```cypher
(:CodeNode {
  id: String,          -- Unique symbol ID (e.g. "repo/src/auth.py:validate_token")
  name: String,        -- Symbol name (e.g. "validate_token")
  kind: String,        -- "File" | "Class" | "Function"
  -- GraphBLAS scores written at ingestion time:
  pagerank: Float,     -- Authority / utility score (algo.pageRank)
  harmonic: Float,     -- Root orchestrator / entrypoint score (algo.HarmonicCentrality)
  betweenness: Float,  -- Bridge / chokepoint score (algo.betweenness)
  community_id: Int,   -- Subsystem cluster (algo.labelPropagation)
  wcc_id: Int          -- Connected component / dead-code island (algo.WCC)
})
```

### Relationships

```cypher
(:CodeNode)-[:CONTAINS]->(:CodeNode)    -- File contains Class or Function
(:CodeNode)-[:CALLS]->(:CodeNode)       -- Function calls another Function
(:CodeNode)-[:IMPORTS]->(:CodeNode)     -- File imports another module/file
(:CodeNode)-[:INHERITS]->(:CodeNode)    -- Class inherits from Base Class
(:CodeNode)-[:HAS_METHOD]->(:CodeNode)  -- Class defines Method
```

### Key GraphBLAS Algorithms & Cypher Queries

#### 1. Ingestion-Time GraphBLAS Algorithms
```cypher
-- 1. In-Degree Authority (PageRank)
CALL algo.pageRank('CodeNode', 'CALLS') YIELD node, score SET node.pagerank = score;

-- 2. Root Orchestrator Reachability (Harmonic Centrality)
CALL algo.HarmonicCentrality() YIELD node, score SET node.harmonic = score;

-- 3. Architectural Chokepoints (Betweenness Centrality with fixed seed)
CALL algo.betweenness({samplingSeed: 42}) YIELD node, score SET node.betweenness = score;

-- 4. Functional Domain Clusters (Community Detection / CDLP)
CALL algo.labelPropagation() YIELD node, communityId SET node.community_id = communityId;

-- 5. Disconnected Dead-Code Islands (Weakly Connected Components)
CALL algo.WCC(null) YIELD node, componentId SET node.wcc_id = componentId;
```

#### 2. Runtime Agent Queries
```cypher
-- Shortest execution path between two symbols (trace_execution_path)
MATCH (a:CodeNode), (b:CodeNode)
WHERE a.name = $src AND b.name = $tgt
WITH a, b, shortestPath((a)-[:CALLS*]->(b)) AS p
RETURN [n IN nodes(p) | n.name] AS path, length(p) AS hops;

-- Multi-hop blast radius & callers (assess_refactor_risk)
MATCH (c:CodeNode)-[:CALLS*1..4]->(t:CodeNode)
WHERE t.name = $sym
RETURN count(DISTINCT c) AS blast_radius;

-- God Object Discovery (Log-dampened PageRank & Betweenness Synergy)
CALL db.idx.fulltext.queryNodes('CodeNode', $query) YIELD node, score
WITH node, (coalesce(node.pagerank, 0.0) * log(2.0 + coalesce(node.betweenness, 0.0))) AS authority
RETURN node.kind AS kind, node.name AS name, node.id AS id, authority
ORDER BY authority DESC LIMIT $limit;
```

---

## 🏗️ What Was Built During the Hackathon (Oct 15–18, 2026)

The base FastAPI + React scaffold (authentication, session management, Qdrant vector search, basic chat UI) was built prior to the hackathon as a general RAG platform.

**Everything below was designed and implemented during Graph Hacks (Oct 16–18, 2026):**

| Component | Description |
|-----------|-------------|
| **FalkorDB multi-tenant graph driver** | `select_graph(kb_id)` isolation, sync + async drivers, LangChain bridge |
| **5 GraphBLAS enrichment algorithms** | PageRank, Harmonic Centrality, Betweenness, Label Propagation, WCC — run at ingestion on every graph |
| **8 graph-powered agent tools** | `trace_call_graph`, `trace_execution_path`, `assess_refactor_risk`, `search_syntax`, `get_project_context`, `explore_codebase`, `read_code`, `ask_architecture` |
| **Mermaid diagram generation** | Dynamic call-graph and shortest-path visualizations rendered in chat |
| **ToolTraceTimeline UI component** | Real-time tool execution trace visible in the frontend |
| **MCP on-demand tool gating** | Per-server toggle in UI to prevent context bloat |
| **Graph-enriched project context** | Agent startup fetches PageRank pillars, Harmonic entrypoints, and community clusters as context |

---

## 🤖 AI Tool Disclosure

This project was built with the assistance of AI coding tools (Antigravity IDE / Gemini). As required by the hackathon rules:

- AI tools were used for code completion, refactoring suggestions, and documentation drafting.
- All graph data models, Cypher queries, algorithm selection, and architectural decisions were designed and reviewed by the developer.
- The developer understands the codebase and can explain the graph model, queries, and technical decisions.

---

## 🤝 Contributing

1. Fork the project.
2. Create your feature branch (`git checkout -b feat/amazing-feature`).
3. Commit your changes (`git commit -m 'feat: add amazing feature'`).
4. Push to the branch (`git push origin feat/amazing-feature`).
5. Open a Pull Request.

Please run `ruff check src` in the backend and prettier formatting on the frontend before submitting.

---

## 📄 License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.
