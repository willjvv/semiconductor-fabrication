# MiniFab — Semiconductor Fab MES Simulation MVP

MiniFab is a small, Dockerized simulation of a semiconductor fabrication environment paired with an MES-style data platform.

The project is intentionally **not a semiconductor-physics simulator** and is not intended to control real industrial equipment. Instead, it models the software and data problems that surround production: wafers moving through process steps, equipment generating telemetry, recipes defining process settings, quality results, manufacturing events, and traceability from a finished wafer back through its processing history.

The immediate goal is to demonstrate a practical software/data-integration architecture that resembles the responsibilities found in MES, manufacturing systems integration, industrial data, and manufacturing software engineering roles.

## What the MVP is

The MVP simulates a fictional semiconductor fab producing a product called the **VX-900 AI Accelerator** (`AI-ACCEL-V1`). It starts with 50 synthetic wafers distributed across five lots and moves them through four process areas:

```text
Silicon / Lot
    |
    v
[LITHOGRAPHY]
 LITHO-01
    |
    v
[ETCH]
 ETCH-01
    |
    v
[DEPOSITION]
 DEP-01
    |
    v
[INSPECTION]
 METRO-01
    |
    v
 PASS / SCRAP
```

Each station has a simulated machine. Machines generate telemetry while processing a wafer. The telemetry travels through MQTT into an edge-gateway service, where it is persisted to PostgreSQL. The simulator also emits manufacturing events such as `ProcessStarted`, `ProcessCompleted`, `EquipmentDown`, `EquipmentRepaired`, `WaferHold`, `WaferReleased`, and `QualityResult`.

The FastAPI service exposes that information as an MES-style API, and the Next.js dashboard polls the API to show the live factory state.

## MVP features

### Synthetic fab simulation

- 50 wafers across 5 lots
- One work order for the demo production run
- Four process steps:
  - Lithography
  - Etch
  - Deposition
  - Inspection
- Four simulated pieces of equipment
- Process-specific cycle times
- Process-specific synthetic failure rates
- Recipe metadata for each process

The process values are deliberately synthetic. They are useful for demonstrating software behavior but should not be interpreted as accurate semiconductor manufacturing parameters.

### Manufacturing data model

The PostgreSQL schema contains:

- `products`
- `lots`
- `work_orders`
- `equipment`
- `recipes`
- `wafers`
- `process_runs`
- `telemetry`
- `quality_results`
- `events`

The important relationship is:

```text
Product
  |
  +-- Lot
       |
       +-- Wafer
            |
            +-- Process Runs
            |     |
            |     +-- Equipment
            |     +-- Recipe
            |
            +-- Quality Results

Equipment
  |
  +-- Telemetry
  +-- Events
```

### Wafer history / genealogy

Every wafer gets a stable identifier such as:

```text
WAFER-00037
```

The API can return the wafer's complete process history, including:

- process type
- equipment used
- recipe and version
- start/end time
- process result
- process metrics
- quality measurements

This is the beginning of the forward/backward traceability model that a larger MES would extend much further.

### Equipment telemetry

While a machine is running, the simulator publishes synthetic values including:

- temperature
- pressure
- RF power
- machine status
- current wafer
- remaining process time

Example MQTT topic:

```text
fab/telemetry/ETCH-01
```

The gateway subscribes to the telemetry topics and writes readings to PostgreSQL.

### Event-driven process tracking

The simulator publishes manufacturing events to:

```text
fab/events
```

Each event contains a unique `event_id`. The gateway stores events using an idempotent insert (`ON CONFLICT DO NOTHING`) so a duplicate event does not create a second manufacturing event record.

### Deliberate equipment failure

The MVP includes a deterministic failure demonstration:

- Approximately 35 seconds after the simulator starts, `ETCH-01` is taken offline.
- If a wafer is currently running there, it is placed on hold.
- The equipment is marked `DOWN`.
- After approximately 15 seconds, maintenance is considered complete.
- `ETCH-01` is marked `IDLE` and the held wafer is released so its process can resume.

During the failure window, the simulator's synthetic telemetry also trends upward to make the event visible as a pre-alarm signal.

This is deliberately simple, but it demonstrates a core industrial-systems pattern: **equipment state changes affect production state**.

### Quality inspection

Inspection generates a synthetic critical-dimension measurement.

Example:

```text
Expected: 45.0 nm
Tolerance: +/- 0.5 nm
Measured: 45.18 nm
Result: PASS
```

A configurable synthetic failure probability allows some wafers to fail inspection and become `SCRAPPED`.

### Live dashboard

The Next.js dashboard provides:

- total wafers
- in-process / hold count
- completed count
- scrapped count
- inspection yield
- equipment status
- current wafer on each station
- work-in-process by process area
- recent manufacturing events
- wafer queue
- click-through wafer traceability drawer
- process history
- quality results
- simulation reset button

## Architecture

```text
                         +---------------------------+
                         |     Next.js Dashboard     |
                         |       localhost:3000      |
                         +-------------+-------------+
                                       |
                                      HTTP
                                       |
                         +-------------v-------------+
                         |      FastAPI MES API      |
                         |       localhost:8000      |
                         +-------------+-------------+
                                       |
                                       v
                              +----------------+
                              |   PostgreSQL    |
                              |   fab database  |
                              +-------^--------+
                                      |
                               reads / writes
                                      |
                         +------------+-----------+
                         |     Edge Gateway       |
                         | MQTT -> PostgreSQL     |
                         +------------^-----------+
                                      |
                                     MQTT
                                      |
                              +-------^--------+
                              |    Mosquitto    |
                              | localhost:1883  |
                              +-------^--------+
                                      |
                              +-------+--------+
                              | Fab Simulator   |
                              | wafers +       |
                              | equipment      |
                              +----------------+
```

### Why the gateway is separate

The gateway is intentionally separate from the MES API. This makes the architecture closer to a real integration problem:

```text
Industrial / device side
        |
        v
     Gateway
        |
        v
   MES / data layer
```

The simulator is therefore pretending to be an equipment/data source. The gateway pretends to be the integration boundary.

## Repository layout

```text
semiconductor-fab-mes-mvp/
├── docker-compose.yml
├── .env.example
├── README.md
├── db/
│   └── init.sql
├── mosquitto/
│   └── config/
│       └── mosquitto.conf
├── services/
│   ├── api/
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── requirements.txt
│   ├── gateway/
│   │   ├── Dockerfile
│   │   ├── main.py
│   │   └── requirements.txt
│   └── simulator/
│       ├── Dockerfile
│       ├── main.py
│       └── requirements.txt
└── web/
    ├── Dockerfile
    ├── next.config.mjs
    ├── package.json
    ├── next-env.d.ts
    ├── tsconfig.json
    └── app/
        ├── globals.css
        ├── layout.tsx
        └── page.tsx
```

## Technologies

### Backend / simulation

- Python 3.12
- FastAPI
- SQLAlchemy
- psycopg2
- Paho MQTT
- Requests

### Data

- PostgreSQL 16
- JSONB for flexible telemetry/event payloads

### Messaging

- Eclipse Mosquitto MQTT 2.x

### Frontend

- Next.js 14
- React 18
- TypeScript

### Runtime

- Docker Compose

## Running the project

### Prerequisites

Install:

- Docker Desktop (Windows/macOS) or Docker Engine + Docker Compose (Linux)

No local Python, Node.js, PostgreSQL, or Mosquitto installation is required when using Docker Compose.

### Start the stack

From the project root:

```bash
docker compose up --build
```

Then open:

```text
Dashboard: http://localhost:3000
API:       http://localhost:8000
API docs:  http://localhost:8000/docs
Postgres:  localhost:5432
MQTT:      localhost:1883
```

On the first run, the API seeds the demo product, lots, equipment, recipes, work order, and 50 wafers.

The simulator then resets the wafer/process data at startup and begins producing events and telemetry.

### Stop the stack

```bash
docker compose down
```

### Reset everything, including PostgreSQL data

For a completely fresh database:

```bash
docker compose down -v
docker compose up --build
```

The `-v` is destructive to the project's local PostgreSQL volume, which is appropriate for this demo but should not be treated as a production workflow.

## What to look for while it runs

### Seconds 0–35

You should see wafers begin to move through the four stages.

Typical state progression:

```text
READY
  -> PROCESSING
  -> READY
  -> PROCESSING
  -> READY
  -> PROCESSING
  -> READY
  -> PROCESSING
  -> COMPLETED
```

An inspection failure changes the final state to:

```text
SCRAPPED
```

### Around 35 seconds

Look for:

```text
EquipmentDown
ETCH-01
```

and possibly:

```text
WaferHold
WAFER-xxxxx
```

The dashboard should show `ETCH-01` as `DOWN` while the failure is active.

### Around 50 seconds

Look for:

```text
EquipmentRepaired
ETCH-01
```

and a held wafer being released.

## Useful API calls

List the current wafers:

```bash
curl http://localhost:8000/wafers
```

List equipment:

```bash
curl http://localhost:8000/equipment
```

Get dashboard state:

```bash
curl http://localhost:8000/dashboard
```

Get recent events:

```bash
curl http://localhost:8000/events
```

Inspect one wafer:

```bash
curl http://localhost:8000/wafers/WAFER-00037
```

Get production summary:

```bash
curl http://localhost:8000/production/summary
```

Reset the simulation data:

```bash
curl -X POST http://localhost:8000/simulation/reset
```

After resetting through the API, restart the simulator container to begin a new run:

```bash
docker compose restart simulator
```

## Engineering notes

### Synthetic data vs. real process control

The simulator creates values that have recognizable manufacturing semantics, but it does **not** implement a physical model of lithography, etch, deposition, or semiconductor yield. This distinction is intentional.

For a portfolio project, the useful engineering target is the information system around production rather than a claim of semiconductor-process expertise.

### Why MQTT instead of directly writing to the database?

Direct database writes from the simulator would hide the integration problem. MQTT adds a message boundary between the equipment side and the data platform.

That gives you a natural place to add later:

- buffering
- retry
- replay
- message validation
- dead-letter handling
- protocol translation
- device identity
- event versioning

### Why PostgreSQL?

The MVP has both relational manufacturing data and semi-structured telemetry/events. PostgreSQL gives you relational integrity for entities such as wafers, lots, equipment, process runs, and recipes while JSONB provides flexibility for synthetic telemetry and event payloads.

## Current limitations

The current MVP intentionally leaves out several systems that would belong in a larger production-style implementation:

- no real PLC communication
- no OPC-UA connection yet
- no production historian
- no Kafka/RabbitMQ layer
- no authentication/authorization
- no formal MES role model
- no operator UI for releasing/holding work
- no advanced dispatching/scheduling optimizer
- no maintenance management subsystem
- no electronic batch record / regulated validation workflow
- no cloud deployment
- no distributed tracing / metrics stack
- no real semiconductor process physics
- no AI analyst yet

Those are deliberate scope boundaries, not missing requirements for the MVP.

# End goal

The eventual project can become a much larger **Semiconductor Manufacturing Data Platform / MES Simulator** that demonstrates the complete lifecycle from equipment data to manufacturing intelligence.

A possible end-state architecture is:

```text
                  SEMICONDUCTOR FAB DIGITAL TWIN
                              |
        +---------------------+---------------------+
        |                     |                     |
   Equipment              Material              Production
   Simulation             Simulation             Scheduling
        |                     |                     |
   OPC-UA / MQTT             Lots                Work Orders
        |                     |                     |
        +----------+----------+----------+----------+
                   |
             Edge / Gateway
                   |
           Event Streaming Layer
             Kafka / RabbitMQ
                   |
        +----------+----------+
        |                     |
      MES API          Operational Historian
        |                     |
        +----------+----------+
                   |
               PostgreSQL
                   |
       +-----------+-----------+
       |           |           |
   Quality     Genealogy    Analytics
       |           |           |
       +-----------+-----------+
                   |
             Data Warehouse
                   |
          +--------+--------+
          |                 |
       Dashboards       AI Analyst
```

The end goal is not simply a prettier dashboard. It is a coherent system that can demonstrate the following chain:

```text
Equipment
   -> Industrial protocols
   -> Edge integration
   -> Messaging
   -> Event processing
   -> MES state
   -> Relational manufacturing data
   -> Traceability / genealogy
   -> Quality
   -> Scheduling
   -> Analytics
   -> AI-assisted investigation
```

## End-goal feature roadmap

### Phase 1 — Stronger MES model

Add:

- richer work orders
- operation sequences
- WIP states
- operator assignments
- material consumption
- holds/releases
- rework
- scrap reasons
- maintenance records
- explicit recipe revisions

### Phase 2 — Real industrial connectivity patterns

Add an OPC-UA equipment simulator and have the gateway support both:

```text
OPC-UA -> Gateway -> MQTT/Event Layer -> MES
```

This would make the project directly useful for discussing industrial protocol integration in interviews.

### Phase 3 — Reliable distributed integration

Introduce a real broker beyond basic MQTT transport, such as RabbitMQ or Kafka, and implement:

- retries
- exponential backoff
- dead-letter queues
- event replay
- idempotent consumers
- schema/version validation
- connection loss buffering
- graceful recovery after database outages

The goal is to make failures first-class parts of the demonstration.

### Phase 4 — Traceability / genealogy graph

Expand from a single wafer history into full manufacturing genealogy:

```text
Supplier lot
     |
 Material lot
     |
 Fab lot
     |
 Wafer
     |
 Process run
     |
 Equipment + recipe
     |
 Die
     |
 Package
     |
 Finished accelerator
```

Then support forward and backward trace queries such as:

- Which finished parts used this material lot?
- Which wafers ran through this equipment during an alarm?
- Which recipes contributed to this quality excursion?
- Which finished units contain a particular component batch?

### Phase 5 — Scheduling and optimization

Add a dispatching engine that understands:

- equipment compatibility
- recipe compatibility
- equipment availability
- WIP
- work-order priority
- due dates
- estimated cycle times
- preventive maintenance windows

This turns the system from a tracking system into an operational planning system.

### Phase 6 — Quality and statistical process control

Add:

- control charts
- process capability metrics
- automated out-of-control rules
- defect Pareto views
- correlation of defects to equipment/recipes/material lots
- hold/release workflows

### Phase 7 — Observability

Add:

- Prometheus
- Grafana
- service health metrics
- message latency
- ingestion throughput
- queue depth
- equipment uptime
- database latency
- error rates

This would demonstrate that the system itself is observable, not just the simulated factory.

### Phase 8 — AI manufacturing analyst

The final AI layer should operate against real project data rather than inventing observations.

Example questions:

```text
Why did wafer yield decrease this morning?

Which equipment has the highest downtime?

Which material lots are associated with the most failures?

Show the process history for WAFER-00421.

What changed before the increase in inspection failures?
```

The analyst should retrieve structured data, show the evidence behind its answer, and clearly distinguish correlation from causation.

### Phase 9 — Digital-twin style control room

The final UI could represent the fab as an interactive operational view:

```text
FAB-01

[LITHO-01] ------> [ETCH-01] ------> [DEP-01] ------> [METRO-01]
   RUNNING             DOWN              RUNNING            IDLE
   W-183               W-184             W-181             —
```

Clicking equipment could reveal live telemetry, current recipe, recent alarms, maintenance history, utilization, and the wafers affected by the equipment.

## What a finished portfolio version would demonstrate

A mature version of MiniFab could credibly be described as:

> Built a simulated semiconductor manufacturing environment and MES/data-integration platform that models wafer production, equipment telemetry, manufacturing events, recipes, quality inspection, downtime, and traceability. Implemented MQTT-based equipment integration, PostgreSQL manufacturing data models, REST APIs, event-driven state transitions, failure recovery, operational analytics, and an AI-assisted manufacturing investigation layer.

That is the intended trajectory. The MVP in this repository is the foundation rather than the final system.

## Portfolio / interview angle

The project is designed to give you a concrete engineering story beyond "I made a dashboard."

A useful explanation is:

> I built a simulated semiconductor fab where wafers move through multiple process operations. Equipment generates telemetry over MQTT, a gateway persists that information, and an MES-style API maintains wafer/process state and genealogy. I also simulated equipment failure so the system had to put a wafer on hold and resume it after maintenance.

From there, the later milestones can demonstrate distributed systems, industrial protocols, database design, reliability engineering, analytics, and AI.

## License

This project is provided as a portfolio/learning project. Add a license of your choice before publishing it as a public repository.

## Troubleshooting
## Browser/API connection troubleshooting

The dashboard uses a same-origin `/api` proxy. Your browser talks to `http://localhost:3000/api/...`; Next.js forwards those requests inside the Docker network to the `api` service at `http://api:8000`. This avoids browser-side CORS and `localhost` resolution problems. The proxy target is configurable through `API_PROXY_TARGET`, so local Next.js development can use `API_PROXY_TARGET=http://localhost:8000` if the API is running directly on the host.

After rebuilding, verify:

```bash
docker compose ps
```

Then open these in the browser:

- `http://localhost:3000` — dashboard
- `http://localhost:3000/api/health` — proxied API health check
- `http://localhost:8000/health` — direct API health check

If `/api/health` works but the dashboard still shows an error, perform a full rebuild:

```bash
docker compose down
docker compose up --build
```

If the direct API health check fails, inspect the API container:

```bash
docker compose logs api --tail=100
```

