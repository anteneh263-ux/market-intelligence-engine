# MIE lokal arbeidsflate

## Start og stopp

Dobbeltklikk `Start MIE.command` i prosjektmappen. Et terminalvindu starter den
lokale serveren og åpner nettleseren på http://127.0.0.1:8765. Hold terminalvinduet
åpent mens du bruker MIE. Stopp med Ctrl+C. En ny start gjenbruker en allerede
kjørende MIE-arbeidsflate på denne porten.

Alternativt, fra prosjektmappen:

```sh
.venv/bin/python -m app.workspace_launcher
```

Serveren kjører kun på maskinens loopback-adresse. Den bruker ingen VM,
modellabonnement, nettbaserte data eller nye pakker utover eksisterende MIE-miljø.
Hvis `.venv` mangler, følg installasjonsdelen i prosjektets README først.
Ved opptatt port: stopp programmet som bruker 8765; ikke bytt tilfeldig motor.

## Daglig bruk

1. **Oversikt:** se antall tilgjengelige strategier og nyere studier.
2. **Forskningsarkiv:** søk på navn, ID eller versjon; filtrer på utvalg og dom.
3. **Rapport:** les nøkkeltall, opprinnelig rapport og kildehash; last ned JSON.
   Åpne **Forklar enkelt** for forklaring av dommen, risiko og avkastning med
   regneeksempler i kroner. En liten ordliste forklarer økonomiske begreper.
   Forklaringene bygger på lagrede resultater, uten modellkall eller nye analyser.
   **Anbefalt neste steg** gir én prioritert forskningsoppgave, begrunnelse og
   manglende dokumentasjon. Ukjente begrunnelser gir «Utilstrekkelig grunnlag».
   Veiledningen endrer ingen dommer og starter ingen kjøring.
   **Spør MIE** svarer lokalt på spørsmål om dom, handler, risiko, avkastning,
   begreper og neste steg. Dette er regelbasert spørsmålshjelp uten språkmodell.
   Svar oppgir kilde; ukjente spørsmål får et tydelig forbehold. Ingen spørsmål
   sendes til en ekstern tjeneste. Samtalen tømmes ved rapportbytte eller omlasting.
4. **Beskriv en strategi:** lim inn eller åpne Pine-kode, eller bruk eksemplet.
   Last ned den foreslåtte beskrivelsen. Importen er heuristisk, ikke en full
   Pine-kompilator, og utfører ingen kode eller markedstest.
5. **Motor og avgrensning:** se faktisk lokal motor og skillet mot lagrede studier.

## Eksplisitt omfang

Dette er en lokal applikasjon for arkiv, rapporter og strategibeskrivelse.
Den viser H-002 til H-028 (unntatt H-025) fra navngitte v2-resultatfiler samt de to eksplisitt
utvalgte BTC-studiene fra 27. oktober. Manglende eller skadede kilder vises som
feil; de telles ikke som tilgjengelige rapporter. Ingen generelle filstier
aksepteres fra nettleseren. H-025, holdout og paper/forward-state åpnes ikke.

Denne koden bruker `app.workspace:app`, **ikke** det eldre API-et `app.main:app`.
Dermed eksponeres ingen gamle endepunkter for innhenting, evaluering eller handel.
Rapporttekst vises som tekst, ikke kjørbar HTML. Opplastet Pine-tekst behandles
kun i minnet; maksimalt 64 KB per forespørsel. Arkivet er skrivebeskyttet.

Fra 03.11.2026 ligger arbeidsflaten på grenen `workspace/secure-2026-11-03`, bygget på GitHub-main
(`aa4376d`), der aktiv motor er `srp-matrix-v4`. Motorstatus viser den aktive versjonen fra
`app.research_core.versioning`. Strategiarkivet viser fortsatt lagrede dommer fra `srp-matrix-v2`:
det finnes ingen lagrede v4-resultater for H-002–H-028, og arbeidsflaten kjører dem ikke på nytt.
Donchian-studien brukte v4, chartmønstrene en egen eksperimentell motor. Ingen historiske dommer er
endret. Nye forskningskjøringer, full Pine-kompilering, handelsintegrasjon og dokumentert inntjening
er utenfor leveransen. Discovery Lab er ikke koblet inn.

## Verifikasjon 2026-11-03 (på grenen mot main)

- Åtte målrettede enhetstester: grenser for filtilgang, ugyldige/svært store
  importer, manglende rapporter, kildehash, opprinnelsesvern og tillatte ruter.
- Nettleser: oversikt med 23 strategier og to studier, søk etter Donchian,
  rapportens faktiske nøkkeltall, eksempelimport med gjenkjente indikatorer.
- Ingen strategitester, modellkall eller endring av eksisterende resultater.

```sh
.venv/bin/python -m unittest tests.test_workspace_app -v
```
