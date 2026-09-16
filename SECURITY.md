# Security and sensitive data

MemoryCheck executes caller-selected adapter/factory Python code and destructive synthetic lifecycle operations. It is not a sandbox, tenant authorization mechanism, compliance certification, or secure-erasure product. Use dedicated test storage and least-privilege credentials; a backend that ignores scopes can still expose or damage application data.

Trace serialization is conservative by default: values/identifiers/scopes are keyed-redacted, provider raw data and arbitrary metadata are omitted, and exception text is not blindly copied to artifacts. Synthetic display only reveals exact registered generated values. Do not put personal or secret information in canary labels, test names, contract descriptions, project paths, or explicitly public configuration identifiers.

The privacy boundary does not cover live in-process objects, arbitrary caller code, provider telemetry, SDK logging, debugger locals, pytest `-s`, application stdout/stderr, or user-managed CI scripts. Inspect every artifact before sharing it. Upload only `.memorycheck/traces/` when configured appropriately, never `.memorycheck/runs/`, `.env`, private configs, provider snapshots, or general working directories.

Private cleanup manifests contain native target identifiers and generated scopes so a trusted local process can remove its own synthetic state later. They are not signed documents and must not be accepted from untrusted sources or manually edited into broader scopes. Scoped replay checks run identity and configured target matching and refuses ambiguous timed-out runs, but those checks are not authorization against a malicious local user.

A timeout bounds waiting, not a provider transaction. A pending call may continue remotely; automatic reuse/cleanup/close is blocked on the quarantined adapter. Confirm transaction completion and use provider-side tooling where necessary. Add backend/network and CI process deadlines for defense in depth.

To report a vulnerability, use the repository host's private vulnerability reporting mechanism when the maintainer enables it. Until a private contact is configured, do not post credentials, exploit-sensitive traces, or private data in a public issue. Public bug templates are for already-redacted reproductions only.
