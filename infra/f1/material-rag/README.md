# Dedicated material-RAG verification stack

This directory is an internal, non-production verification seam.  The
standalone Compose project has no host ports and shares no containers,
networks, data volumes, or databases with the engineering-closeout stack.

Only `material-rag-egress-proxy` joins the non-internal egress network.  It is
an endpoint-aware HTTP relay that accepts only `POST
/api/plan/v3/embeddings/multimodal`, the exact
`doubao-embedding-vision` model and text-only inputs whose exact UTF-8 SHA-256
was pre-authorized by the one-shot authorizer.  It then opens
certificate-verified TLS
to the fixed Ark authority.  RAGFlow reaches the relay over a second internal
network shared by only those two services.  The verifier cannot connect
directly to the relay.  The four SHA-pinned Demo
PDFs are mounted as individual read-only files into the one-shot,
network-disabled `material-rag-authorizer` and the ordinary
`material-rag-verifier` only; the parent Demo directory is not mounted.  The
authorizer has no database, control, secret or Ark-key mount.  It alone can
write the authorization volume; the ordinary verifier and relay mount that
volume read-only.  The Ark key is mounted
read-only into the one-shot `material-rag-provider-provisioner` only.
The independent at-rest and manifest-HMAC keys are copied only into the
verifier secret volume; neither key is exposed to RAGFlow or the provider
provisioner.

The provider provisioner contract is:

- use only the fixed v0.26.4 account and token routes (`POST /api/v1/users`,
  `POST /api/v1/auth/login`, and `GET`/`POST /api/v1/system/tokens`) and
  never print either token;
- write it mode `0600` to
  `/run/material-rag-control/ragflow_api_key`;
- reconcile exactly one VolcEngine provider, one `material-rag-ark` instance
  and one `doubao-embedding-vision` embedding model through RAGFlow v0.26.4's
  official Peewee models, under one connection, tenant-scoped database lock
  and atomic transaction;
- fail closed on every pre-existing sibling, duplicate or field mismatch;
- never call the public provider-instance endpoint or its implicit API-key
  probe; the first explicitly allowlisted verifier embedding is the real
  connection validation;
- never invoke an OCR or LLM provider.

The one-shot provisioner receives the same dedicated internal datastore
configuration as the pinned RAGFlow service.  It renders `service_conf.yaml`
only in its disposable container overlay, verifies and restores the pinned
image copy before exit, has no external network, and shares no filesystem
with the long-running RAGFlow container.  No raw SQL or nested service-layer
connection decorator is used for provider reconciliation.

Before provider configuration, the authorizer parses the four sources and
locally constructs the verifier's deterministic synthetic inputs.  It applies
the same canonicalization and sensitive-text filter as the normal verifier,
then writes a sorted, unique set of exact UTF-8 body hashes to a private
`0600` authorization file.  The manifest is deliberately limited to
`schema` plus `body_sha256`; it contains no text, source identity, scope,
filename, object key, local path or dataset reference.

The authorized text classes are narrowly fixed to the four Demo PDFs'
sanitized canonical units, the provider policy canary, the client-scope
isolation canaries, the deterministic queries required by this verifier, and
six fixed opaque remote-document aliases derived only from the six authorized
source SHA values.  RAGFlow embeds a document title with every chunk, so these
aliases are pre-authorized explicitly; they contain no original filename,
record/version/scope identity, object key or local path.
Any other synthetic verifier text must be an explicitly enumerated constant,
pass the local sensitive-text filter, and enter the same SHA manifest before
it can reach Ark.  Unregistered user input remains denied.  Original PDFs,
page images, original filenames, object keys and local paths never enter the
relay.  The authorizer reaches local OCR only through the dedicated FIFO
volume and has `network_mode: none`.

The relay's restart-safe audit records the exact route, model,
authorized/forwarded embedding counts and fixed zero counters for
non-embedding, LLM and OCR requests; it never records text, body hashes,
source identities, queries or adapter IDs.

`scripts/localctl material-rag-verify` owns the project lifecycle.  It starts
with registration enabled only for token bootstrap, recreates RAGFlow with
registration and password login disabled before verification, and always
removes the exact triple-labelled project, its volumes, and its separately
tagged runtime image.  It never rebuilds or overwrites the shared local-stack
runtime image.

Local OCR is a strict single-consumer FIFO exchange.  The authorizer and
verifier use it only in separate one-shot phases.  The request FIFO is
`/run/material-rag-ocr/request.fifo` and the response FIFO is
`/run/material-rag-ocr/response.fifo`: request is `uint32be envelope_length`
+ one exact `f0e-envelope-v1`; response is `uint32be json_length` + canonical
JSON.  The whole envelope is limited to 64 MiB and output to 8 MiB.  Readiness
requires both `0600` FIFOs and the regular `0600`
`/run/material-rag-ocr/ready` file; the original F0-H seccomp profile remains
unchanged and all networking stays denied.
