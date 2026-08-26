# Expert OS Local-First Interaction Design

## Goal

Turn the new Expert OS screens from isolated mockups into one complete local-first
workflow. Users can create, configure, test, publish, deploy, and run custom
Experts without waiting for backend APIs. The resulting local data contract becomes
the backend API contract for a later integration phase.

## Scope

The first delivery covers the following screens as one shared workflow:

- Expert Center
- Expert Skill, MCP, Provider, Knowledge Base, and Memory resource screens
- AiChat
- Runtime Center
- Approval Queue

The existing FlowHub task and project workflows remain unchanged.

## Shared Local Store

Create a single Expert OS store backed by `localStorage`.

- Initialize once from the current `expert-os-mock.ts` records.
- Keep Experts, resource records, deployments, runs, approvals, activity entries,
  and chat sessions in the same persisted state.
- Expose focused store actions for every write operation instead of allowing pages
  to mutate arrays directly.
- Retain a reset-to-demo action for local development.
- Keep the persisted schema versioned so later changes can migrate or reset old
  browser data safely.

The store contract is intentionally shaped around resource reads and explicit
commands, so it can later be swapped for API queries and mutations.

## Expert Lifecycle

Custom Experts follow this lifecycle:

`draft -> testing -> published -> suspended`

- New Experts begin as `draft` at version `v0.1`.
- Editing a published Expert creates a new draft version rather than modifying the
  version used by existing deployments.
- A successful test marks the current draft as eligible for publication.
- Publication creates an immutable version snapshot and makes that version eligible
  for deployment and AiChat selection.
- Suspending prevents new runs but preserves historical runs, approvals, versions,
  and deployments.
- Built-in Experts are read-only.
- Only a custom draft without deployments can be deleted.
- A custom Expert can be duplicated into a fresh `draft` with a unique slug.

## Create And Edit Wizard

The Expert Center action opens a multi-step dialog. The dialog supports a draft
save after the first valid basic-information step and resumes the current draft
when reopened.

### Step 1: Identity

- Name, URL-safe slug, description, and owner.
- Name and slug are required.
- Slugs are lowercase letters, numbers, and hyphens, and must be unique.

### Step 2: Instructions And Model

- System prompt is required.
- Select one healthy Provider and one model it exposes.
- Providers missing credentials are visible but cannot be selected for testing or
  publication.

### Step 3: Capabilities

- Bind one or more published or testing Skills.
- Optionally bind indexed Knowledge Bases.
- Show each selected Skill's tool count and whether it contains a subgraph.

### Step 4: Governance

- Each write or critical tool requires an explicit approval policy.
- Read-only tools use no approval by default.
- The wizard displays blockers before save, test, and publish.

### Step 5: Review

- Show the version, model, resources, tool risk summary, and validation result.
- Actions are Save Draft, Run Test, and Publish when validation permits.

## Validation

The store provides one validation result shared by the wizard, detail drawer, and
publish action.

Publication is blocked when any of the following is true:

- Required identity, prompt, provider, or model fields are missing.
- The selected provider is unhealthy or lacks credentials.
- A selected Skill is not published or testing.
- A selected Knowledge Base is not indexed.
- A write or critical tool has no approval policy.
- The current draft has not completed a successful test after its latest change.

The UI lists each blocker in plain language and deep-links to the relevant wizard
step when possible.

## Test Run And Approval

The test panel accepts a sample prompt and creates a local Run record.

- It records context load, Skill load, retrieval, model response, and tool events.
- Retrieval events include a Knowledge Base source locator when one is bound.
- Read-only test flows finish successfully.
- A selected write or critical action produces an `interrupted` Run and a pending
  Approval record rather than simulating a write.
- Approving resumes that run once and marks it successful; rejecting marks it
  cancelled. Both decisions are recorded in activity history.
- A failed provider test creates a failed Run with diagnostic information and no
  write side effect.

## Publish And Deployment

Publishing persists a version snapshot and changes the Expert's active version.

- A published Expert can create a Deployment with environment (`test` or `prod`),
  deployment name, and optional alias.
- Deployment creation always pins the current published version.
- Editing an Expert does not change an existing deployment's pinned version.
- Deployments can be started, suspended, and removed. Removing a Deployment does
  not delete its historical runs.
- AiChat lists published Experts that have at least one active deployment. Test
  deployments remain available only in the Expert test flow.

## Resource Screens

Resource Center actions become local-first dialogs:

- Skill: create draft, edit a custom Skill, publish after required metadata is
  complete, and show its bound Expert count.
- MCP: register a server, discover tools, and assign an approval policy to every
  write or critical tool before it can be bound.
- Provider: add a provider and models, save credential presence without displaying
  secret values, and run a local health check.
- Knowledge Base: create a collection, add local document metadata, and progress
  indexing to indexed before it can be selected by an Expert.
- Memory: filter by namespace, inspect source records, and export the visible
  local records as JSON.

Buttons only expose operations valid for the selected resource type and status.

## AiChat

AiChat reads Experts, Skills, deployments, runs, and approvals from the shared
store.

- The Expert selector lists active deployments only.
- Changing the Expert restricts Skill choices to Skills bound to that Expert.
- Sending a message creates a local Run and corresponding timeline events.
- A governed write intent creates an approval instead of completing the simulated
  operation.
- Chat messages retain the run reference, event trail, citations, and timestamps
  after refresh.

## Runtime And Approvals

Runtime Center shows store-backed runs, filters them by state, and expands the
immutable event timeline for diagnostics.

- Export downloads the filtered run list as JSON.
- Approval Queue reads the same approval record generated by a test or AiChat run.
- An approval can be decided exactly once while pending and unexpired.
- The approval action updates its linked Run and any linked chat timeline.

## Error Handling

- Invalid forms preserve user input and show field-level messages.
- Blocked lifecycle actions never mutate the Expert state.
- Storage read failures fall back to seed records and show a recoverable notice.
- Storage write failures surface an error toast and retain the current in-memory
  state until the user retries or resets.
- No local workflow claims that a real external model, MCP server, or write tool
  was called.

## Verification

Tests cover the store's lifecycle, validation, version pinning, approval state
transitions, persistence migration, and resource eligibility rules.

Browser verification covers:

1. Create a custom Expert through every wizard step and persist it after refresh.
2. Confirm publication is blocked before a successful test.
3. Test an Expert with a governed write tool and approve the resulting request.
4. Publish and deploy a tested Expert, then select it in AiChat.
5. Edit the Expert and confirm the original deployment remains pinned to its old
   version.
6. Confirm invalid resource states block test and publication with actionable
   messages.
7. Confirm Runtime Center and Approval Queue reflect the same generated records.

## Later Backend Replacement

Replace store reads with query endpoints and store commands with API mutations:

- Expert CRUD, draft/version snapshot, validation, test, publication, and status
  transitions.
- Deployment CRUD and pinned-version run creation.
- Resource CRUD and health/index lifecycle actions.
- Run and approval query/decision APIs.
- AiChat session/message/run APIs.

The UI state machine, validation messages, route structure, and lifecycle rules
remain unchanged during this replacement.
