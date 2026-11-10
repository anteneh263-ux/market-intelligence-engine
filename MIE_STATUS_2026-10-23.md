# MIE-status — 23. oktober 2026

Oppdatert etter rydding av arbeidsmapper, push av de siste lokale endringene og flytting av sikkerhetskopien til en bevaringsmappe utenfor Codex. Denne filen er prosjektets Git-versjonerte statusoversikt for 23. oktober 2026.

## GitHub

- Main: `1331d58b2a50f798cc26676c34fa73cf32bd65ba` — v3, v4, v4 som standard, prosjektkontrakt og teknisk Phase 5-avslutning.
- Fase 6-pause: `88cabfbc63bee26eedc8cf7b0e3add3b7d5b9b54`, pushet til `codex/phase6-receipt-small008`.
- Batch diagnostics: `2279e8826613011d5d8654916a36f6b01ba6eef1`, nå pushet til `feature/batch-diagnostics-followup`. GitHub-refen er kontrollert og upstream er satt. Ingen merge eller PR er utført.
- Repo: privat, https://github.com/anteneh263-ux/market-intelligence-engine.

## Gjenværende arbeidsmapper

| Mappe | Gren | Tilstand |
|---|---|---|
| `/Users/antenehhailu/Desktop/market-intelligence-engine` | `feature/shadow-forward-v1` | Brukerendringer bevart; statusfilen her er arbeidskopi av dokumentasjonsgrenen |
| `/Users/antenehhailu/MIE_CALIBRATION_WORKTREE` | `codex/research-calibration001` | Ren, samme commit som main |
| `/Users/antenehhailu/MIE_RECEIPT_MEDIUM_WORKTREE` | `codex/phase6-receipt-small008` | Ren, Fase 6 pauset |

Ti andre arbeidsmapper er fjernet uten `--force`. Alle lokale grener er beholdt.

## Nøyaktig plassering av sikkerhetskopien

Sikkerhetskopien ligger i en egen bevaringsmappe i hjemmemappen, ved siden av kopien fra 26. august. Den er uavhengig av Codex og er ikke en Git-arbeidsmappe.

**De fire unike filene:**

```text
/Users/antenehhailu/MIE_PRESERVATION_2026-10-23/payload
```

**Hele sikkerhetskopien:**

```text
/Users/antenehhailu/MIE_PRESERVATION_2026-10-23
```

De fire bevarte filene, relativt til `payload/`:

- `MIE_P1B_CLOSEOUT_WORKTREE_002/tests/test_phase5_trial_registry_p1b_timing.py`
- `MIE_PHASE5_COMPLETION_WORKTREE/.hermes/plans/2026-09-03_132735-phase5-completion.md`
- `MIE_PHASE5_COMPLETION_WORKTREE/app/research/artifact_inventory.py`
- `MIE_PHASE5_COMPLETION_WORKTREE/tests/test_phase5_artifact_inventory.py`

Alle fire er kontrollert mot de lagrede SHA-256-sjekksummene på nytt. P1B-testen er en annen versjon enn filen med samme navn i hovedrepoet; begge er bevart.

Hele ryddearkivet er bevart uendret under `cleanup-archive/`, inkludert `git-history.bundle`, `untracked-and-ignored.tar.gz`, de opprinnelige sjekksummene, før-/etterstatus og logger for hver fjerning. Tar-arkivet har 4097 verifiserte oppføringer, inkludert de fire unike filene og det gamle Python-miljøet. Historiske arkivnotater beholder opprinnelige stier og beskriver tilstanden da de ble skrevet; denne statusfilen oppdaterer plassering og push-status uten å omskrive historikken.

Bevaringsmappen har `PRESERVATION_MANIFEST.json`, `PRESERVATION_REPORT.txt` og en ny `SHA256SUMS` som dekker alle 39 filer. Alle 39 sjekksummer og Git-bundle er kontrollert. De fire filene i `payload/` er i tillegg sammenlignet med de opprinnelige filhashene. Sikkerhetskopien fra 26. august er uendret.

Kontroller ved behov fra `/Users/antenehhailu/MIE_PRESERVATION_2026-10-23`:

```sh
shasum -a 256 -c SHA256SUMS
```

## Avgrensning

Ingen AWS-kjøring, vitenskapelig evaluering, produksjonskodeendring eller endring av H-025 er gjort i denne oppgaven. Evidensmappene og sikkerhetskopien fra 26. august er utenfor ryddingen.

Bare denne statusfilen inngår i dokumentasjonscommiten på `codex/mie-status-preservation-2026-10-23`, basert på main. Dokumentasjonscommiten er foreløpig lokal og ikke pushet. Hovedrepoets indeks har en eksisterende låsefil åpen i en virtuell maskin. Den er ikke fjernet eller endret; dokumentasjonsgrenen bruker en separat midlertidig indeks. Filen i hovedarbeidsmappen vises derfor fortsatt som usporet der, men innholdet er bevart i Git på dokumentasjonsgrenen. Brukerens øvrige lokale endringer er ikke tatt med, og ingen ny arbeidsmappe er opprettet.

## Beslutning om beviskrav — 23. oktober 2026, ettermiddag

- **H-030** (tidsserietrend, 24 ETF-er, v5) er evaluert: **Not Supported**. Feilet DSR (0,477), kjøp-og-hold
  (Sharpe per bar 0,0156 mot 0,0220) og robusthet (0,00); positiv OOS-Sharpe og slo tilfeldig timing.
  Gjelder den forhåndsregistrerte implementasjonen, ikke trend generelt. Holdout (2025-01-01 – 2026-09-30)
  er fortsatt lukket. Perioden 2005–2024 for dette ETF-universet regnes nå som sett data.
- **Beviskrav videre:**
  1. *Litteratur og implementasjonsgjennomgang (C)* avgjør hvilke hypoteser som fortjener forskning:
     flere studier, også negative resultater, og eksplisitt sammenligning med vår implementasjon.
  2. *En forhåndsregistrert test* avgjør hva våre data støtter — selvstendig avkastning eller
     porteføljebidrag (B), valgt på forhånd med låst referanseportefølje, vekter, rebalansering og
     hovedmål. Kostnader, usikkerhet og antall forsøk regnes med.
  3. *Fremadrettet simulering* er neste trinn for kvalifiserte kandidater. Teknisk korrekt implementasjon
     omtales aldri som dokumentert lønnsomhet, og ingenting av dette alene er grunnlag for å risikere kapital.
- **v6 (strategier som ser alle aktiva samtidig, med vekter) bygges ikke** før en konkret forskningsplan
  viser at den trengs. Første kandidat som utredes: momentum mellom aktiva
  (`_incoming_2026-10-23/PLAN_MOMENTUM_MELLOM_AKTIVA.md` i `MIE_CALIBRATION_WORKTREE`).
- Kriseegenskaper dokumentert for tidsserietrend overføres ikke uten videre til momentum mellom aktiva.

## Merknad om låsefil — 23. oktober 2026

`.git/index.lock` i hovedrepoet (0 byte, opprettet 2026-10-23 09:00:12 norsk tid) ble svært sannsynlig
opprettet av Claudes skrivebeskyttede Git-kartlegging fra Linux-miljøet på denne Mac-en; der er sletting
blokkert, så Git kunne ikke fjerne låsen. Den inneholder ingen data. Kan fjernes på Mac-en når det er
bekreftet at ingen Git-prosess kjører: `rm .git/index.lock` i hovedrepoet.
Denne seksjonen og seksjonen over er lagt til i arbeidskopien av statusfilen; kopien som er committet på
`codex/mie-status-preservation-2026-10-23` må oppdateres tilsvarende.

## H-025-status og søkeavvik — 23. oktober 2026, ca. 15:00

- **H-025-status: planlagt; frysing og oppstart ikke verifisert.** Søket omfattet hovedrepoets sporede,
  usporede og ignorerte filer, lokal Git-historikk, `MIE_PRESERVATION_2026-08-26`, `MIE_PRESERVATION_2026-10-23`
  og `Desktop/MIE_EVIDENCE`. Ingen datert, frosset registrering fra før 2026-08-01 og ingen løpende registrerte
  signaler etter den datoen ble funnet. `paper_trading/state.json` (inneholder H-025-kontoer i den gamle
  papirmotoren) ble sist endret 2026-07-21. Filhasher bekrefter dagens innhold, ikke en frysing før teststart.
  H-025 omtales derfor ikke som en pågående test. Et lager utenfor søkeområdet er ikke utelukket.
  Ingenting er opprettet, endret, flyttet eller rekonstruert.
- **Søkeavvik:** den siste filopplistingen manglet filter for holdout-stier og leste utilsiktet ett
  `MANIFEST.json` under Phase 5-mappen `holdout-execution` (diagnostic003, holdout brukt én gang 2026-10-06,
  resultat HOLDOUT_FAILED allerede rapportert). Ingen kursdata eller resultatfiler der ble lest. H-030-holdout
  ble ikke åpnet. Søket ble stoppet. Vurdering: ingen ubrukte testdata kompromittert, men brudd på avtalt
  søkegrense. Tiltak: fremtidige søk utelukker eksplisitt alle stier som inneholder `holdout` før lesing.

## Notat om v6 (uten tidsplan)

Mulig rekkefølge hvis v6 tas opp igjen: forskningsgrunnlag (C) → forhåndsregistrert regel → v6 med vekter
(for eksempel risikobalansering, som løser at volatile aktiva dominerer likevektede v5-porteføljer) →
kalibrering av MOTOREN (≥ 80 % ved Sharpe 0,5, ≤ 5 % ved 0 på syntetiske data) → én låst test på ekte data.
Kalibreringskravet gjelder testen, ikke strategien. Parhandel står på idélisten uten tidsplan.
Nye strategitester er pauset (beslutning 2026-10-23).

## Kalibrering av kontrollgrensen — 23. oktober 2026, ca. 15:10

Metodearbeid i skyen på kopi av `target_control.py` (byte-lik origin/main). Ingen dommer endret, ingen holdout.
Grensen sammenligner forskjell i Sharpe **per dag** med 0,05 (≈ 0,96 årlig). Med syntetiske data (4 aktiva,
3100 dager, 20 gjentak) ga en sann fordel over kontrollen på 0,5 årlig Sharpe `candidate_wins` 1/20 ganger,
1,0 ga 10/20, uten signal 0/20. Et fast bånd på 0,05 per år ville gitt 12/20 «seire» uten signal. Konklusjon:
porten krever i praksis ≈ 1 årlig Sharpe i fordel; en enkel enhetsretting løser det ikke. En eventuell
erstatning må være en versjonert, kalibrert statistisk test. Detaljer: `KONTROLLGRENSE_KALIBRERING.md`
i `MIE_CALIBRATION_WORKTREE/_incoming_2026-10-23/`.

## Crypto carry (lang spot, kort perpetual) — avsluttet 24.10.2026 kl. 12:15

Utenfor MIE-repoet; ingen kode, dommer eller holdout berørt, ingen ekte handler.

- **v1** (`crypto_carry_test.py`, sha b7b69170…): **nedlastingsfeil, ikke et resultat.** `startTime=0` ga bare
  siste 1000 rader (2026-05-11..2026-09-30), og renten ble fylt med senere verdier. Brukes ikke.
- **v2** (`crypto_carry_test_v2.py`, sha 0d287dc8…, regler låst før data): `fetch` og `run` kjørt én gang hver
  av Codex, uendret. Dekning: 2019-11-27..2026-09-30, 7 499 finansieringsperioder (100 %), premiedager 98,92 %.
  RESULTS.json sha b3d5f793…, MANIFEST.json sha 677b0c5c…; filer i `~/MIE_CRYPTO_CARRY_V2_2026-10-24/`.
- **Resultat** (meravkastning over 3-mnd T-bill, prosentpoeng per år): 2020 +16,9; 2021 +26,8; 2022 −0,5;
  2023 +0,8; 2024 +4,6; 2025 −0,5; 2026 (t.o.m. sep.) −2,4. Samlet 9,63 % netto mot 2,84 % rente.
  Fra 2022 er snittet ≈ +0,5 pp per år. 15 dager med kurshopp > 20 % innen dagen (BTC 5, ETH 10); mulige
  likvidasjonstap er ikke trukket fra. Børsrisiko er ikke modellert. `basis_pnl_total` ≈ 0 er forventet
  for alltid-på-posisjon (premieendringene teleskoperer) og er ikke et funn.
- **Dom:** passiv crypto carry var lønnsom 2020–2021, men er **ikke påvist lønnsom etter 2022** når børs- og
  marginrisiko tas med. Skal ikke handles. Ingen varianter (f.eks. tidsfiltre) testes på disse dataene.

## AI-utvalgspilot — 24.10.2026

Codex' historiske pilot: AI valgte blant fem anonymiserte kandidater, med valgene lagret før utfallene ble åpnet.
Bare 3 tilfeller blant 36 månedsslutter. Resultatet: AI −1,70 %, fast regel −0,81 % og tilfeldig valg −3,19 %
per handel. **Ikke informativt:** n = 3 gir en usikkerhet på omtrent ±4 pp, og modellen kan kjenne historikken.
Bare en fremadrettet papirtest ville vært ren. Den er ikke planlagt; det trengs omtrent 200 handler for å
påvise 1 pp per handel. Arbeidsflyten med anonymisering og lagring før utfall er gjenbrukbar.

## Sektor-momentum (replikasjon) — 23.10.2026, lagt inn i status 28.10.2026

Utforskende replikasjon av Vanstone, Hahn & Earea (2021) utenfor repoet, i `~/MIE_SECTOR_MOM_2026-10-23/`
(`sector_momentum_replication.py` sha d1c1457e…, `RESULTS.json` sha cbbe4711…). Ikke en MIE-dom. Data stopper
2024-12-31; 2025+ er ikke lastet ned. Ni SPDR-sektor-ETF-er, lang beste / kort dårligste, netto etter 10 bp kostnad.

| Regel | 2000–2015 (studieperiode) | 2016–2024 (etter studien) |
|---|---|---|
| Studiens 24/1/24 | Sharpe 0,39, t ≈ 1,4, maks fall 36 % | Sharpe −0,17, t ≈ −0,5, maks fall 71 % |
| Referanse 6/1/6 | Sharpe 0,24, t ≈ 1,0 | Sharpe 0,24, t ≈ 0,7 |

- **Dom:** studiens hovedregel holdt ikke etter publisering. Ingen av variantene er statistisk signifikante
  (t < 2). Ingen varianter testes videre på disse dataene.

## BTC Donchian 20/10 — 27.10.2026

`knowledge_base/studies/BTC_DONCHIAN_2026-10-27/` (usporet). Binance BTCUSDT spot, lang/kontant, én innstilling,
srp-matrix-v4 på c41afe4, data 2020-02-20..2024-12-31 (tidligere sett historikk, ikke holdout).

- **4H (primær): Not Supported.** Netto +91,9 %, årlig Sharpe 0,66, maks fall 51,6 %, 114 handler, DSR 0,904
  (< 0,95). Slo ikke tilfeldig timing (p95 ≈ 1,13), feilet robusthet (0/9). Kjøp-og-hold: +97,0 % med 77 % fall.
- **1D (referanse): Inconclusive.** 22 handler (< 30), DSR 0,840, slo ingen av referansene.
- Ingen uavhengig gjennomgang. Ikke hele Turtle-systemet (ingen short, ATR-størrelse eller pyramidering).

## BTC chartmønstre v1.1 — 27.10.2026

Ny eksperimentell motorutvidelse `mie-chart-patterns-v1.1` (`app/strategy_research/chart_patterns_*.py`,
tester og `imports/`-filer er **usporet**, ikke committet). Resultater i
`knowledge_base/studies/BTC_CHART_PATTERNS_2026-10-27_v1_1/` (v1 bevart med valideringsvarsel).
BTCUSDT-perpetual 4H, evaluert 2021–2024, fem låste regler, ingen tuning, kostnader og funding med.

- **Ingen kvalifiserte kandidater.** Dobbel bunn +27,1 % (10 handler, Sharpe 0,54, Holm-korrigert p = 0,20);
  dobbel topp −17,1 % (7); omvendt H&S −20,1 % (4); H&S og symmetrisk trekant 0 handler. Minstekrav var 30 handler.
- 4 048 fundingoppgjør manglet markpris; brukt markpris-candlens åpning ved samme tidspunkt (registrert før resultat).
- v1.1 retter regnskapsfeil i kjøp-og-hold-referansen; strategienes tall er uendret. 54 tester bestod.
  Ingen uavhengig gjennomgang.

## Merknad — 28.10.2026

- Begge BTC-studiene 27.10 er nye strategitester etter beslutningen 23.10 om å pause nye strategitester.
  Avviket er notert her, ikke vurdert. Ingen av dem gir en kandidat.
- Status for disse to studiene og den nye motorkoden er ikke committet. `.git/index.lock` finnes fortsatt.
- H-025 er uendret og fortsatt ikke verifisert som frosset/startet (se seksjonen over).

## MIE Discovery Lab v0.1 — 28.–30.10.2026, lagt inn i status 30.10.2026

Nytt, separat delprosjekt i `discovery_lab/` (**usporet**, ikke committet; `runs/` er ignorert av Git).
Automatisert forsøkslaboratorium: oppdrag → arXiv-søk → evidens → hypoteser → AI-generert kandidatkode →
forhåndsregistrert, isolert forsøk → evaluering → rapport. Importerer ingenting fra `app/` og berører ikke
MIE-strategiforskning, dommer, holdout eller H-025. **Ikke et handelsverktøy; testproblemet er Levenshtein-avstand.**

- **28.10:** implementert, 39/39 tester (bwrap på Linux-VM). Live-løp stoppet ved arXiv (proxy 403); ingen modell.
- **29.10:** ett live-løp i VM-en (`vm_runs/20261029T203102/…`): 15 arXiv-kilder, 21 modellkall, én kandidat.
  T01 **SUPPORTED** etter låste kriterier: eksakt på alle tilfeller, median hastighet 1,26× mot baseline,
  vant 7/7 parvise målinger. Tittelpilot (10 artikler): hastighetskravet **ikke** oppfylt, ingen verdi påvist.
- **30.10:** observasjonspilot for tidlig hastighetsscreening (6 kandidater, 3 kontroller). Ingen AI-kandidat
  ville blitt feilaktig avvist; for AI-kandidatene alene ga screeningen 1,39 s **ekstra** arbeid.
  Automatisk avvisning er ikke aktivert.
- Begrensninger: ett problem, én kildetype (arXiv-sammendrag), ren Python-baseline, macOS-sandkasse ikke verifisert.

## Merknad — 30.10.2026

- Discovery Lab er et nytt delprosjekt startet fem dager etter pausen på nye strategitester (23.10). Det er ikke
  en strategitest, men det er ny retning før tidligere arbeid er committet. Notert, ikke vurdert.
- Uncommittet arbeid er nå: chartmønster-motoren, to BTC-studier, Discovery Lab og denne statusfilen.
- `git pull` er ikke utført: `.git/index.lock` finnes fortsatt, og GitHub kan ikke nås fra Claudes Linux-miljø
  (proxy 403). De 5 manglende commitene på `feature/shadow-forward-v1` (26.08–07.10) er gamle og finnes allerede på main.

## Beslutning: to deler — 30.10.2026

Prosjektet deles i to med hvert sitt mål (bestemt av Anteneh 30.10.2026):

- **Del 1 — Trading (MIE).** Mål: en strategi som tjener penger etter kostnader. Rekkefølge: lagre uncommittet
  arbeid → kalibrert erstatning for kontrollgrensen → avklare H-025 → én forhåndsregistrert kandidat valgt fra
  litteraturen → ferske data → låst fremadrettet test. Ekte beløp, og da små, først etter bestått fremadrettet test.
  Ingen garanti for at en lønnsom strategi finnes.
- **Del 2 — Discovery Lab.** Mål: verktøy for automatisert forskning på avgrensede problemer med låste kriterier.
  Flyttes til **eget Git-repo** utenfor MIE (kopi først; mappen i MIE fjernes først når kopien er verifisert).
  Importerer ingenting fra `app/`. Neste problem utover Levenshtein velges senere.
- **Grense mellom delene:** Discovery Lab kan brukes til litteratursøk og kildekontroll for del 1, men skal ikke
  generere og teste handelsstrategier i bulk. Hvert forsøk mot markedsdata telles i forsøksregnskapet (DSR).
- Oversikt: dokumentet «MIE i dag: hva motoren faktisk gjør» (Claude Docs, 30.10.2026).

- **Publisert 30.10.2026:** Discovery Lab er offentlig på https://github.com/anteneh263-ux/discovery-lab
  (`main` = `1de5f13`, én commit, 49 filer, noreply-adresse). Kode, tester og nødvendig dokumentasjon er med;
  forsøks- og resultatmapper, rålogger, modellmeldinger og målinger er holdt lokalt. Første commit ble renset
  før push etter Antenehs kontroll (den første versjonen inneholdt 106 forsøks-/resultatfiler, personlige stier
  og Gmail i metadata). Gammel historikk ligger lokalt i `~/Desktop/discovery-lab/_to_delete/`, ikke publisert.
  `discovery_lab/` i MIE-arbeidsmappen er ennå ikke fjernet.

## Prioritet og rekkefølge — 03.11.2026 (bestemt av Anteneh)

- **Hovedprioritet nå: jobbprosjektet Bittensor Subnet Monitor** (eget repo, `~/Desktop/bittensor-subnet-monitor`).
  MIE er satt på vent til det er avsluttet. Ingen nye strategitester, motorendringer eller nye MIE-funksjoner før da.
- **Når MIE gjenopptas, i denne rekkefølgen:**
  1. **Sikre de nye appfilene** (lokal arbeidsflate fra 03.11: `app/workspace.py`, `app/workspace_launcher.py`,
     `app/workspace_ui/`, `tests/test_workspace_app.py`, `docs/WORKSPACE_APP.md`, `Start MIE.command`) sammen med
     øvrig uncommittet arbeid. Krever at `.git/index.lock` fjernes først.
  2. **Samle riktig kodeversjon og status:** arbeidsmappen står på `feature/shadow-forward-v1` (motor v2, 5 commits bak),
     mens GitHub-main har v4 som standard, v5 og H-030. Avklar hvilken versjon som gjelder, og bygg videre der.
     Arbeidsflaten er laget mot v2 og må tilpasses. Overflødig `discovery_lab/`-kopi fjernes (eget repo siden 30.10).
  3. **Kalibrere kontrollporten:** erstatte dagens grense (krever i praksis ≈ 1 årlig Sharpe) med en versjonert,
     kalibrert statistisk test (se seksjonen «Kalibrering av kontrollgrensen»).
  4. **Velge én begrunnet kandidat:** litteraturgjennomgang først, deretter én forhåndsregistrert test med låst
     referanse, kostnader og hovedmål. H-025-statusen avklares i samme runde.

## Fremdrift i runden «pålitelig evaluering» — 03.11.2026

Fra nå er det denne kopien av statusfilen (på grenen `workspace/secure-2026-11-03`) som oppdateres. Kopien i den gamle
arbeidsmappen på skrivebordet er historikk.

- **Steg 1 ferdig (20:15).** Grenen `workspace/secure-2026-11-03` er laget fra GitHub-main `aa4376d` i en egen git worktree
  (`~/MIE_SECURE_WORKTREE_2026-11-03`, utenfor iCloud) og pushet. Commits: `63dc64e` (95 filer: arbeidsflate,
  chartmønster-motor og BTC-studier, research-safety-logger, statusfil, README/pyproject-tillegg) og `d9edcc3`
  (RED-testene for Bitstamp-innsamleren, som skal feile til innsamleren finnes). Alle 96 filer kontrollert byte for byte
  mot sikkerhetskopien. Ikke med: `discovery_lab/` (eget repo), `.claude/`, `.agents/`, lokal P1B-testkladd (main har
  den committede versjonen). Alt ligger fortsatt i `~/Desktop/MIE_BACKUP_2026-11-03/`.
- **Funn: iCloud.** Skrivebordet synkroniseres med iCloud; nylig hentede git-objekter lå bare i skyen og kunne ikke leses
  fra Claudes miljø («Resource deadlock avoided»). Sannsynlig medvirkende årsak til låsproblemene. Repoene bør flyttes ut
  av iCloud når runden er ferdig. Låsen i den gamle mappen er ikke rørt.
- **Steg 2 (arbeidsflate mot main-motoren):**
  - På main er aktiv motor `srp-matrix-v4`. Det finnes ingen lagrede v4-resultater for H-002–H-028; arkivet viser
    derfor fortsatt de lagrede v2-dommene, merket som historiske. Ingen dommer er endret, ingen strategier kjørt.
  - Rettet: motorstatus sa «Denne arbeidsmappen tilbyr v2», som ikke lenger stemmer. Teksten bygger nå på aktiv motor
    fra `app.research_core.versioning` og skiller lagrede v2-dommer fra aktiv motor. Arkivet utvidet med H-026–H-028
    (lagrede v2-resultater som allerede lå på main); H-025 er fortsatt sperret. Dokumentasjon oppdatert.
  - Verifisert i Claudes skymiljø med fastapi 0.139.2 / starlette 1.3.1 fra MIE-miljøet på Mac-en: 9/9 tester for
    arbeidsflaten består (én ny), appen svarer over HTTP (28 rapporter, 0 feil, H-025 gir 404).
  - Kjent avvik i main sin egen test: `test_p1b_29b_legacy_rendering_carries_no_sequence_token` feiler i Claudes miljø
    på forskjell i midlertidige stier (`/pytest-of…`). Testen og koden den tester er uendret på grenen. Må kjøres på
    Mac-en for å avgjøre om det bare gjelder dette miljøet.
  - Mangler i arkivet: H-030 ble evaluert 23.10 (Not Supported), men resultatfilen ligger ikke på main, bare
    forhåndsregistreringen. Hentes inn som en egen, dokumentert endring.

- *P1B-testen (07.11):* `test_p1b_29b_legacy_rendering_carries_no_sequence_token` består på Mac-en (kjørt av
  `apply_step2.sh` 03.11). Feilen i Claudes miljø gjaldt bare det miljøet. Steg 2 committet som `7d134bd`.
  *Rettelse 08.11:* push av `7d134bd` er ikke bekreftet; lokal `origin/workspace/secure-2026-11-03` sto på
  `d9edcc3` per 08.11. Grenen pushes sammen med steg 3.

## Steg 3 — kalibrert kontrollport (`target-control-test-v1`), 04.–07.11.2026

- Spesifikasjon `docs/specs/SPEC-target-control-calibrated-v1.md`, godkjent 04.11. To daterte rettelser før/under
  utvikling: lag A bruker 1 710 dager (testens faktiske lengde utenfor utvalget, ikke 2 140); lag B begrenser
  syntetisk døgnavkastning til ±0,5 i log (binder på 0,007 % av dagene).
- Kode: `app/research_safety/control_test.py` (testen), `control_test_calibration.py` (simulering, frø, sperre mot
  gjentatt sluttkjøring), opt-in i `promotion_gate.py` (`control_test="target-control-test-v1"`, standard uendret).
  Tester: 14 nye i `tests/test_control_test.py`; 186 eksisterende port-/sikkerhetstester og 9 arbeidsflatetester består.
- Feil funnet og rettet i utvikling: syntetiske tidsstempler i feil enhet (lag B ugyldig, beholdt i `void/`);
  urealistiske døgnbevegelser over +100 %. Testen har fått sperrer mot begge.
- **Utviklingsresultat** (200 gjentak per scenario i lag A, 100 i lag B; andel godkjent):

  | Sann fordel | ρ 0,3 | ρ 0,5 | ρ 0,8 |
  | --- | --- | --- | --- |
  | 0.0 | 5.5% | 3.5% | 4.5% |
  | 0.25 | 23.5% | 34.5% | 47.5% |
  | 0.5 | 57.5% | 69.5% | 94.0% |
  | 1.0 | 97.0% | 98.0% | 100.0% |

  Lag B: nullkandidat (sann fordel -0.036) godkjent 7%;
  fordelkandidat (sann 0,50, q = 0.0531) godkjent 100%.
- Vurdering: falske godkjenninger rundt 5 % (samlet 4,5 % i lag A). Treffraten ved 0,5 i hovedscenariet (69,5 %)
  er under målet på 80 % og er begrenset av datamengden (målt forskjell svinger ±0,27 av tilfeldigheter), ikke av
  testvarianten. Akseptkravet ≤ 5,0 % med øvre grense ≤ 6,0 % er strengt for en test som ligger på 5 %.
- **Beslutning 07.11 (Anteneh):** sluttvalideringen kjøres **som låst**, uten endringer i krav eller parametre.
  Utfallet registreres ærlig; om testen tas i bruk avgjøres etterpå.

## Avvik: H-032 låst 04.11.2026 utenfor rekkefølgen

`~/Desktop/h032` inneholder en ny fremadrettet test, H-032 (lang/kontant trend med volatilitetsstyring på BTC og
ETH, vindu 01.12.2026–30.11.2027), låst 04.11 kl. 13.59 med sjekksummer (uendret ved kontroll 07.11). Gode trekk:
bare fremadrettet dom, forhåndsregistrerte kriterier, nøkternt mål (mindre fall, ikke høyere avkastning).

- Avvik fra rekkefølgen: steg 4 (én begrunnet kandidat) skulle vente til steg 1–3 var ferdige. Fordi H-032 kun
  måles fremover, brukes ingen historiske data, men avviket registreres her.
- Spesifikasjonens eget åpne spørsmål — overlapp med H-025 — ble ikke avklart før låsing. Ifølge kunnskapsbasen er
  H-025 en 200-dagers SMA-trendregel (lang/kontant) som blant annet omfatter BTC og ETH; overlappet er trolig stort.
  H-025 er ikke rørt.
- H-032 viser til forsøk 29–31 (H-FUND-R1, H-FUNDSIG-R1, H-TSMOM-R1) som ikke står i denne statusfilen. De må inn i
  forsøksregnskapet (DSR).
- Driftsrisiko: mappen ligger i iCloud-synkronisert skrivebord, og manuelle endringer av pris-/tilstandsfiler regnes
  som operasjonell feil. Bør flyttes ut av iCloud før første beslutning 30.11 (plassering er ikke låst).

## Oppdatert samling — 10. november 2026

Se `docs/MIE_CONSOLIDATION_2026-11-10.md` for gjeldende app, Git-bevaring og kontrollstatus. Appen starter fra `~/MIE_SECURE_WORKTREE_2026-11-03` med v4-motor. Lokal kildekode er bevart i commit `614be3b`; den foreldede index-låsen er fjernet fra Git-mappen. Kontrollport-v1 er rapportert NOT ACCEPTED, og TradingView-kontinuiteten venter fortsatt på endringshistorikk. Tidligere daterte beskrivelser over er historiske, ikke dagens status.
