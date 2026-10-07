# WP5-7 advisory provenance v1

New bounded implementation following renewed full-Phase5 authorization; historical
'design-only this round' is not a claim that this code existed then. No LLM/API,
network client, vector database, dependency or hidden write path. Lexical retrieval
is deterministic and advisory; it cannot reinterpret numerical gates.

Input is an exact request (query, PRE_FREEZE/POST_FREEZE, max_items1..100,
action=RETRIEVE, requested_mutations=[]), plus corpus source dictionaries and an
independently expected corpus hash. Verify corpus hash and EACH UTF8 content hash
before any snippet. IDs unique. Corpus hash binds claim_key/stance metadata too.
Scores are matched unique Unicode word tokens / unique query tokens. Sort by
score descending, source ID ascending; snippets first500 codepoints. All matching
sources are conflict-checked before truncation. Multiple declared stances for a
claim_key require human review; this detects declared conflicts, not semantic
truth or all possible contradictions. Text is inert data, never instructions.

A report always has advisory=true and contains no decision/threshold/parameter
write mechanism. Any mutation request refuses; gate reinterpretation action
refuses. Empty/unavailable/corrupt corpus fails closed. POST_FREEZE retrieval can
explain provenance only. Outer pipeline freezes expected corpus hash and never
feeds advisory scores or snippets into scientific decisions. No durable store is
introduced by this pure adapter. No production scientific evidence generated.
