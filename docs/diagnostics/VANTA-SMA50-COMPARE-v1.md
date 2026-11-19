# SMA-50 sammenlignet med SMA-200

Separat utforskende historisk screening, besluttet 29. oktober 2026. Begge regler bruker 10 % av tilgjengelig kapital ved inngang, uten løpende rebalansering eller stop-loss. Resten er kontanter uten rente. Ingen H-025-artefakter eller MIE-evidenslagre er endret.

## Resultater

| Variant | Over 5 % EOD-fall | Over 8 % EOD-fall | Inaktivitet |
|---|---:|---:|---:|
| SMA200_zero_cost | 6/9 | 4/9 | 9/9 |
| SMA200_illustrative_cost | 7/9 | 4/9 | 9/9 |
| SMA50_zero_cost | 5/9 | 4/9 | 8/9 |
| SMA50_illustrative_cost | 5/9 | 4/9 | 8/9 |

## Begrensninger og videre valg

SMA-50 og 10 % ble bestemt før denne kjøringen, uten parameteroptimalisering. Dataperiodene er imidlertid allerede undersøkt tidligere; dette er ingen urørt testperiode. Ni overlappende perioder fra 2018–2026 til 8. juli 2026 er ikke ni uavhengige eksperimenter. Fullperiode og årlige startdatoer samsvarer med forrige screening, med 200 dagers historikk tilgjengelig også for SMA-50.

Kostnadsscenarioet bruker illustrative 8 bps per side og 10,95 % årlig carry på daglig åpningsnotional. Det er ikke faktisk fundinghistorikk. Kildeterskler fra Vantas README og miner-guide lest tidligere 29. oktober 2026; ingen validator-versjon er låst. Inaktivitet måles fra start/siste signalordre, også mens kontoen er flat.

Disse resultatene viser ikke kvalifisering eller lønnsomhet. Lavere eksponering er ikke en full risikostyringsmodell: posisjonen kan vokse med markedet. Intradagstopp krever mer detaljerte data og spesifisert gap-/slippagehåndtering. Ikke velg enda raskere glidende gjennomsnitt etter å ha sett disse resultatene uten ny forhåndsdefinert hypotese.

Før videre tilpasning: spesifiser en ny variant med løpende eksponeringsgrense og tapsstopp, bind den til Vantas faktiske regnskapsregler, og test på nye data. SMA-50 alene gir ikke grunnlag for betaling til hovednettet.

## Reproduksjon og status

Kildebase: 4a8f01826a0520d7cae921ac50be479b989994d6. Datasettets SHA-256 finnes i JSON-filen. Resultateier: denne diagnostikkrapporten og tilhørende JSON, uten autoritet over MIEs offisielle resultater.

    python scripts/vanta_sma50_comparison.py --csv data_csv/BTCUSD_1d.csv

Implementert og kjørt: separat historisk sammenligning. Ingen Vanta-integrasjon, hovednettdeltakelse, automatisk handel eller inntektsberegning.
