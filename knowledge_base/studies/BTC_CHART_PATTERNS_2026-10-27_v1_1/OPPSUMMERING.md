# BTC chart patterns: ingen kvalifiserte kandidater

Fem låste mønsterregler på Binance BTCUSDT-perpetual, 4H, evaluert 2021–2024.
Prisdata fra 2020-02-20 brukes til oppvarming. Perioden er historisk og delvis sett
tidligere; den er ikke en ny urørt holdout. Én innstilling per mønster, ingen tuning.

| Mønster | Samlet nettoavkastning | Maksimalt fall ved candle-slutt | Handler | Vurdering |
|---|---:|---:|---:|---|
| Dobbel bunn | +27,1 % | 17,5 % | 10 | For lite datagrunnlag |
| Dobbel topp | −17,1 % | 17,9 % | 7 | Tap og for lite datagrunnlag |
| Omvendt hode–skuldre | −20,1 % | 31,0 % | 4 | Tap og for lite datagrunnlag |
| Hode–skuldre | Ingen handler | — | 0 | Ingen testbar handelsserie |
| Symmetrisk trekant | Ingen handler | — | 0 | Ingen testbar handelsserie |

Tallene er samlet avkastning over perioden, etter modellerte gebyrer, slippage og
funding. Strategiene har separate kontoer; avkastningen skal ikke summeres.
Det forhåndsvalgte minstekravet var 30 avsluttede handler per strategi.

Dobbel bunn hadde Sharpe 0,54. Den betingede kontrolltesten for tilfeldig timing ga
rå p=0,04, men p=0,20 etter Holm-korreksjon for alle fem. Dette er ikke tilstrekkelig
støtte for en fordel. En slik kontrolltest er heller ikke en fullstendig modell av
all utvelgelse og alle intrabar-exits. Ingen mønstergrenser endres etter resultatet.

## Hva som faktisk er implementert

Egen eksperimentell MIE-versjon `mie-chart-patterns-v1.1`, med bekreftede 5/5-pivoter,
inngang ved neste åpning, fast mengde etter inngang med nominell eksponering lik
egenkapital, stop, 2R-mål, tidsutgang etter sju dager og funding per oppgjør.
Gebyr/slippage er hver 5 basispunkter per side i hovedresultatet; ni
kostnadsscenarier er beregnet. Ved stop og mål i samme candle antas stop først.

Dette er presise, forholdsvis strenge maskinregler. Null handler betyr at akkurat
disse reglene ikke gav handler, ikke at mønsteret aldri forekommer på BTC.
Kodens tester viser positive syntetiske tilfeller for alle fem og kontrollerer
at signalene ikke finnes før informasjonen er tilgjengelig.

## Viktige begrensninger og kontrollfunn

- 4 048 eldre fundingoppgjør manglet markpris i funding-API-et. Vi brukte den
  historiske markpris-candlens åpning ved samme oppgjørstidspunkt, ikke en senere
  kurs. Tidsstempelavvik på opptil 47 ms er normalisert til oppgjørsgrensen.
- Referansen som kjøper og holder samme perpetual tømte egenkapitalen i modellen
  under langvarig funding og kursfall. Dens Sharpe er udefinert. Den kan ikke brukes
  til å hevde at strategiene slår kjøp-og-hold. Dette gjelder ikke vanlig BTC spot,
  og modellen simulerer ikke børsens faktiske margin- og likvidasjonsregler.
- Første v1-kjøring håndterte den negative restsaldoen feil ved å klippe den til
  null og tilordne Sharpe=0. Den er bevart med et valideringsvarsel. v1.1 retter
  regnskapet og lar sammenligningen feile når Sharpe er udefinert. Ingen av de fem
  strategienes handler eller avkastninger endret seg ved rettelsen.
- 54 tester ble samlet og kjørt i den kombinerte testen: alle bestod, inkludert
  26 nye tester. Etterkontrollen beregnet pris-P&L, begge handelsgebyrer og funding
  per handel på nytt og avstemte alle kontoer til sluttsaldo, også negativ saldo.
- Ingen uavhengig agentgjennomgang eller produksjonsverifisering er gjennomført.
  Dette er et forskningsresultat fra en ny motorutvidelse, ikke en offisiell
  `Supported`-dom fra de tidligere MIE-matrisene eller grunnlag for ekte handel.

Detaljer: [REPORT.md](REPORT.md), [RESULTS.json](RESULTS.json),
[CORRECTION.md](CORRECTION.md), [POST_RUN_VERIFICATION.json](POST_RUN_VERIFICATION.json).
De opprinnelige v1-filene er bevart i nabomappen uten omskriving av resultatene.
MIE-indeksen, gamle strategidommer, H-025 og paper/forward-filer er ikke endret.
